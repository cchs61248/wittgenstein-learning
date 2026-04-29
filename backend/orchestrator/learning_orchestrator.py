import hashlib
import json
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

WSEmitter = Callable[[dict], Awaitable[None]]


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

    def _build_progress_table(self, stages: list[dict], current_idx: int) -> str:
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

    async def _verify_grounding(
        self,
        session_id: str,
        user_id: str,
        stage: dict,
        content_type: str,
        candidate_text: str,
    ) -> dict:
        verify_ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={
                "content_type": content_type,
                "source_chunks": self._normalize_stage_source_chunks(stage),
                "candidate_text": candidate_text,
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
        # 優先走順序：若下一個 stage 尚未完成，直接前進，不做排名
        seq_idx = current_idx + 1
        if seq_idx < len(stages) and stages[seq_idx]["stage_id"] not in completed_stage_ids:
            ranked = self._rank_next_stage_candidates(
                stages=stages,
                current_idx=current_idx,
                completed_stage_ids=completed_stage_ids,
                weak_concepts=weak_concepts,
                mastery_map=mastery_map,
                stable_high=stable_high,
            )
            return seq_idx, ranked

        # 順序 stage 已完成（或已到末尾），才用排名演算法選最佳待學節點
        ranked = self._rank_next_stage_candidates(
            stages=stages,
            current_idx=current_idx,
            completed_stage_ids=completed_stage_ids,
            weak_concepts=weak_concepts,
            mastery_map=mastery_map,
            stable_high=stable_high,
        )
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

    async def _insert_remediation_stage(
        self,
        session_id: str,
        stages: list[dict],
        current_idx: int,
        remediation_focus: list[str],
    ) -> tuple[list[dict], int]:
        current = stages[current_idx]
        max_stage_id = max((s.get("stage_id", 0) for s in stages), default=0)
        new_stage_id = max_stage_id + 1
        node_id = f"R.{new_stage_id}"
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
            "source_stage_id": current.get("stage_id"),
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
            understanding_notes={"dynamic": True, "focus": remediation_focus[:3]},
        )
        return updated, insert_idx

    async def _insert_enrichment_stage(
        self,
        session_id: str,
        stages: list[dict],
    ) -> tuple[list[dict], int]:
        max_stage_id = max((s.get("stage_id", 0) for s in stages), default=0)
        new_stage_id = max_stage_id + 1
        node_id = f"E.{new_stage_id}"
        recent_titles = "、".join(s.get("title", "") for s in stages[-3:])
        new_stage = {
            "stage_id": new_stage_id,
            "node_id": node_id,
            "title": "整合挑戰：跨章節應用",
            "content": (
                "本節為整合挑戰節點。請整合前面已掌握內容，處理跨情境應用與觀點比較。\n\n"
                f"可優先整合這些節點：{recent_titles}"
            ),
            "key_concepts": list(dict.fromkeys([c for s in stages[-3:] for c in s.get("key_concepts", [])]))[:5],
            "prerequisites": [s.get("title", "") for s in stages[-2:]],
            "estimated_questions": 4,
            "source_chunks": [
                {
                    "chunk_id": f"s{new_stage_id}_c1",
                    "quote": "；".join(s.get("content", "")[:240] for s in stages[-3:] if s.get("content")),
                    "note": "來自最近三個節點的整合摘錄",
                }
            ],
            "is_dynamic": True,
            "kind": "enrichment",
        }
        updated = stages + [new_stage]
        await session_memory.store_stages(session_id, updated)
        await session_memory.upsert_stage_progress(
            session_id=session_id,
            stage_id=new_stage_id,
            status="pending",
            attempts=0,
            best_score=0.0,
            understanding_notes={"dynamic": True, "kind": "enrichment"},
        )
        return updated, len(updated) - 1

    # ── 初始化：切割內容，等待確認 ──────────────────────────

    async def start_session(
        self,
        session_id: str,
        user_id: str,
        raw_content: str,
        provider_file_ref: dict | None,
        target_depth: str,
        question_mode: str,
        provider_name: str | None,
        model_name: str | None,
        emit: WSEmitter,
    ) -> None:
        hash_seed = raw_content if raw_content else json.dumps(provider_file_ref or {}, ensure_ascii=False)
        content_hash = hashlib.sha256(hash_seed.encode()).hexdigest()[:16]

        ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={
                "raw_content": raw_content,
                "provider_file_ref": provider_file_ref,
                "max_stages": 8,
                "target_depth": target_depth,
            },
        )
        split_result = await self.splitter.run(ctx)
        stages: list[dict] = split_result["stages"]
        for stage in stages:
            stage["source_chunks"] = self._normalize_stage_source_chunks(stage)
        summary: str = split_result.get("summary", "")

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

        # 立即存入 DB，讓重整後能恢復此狀態
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
        wm.enrichment_stage_added = any(s.get("kind") == "enrichment" for s in stages)

        await emit({
            "type": "session_started",
            "payload": {
                "session_id": session_id,
                "total_stages": len(stages),
                "stages": [
                    {
                        "stage_id": s["stage_id"],
                        "title": s["title"],
                        "source_chunks": self._normalize_stage_source_chunks(s),
                    }
                    for s in stages
                ],
            },
        })

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
    ) -> None:
        wm = get_working_memory(session_id)
        wm.reset_for_new_stage(stages[stage_index]["stage_id"])
        wm.question_mode = question_mode
        wm.source_corpus = "\n\n".join(
            f"[{s.get('node_id', s['stage_id'])}] {s['title']}\n{s.get('content', '')}\n"
            + "\n".join(
                f"- [{c.get('chunk_id', 'unknown')}] {c.get('quote', '')}"
                for c in self._normalize_stage_source_chunks(s)
            )
            for s in stages
        )
        stage = stages[stage_index]

        await session_memory.update_current_stage(session_id, stage["stage_id"])
        await session_memory.upsert_stage_progress(
            session_id, stage["stage_id"], "in_progress", 0, 0.0, {}
        )

        user_profile_summary = await longterm_memory.get_user_profile_summary(user_id)
        weak_concepts = await longterm_memory.get_weak_concepts(user_id)
        prev_stage = stages[stage_index - 1] if stage_index > 0 else None

        # 1. 進度表
        progress_md = self._build_progress_table(stages, stage_index)
        await emit({"type": "explanation_chunk", "payload": {"chunk": progress_md, "is_final": False}})

        # 2. 串流講解（📖 + 🔗）
        ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={
                "stage": stage,
                "prev_stage_title": prev_stage["title"] if prev_stage else None,
                "user_profile_summary": user_profile_summary,
                "weak_concepts": weak_concepts,
            },
        )
        full_explanation = ""
        async for chunk in self.teacher.stream_explanation(ctx):
            full_explanation += chunk
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
                    "weak_concepts": weak_concepts,
                },
            )
            full_explanation = ""
            async for chunk in self.teacher.stream_explanation(retry_ctx):
                full_explanation += chunk
            explanation_rewritten = True
        if explanation_rewritten:
            await emit({"type": "explanation_reset", "payload": {}})
            await emit({"type": "explanation_chunk", "payload": {"chunk": progress_md, "is_final": False}})
            await emit({"type": "explanation_chunk", "payload": {"chunk": full_explanation, "is_final": False}})
        wm.current_explanation = full_explanation
        await session_memory.store_stage_explanation(session_id, stage["stage_id"], full_explanation)

        # 3. 生成問題
        q_ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={
                "stage": stage,
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

    # ── 處理使用者答案 ────────────────────────────────────────

    async def handle_answer(
        self,
        session_id: str,
        user_id: str,
        question_id: str,
        answer: str,
        emit: WSEmitter,
    ) -> None:
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
        for concept in stage.get("key_concepts", []):
            await longterm_memory.update_concept_mastery(
                user_id=user_id,
                concept_name=concept,
                new_score=mastery_score,
                confused_concepts=eval_result.get("confused_concepts", []),
                successful_analogies=[],
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
            },
        )
        decision = await self.progress.run(prog_ctx)
        d = decision["decision"]
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
            if next_stage_idx is None and stable_high and not wm.enrichment_stage_added:
                stages_for_run, next_stage_idx = await self._insert_enrichment_stage(
                    session_id=session_id,
                    stages=stages,
                )
                wm.stages = stages_for_run
                stages = stages_for_run
                wm.enrichment_stage_added = True
                dynamic_stage_inserted = True
                decision_reasons.append("原始節點已完成，新增『整合挑戰』節點以延伸應用能力。")
            elif next_stage_idx is not None:
                decision_reasons.append(
                    f"下一節選擇：{stages[next_stage_idx]['title']}（依弱點/掌握度/新知權重計算）。"
                )
        elif d in ("remediate", "reteach"):
            # 若判定需要補強，且目前沒有明確對應的待學節點，插入動態補強節點
            focus = decision.get("remediation_focus") or []
            weak_raw = await longterm_memory.get_weak_concepts(user_id)
            weak_concepts = [] if weak_raw == "無" else [c.strip() for c in weak_raw.split("、") if c.strip()]
            all_concepts = list(
                dict.fromkeys([c for s in stages for c in s.get("key_concepts", [])])
            )
            mastery_map = await longterm_memory.get_concept_mastery_map(user_id, all_concepts)
            candidate_idx, ranked_candidates = self._pick_next_stage_index(
                stages=stages,
                current_idx=current_idx,
                completed_stage_ids=completed_stage_ids,
                weak_concepts=weak_concepts,
                mastery_map=mastery_map,
                stable_high=False,
            )
            if focus and candidate_idx is None:
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
                    "偵測到需補強概念且無現成節點可對應，已動態插入補強節點。"
                )
            else:
                decision_reasons.append("目前先在本節補強，待下輪評估是否轉移到其他節點。")
            if focus:
                decision_reasons.append("補強焦點：" + "、".join(focus[:3]))
            decision_reasons.append("補強不影響整體進度：知識地圖中所有節點最終都會完整覆蓋。")
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
                refreshed_statuses = await session_memory.get_stage_statuses(session_id)
                await emit({
                    "type": "session_started",
                    "payload": {
                        "session_id": session_id,
                        "total_stages": len(stages),
                        "stages": [
                            {
                                "stage_id": s["stage_id"],
                                "title": s["title"],
                                "source_chunks": self._normalize_stage_source_chunks(s),
                            }
                            for s in stages
                        ],
                        "stage_statuses": {str(k): v for k, v in refreshed_statuses.items()},
                    },
                })
                await self.run_stage(
                    session_id, user_id, stages, next_stage_idx, wm.question_mode, emit
                )
            else:
                await session_memory.complete_session(session_id)
                await emit({"type": "course_completed", "payload": {"message": "恭喜！你已完成所有學習階段。"}})

        elif d in ("retry", "remediate"):
            wm.current_attempt += 1

            # 清除並重建畫面：顯示進度表 + 補強說明 + 新問題
            await emit({"type": "explanation_reset", "payload": {}})

            progress_md = self._build_progress_table(stages, current_idx)
            await emit({"type": "explanation_chunk", "payload": {"chunk": progress_md, "is_final": False}})

            remediation_focus = ", ".join(decision.get("remediation_focus") or [])
            remediation_md = (
                f"### 💬 補強說明\n\n"
                f"{decision['message']}"
                + (f"\n\n需要特別注意的概念：**{remediation_focus}**" if remediation_focus else "")
                + "\n\n---\n\n"
            )
            await emit({"type": "explanation_chunk", "payload": {"chunk": remediation_md, "is_final": False}})

            q_ctx = AgentContext(
                session_id=session_id,
                user_id=user_id,
                task_payload={
                    "stage": stage,
                    "num_questions": 4 if wm.question_mode == "multiple_choice" else 2,
                    "attempt_number": wm.current_attempt,
                    "previous_question_ids": [t.question_id for t in wm.stage_turns],
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
                        "num_questions": 4 if wm.question_mode == "multiple_choice" else 2,
                        "attempt_number": wm.current_attempt,
                        "previous_question_ids": [t.question_id for t in wm.stage_turns],
                        "question_mode": wm.question_mode,
                    },
                )
                q_result = await self.questioner.run(retry_q_ctx)
                questions = q_result.get("questions", [])
            # 確保問題 ID 在 stage_turns 中唯一，避免與前次嘗試碰撞
            used_ids = {t.question_id for t in wm.stage_turns}
            for q in questions:
                if not q.get("question_id") or q["question_id"] in used_ids:
                    q["question_id"] = f"q_{stage['stage_id']}_{wm.current_attempt}_{uuid.uuid4().hex[:8]}"
                used_ids.add(q["question_id"])
            wm.pending_questions = questions
            wm.stage_evaluations = []

            questions_md = self._build_questions_section(questions)
            if questions_md:
                await emit({"type": "explanation_chunk", "payload": {"chunk": questions_md, "is_final": False}})

            await emit({"type": "explanation_chunk", "payload": {"chunk": "", "is_final": True}})

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

        elif d == "reteach":
            wm.current_attempt += 1
            await emit({"type": "explanation_reset", "payload": {}})

            progress_md = self._build_progress_table(stages, current_idx)
            await emit({"type": "explanation_chunk", "payload": {"chunk": progress_md, "is_final": False}})

            user_profile_summary = await longterm_memory.get_user_profile_summary(user_id)
            weak_concepts = ", ".join(decision.get("remediation_focus") or [])
            prev_stage = stages[current_idx - 1] if current_idx > 0 else None

            reteach_content = stage["content"] + "\n\n（請換一個完全不同的比喻框架重新解釋）"
            reteach_stage = {**stage, "content": reteach_content}

            ctx = AgentContext(
                session_id=session_id,
                user_id=user_id,
                task_payload={
                    "stage": reteach_stage,
                    "prev_stage_title": prev_stage["title"] if prev_stage else None,
                    "user_profile_summary": user_profile_summary,
                    "weak_concepts": weak_concepts or "無",
                },
            )
            full_explanation = ""
            async for chunk in self.teacher.stream_explanation(ctx):
                full_explanation += chunk
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
                            **reteach_stage,
                            "content": reteach_stage.get("content", "") + f"\n\n（對齊修正要求：{guidance}）",
                        },
                        "prev_stage_title": prev_stage["title"] if prev_stage else None,
                        "user_profile_summary": user_profile_summary,
                        "weak_concepts": weak_concepts or "無",
                    },
                )
                full_explanation = ""
                async for chunk in self.teacher.stream_explanation(retry_ctx):
                    full_explanation += chunk
                explanation_rewritten = True
            if explanation_rewritten:
                await emit({"type": "explanation_reset", "payload": {}})
                await emit({"type": "explanation_chunk", "payload": {"chunk": progress_md, "is_final": False}})
                await emit({"type": "explanation_chunk", "payload": {"chunk": full_explanation, "is_final": False}})
            wm.current_explanation = full_explanation
            wm.stage_evaluations = []

            q_ctx = AgentContext(
                session_id=session_id,
                user_id=user_id,
                task_payload={
                    "stage": stage,
                    "num_questions": 4 if wm.question_mode == "multiple_choice" else 2,
                    "attempt_number": wm.current_attempt,
                    "previous_question_ids": [t.question_id for t in wm.stage_turns],
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
                        "num_questions": 4 if wm.question_mode == "multiple_choice" else 2,
                        "attempt_number": wm.current_attempt,
                        "previous_question_ids": [t.question_id for t in wm.stage_turns],
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

            questions_md = self._build_questions_section(questions)
            if questions_md:
                await emit({"type": "explanation_chunk", "payload": {"chunk": questions_md, "is_final": False}})

            await emit({"type": "explanation_chunk", "payload": {"chunk": "", "is_final": True}})

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

    async def handle_student_question(
        self,
        session_id: str,
        question: str,
        emit: WSEmitter,
    ) -> None:
        wm = get_working_memory(session_id)
        stage = next((s for s in wm.stages if s.get("stage_id") == wm.current_stage_id), None)
        stage_title = stage["title"] if stage else "目前節點"
        stage_content = stage.get("content", "") if stage else ""
        source = wm.source_corpus or stage_content
        if not source:
            await emit({
                "type": "tutor_reply",
                "payload": {"question": question, "answer": "目前沒有可用教材內容，請先開始學習流程。"},
            })
            return

        judge_messages = [
            LLMMessage(
                role=MessageRole.USER,
                content=(
                    f"教材內容：\n{source[:4000]}\n\n"
                    f"當前節點：{stage_title}\n"
                    f"學生提問：{question}\n\n"
                    "請判斷是否可由教材直接回答。"
                ),
            )
        ]
        judge_resp = await self.teacher.llm.chat(
            judge_messages, system_prompt=SYSTEM_PROMPTS["scope_judge"]
        )
        try:
            judge_data = json.loads(extract_json(judge_resp.content))
            in_scope = bool(judge_data.get("in_scope", False))
        except Exception:
            in_scope = True

        web_context = ""
        if not in_scope:
            try:
                results = search_web(question, max_results=3)
                if results:
                    web_context = "\n".join(
                        f"- {r['title']}: {r['snippet']} ({r['url']})"
                        for r in results
                    )
            except Exception:
                web_context = ""

        answer_messages = [
            LLMMessage(
                role=MessageRole.USER,
                content=(
                    f"in_scope={str(in_scope).lower()}\n"
                    f"當前節點：{stage_title}\n"
                    f"學生問題：{question}\n\n"
                    f"教材內容：\n{source[:5000]}\n\n"
                    f"搜尋摘要（若有）：\n{web_context or '無'}"
                ),
            )
        ]
        ans_resp = await self.teacher.llm.chat(
            answer_messages, system_prompt=SYSTEM_PROMPTS["tutor_reply"]
        )
        await emit(
            {
                "type": "tutor_reply",
                "payload": {
                    "question": question,
                    "answer": ans_resp.content.strip(),
                    "in_scope": in_scope,
                },
            }
        )

    # ── 恢復已存在的學習會話 ──────────────────────────────────

    async def resume_session(
        self,
        session_id: str,
        user_id: str,
        emit: WSEmitter,
    ) -> None:
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
        wm.enrichment_stage_added = any(s.get("kind") == "enrichment" for s in stages)
        wm.source_corpus = "\n\n".join(
            f"[{s.get('node_id', s['stage_id'])}] {s['title']}\n{s.get('content', '')}\n"
            + "\n".join(
                f"- [{c.get('chunk_id', 'unknown')}] {c.get('quote', '')}"
                for c in self._normalize_stage_source_chunks(s)
            )
            for s in stages
        )

        current_stage_id = session["current_stage_id"]
        current_idx = next(
            (i for i, s in enumerate(stages) if s["stage_id"] == current_stage_id), 0
        )

        await emit({
            "type": "session_started",
            "payload": {
                "session_id": session_id,
                "total_stages": len(stages),
                "stages": [
                    {
                        "stage_id": s["stage_id"],
                        "title": s["title"],
                        "source_chunks": self._normalize_stage_source_chunks(s),
                    }
                    for s in stages
                ],
                "stage_statuses": {str(k): v for k, v in statuses.items()},
            },
        })

        all_explanations = await session_memory.get_all_stage_explanations(session_id)
        all_histories = await session_memory.get_all_stage_qa_records(session_id)
        decision_history = await session_memory.get_decision_records(session_id)
        await emit({
            "type": "session_snapshot",
            "payload": {
                "stage_explanations": {str(k): v for k, v in all_explanations.items()},
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

        await session_memory.update_current_stage(session_id, stage["stage_id"])
        await session_memory.upsert_stage_progress(
            session_id, stage["stage_id"], "in_progress", 0, 0.0, {}
        )

        progress_md = self._build_progress_table(stages, stage_index)
        full_text = progress_md + stored_explanation
        await emit({"type": "explanation_chunk", "payload": {"chunk": full_text, "is_final": False}})
        wm.current_explanation = stored_explanation

        # 從 DB 還原已儲存的問題，完全跳過 LLM
        questions: list[dict] = await session_memory.get_stage_questions(session_id, stage["stage_id"])
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
                "full_explanation": full_text + questions_md,
            },
        })

        # 從 DB 查出已回答過的題目，跳過不再重複發送
        qa_records = await session_memory.get_stage_qa_records(session_id, stage["stage_id"])
        answered_ids = {r["question_id"] for r in qa_records}
        unanswered = [q for q in questions if q["question_id"] not in answered_ids]
        latest_feedback = qa_records[-1] if qa_records else None

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
            # 所有題目都已回答，從 DB 重建評估結果並做進度決策
            wm.stage_evaluations = [
                {"score": r["score"], "feedback": r["feedback"]}
                for r in qa_records
            ]
            await self._make_progress_decision(
                session_id, user_id, stages, stage, stage_index, wm, emit
            )
