"""QuestionGenerator task_payload 的單一組裝點（run_stage / retry / resume）。"""

from typing import Any


def build_qg_task_payload(
    *,
    stage: dict,
    full_explanation: str,
    teaching_intent: dict | None,
    adaptive_ctx: dict | None,
    question_mode: str,
    attempt_number: int,
    previous_question_ids: list[str] | None = None,
    previous_question_texts: list[str] | None = None,
    mastery_map_override: dict | None = None,
    stage_content_suffix: str = "",
) -> dict[str, Any]:
    adaptive_ctx = adaptive_ctx or {}
    learner = adaptive_ctx.get("learner_state") or {}
    requirements = adaptive_ctx.get("next_lesson_requirements") or {}
    mastery_map = mastery_map_override if mastery_map_override is not None else (
        learner.get("mastery_map") or {}
    )
    must_reinforce = requirements.get("must_reinforce") or []
    stage_body = dict(stage)
    if stage_content_suffix:
        stage_body = {
            **stage,
            "content": (stage.get("content", "") or "") + stage_content_suffix,
        }
    num_questions = (
        max(4, stage.get("estimated_questions", 2) * 2)
        if question_mode == "multiple_choice"
        else stage.get("estimated_questions", 2)
    )
    return {
        "stage": stage_body,
        "teaching_intent": teaching_intent or {},
        "allowed_evidence": adaptive_ctx.get("allowed_evidence", []),
        "full_explanation": full_explanation,
        "mastery_map": mastery_map,
        "must_reinforce": must_reinforce,
        "num_questions": num_questions,
        "attempt_number": attempt_number,
        "previous_question_ids": list(previous_question_ids or []),
        "previous_question_texts": list(previous_question_texts or []),
        "question_mode": question_mode,
    }
