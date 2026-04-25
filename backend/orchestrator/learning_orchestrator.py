import hashlib
import json
import uuid
from typing import Callable, Awaitable

from ..agents.base_agent import AgentContext
from ..agents.content_splitter import ContentSplitterAgent
from ..agents.teacher import TeacherAgent
from ..agents.question_generator import QuestionGeneratorAgent
from ..agents.evaluator import EvaluatorAgent
from ..agents.progress_manager import ProgressManagerAgent
from ..memory.working_memory import get_working_memory, TurnContext
from ..memory import session_memory, longterm_memory
from ..llm.base_provider import BaseLLMProvider
from ..utils.token_counter import TokenCounter

WSEmitter = Callable[[dict], Awaitable[None]]


class LearningOrchestrator:
    def __init__(self, llm: BaseLLMProvider):
        tc = TokenCounter()
        self.splitter = ContentSplitterAgent(llm, tc)
        self.teacher = TeacherAgent(llm, tc)
        self.questioner = QuestionGeneratorAgent(llm, tc)
        self.evaluator = EvaluatorAgent(llm, tc)
        self.progress = ProgressManagerAgent(llm, tc)
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

    # ── 初始化：切割內容，等待確認 ──────────────────────────

    async def start_session(
        self,
        session_id: str,
        user_id: str,
        raw_content: str,
        provider_file_ref: dict | None,
        target_depth: str,
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
        summary: str = split_result.get("summary", "")

        self._pending_stages = stages
        self._pending_start_args = {
            "session_id": session_id,
            "user_id": user_id,
            "content_hash": content_hash,
            "summary": summary,
        }

        await emit({
            "type": "knowledge_map",
            "payload": {
                "nodes": [
                    {"node_id": s["node_id"], "stage_id": s["stage_id"], "title": s["title"]}
                    for s in stages
                ],
                "summary": summary,
            },
        })

    # ── 使用者確認知識地圖後開始教學 ────────────────────────

    async def confirm_session(self, session_id: str, user_id: str, emit: WSEmitter) -> None:
        stages = self._pending_stages
        args = self._pending_start_args
        if not stages or not args:
            await emit({"type": "error", "payload": {"message": "無法確認學習路線，請重新上傳材料"}})
            return

        await session_memory.create_session(
            session_id=session_id,
            user_id=user_id,
            content_hash=args["content_hash"],
            total_stages=len(stages),
            raw_content_summary=args["summary"],
        )
        await session_memory.store_stages(session_id, stages)
        for s in stages:
            await session_memory.upsert_stage_progress(
                session_id, s["stage_id"], "pending", 0, 0.0, {}
            )

        wm = get_working_memory(session_id)
        wm.reset_for_new_stage(0)
        wm.stages = stages

        await emit({
            "type": "session_started",
            "payload": {
                "session_id": session_id,
                "total_stages": len(stages),
                "stages": [{"stage_id": s["stage_id"], "title": s["title"]} for s in stages],
            },
        })

        await self.run_stage(session_id, user_id, stages, 0, emit)

    # ── 教學單一節點 ─────────────────────────────────────────

    async def run_stage(
        self,
        session_id: str,
        user_id: str,
        stages: list[dict],
        stage_index: int,
        emit: WSEmitter,
    ) -> None:
        wm = get_working_memory(session_id)
        wm.reset_for_new_stage(stages[stage_index]["stage_id"])
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
        wm.current_explanation = full_explanation
        await session_memory.store_stage_explanation(session_id, stage["stage_id"], full_explanation)

        # 3. 生成問題
        q_ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={
                "stage": stage,
                "num_questions": stage.get("estimated_questions", 2),
                "attempt_number": 1,
                "previous_question_ids": [],
            },
        )
        q_result = await self.questioner.run(q_ctx)
        questions: list[dict] = q_result.get("questions", [])
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

        for concept in stage.get("key_concepts", []):
            await longterm_memory.update_concept_mastery(
                user_id=user_id,
                concept_name=concept,
                new_score=eval_result.get("score", 0.0),
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
                    "stage_id": current_stage_id,
                    "attempt_number": len(wm.stage_turns) + 1,
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
            },
        )
        decision = await self.progress.run(prog_ctx)
        d = decision["decision"]

        await session_memory.upsert_stage_progress(
            session_id=session_id,
            stage_id=stage["stage_id"],
            status="completed" if d == "advance" else "in_progress",
            attempts=len(wm.stage_turns),
            best_score=decision["best_score"],
            understanding_notes={"confused": decision.get("remediation_focus") or []},
        )

        await emit({
            "type": "stage_decision",
            "payload": {
                "decision": d,
                "message": decision["message"],
                "next_stage_id": decision.get("next_stage_id"),
                "best_score": decision["best_score"],
            },
        })

        if d == "advance":
            if current_idx + 1 < len(stages):
                await longterm_memory.update_user_profile(user_id, len(wm.stage_turns))
                await self.run_stage(session_id, user_id, stages, current_idx + 1, emit)
            else:
                await session_memory.complete_session(session_id)
                await emit({"type": "course_completed", "payload": {"message": "恭喜！你已完成所有學習階段。"}})

        elif d in ("retry", "remediate"):
            attempt = len(wm.stage_turns) + 1

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
                    "num_questions": 2,
                    "attempt_number": attempt,
                    "previous_question_ids": [t.question_id for t in wm.stage_turns],
                },
            )
            q_result = await self.questioner.run(q_ctx)
            questions: list[dict] = q_result.get("questions", [])
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
                        "stage_id": stage["stage_id"],
                        "attempt_number": attempt,
                    },
                })

        elif d == "reteach":
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
            wm.current_explanation = full_explanation
            wm.stage_evaluations = []

            q_ctx = AgentContext(
                session_id=session_id,
                user_id=user_id,
                task_payload={
                    "stage": stage,
                    "num_questions": 2,
                    "attempt_number": len(wm.stage_turns) + 1,
                    "previous_question_ids": [t.question_id for t in wm.stage_turns],
                },
            )
            q_result = await self.questioner.run(q_ctx)
            questions = q_result.get("questions", [])
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
                        "stage_id": stage["stage_id"],
                        "attempt_number": len(wm.stage_turns) + 1,
                    },
                })

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

        current_stage_id = session["current_stage_id"]
        current_idx = next(
            (i for i, s in enumerate(stages) if s["stage_id"] == current_stage_id), 0
        )

        await emit({
            "type": "session_started",
            "payload": {
                "session_id": session_id,
                "total_stages": len(stages),
                "stages": [{"stage_id": s["stage_id"], "title": s["title"]} for s in stages],
                "stage_statuses": {str(k): v for k, v in statuses.items()},
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
            await self.run_stage(session_id, user_id, stages, current_idx, emit)

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
                    "stage_id": stage["stage_id"],
                    "attempt_number": 1,
                },
            })
