import hashlib
import json
import logging
import time
import uuid
from typing import Callable, Awaitable

from ..agents.base_agent import AgentContext
from ..agents.content_splitter import ContentSplitterAgent
from ..agents.teacher import TeacherAgent
from ..agents.question_generator import QuestionGeneratorAgent
from ..agents.evaluator import EvaluatorAgent
from ..agents.progress_manager import ProgressManagerAgent, correct_mc_score
from ..agents.drift_verifier import DriftVerifierAgent
from ..memory.working_memory import get_working_memory, TurnContext
from ..memory import session_memory, longterm_memory
from ..llm.base_provider import BaseLLMProvider
from ..llm.base_provider import MessageRole, LLMMessage
from ..utils import extract_json
from ..utils.token_counter import TokenCounter
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..tools.web_search import search_web
from .context_builder import build_adaptive_context

WSEmitter = Callable[[dict], Awaitable[None]]

_log = logging.getLogger("wl.orchestrator")

# 持久化用分隔符：僅存於 DB，不應出現在 WS／前端顯示字串中
_EXPL_PERSIST_SEP = "\n<<WL_EXPL_BODY>>\n"


def _pack_persisted_explanation(progress_md: str, teacher_body: str) -> str:
    return progress_md + _EXPL_PERSIST_SEP + teacher_body


def _persisted_progress_teacher_parts(stored: str) -> tuple[str | None, str]:
    """(progress_md, teacher_body)；progress_md 為 None 表示舊版單欄位（整段當作教師區塊還原）。"""
    if _EXPL_PERSIST_SEP in stored:
        prog, teacher = stored.split(_EXPL_PERSIST_SEP, 1)
        return prog, teacher
    return None, stored


def _markdown_for_client_from_persisted(stored: str, progress_fallback: str) -> str:
    prog, teacher = _persisted_progress_teacher_parts(stored)
    if prog is None:
        return (progress_fallback + teacher).rstrip() if teacher else progress_fallback.rstrip()
    body = teacher.strip()
    if body:
        return prog.rstrip() + "\n\n" + body
    return prog.rstrip()


def _teacher_only_from_persisted(stored: str) -> str:
    prog, teacher = _persisted_progress_teacher_parts(stored)
    return teacher if prog is not None else stored


def build_progress_table(stages: list[dict], current_idx: int) -> str:
    if not stages or current_idx < 0 or current_idx >= len(stages):
        return ""
    current = stages[current_idx]
    lines = [
        "### 📊 學習進度\n\n",
        f"> 當前節點：**{current['node_id']} — {current['title']}**\n\n",
        "| 節點編號 | 知識點名稱 | 狀態 |\n",
        "|----------|------------|------|\n",
    ]
    for i, s in enumerate(stages):
        if i < current_idx:
            status = "✅ 已完成"
        elif i == current_idx:
            status = "🔄 進行中"
        else:
            status = "⏳ 待學習"
        lines.append(f"| {s['node_id']} | {s['title']} | {status} |\n")
    lines.append("\n---\n\n")
    return "".join(lines)


# 受 generation_id 保護的 emit 包裝
# - 對「會被串流疊代取代」的訊息類型自動附帶 generation_id 並比對當前
# - 對系統訊息（error / kicked / explanation_complete 等）直接通過
_GENERATION_FILTERED_TYPES = {
    "explanation_chunk",
    "explanation_reset",
}


