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

# WebSocket 傳送函式型別
WSEmitter = Callable[[dict], Awaitable[None]]


class LearningOrchestrator:
    def __init__(self, llm: BaseLLMProvider):
        tc = TokenCounter()
        self.splitter = ContentSplitterAgent(llm, tc)
        self.teacher = TeacherAgent(llm, tc)
        self.questioner = QuestionGeneratorAgent(llm, tc)
        self.evaluator = EvaluatorAgent(llm, tc)
        self.progress = ProgressManagerAgent(llm, tc)

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

        await session_memory.create_session(
            session_id=session_id,
            user_id=user_id,
            content_hash=content_hash,
            total_stages=len(stages),
            raw_content_summary=summary,
        )
        for s in stages:
            await session_memory.upsert_stage_progress(
                session_id, s["stage_id"], "pending", 0, 0.0, {}
            )

        wm = get_working_memory(session_id)
        wm.reset_for_new_stage(0)

        await emit({
            "type": "session_started",
            "payload": {
                "session_id": session_id,
                "total_stages": len(stages),
                "stages": [{"stage_id": s["stage_id"], "title": s["title"]} for s in stages],
            },
        })

        # 獨立保存整份 stages，避免與 pending_questions 混用
        wm.stages = stages

        await self.run_stage(session_id, user_id, stages, 0, emit)

    async def run_stage(
        self,
        session_id: str,
        user_id: str,
        stages: list[dict],
        stage_index: int,
        emit: WSEmitter,
    ) -> None:
        wm = get_working_memory(session_id)
        wm.reset_for_new_stage(stage_index)
        stage = stages[stage_index]

        await session_memory.update_current_stage(session_id, stage["stage_id"])
        await session_memory.upsert_stage_progress(
            session_id, stage["stage_id"], "in_progress", 0, 0.0, {}
        )

        user_profile_summary = await longterm_memory.get_user_profile_summary(user_id)
        weak_concepts = await longterm_memory.get_weak_concepts(user_id)

        ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={
                "stage": stage,
                "user_profile_summary": user_profile_summary,
                "weak_concepts": weak_concepts,
            },
        )

        full_explanation = ""
        async for chunk in self.teacher.stream_explanation(ctx):
            full_explanation += chunk
            await emit({"type": "explanation_chunk", "payload": {"chunk": chunk, "is_final": False}})

        await emit({
            "type": "explanation_chunk",
            "payload": {"chunk": "", "is_final": True},
        })
        await emit({
            "type": "explanation_complete",
            "payload": {
                "stage_id": stage["stage_id"],
                "stage_title": stage["title"],
                "full_explanation": full_explanation,
            },
        })

        wm.current_explanation = full_explanation

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

        # 找到對應的問題物件
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

        # 更新長期記憶
        for concept in stage.get("key_concepts", []):
            await longterm_memory.update_concept_mastery(
                user_id=user_id,
                concept_name=concept,
                new_score=eval_result.get("score", 0.0),
                confused_concepts=eval_result.get("confused_concepts", []),
                successful_analogies=[],
            )

        # 決定是否繼續問下一題或進行進度決策
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
            await self._make_progress_decision(
                session_id, user_id, stages, stage, wm, emit
            )

    async def _make_progress_decision(
        self,
        session_id: str,
        user_id: str,
        stages: list[dict],
        stage: dict,
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
            understanding_notes={
                "confused": decision.get("remediation_focus") or [],
            },
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
            next_idx = stage["stage_id"]  # stage_id 與 index 對齊（0-based 後需調整）
            # 找到下一個 stage
            current_idx = next((i for i, s in enumerate(stages) if s["stage_id"] == stage["stage_id"]), -1)
            if current_idx + 1 < len(stages):
                await longterm_memory.update_user_profile(user_id, len(wm.stage_turns))
                await self.run_stage(session_id, user_id, stages, current_idx + 1, emit)
            else:
                await emit({"type": "course_completed", "payload": {"message": "恭喜！你已完成所有學習階段。"}})

        elif d in ("retry", "remediate"):
            attempt = len(wm.stage_turns) + 1
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

            # 保留原有 stages，只更新問題清單
            wm.pending_questions = questions
            wm.stage_evaluations = []

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
            # 重新講解，換比喻框架
            user_profile_summary = await longterm_memory.get_user_profile_summary(user_id)
            weak_concepts = ", ".join(decision.get("remediation_focus") or [])
            ctx = AgentContext(
                session_id=session_id,
                user_id=user_id,
                task_payload={
                    "stage": {**stage, "content": stage["content"] + "\n\n（請換一個完全不同的比喻框架重新解釋）"},
                    "user_profile_summary": user_profile_summary,
                    "weak_concepts": weak_concepts or "無",
                },
            )
            full_explanation = ""
            async for chunk in self.teacher.stream_explanation(ctx):
                full_explanation += chunk
                await emit({"type": "explanation_chunk", "payload": {"chunk": chunk, "is_final": False}})

            await emit({"type": "explanation_chunk", "payload": {"chunk": "", "is_final": True}})
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
