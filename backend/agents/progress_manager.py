from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole

_MC_N_OPTIONS = 4  # 標準選擇題選項數


def correct_mc_score(raw: float, n: int = _MC_N_OPTIONS) -> float:
    """去除猜測成分的校正公式：corrected = (raw - 1/n) / (1 - 1/n)，下限 0.0。"""
    return max(0.0, (raw - 1 / n) / (1 - 1 / n))


class ProgressManagerAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        payload = ctx.task_payload
        evaluations: list[dict] = payload["evaluations"]
        pass_threshold: float = payload.get("pass_threshold", 0.75)
        max_attempts: int = payload.get("max_attempts", 3)
        total_stages: int = payload.get("total_stages", 1)
        current_stage_id: int = payload.get("current_stage_id", 0)
        question_mode: str = payload.get("question_mode", "short_answer")

        attempts = len(evaluations)
        raw_scores = [e.get("score", 0.0) for e in evaluations]

        # 選擇題套猜測校正，使分數與簡答題具可比性
        if question_mode == "multiple_choice":
            scores = [correct_mc_score(s) for s in raw_scores]
        else:
            scores = raw_scores

        best_score = max(scores) if scores else 0.0
        latest_score = scores[-1] if scores else 0.0

        if best_score >= pass_threshold:
            decision = "advance"
            next_stage = current_stage_id + 1 if current_stage_id + 1 < total_stages else None
            message = f"很好！你已理解這個階段（校正得分：{best_score:.0%}），讓我們繼續。"
        elif attempts < max_attempts:
            decision = "retry"
            next_stage = None
            message = f"還差一點（校正得分：{latest_score:.0%}），讓我們再試一次，這次題目難度會調整。"
        elif attempts == max_attempts and latest_score < 0.5:
            decision = "reteach"
            next_stage = None
            message = "讓我換個方式重新解釋這個概念，有時候換個角度就會豁然開朗。"
        else:
            decision = "remediate"
            next_stage = None
            message = "讓我補充一些額外的例子，幫助你從不同角度理解這個概念。"

        confused_concepts: list[str] = []
        for ev in evaluations:
            confused_concepts.extend(ev.get("confused_concepts", []))
        unique_confused = list(dict.fromkeys(confused_concepts))

        return {
            "decision": decision,
            "message": message,
            "next_stage_id": next_stage,
            "best_score": best_score,
            "remediation_focus": unique_confused[:3] if unique_confused else None,
        }