def make_generation_scoped_emit(base_emit, generation_id: str, get_current):
    """
    生成一個 emit 函式：
    - 若訊息類型在 _GENERATION_FILTERED_TYPES 中：
      * 比對 get_current() != generation_id 時直接丟棄（不呼叫 base_emit）
      * 否則 payload 自動補上 generation_id 後 emit
    - 其他訊息直接通過。
    """
    async def scoped_emit(msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type in _GENERATION_FILTERED_TYPES:
            if get_current() != generation_id:
                return  # 自己已被取代，靜默丟棄
            payload = dict(msg.get("payload") or {})
            payload["generation_id"] = generation_id
            msg = {**msg, "payload": payload}
        await base_emit(msg)
    return scoped_emit


class LearningOrchestrator:
    def __init__(self, llm: BaseLLMProvider):
        tc = TokenCounter()
        self.splitter = ContentSplitterAgent(llm, tc)
        self.teacher = TeacherAgent(llm, tc)
        self.questioner = QuestionGeneratorAgent(llm, tc)
        self.evaluator = EvaluatorAgent(llm, tc)
        self.progress = ProgressManagerAgent(llm, tc)
        self.drift_verifier = DriftVerifierAgent(llm, tc)
        self._pending_stages: list[dict] | None = None
        self._pending_start_args: dict | None = None

    # ── 工具方法 ──────────────────────────────────────────────

    def _build_questions_section(self, questions: list[dict]) -> str:
        if not questions:
            return ""
        lines = ["\n---\n\n### ✏️ 學習反饋\n\n"]
        lines.append("請回答以下問題，或寫下你的感悟與疑問：\n\n")
        for i, q in enumerate(questions):
            lines.append(f"{i + 1}. {q['text']}\n\n")
        lines.append(
            "> 💡 **提示**：不需要回答得很完美，寫下你目前的理解就好。"
            "答錯了我們一起釐清，這正是學習的過程。\n"
        )
        return "".join(lines)

    def _normalize_stage_source_chunks(self, stage: dict) -> list[dict]:
        chunks = stage.get("source_chunks")
        if isinstance(chunks, list) and chunks:
            return [c for c in chunks if isinstance(c, dict)]
        content = str(stage.get("content", "")).strip()
        if not content:
            return []
        return [{
            "chunk_id": f"s{stage.get('stage_id', 'x')}_c1",
            "quote": content[:500],
            "note": "fallback",
        }]

    def _build_source_corpus(self, stages: list[dict]) -> str:
        return "\n\n".join(
            f"[{s.get('node_id', s['stage_id'])}] {s['title']}\n{s.get('content', '')}\n"
            + "\n".join(
                f"- [{c.get('chunk_id', 'unknown')}] {c.get('quote', '')}"
                for c in self._normalize_stage_source_chunks(s)
            )
            for s in stages
        )

    async def _emit_session_started(
        self,
        session_id: str,
        stages: list[dict],
        emit: WSEmitter,
        stage_statuses: dict | None = None,
    ) -> None:
        payload: dict = {
            "session_id": session_id,
            "total_stages": len(stages),
            "stages": [
                {
                    "stage_id": s["stage_id"],
                    "node_id": s.get("node_id", ""),
                    "title": s["title"],
                    "kind": s.get("kind"),
                    "source_stage_id": s.get("source_stage_id"),
                    "source_chunks": self._normalize_stage_source_chunks(s),
                }
                for s in stages
            ],
        }
        if stage_statuses is not None:
            payload["stage_statuses"] = {str(k): v for k, v in stage_statuses.items()}
        await emit({"type": "session_started", "payload": payload})

    async def _verify_grounding(
        self,
        session_id: str,
        user_id: str,
        stage: dict,
        content_type: str,
        candidate_text: str,
        full_explanation: str = "",
    ) -> dict:
        verify_ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={
                "content_type": content_type,
                "source_chunks": self._normalize_stage_source_chunks(stage),
                "candidate_text": candidate_text,
                "full_explanation": full_explanation,
            },
        )
        return await self.drift_verifier.run(verify_ctx)

    def _rank_next_stage_candidates(
        self,
        stages: list[dict],
        current_idx: int,
        completed_stage_ids: set[int],
        weak_concepts: list[str],
        mastery_map: dict[str, float],
        stable_high: bool,
    ) -> list[dict]:
        pending = [
            (i, s) for i, s in enumerate(stages)
            if s["stage_id"] not in completed_stage_ids and i != current_idx
        ]
        if not pending:
            return []

        ranked: list[tuple[float, int]] = []
        weak_set = set(weak_concepts)
        for i, s in pending:
            concepts = s.get("key_concepts", [])
            if not concepts:
                concepts = [s.get("title", "")]
            weak_overlap = len(set(concepts).intersection(weak_set))
            low_mastery = sum(1 for c in concepts if mastery_map.get(c, 0.5) < 0.75)
            mastered = sum(1 for c in concepts if mastery_map.get(c, 0.0) >= 0.9)
            unseen = sum(1 for c in concepts if c not in mastery_map)
            distance_penalty = abs(i - current_idx) * 0.1

            if stable_high:
                # 穩定高掌握時：優先新概念，次優先未完整掌握，弱化已熟節點
                score = unseen * 3.0 + low_mastery * 1.2 + weak_overlap * 0.8 - mastered * 1.5 - distance_penalty
            else:
                # 仍在建立掌握時：優先補弱點與低掌握概念
                score = weak_overlap * 3.0 + low_mastery * 2.2 + unseen * 0.8 - mastered * 0.5 - distance_penalty
            ranked.append((score, i))

        ranked.sort(reverse=True)
        return [
            {
                "stage_id": stages[i]["stage_id"],
                "title": stages[i].get("title", ""),
                "score": round(score, 3),
                "is_dynamic": bool(stages[i].get("is_dynamic")),
            }
            for score, i in ranked
        ]

    def _pick_next_stage_index(
        self,
        stages: list[dict],
        current_idx: int,
        completed_stage_ids: set[int],
        weak_concepts: list[str],
        mastery_map: dict[str, float],
        stable_high: bool,
    ) -> tuple[int | None, list[dict]]:
        # 優先走順序：若下一個 stage 尚未完成，直接前進
        seq_idx = current_idx + 1
        sequential_pending = (
            seq_idx < len(stages)
            and stages[seq_idx]["stage_id"] not in completed_stage_ids
        )

        ranked = self._rank_next_stage_candidates(
            stages=stages,
            current_idx=current_idx,
            completed_stage_ids=completed_stage_ids,
            weak_concepts=weak_concepts,
            mastery_map=mastery_map,
            stable_high=stable_high,
        )

        if sequential_pending:
            return seq_idx, ranked

        # 順序 stage 已完成（或已到末尾），才用排名演算法選最佳待學節點
        if ranked:
            best = ranked[0]
            idx = next((i for i, s in enumerate(stages) if s["stage_id"] == best["stage_id"]), None)
            if idx is not None:
                return idx, ranked
        return None, ranked

    def _is_stable_high_performance(self, evaluations: list[dict]) -> bool:
        if len(evaluations) < 2:
            return False
        scores = [float(e.get("score", 0.0)) for e in evaluations]
        if min(scores) < 0.8:
            return False
        avg = sum(scores) / len(scores)
        return avg >= 0.87

    def _source_stage_id(self, stage: dict) -> int:
        return int(stage.get("source_stage_id") or stage.get("stage_id"))

    def _count_child_stages(self, stages: list[dict], source_stage_id: int, kind: str) -> int:
        return sum(
            1
            for s in stages
            if int(s.get("source_stage_id") or -1) == int(source_stage_id)
            and s.get("kind") == kind
        )

    async def _insert_reteach_stage(
        self,
        session_id: str,
        stages: list[dict],
        current_idx: int,
        reteach_focus: list[str],
    ) -> tuple[list[dict], int]:
        current = stages[current_idx]
        source_stage_id = self._source_stage_id(current)
        max_stage_id = max((s.get("stage_id", 0) for s in stages), default=0)
        new_stage_id = max_stage_id + 1
        reteach_number = self._count_child_stages(stages, source_stage_id, "reteach") + 1
        focus_text = "、".join(reteach_focus[:3]) if reteach_focus else "核心概念"
        new_stage = {
            "stage_id": new_stage_id,
            "node_id": f"T.{source_stage_id}.{reteach_number}",
            "title": f"重教：{current.get('title', focus_text)}",
            "content": (
                f"本節為重教子章節，請針對「{focus_text}」用完全不同的教學框架重新組織。\n\n"
                f"原章節內容：\n{current.get('content', '')[:1200]}"
            ),
            "key_concepts": current.get("key_concepts", [])[:5],
            "prerequisites": [current.get("title", "")],
            "estimated_questions": 3,
            "source_chunks": self._normalize_stage_source_chunks(current),
            "is_dynamic": True,
            "kind": "reteach",
            "source_stage_id": source_stage_id,
        }
        insert_idx = current_idx + 1
        updated = stages[:insert_idx] + [new_stage] + stages[insert_idx:]

        await session_memory.store_stages(session_id, updated)
        await session_memory.upsert_stage_progress(
            session_id=session_id,
            stage_id=new_stage_id,
            status="pending",
            attempts=0,
            best_score=0.0,
            understanding_notes={
                "dynamic": True,
                "kind": "reteach",
                "source_stage_id": source_stage_id,
                "focus": reteach_focus[:3],
            },
        )
        return updated, insert_idx

    async def _insert_remediation_stage(
        self,
        session_id: str,
        stages: list[dict],
        current_idx: int,
        remediation_focus: list[str],
    ) -> tuple[list[dict], int]:
        current = stages[current_idx]
        source_stage_id = self._source_stage_id(current)
        max_stage_id = max((s.get("stage_id", 0) for s in stages), default=0)
        new_stage_id = max_stage_id + 1
        remediation_number = self._count_child_stages(stages, source_stage_id, "remediation") + 1
        node_id = f"R.{source_stage_id}.{remediation_number}"
        focus_text = "、".join(remediation_focus[:3]) if remediation_focus else "核心概念"
        new_stage = {
            "stage_id": new_stage_id,
            "node_id": node_id,
            "title": f"補強：{focus_text}",
            "content": (
                f"本節為補強節點，針對「{focus_text}」重新建立理解。\n\n"
                f"請以原教材內容為主，回到前一節重點：\n{current.get('content', '')[:1200]}"
            ),
            "key_concepts": remediation_focus[:3] or current.get("key_concepts", [])[:2],
            "prerequisites": [current.get("title", "")],
            "estimated_questions": 3 if remediation_focus else 2,
            "source_chunks": self._normalize_stage_source_chunks(current),
            "is_dynamic": True,
            "kind": "remediation",
            "source_stage_id": source_stage_id,
        }
        insert_idx = current_idx + 1
        updated = stages[:insert_idx] + [new_stage] + stages[insert_idx:]

        await session_memory.store_stages(session_id, updated)
        await session_memory.upsert_stage_progress(
            session_id=session_id,
            stage_id=new_stage_id,
            status="pending",
            attempts=0,
            best_score=0.0,
            understanding_notes={
                "dynamic": True,
                "kind": "remediation",
                "source_stage_id": source_stage_id,
                "focus": remediation_focus[:3],
            },
        )
        return updated, insert_idx

    # ── 品質檢查 ──────────────────────────────────────────────

    def _check_stage_quality(
        self, stages: list[dict], all_chunks: list[dict]
    ) -> list[dict]:
        issues: list[dict] = []
        referenced: set[str] = set()

        for s in stages:
            chunk_ids = s.get("source_chunk_ids") or []
            referenced.update(chunk_ids)
            if len(chunk_ids) <= 1:
                issues.append({
                    "type": "possibly_too_small",
                    "stage_id": s.get("stage_id"),
                    "title": s.get("title", ""),
                })

        concept_map: dict[str, list] = {}
        for s in stages:
            for c in s.get("key_concepts", []):
                concept_map.setdefault(c, []).append(s.get("stage_id"))
        for concept, ids in concept_map.items():
            if len(ids) >= 3:
                issues.append({
                    "type": "concept_fragmented",
                    "concept": concept,
                    "stage_ids": ids,
                })

        orphans = [c["chunk_id"] for c in all_chunks if c["chunk_id"] not in referenced]
        if orphans:
            issues.append({"type": "orphaned_chunks", "chunk_ids": orphans})

        return issues

    # ── 初始化：切割內容，等待確認 ──────────────────────────

    async def start_session(
        self,
        session_id: str,
        user_id: str,
        source_chunks: list[dict],
        target_depth: str,
        question_mode: str,
        provider_name: str | None,
        model_name: str | None,
        emit: WSEmitter,
        source_file_ids: list[str] | None = None,
    ) -> None:
        hash_seed = "".join(c["text"][:80] for c in source_chunks)
        content_hash = hashlib.sha256(hash_seed.encode()).hexdigest()[:16]

        _log.info(
            "start_session  session=%s  user=%s  chunks=%d  depth=%s  mode=%s",
            session_id, user_id, len(source_chunks), target_depth, question_mode,
        )

        # ── 1. ContentSplitter 執行前，先建立 generating stub + 存入 source_chunks
        #       讓書櫃在 LLM 呼叫期間持久顯示「生成中」，頁面重整也不消失
        await session_memory.create_generating_stub(session_id, user_id, content_hash)
        await session_memory.insert_source_chunks(session_id, source_chunks)
        await emit({"type": "session_generating", "payload": {"session_id": session_id}})

        # ── 2. ContentSplitter（LLM 呼叫，可能耗時 10–60s）
        ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={
                "source_chunks": source_chunks,
                "max_stages": 30,
                "target_depth": target_depth,
            },
        )
        try:
            split_result = await self.splitter.run(ctx)
        except Exception:
            await session_memory.abandon_generating_stub(session_id)
            raise

        stages: list[dict] = split_result["stages"]
        summary: str = split_result.get("summary", "")
        _log.info(
            "start_session split done  session=%s  stages=%d",
            session_id, len(stages),
        )

        # 品質檢查（記錄 issues，不中斷流程）
        quality_issues = self._check_stage_quality(stages, source_chunks)
        if quality_issues:
            _log.warning(
                "start_session stage quality issues  session=%s  issues=%s",
                session_id, quality_issues,
            )

        nodes = [
            {"node_id": s["node_id"], "stage_id": s["stage_id"], "title": s["title"]}
            for s in stages
        ]

        self._pending_stages = stages
        self._pending_start_args = {
            "session_id": session_id,
            "user_id": user_id,
            "content_hash": content_hash,
            "summary": summary,
            "question_mode": question_mode,
        }

        # ── 3. ContentSplitter 完成，UPSERT 為 pending_confirmation
        #       source_chunks 已在步驟 1 存入，不需重複
        await session_memory.create_pending_session(
            session_id=session_id,
            user_id=user_id,
            content_hash=content_hash,
            summary=summary,
            stages=stages,
            nodes=nodes,
            provider_name=provider_name,
            model_name=model_name,
            question_mode=question_mode,
            source_file_ids=source_file_ids or [],
        )

        await emit({
            "type": "knowledge_map",
            "payload": {
                "nodes": nodes,
                "summary": summary,
            },
        })

    # ── 使用者確認知識地圖後開始教學 ────────────────────────

    async def confirm_session(self, session_id: str, user_id: str, emit: WSEmitter) -> None:
        _log.info("confirm_session  session=%s  user=%s", session_id, user_id)
        stages = self._pending_stages
        args = self._pending_start_args

        if not stages or not args:
            # in-memory 遺失（例如重整後），嘗試從 DB 恢復
            session_row = await session_memory.get_session(session_id)
            if not session_row or session_row.get("status") != "pending_confirmation":
                await emit({"type": "error", "payload": {"message": "無法確認學習路線，請重新上傳材料"}})
                return
            stages = json.loads(session_row.get("stages_json") or "[]")
            if not stages:
                await emit({"type": "error", "payload": {"message": "無法確認學習路線，請重新上傳材料"}})
                return
            args = {
                "session_id": session_id,
                "user_id": user_id,
                "content_hash": session_row["content_hash"],
                "summary": session_row.get("raw_content_summary") or "",
                "question_mode": session_row.get("question_mode") or "short_answer",
            }

        await session_memory.activate_pending_session(session_id)
        await session_memory.store_stages(session_id, stages)
        for s in stages:
            await session_memory.upsert_stage_progress(
                session_id, s["stage_id"], "pending", 0, 0.0, {}
            )

        wm = get_working_memory(session_id)
        wm.reset_for_new_stage(0)
        wm.stages = stages

        await self._emit_session_started(session_id, stages, emit)

        await self.run_stage(
            session_id,
            user_id,
            stages,
            0,
            args.get("question_mode", "short_answer"),
            emit,
        )

    # ── 教學單一節點 ─────────────────────────────────────────

    async def run_stage(
        self,
        session_id: str,
        user_id: str,
        stages: list[dict],
        stage_index: int,
        question_mode: str,
        emit: WSEmitter,
        skip_progress_emit: bool = False,
    ) -> None:
        _t_stage = time.perf_counter()
        wm = get_working_memory(session_id)
        wm.reset_for_new_stage(stages[stage_index]["stage_id"])
        wm.question_mode = question_mode
        wm.source_corpus = self._build_source_corpus(stages)
        stage = stages[stage_index]
        _log.info(
            "run_stage  session=%s  stage_id=%s  title=%s  attempt=%d  mode=%s",
            session_id, stage["stage_id"], stage.get("title", "")[:40],
            wm.current_attempt, question_mode,
        )

        await session_memory.update_current_stage(session_id, stage["stage_id"])
        await session_memory.upsert_stage_progress(
            session_id, stage["stage_id"], "in_progress", 0, 0.0, {}
        )

        user_profile_summary = await longterm_memory.get_user_profile_summary(user_id)
        prev_stage = stages[stage_index - 1] if stage_index > 0 else None

        # 組裝完整學生狀態包
        adaptive_ctx = await build_adaptive_context(
            session_id=session_id,
            user_id=user_id,
            stage=stage,
            current_attempt=wm.current_attempt,
            stages=stages,
        )

        # 1. 進度表（可跳過 emit：例如 resume 已先播出進度表，僅接續教師串流）
        progress_md = build_progress_table(stages, stage_index)
        if not skip_progress_emit:
            await emit({"type": "explanation_chunk", "payload": {"chunk": progress_md, "is_final": False}})
        # 立即持久化：即使尚未收到第一段教師串流，重整後也能還原進度表、並走 _resume 而非重跑整段
        await session_memory.store_stage_explanation(
            session_id, stage["stage_id"],
            _pack_persisted_explanation(progress_md, ""),
        )

        # 2. 串流講解（📖 + 🔗）
        ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={
                "stage": stage,
                "prev_stage_title": prev_stage["title"] if prev_stage else None,
                "user_profile_summary": user_profile_summary,
                "adaptive_context": adaptive_ctx,
            },
        )
        full_explanation = ""
        async for chunk in self.teacher.stream_explanation(ctx):
            full_explanation += chunk
            await session_memory.store_stage_explanation(
                session_id, stage["stage_id"],
                _pack_persisted_explanation(progress_md, full_explanation),
            )
            await emit({"type": "explanation_chunk", "payload": {"chunk": chunk, "is_final": False}})
        explanation_rewritten = False
        explain_verify = await self._verify_grounding(
            session_id=session_id,
            user_id=user_id,
            stage=stage,
            content_type="explanation",
            candidate_text=full_explanation,
        )
        if not explain_verify.get("aligned", False):
            guidance = explain_verify.get("revision_hint") or "請僅依據 source_chunks 重寫，避免教材外推。"
            retry_ctx = AgentContext(
                session_id=session_id,
                user_id=user_id,
                task_payload={
                    "stage": {
                        **stage,
                        "content": stage.get("content", "") + f"\n\n（對齊修正要求：{guidance}）",
                    },
                    "prev_stage_title": prev_stage["title"] if prev_stage else None,
                    "user_profile_summary": user_profile_summary,
                    "adaptive_context": adaptive_ctx,
                },
            )
            full_explanation = ""
            async for chunk in self.teacher.stream_explanation(retry_ctx):
                full_explanation += chunk
                await session_memory.store_stage_explanation(
                    session_id, stage["stage_id"],
                    _pack_persisted_explanation(progress_md, full_explanation),
                )
            explanation_rewritten = True
        if explanation_rewritten:
            await emit({"type": "explanation_reset", "payload": {}})
            await emit({"type": "explanation_chunk", "payload": {"chunk": progress_md, "is_final": False}})
            await emit({"type": "explanation_chunk", "payload": {"chunk": full_explanation, "is_final": False}})
        wm.current_explanation = full_explanation
        await session_memory.store_stage_explanation(
            session_id, stage["stage_id"],
            _pack_persisted_explanation(progress_md, full_explanation),
        )

        # 3. 提取教學意圖（non-streaming call，供問題生成器對齊）
        teaching_intent = await self.teacher.extract_teaching_intent(full_explanation, stage)
        wm.current_teaching_intent = teaching_intent

        # 4. 生成問題
        q_ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={
                "stage": stage,
                "teaching_intent": teaching_intent,
                "allowed_evidence": adaptive_ctx.get("allowed_evidence", []),
                "full_explanation": full_explanation,
                "num_questions": max(4, stage.get("estimated_questions", 2) * 2)
                if question_mode == "multiple_choice"
                else stage.get("estimated_questions", 2),
                "attempt_number": 1,
                "previous_question_ids": [],
                "question_mode": question_mode,
            },
        )
        q_result = await self.questioner.run(q_ctx)
        questions: list[dict] = q_result.get("questions", [])
        questions_verify = await self._verify_grounding(
            session_id=session_id,
            user_id=user_id,
            stage=stage,
            content_type="questions",
            candidate_text=json.dumps(questions, ensure_ascii=False),
            full_explanation=full_explanation,
        )
        if not questions_verify.get("aligned", False):
            retry_q_ctx = AgentContext(
                session_id=session_id,
                user_id=user_id,
                task_payload={
                    "stage": {
                        **stage,
                        "content": stage.get("content", "")
                        + "\n\n（對齊修正要求：請每題僅依 source_chunks 設計，並補 evidence_chunk_ids）",
                    },
                    "full_explanation": full_explanation,
                    "num_questions": max(4, stage.get("estimated_questions", 2) * 2)
                    if question_mode == "multiple_choice"
                    else stage.get("estimated_questions", 2),
                    "attempt_number": 1,
                    "previous_question_ids": [],
                    "question_mode": question_mode,
                },
            )
            q_result = await self.questioner.run(retry_q_ctx)
            questions = q_result.get("questions", [])
        wm.pending_questions = questions
        await session_memory.store_stage_questions(session_id, stage["stage_id"], questions)

        # 4. 問題區塊
        questions_md = self._build_questions_section(questions)
        if questions_md:
            await emit({"type": "explanation_chunk", "payload": {"chunk": questions_md, "is_final": False}})

        # 5. 結束串流
        await emit({"type": "explanation_chunk", "payload": {"chunk": "", "is_final": True}})
        await emit({
            "type": "explanation_complete",
            "payload": {
                "stage_id": stage["stage_id"],
                "stage_title": stage["title"],
                "full_explanation": progress_md + full_explanation + questions_md,
            },
        })

        # 6. 發送第一道問題事件
        if questions:
            q = questions[0]
            wm.current_turn = TurnContext(
                turn_id=str(uuid.uuid4()),
                question_id=q["question_id"],
                question_text=q["text"],
            )
            await emit({
                "type": "question",
                "payload": {
                    "question_id": q["question_id"],
                    "text": q["text"],
                    "type": q.get("type", "understand"),
                    "answer_mode": q.get("answer_mode", "short_answer"),
                    "options": q.get("options", []),
                    "evidence_chunk_ids": q.get("evidence_chunk_ids", []),
                    "stage_id": stage["stage_id"],
                    "attempt_number": 1,
                },
            })
        _log.info(
            "run_stage DONE  session=%s  stage_id=%s  questions=%d  elapsed=%.2fs",
            session_id, stage["stage_id"], len(questions),
            time.perf_counter() - _t_stage,
        )

    # ── 處理使用者答案 ────────────────────────────────────────

    async def handle_answer(
        self,
        session_id: str,
        user_id: str,
        question_id: str,
        answer: str,
        emit: WSEmitter,
    ) -> None:
        _log.info(
            "handle_answer  session=%s  question_id=%s  answer_len=%d",
            session_id, question_id, len(answer),
        )
        wm = get_working_memory(session_id)
        stages: list[dict] = wm.stages
        if not stages:
            await emit({"type": "error", "payload": {"message": "學習階段資料遺失，請重新開始會話"}})
            return

        session = await session_memory.get_session(session_id)
        if not session:
            await emit({"type": "error", "payload": {"message": "會話不存在"}})
            return

        current_stage_id: int = session["current_stage_id"]
        stage = next((s for s in stages if s["stage_id"] == current_stage_id), None)
        if not stage:
            return

        current_turn = wm.current_turn
        if not current_turn or current_turn.question_id != question_id:
            return

        current_turn.user_answer = answer

        q_obj = next(
            (q for q in wm.pending_questions if isinstance(q, dict) and q.get("question_id") == question_id),
            {"question_id": question_id, "text": current_turn.question_text,
             "type": "understand", "key_concepts_tested": [], "expected_answer_hints": []},
        )

        eval_ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={
                "question": q_obj,
                "user_answer": answer,
                "compressed_history": wm.get_compressed_history(max_turns=3),
                "source_chunks": self._normalize_stage_source_chunks(stage),
            },
        )
        eval_result = await self.evaluator.run(eval_ctx)
        current_turn.evaluation = eval_result
        wm.record_completed_turn()

        await session_memory.insert_qa_record(
            session_id=session_id,
            stage_id=current_stage_id,
            question_id=question_id,
            question_text=current_turn.question_text,
            question_type=q_obj.get("type", "understand"),
            user_answer=answer,
            score=eval_result.get("score", 0.0),
            feedback=eval_result.get("feedback", ""),
        )

        await emit({
            "type": "feedback",
            "payload": {
                "question_id": question_id,
                "score": eval_result.get("score", 0.0),
                "feedback_text": eval_result.get("feedback", ""),
                "needs_clarification": eval_result.get("needs_clarification", False),
                "clarification_question": eval_result.get("clarification_question"),
            },
        })

        raw_score = eval_result.get("score", 0.0)
        mastery_score = (
            correct_mc_score(raw_score) if wm.question_mode == "multiple_choice" else raw_score
        )
        misconception_patterns_all: list[dict] = eval_result.get("misconception_patterns", [])
        understood_concepts: list[str] = eval_result.get("understood_concepts", [])
        # 高分且有教學意圖記錄時，回寫成功類比
        analogies_to_record: list[str] = (
            wm.current_teaching_intent.get("analogies_used", [])
            if wm.current_teaching_intent and mastery_score >= 0.8
            else []
        )
        for concept in stage.get("key_concepts", []):
            mp = next(
                (p for p in misconception_patterns_all if p.get("concept") == concept), None
            )
            effective = concept in understood_concepts and bool(analogies_to_record)
            await longterm_memory.update_concept_mastery(
                user_id=user_id,
                concept_name=concept,
                new_score=mastery_score,
                confused_concepts=eval_result.get("confused_concepts", []),
                misconception_pattern=mp,
                analogy_used=analogies_to_record[0] if effective and analogies_to_record else None,
                lesson_was_effective=effective,
            )

        remaining_qs = [
            q for q in wm.pending_questions
            if isinstance(q, dict) and q.get("question_id")
            and q["question_id"] not in [t.question_id for t in wm.stage_turns]
            and q["question_id"] != question_id
        ]

        if remaining_qs:
            q = remaining_qs[0]
            wm.current_turn = TurnContext(
                turn_id=str(uuid.uuid4()),
                question_id=q["question_id"],
                question_text=q["text"],
            )
            await emit({
                "type": "question",
                "payload": {
                    "question_id": q["question_id"],
                    "text": q["text"],
                    "type": q.get("type", "understand"),
                    "answer_mode": q.get("answer_mode", "short_answer"),
                    "options": q.get("options", []),
                    "evidence_chunk_ids": q.get("evidence_chunk_ids", []),
                    "stage_id": current_stage_id,
                    "attempt_number": wm.current_attempt,
                },
            })
        else:
            current_idx = next(
                (i for i, s in enumerate(stages) if s["stage_id"] == current_stage_id), -1
            )
            await self._make_progress_decision(
                session_id, user_id, stages, stage, current_idx, wm, emit
            )

    # ── 進度決策 ─────────────────────────────────────────────

    async def _make_progress_decision(
        self,
        session_id: str,
        user_id: str,
        stages: list[dict],
        stage: dict,
        current_idx: int,
        wm,
        emit: WSEmitter,
    ) -> None:
        source_stage_id = self._source_stage_id(stage)
        prog_ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={
                "evaluations": wm.stage_evaluations,
                "pass_threshold": 0.75,
                "max_attempts": 3,
                "total_stages": len(stages),
                "current_stage_id": stage["stage_id"],
                "question_mode": wm.question_mode,
                "current_attempt": wm.current_attempt,
                "is_dynamic": stage.get("is_dynamic", False),
                "stage_kind": stage.get("kind", "main"),
                "source_stage_id": source_stage_id,
                "source_reteach_count": self._count_child_stages(stages, source_stage_id, "reteach"),
                "source_remediation_count": self._count_child_stages(stages, source_stage_id, "remediation"),
                "max_reteach": 2,
                "max_remediation": 2,
            },
        )
        decision = await self.progress.run(prog_ctx)
        d = decision["decision"]
        _log.info(
            "progress_decision  session=%s  stage_id=%s  decision=%s  "
            "best_score=%.2f  attempt=%d",
            session_id, stage["stage_id"], d,
            decision.get("best_score", 0), wm.current_attempt,
        )
        _log.debug(
            "progress_decision DETAIL  session=%s\n%s",
            session_id, json.dumps(decision, ensure_ascii=False, default=str),
        )
        decision_reasons: list[str] = []
        dynamic_stage_inserted = False
        ranked_candidates: list[dict] = []
        weak_concepts: list[str] = []
        mastery_map: dict[str, float] = {}
        stable_high = self._is_stable_high_performance(wm.stage_evaluations)
        stage_statuses = await session_memory.get_stage_statuses(session_id)
        completed_stage_ids = {
            sid for sid, status in stage_statuses.items() if status == "completed"
        }
        next_stage_idx: int | None = None
        stages_for_run = stages

        await session_memory.upsert_stage_progress(
            session_id=session_id,
            stage_id=stage["stage_id"],
            status="completed" if d == "advance" else "in_progress",
            attempts=len(wm.stage_turns),
            best_score=decision["best_score"],
            understanding_notes={"confused": decision.get("remediation_focus") or []},
        )
        selection_reason: dict | None = None
        if d == "advance":
            completed_stage_ids.add(stage["stage_id"])
            weak_raw = await longterm_memory.get_weak_concepts(user_id)
            weak_concepts = [] if weak_raw == "無" else [c.strip() for c in weak_raw.split("、") if c.strip()]
            all_concepts = list(
                dict.fromkeys([c for s in stages for c in s.get("key_concepts", [])])
            )
            mastery_map = await longterm_memory.get_concept_mastery_map(user_id, all_concepts)
            decision_reasons.append(
                f"本節最佳分數 {decision['best_score']:.0%}，達到前進門檻。"
            )
            decision_reasons.append(
                "近期作答穩定度：" + ("高（可加速引入新知）" if stable_high else "一般（維持補強優先）")
            )
            next_stage_idx, ranked_candidates = self._pick_next_stage_index(
                stages=stages,
                current_idx=current_idx,
                completed_stage_ids=completed_stage_ids,
                weak_concepts=weak_concepts,
                mastery_map=mastery_map,
                stable_high=stable_high,
            )
            if next_stage_idx is not None:
                decision_reasons.append(
                    f"下一節選擇：{stages[next_stage_idx]['title']}（依弱點/掌握度/新知權重計算）。"
                )
            # 組裝選課理由（Phase 4）：讓 ContextBuilder → TeacherAgent 知道為什麼選這個節點
            if next_stage_idx is not None:
                next_stage_concepts = stages[next_stage_idx].get("key_concepts", [])
                target_concepts = [c for c in next_stage_concepts if mastery_map.get(c, 0.5) < 0.75]
                weak_overlap_count = len(set(next_stage_concepts).intersection(set(weak_concepts)))
                selection_reason = {
                    "reason": f"弱點重疊度={weak_overlap_count}，低掌握概念數={len(target_concepts)}，{'穩定高分模式' if stable_high else '補強優先模式'}",
                    "target_concepts": target_concepts,
                    "stable_high": stable_high,
                }
        elif d == "reteach":
            focus = decision.get("remediation_focus") or stage.get("key_concepts", [])[:2]
            weak_raw = await longterm_memory.get_weak_concepts(user_id)
            weak_concepts = [] if weak_raw == "無" else [c.strip() for c in weak_raw.split("、") if c.strip()]
            all_concepts = list(
                dict.fromkeys([c for s in stages for c in s.get("key_concepts", [])])
            )
            mastery_map = await longterm_memory.get_concept_mastery_map(user_id, all_concepts)
            if focus:
                stages_for_run, next_stage_idx = await self._insert_reteach_stage(
                    session_id=session_id,
                    stages=stages,
                    current_idx=current_idx,
                    reteach_focus=focus,
                )
                wm.stages = stages_for_run
                stages = stages_for_run
                dynamic_stage_inserted = True
                decision_reasons.append(
                    "已動態插入重教子章節（" + "、".join(focus[:3]) + "）。"
                )
            if focus:
                decision_reasons.append("重教焦點：" + "、".join(focus[:3]))
            decision_reasons.append("重教以獨立子章節呈現，原章節講解與答題紀錄保持不變。")
        elif d == "remediate":
            focus = decision.get("remediation_focus") or stage.get("key_concepts", [])[:2]
            weak_raw = await longterm_memory.get_weak_concepts(user_id)
            weak_concepts = [] if weak_raw == "無" else [c.strip() for c in weak_raw.split("、") if c.strip()]
            all_concepts = list(
                dict.fromkeys([c for s in stages for c in s.get("key_concepts", [])])
            )
            mastery_map = await longterm_memory.get_concept_mastery_map(user_id, all_concepts)
            if focus:
                stages_for_run, next_stage_idx = await self._insert_remediation_stage(
                    session_id=session_id,
                    stages=stages,
                    current_idx=current_idx,
                    remediation_focus=focus,
                )
                wm.stages = stages_for_run
                stages = stages_for_run
                dynamic_stage_inserted = True
                decision_reasons.append(
                    "已動態插入補強子章節（" + "、".join(focus[:3]) + "）。"
                )
            if focus:
                decision_reasons.append("補強焦點：" + "、".join(focus[:3]))
            decision_reasons.append("補強以獨立子章節呈現，原章節講解與答題紀錄保持不變。")
        elif d == "retry":
            decision_reasons.append("尚未達門檻，先在同節點調整題目難度再嘗試。")

        next_stage_id = stages[next_stage_idx]["stage_id"] if next_stage_idx is not None else None
        next_stage_score = ranked_candidates[0]["score"] if ranked_candidates else None
        strategy_snapshot = {
            "current_stage_id": stage["stage_id"],
            "current_stage_title": stage.get("title", ""),
            "stable_high": stable_high,
            "weak_concepts": weak_concepts,
            "mastery_map": mastery_map,
            "score_trend": [round(float(e.get("score", 0.0)), 3) for e in wm.stage_evaluations[-5:]],
            "next_stage_candidates": ranked_candidates[:5],
            "remediation_focus": decision.get("remediation_focus") or [],
            "dynamic_stage_inserted": dynamic_stage_inserted,
            "selection_reason": selection_reason,
            "high_severity_misconceptions": decision.get("high_severity_misconceptions") or [],
            "repeated_patterns_detected": decision.get("repeated_patterns_detected", False),
        }
        payload = {
            "decision": d,
            "message": decision["message"],
            "next_stage_id": next_stage_id,
            "next_stage_score": next_stage_score,
            "best_score": decision["best_score"],
            "reason_lines": decision_reasons,
            "strategy_snapshot": strategy_snapshot,
        }
        await session_memory.insert_decision_record(
            session_id=session_id,
            stage_id=stage["stage_id"],
            decision=d,
            best_score=decision["best_score"],
            next_stage_id=next_stage_id,
            next_stage_score=next_stage_score,
            reason_lines=decision_reasons,
            strategy_snapshot=strategy_snapshot,
        )
        await emit({"type": "stage_decision", "payload": payload})

        if d == "advance":
            if next_stage_idx is not None:
                await longterm_memory.update_user_profile(user_id, len(wm.stage_turns))
                next_stage = stages[next_stage_idx]
                await session_memory.update_current_stage(session_id, next_stage["stage_id"])
                await session_memory.upsert_stage_progress(
                    session_id=session_id,
                    stage_id=next_stage["stage_id"],
                    status="in_progress",
                    attempts=0,
                    best_score=0.0,
                    understanding_notes={
                        "source_stage_id": self._source_stage_id(next_stage),
                    },
                )
                refreshed_statuses = await session_memory.get_stage_statuses(session_id)
                await self._emit_session_started(session_id, stages, emit, refreshed_statuses)
                await self.run_stage(
                    session_id, user_id, stages, next_stage_idx, wm.question_mode, emit
                )
            else:
                await session_memory.complete_session(session_id)
                await emit({"type": "course_completed", "payload": {"message": "恭喜！你已完成所有學習階段。"}})

        elif d == "retry":
            wm.current_attempt += 1
            # 立即持久化輪次：讓 _resume_from_stored 能還原 current_attempt，
            # 防止 resume 在題目生成完成前觸發重複的 _make_progress_decision。
            await session_memory.update_stage_attempt(
                session_id, stage["stage_id"], wm.current_attempt
            )

            # retry：不清除原講解，在末尾附加分隔線提示再試
            retry_separator = (
                f"\n\n---\n\n### 🔄 第 {wm.current_attempt} 次嘗試\n\n"
                f"{decision['message']}\n\n"
            )
            await emit({"type": "explanation_chunk", "payload": {"chunk": retry_separator, "is_final": False}})

            prev_q_ids = [t.question_id for t in wm.stage_turns]
            prev_q_texts = [t.question_text for t in wm.stage_turns]
            q_ctx = AgentContext(
                session_id=session_id,
                user_id=user_id,
                task_payload={
                    "stage": stage,
                    "full_explanation": wm.current_explanation,
                    "num_questions": 4 if wm.question_mode == "multiple_choice" else 2,
                    "attempt_number": wm.current_attempt,
                    "previous_question_ids": prev_q_ids,
                    "previous_question_texts": prev_q_texts,
                    "question_mode": wm.question_mode,
                },
            )
            q_result = await self.questioner.run(q_ctx)
            questions: list[dict] = q_result.get("questions", [])
            questions_verify = await self._verify_grounding(
                session_id=session_id,
                user_id=user_id,
                stage=stage,
                content_type="questions",
                candidate_text=json.dumps(questions, ensure_ascii=False),
                full_explanation=wm.current_explanation,
            )
            if not questions_verify.get("aligned", False):
                retry_q_ctx = AgentContext(
                    session_id=session_id,
                    user_id=user_id,
                    task_payload={
                        "stage": {
                            **stage,
                            "content": stage.get("content", "")
                            + "\n\n（對齊修正要求：請每題僅依 source_chunks 設計，並補 evidence_chunk_ids）",
                        },
                        "full_explanation": wm.current_explanation,
                        "num_questions": 4 if wm.question_mode == "multiple_choice" else 2,
                        "attempt_number": wm.current_attempt,
                        "previous_question_ids": prev_q_ids,
                        "previous_question_texts": prev_q_texts,
                        "question_mode": wm.question_mode,
                    },
                )
                q_result = await self.questioner.run(retry_q_ctx)
                questions = q_result.get("questions", [])
            used_ids = {t.question_id for t in wm.stage_turns}
            for q in questions:
                if not q.get("question_id") or q["question_id"] in used_ids:
                    q["question_id"] = f"q_{stage['stage_id']}_{wm.current_attempt}_{uuid.uuid4().hex[:8]}"
                used_ids.add(q["question_id"])
            wm.pending_questions = questions
            wm.stage_evaluations = []

            # 持久化：讓重整後 resume 直接還原而不重生成
            combined_explanation_retry = wm.current_explanation + retry_separator
            progress_md = build_progress_table(stages, current_idx)
            await session_memory.store_stage_explanation(
                session_id,
                stage["stage_id"],
                _pack_persisted_explanation(progress_md, combined_explanation_retry),
            )
            await session_memory.store_stage_questions(session_id, stage["stage_id"], questions)
            wm.current_explanation = combined_explanation_retry

            questions_md = self._build_questions_section(questions)
            if questions_md:
                await emit({"type": "explanation_chunk", "payload": {"chunk": questions_md, "is_final": False}})

            await emit({"type": "explanation_chunk", "payload": {"chunk": "", "is_final": True}})
            await emit({
                "type": "explanation_complete",
                "payload": {
                    "stage_id": stage["stage_id"],
                    "full_explanation": combined_explanation_retry + questions_md,
                },
            })

            if questions:
                q = questions[0]
                wm.current_turn = TurnContext(
                    turn_id=str(uuid.uuid4()),
                    question_id=q["question_id"],
                    question_text=q["text"],
                )
                await emit({
                    "type": "question",
                    "payload": {
                        "question_id": q["question_id"],
                        "text": q["text"],
                        "type": q.get("type", "understand"),
                        "answer_mode": q.get("answer_mode", "short_answer"),
                        "options": q.get("options", []),
                        "evidence_chunk_ids": q.get("evidence_chunk_ids", []),
                        "stage_id": stage["stage_id"],
                        "attempt_number": wm.current_attempt,
                    },
                })

        elif d in ("remediate", "reteach") and next_stage_idx is not None:
            await session_memory.upsert_stage_progress(
                session_id=session_id,
                stage_id=stage["stage_id"],
                status="completed",
                attempts=wm.current_attempt,
                best_score=decision["best_score"],
                understanding_notes={
                    "branched_to": d,
                    "focus": decision.get("remediation_focus") or [],
                    "source_stage_id": source_stage_id,
                },
            )
            next_stage = stages[next_stage_idx]
            await session_memory.update_current_stage(session_id, next_stage["stage_id"])
            await session_memory.upsert_stage_progress(
                session_id=session_id,
                stage_id=next_stage["stage_id"],
                status="in_progress",
                attempts=0,
                best_score=0.0,
                understanding_notes={
                    "source_stage_id": self._source_stage_id(next_stage),
                },
            )
            refreshed_statuses = await session_memory.get_stage_statuses(session_id)
            await self._emit_session_started(session_id, stages, emit, refreshed_statuses)
            await self.run_stage(
                session_id, user_id, stages, next_stage_idx, wm.question_mode, emit
            )

    async def handle_student_question(
        self,
        session_id: str,
        question: str,
        emit: WSEmitter,
        stage_id: int | None = None,
    ) -> None:
        _log.info(
            "handle_student_question  session=%s  stage_id=%s  question_len=%d",
            session_id, stage_id, len(question),
        )
        wm = get_working_memory(session_id)
        effective_stage_id = stage_id if stage_id is not None else wm.current_stage_id
        stage = next((s for s in wm.stages if s.get("stage_id") == effective_stage_id), None)
        stage_title = stage["title"] if stage else "目前節點"
        stage_content = stage.get("content", "") if stage else ""
        source = wm.source_corpus or stage_content
        if not source:
            await emit({
                "type": "tutor_reply",
                "payload": {
                    "question": question,
                    "answer": "目前沒有可用教材內容，請先開始學習流程。",
                    "stage_id": effective_stage_id,
                },
            })
            return

        # 邊界判定只用當前 stage 的 source chunks，避免全文截斷導致 judge 只看到第一章
        if stage:
            _judge_lines = []
            for c in self._normalize_stage_source_chunks(stage):
                _cid = c.get("chunk_id", "unknown")
                _txt = (c.get("quote") or c.get("text") or "").strip()
                if _txt:
                    _judge_lines.append(f"[{_cid}] {_txt}")
            judge_source = "\n".join(_judge_lines) or stage_content
        else:
            judge_source = stage_content

        # 建構全課程章節索引（供 judge 判斷 other_chapter）
        _index_lines = []
        for s in wm.stages:
            _nid = s.get("node_id", str(s["stage_id"]))
            _title = s.get("title", "")
            _concepts = ", ".join(s.get("key_concepts", [])[:5])
            if s.get("is_dynamic"):
                _parent_nid = next(
                    (ps.get("node_id", str(ps["stage_id"]))
                     for ps in wm.stages if ps["stage_id"] == s.get("source_stage_id")),
                    str(s.get("source_stage_id", ""))
                )
                _index_lines.append(
                    f"[{_nid}] (動態節點，源自 {_parent_nid}) key_concepts: {_concepts}"
                )
            else:
                _index_lines.append(f"[{_nid}: {_title}] key_concepts: {_concepts}")
        chapter_index = "\n".join(_index_lines)
        current_node_id = stage.get("node_id", str(effective_stage_id)) if stage else str(effective_stage_id)

        judge_messages = [
            LLMMessage(
                role=MessageRole.USER,
                content=(
                    f"當前章節：{stage_title}（{current_node_id}）\n\n"
                    f"【當前章節教材原文】\n{judge_source}\n\n"
                    f"【全課程章節索引】\n{chapter_index}\n\n"
                    f"學生提問：{question}"
                ),
            )
        ]
        judge_resp = await self.teacher.llm.chat(
            judge_messages, system_prompt=SYSTEM_PROMPTS["scope_judge"]
        )
        scope = "current_chapter"
        relevant_node_ids: list[str] = []
        try:
            judge_data = json.loads(extract_json(judge_resp.content))
            if "scope" in judge_data:
                scope = judge_data["scope"]
                relevant_node_ids = judge_data.get("relevant_node_ids") or []
            else:
                # 向後相容舊 schema
                scope = "current_chapter" if judge_data.get("in_scope", True) else "out_of_scope"
        except Exception:
            scope = "current_chapter"
        in_scope = scope != "out_of_scope"

        web_context = ""
        if scope == "out_of_scope":
            try:
                results = await search_web(question, max_results=3)
                if results:
                    web_context = "\n".join(
                        f"- {r['title']}: {r['snippet']} ({r['url']})"
                        for r in results
                    )
            except Exception:
                web_context = ""

        # 決定回答用的教材來源
        if scope == "current_chapter":
            answer_source = judge_source
        elif scope == "other_chapter":
            # 過濾動態節點，改用父章節
            filtered_node_ids: list[str] = []
            for nid in relevant_node_ids:
                matched = next((s for s in wm.stages if s.get("node_id") == nid), None)
                if matched and matched.get("is_dynamic"):
                    parent = next(
                        (s for s in wm.stages if s["stage_id"] == matched.get("source_stage_id")),
                        None,
                    )
                    if parent:
                        filtered_node_ids.append(parent.get("node_id", str(parent["stage_id"])))
                elif matched:
                    filtered_node_ids.append(nid)
            # 取相關章節 chunks
            relevant_lines: list[str] = []
            for nid in dict.fromkeys(filtered_node_ids):  # 去重保序
                rel_stage = next((s for s in wm.stages if s.get("node_id") == nid), None)
                if rel_stage:
                    for c in self._normalize_stage_source_chunks(rel_stage):
                        _cid = c.get("chunk_id", "unknown")
                        _txt = (c.get("quote") or c.get("text") or "").strip()
                        if _txt:
                            relevant_lines.append(f"[{_cid}] {_txt}")
            answer_source = "\n".join(relevant_lines) if relevant_lines else source
            if not relevant_lines:
                _log.warning(
                    "handle_student_question other_chapter fallback to corpus  session=%s  node_ids=%s",
                    session_id, relevant_node_ids,
                )
        else:
            answer_source = source  # out_of_scope：給全文讓 LLM 自行確認

        answer_messages = [
            LLMMessage(
                role=MessageRole.USER,
                content=(
                    f"scope={scope}\n"
                    f"當前節點：{stage_title}\n"
                    f"學生問題：{question}\n\n"
                    f"教材原文（回答依據）：\n{answer_source}\n\n"
                    f"搜尋摘要（僅 out_of_scope 時有值）：\n{web_context or '無'}"
                ),
            )
        ]
        ans_resp = await self.teacher.llm.chat(
            answer_messages, system_prompt=SYSTEM_PROMPTS["tutor_reply"]
        )
        answer = ans_resp.content.strip()
        record_id: int | None = None
        try:
            record_id = await session_memory.insert_tutor_record(
                session_id, effective_stage_id, question, answer, in_scope, scope=scope
            )
        except Exception as e:
            _log.warning("insert_tutor_record failed: %s", e)
        payload: dict = {
            "question": question,
            "answer": answer,
            "in_scope": in_scope,
            "scope": scope,
            "stage_id": effective_stage_id,
        }
        if record_id is not None:
            payload["id"] = record_id
        await emit({"type": "tutor_reply", "payload": payload})

    # ── 恢復已存在的學習會話 ──────────────────────────────────

    async def resume_session(
        self,
        session_id: str,
        user_id: str,
        emit: WSEmitter,
    ) -> None:
        _log.info("resume_session  session=%s  user=%s", session_id, user_id)
        session = await session_memory.get_session(session_id)
        if not session:
            await emit({"type": "error", "payload": {"message": "找不到會話，請重新上傳材料"}})
            return

        stages_raw = session["stages_json"] or "[]"
        stages: list[dict] = json.loads(stages_raw)
        if not stages:
            await emit({"type": "error", "payload": {"message": "會話資料不完整，請重新上傳材料"}})
            return

        statuses = await session_memory.get_stage_statuses(session_id)

        wm = get_working_memory(session_id)
        wm.stages = stages
        wm.question_mode = session.get("question_mode") or "short_answer"
        wm.source_corpus = self._build_source_corpus(stages)

        current_stage_id = session["current_stage_id"]
        current_idx = next(
            (i for i, s in enumerate(stages) if s["stage_id"] == current_stage_id), 0
        )

        await self._emit_session_started(session_id, stages, emit, statuses)

        all_explanations = await session_memory.get_all_stage_explanations(session_id)
        all_histories = await session_memory.get_all_stage_qa_records(session_id)
        decision_history = await session_memory.get_decision_records(session_id)
        idx_by_stage_id = {s["stage_id"]: i for i, s in enumerate(stages)}
        client_explanations = {
            str(sid): _markdown_for_client_from_persisted(
                txt,
                build_progress_table(stages, idx_by_stage_id.get(int(sid), 0)),
            )
            for sid, txt in all_explanations.items()
        }
        try:
            raw_tutor = await session_memory.get_all_tutor_records(session_id)
        except Exception as e:
            _log.warning("get_all_tutor_records failed: %s", e)
            raw_tutor = {}

        await emit({
            "type": "session_snapshot",
            "payload": {
                "stage_explanations": client_explanations,
                "stage_qa_histories": {
                    str(stage_id): [
                        {
                            "question_id": r["question_id"],
                            "question_text": r["question_text"],
                            "question_type": r["question_type"],
                            "user_answer": r["user_answer"],
                            "score": r["score"],
                            "feedback_text": r["feedback"],
                        }
                        for r in records
                    ]
                    for stage_id, records in all_histories.items()
                },
                "decision_history": decision_history,
                "tutor_histories": {
                    str(stage_id): records
                    for stage_id, records in raw_tutor.items()
                },
            },
        })

        current_stage = stages[current_idx]
        stored_explanation = await session_memory.get_stage_explanation(
            session_id, current_stage["stage_id"]
        )

        if stored_explanation:
            # 直接還原已儲存的講解，跳過 TeacherAgent
            await self._resume_from_stored(
                session_id, user_id, stages, current_idx, stored_explanation, emit
            )
        else:
            await self.run_stage(
                session_id, user_id, stages, current_idx, wm.question_mode, emit
            )

    async def _resume_from_stored(
        self,
        session_id: str,
        user_id: str,
        stages: list[dict],
        stage_index: int,
        stored_explanation: str,
        emit: WSEmitter,
    ) -> None:
        wm = get_working_memory(session_id)
        wm.reset_for_new_stage(stages[stage_index]["stage_id"])
        stage = stages[stage_index]

        # 從 DB 還原 current_attempt（reset_for_new_stage 會重置為 1）
        stored_progress = await session_memory.get_stage_progress(session_id, stage["stage_id"])
        if stored_progress and stored_progress["attempts"] > 1:
            wm.current_attempt = stored_progress["attempts"]
        _stored_best_score = stored_progress["best_score"] if stored_progress else 0.0
        existing_status = (stored_progress or {}).get("status", "in_progress")

        await session_memory.update_current_stage(session_id, stage["stage_id"])
        # 不要降級已完成章節 — 保留 DB 中的 completed status
        await session_memory.upsert_stage_progress(
            session_id, stage["stage_id"],
            "completed" if existing_status == "completed" else "in_progress",
            wm.current_attempt,  # 保留已持久化的輪次，不歸零
            _stored_best_score,  # 保留已記錄的最佳分數
            {},
        )

        progress_md = build_progress_table(stages, stage_index)
        teacher_only = _teacher_only_from_persisted(stored_explanation)
        display_md = _markdown_for_client_from_persisted(stored_explanation, progress_md)

        questions: list[dict] = await session_memory.get_stage_questions(session_id, stage["stage_id"])

        # 僅進度表已持久化、教師串流尚未寫入任何字元：接續串流，不重播進度表
        if not questions and not teacher_only.strip():
            await emit({"type": "explanation_chunk", "payload": {"chunk": display_md, "is_final": False}})
            wm.current_explanation = ""
            await self.run_stage(
                session_id,
                user_id,
                stages,
                stage_index,
                wm.question_mode,
                emit,
                skip_progress_emit=True,
            )
            return

        await emit({"type": "explanation_chunk", "payload": {"chunk": display_md, "is_final": False}})
        wm.current_explanation = teacher_only

        if not questions:
            # 若重整發生在講解串流中，可能已存到部分講解但尚未存題目；
            # 這裡直接以已存講解補生成問題，避免使用者看到內容被截斷後重跑整段講解。
            adaptive_ctx = await build_adaptive_context(
                session_id=session_id,
                user_id=user_id,
                stage=stage,
                current_attempt=wm.current_attempt,
                stages=stages,
            )
            teaching_intent = await self.teacher.extract_teaching_intent(teacher_only, stage)
            wm.current_teaching_intent = teaching_intent
            q_ctx = AgentContext(
                session_id=session_id,
                user_id=user_id,
                task_payload={
                    "stage": stage,
                    "teaching_intent": teaching_intent,
                    "allowed_evidence": adaptive_ctx.get("allowed_evidence", []),
                    "full_explanation": teacher_only,
                    "num_questions": max(4, stage.get("estimated_questions", 2) * 2)
                    if wm.question_mode == "multiple_choice"
                    else stage.get("estimated_questions", 2),
                    "attempt_number": 1,
                    "previous_question_ids": [],
                    "question_mode": wm.question_mode,
                },
            )
            q_result = await self.questioner.run(q_ctx)
            questions = q_result.get("questions", [])
            questions_verify = await self._verify_grounding(
                session_id=session_id,
                user_id=user_id,
                stage=stage,
                content_type="questions",
                candidate_text=json.dumps(questions, ensure_ascii=False),
                full_explanation=teacher_only,
            )
            if not questions_verify.get("aligned", False):
                retry_q_ctx = AgentContext(
                    session_id=session_id,
                    user_id=user_id,
                    task_payload={
                        "stage": {
                            **stage,
                            "content": stage.get("content", "")
                            + "\n\n（對齊修正要求：請每題僅依 source_chunks 設計，並補 evidence_chunk_ids）",
                        },
                        "full_explanation": teacher_only,
                        "num_questions": max(4, stage.get("estimated_questions", 2) * 2)
                        if wm.question_mode == "multiple_choice"
                        else stage.get("estimated_questions", 2),
                        "attempt_number": 1,
                        "previous_question_ids": [],
                        "question_mode": wm.question_mode,
                    },
                )
                q_result = await self.questioner.run(retry_q_ctx)
                questions = q_result.get("questions", [])
            await session_memory.store_stage_questions(session_id, stage["stage_id"], questions)
        wm.pending_questions = questions

        questions_md = self._build_questions_section(questions)
        if questions_md:
            await emit({"type": "explanation_chunk", "payload": {"chunk": questions_md, "is_final": False}})

        await emit({"type": "explanation_chunk", "payload": {"chunk": "", "is_final": True}})
        await emit({
            "type": "explanation_complete",
            "payload": {
                "stage_id": stage["stage_id"],
                "stage_title": stage["title"],
                "full_explanation": display_md + questions_md,
            },
        })

        # 從 DB 查出已回答過的題目，跳過不再重複發送
        qa_records = await session_memory.get_stage_qa_records(session_id, stage["stage_id"])
        answered_ids = {r["question_id"] for r in qa_records}
        unanswered = [q for q in questions if q["question_id"] not in answered_ids]
        latest_feedback = qa_records[-1] if qa_records else None

        # 還原 stage_turns：確保 handle_answer 的 remaining_qs 過濾器
        # 不會把已答過的題目再次發送（reset_for_new_stage 清空了 stage_turns）
        for r in qa_records:
            wm.stage_turns.append(TurnContext(
                turn_id=str(uuid.uuid4()),
                question_id=r["question_id"],
                question_text=r["question_text"],
                user_answer=r.get("user_answer"),
            ))
        # 還原 stage_evaluations，讓 ProgressManager 在最後一題後做出正確決策
        wm.stage_evaluations = [
            {"score": r["score"], "feedback": r.get("feedback", "")}
            for r in qa_records
        ]

        # 還原歷史答題記錄給前端
        if qa_records:
            await emit({
                "type": "qa_history",
                "payload": {
                    "records": [
                        {
                            "question_id": r["question_id"],
                            "question_text": r["question_text"],
                            "question_type": r["question_type"],
                            "user_answer": r["user_answer"],
                            "score": r["score"],
                            "feedback_text": r["feedback"],
                        }
                        for r in qa_records
                    ]
                },
            })

        resume_payload: dict = {
            "current_question": None,
            "last_feedback": None,
        }
        if latest_feedback:
            resume_payload["last_feedback"] = {
                "question_id": latest_feedback["question_id"],
                "score": latest_feedback["score"],
                "feedback_text": latest_feedback["feedback"],
                "needs_clarification": False,
                "clarification_question": None,
            }
        if unanswered:
            first_unanswered = unanswered[0]
            resume_payload["current_question"] = {
                "question_id": first_unanswered["question_id"],
                "text": first_unanswered["text"],
                "type": first_unanswered.get("type", "understand"),
                "answer_mode": first_unanswered.get("answer_mode", "short_answer"),
                "options": first_unanswered.get("options", []),
                "evidence_chunk_ids": first_unanswered.get("evidence_chunk_ids", []),
                "stage_id": stage["stage_id"],
                "attempt_number": len(qa_records) + 1,
            }
        await emit({"type": "resume_state", "payload": resume_payload})

        if unanswered:
            q = unanswered[0]
            wm.current_turn = TurnContext(
                turn_id=str(uuid.uuid4()),
                question_id=q["question_id"],
                question_text=q["text"],
            )
            await emit({
                "type": "question",
                "payload": {
                    "question_id": q["question_id"],
                    "text": q["text"],
                    "type": q.get("type", "understand"),
                    "answer_mode": q.get("answer_mode", "short_answer"),
                    "options": q.get("options", []),
                    "evidence_chunk_ids": q.get("evidence_chunk_ids", []),
                    "stage_id": stage["stage_id"],
                    "attempt_number": len(qa_records) + 1,
                },
            })
        elif qa_records:
            # 純複習守門：若此 stage 在 DB 中已 completed，且所有題目都答過，
            # 表示使用者只是切回來看，不應再次呼叫 _make_progress_decision
            #（會造成重複 decision_record 寫入、可能觸發 advance → run_stage 重跑）。
            if existing_status == "completed":
                return

            # 所有 stored_questions 都已回答。需判斷兩種情境：
            # (A) 正常完成：呼叫 _make_progress_decision
            # (B) Race condition：retry 已決策（current_attempt 已 +1 並持久化），
            #     但新一輪題目還沒存入 DB，stored_questions 仍是舊輪次的題目。
            #     特徵：current_attempt > 1 且「當輪答題記錄 == 全部答題記錄」
            #     （若真正跑完了第 N 輪，qa_records 必然包含前面各輪的紀錄，數量會多於 stored_questions）
            current_question_ids = {q["question_id"] for q in questions}
            current_qa = [r for r in qa_records if r["question_id"] in current_question_ids]
            is_race_condition = (
                wm.current_attempt > 1 and len(current_qa) == len(qa_records)
            )

            if is_race_condition:
                # retry 題目尚未生成完畢就被 resume 打斷 → 重新產出當輪題目
                prev_q_ids = [t.question_id for t in wm.stage_turns]
                prev_q_texts = [t.question_text for t in wm.stage_turns]
                q_ctx = AgentContext(
                    session_id=session_id,
                    user_id=user_id,
                    task_payload={
                        "stage": stage,
                        "full_explanation": wm.current_explanation,
                        "num_questions": 4 if wm.question_mode == "multiple_choice" else 2,
                        "attempt_number": wm.current_attempt,
                        "previous_question_ids": prev_q_ids,
                        "previous_question_texts": prev_q_texts,
                        "question_mode": wm.question_mode,
                    },
                )
                q_result = await self.questioner.run(q_ctx)
                new_questions: list[dict] = q_result.get("questions", [])
                used_ids = set(prev_q_ids)
                for q in new_questions:
                    if not q.get("question_id") or q["question_id"] in used_ids:
                        q["question_id"] = (
                            f"q_{stage['stage_id']}_{wm.current_attempt}_{uuid.uuid4().hex[:8]}"
                        )
                    used_ids.add(q["question_id"])
                wm.pending_questions = new_questions
                wm.stage_evaluations = []
                await session_memory.store_stage_questions(
                    session_id, stage["stage_id"], new_questions
                )
                new_questions_md = self._build_questions_section(new_questions)
                if new_questions_md:
                    await emit({
                        "type": "explanation_chunk",
                        "payload": {"chunk": new_questions_md, "is_final": False},
                    })
                await emit({"type": "explanation_chunk", "payload": {"chunk": "", "is_final": True}})
                await emit({
                    "type": "explanation_complete",
                    "payload": {
                        "stage_id": stage["stage_id"],
                        "stage_title": stage["title"],
                        "full_explanation": display_md + new_questions_md,
                    },
                })
                if new_questions:
                    first_q = new_questions[0]
                    wm.current_turn = TurnContext(
                        turn_id=str(uuid.uuid4()),
                        question_id=first_q["question_id"],
                        question_text=first_q["text"],
                    )
                    await emit({
                        "type": "question",
                        "payload": {
                            "question_id": first_q["question_id"],
                            "text": first_q["text"],
                            "type": first_q.get("type", "understand"),
                            "answer_mode": first_q.get("answer_mode", "short_answer"),
                            "options": first_q.get("options", []),
                            "evidence_chunk_ids": first_q.get("evidence_chunk_ids", []),
                            "stage_id": stage["stage_id"],
                            "attempt_number": wm.current_attempt,
                        },
                    })
            else:
                # 正常情境：只用當輪（stored_questions 對應的）答題記錄來評估
                wm.stage_evaluations = [
                    {"score": r["score"], "feedback": r.get("feedback", "")}
                    for r in current_qa
                ]
                await self._make_progress_decision(
                    session_id, user_id, stages, stage, stage_index, wm, emit
                )
