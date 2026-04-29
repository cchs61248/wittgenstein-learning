from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole

_MC_N_OPTIONS = 4  # 標準選擇題選項數


def correct_mc_score(raw: float, n: int = _MC_N_OPTIONS) -> float:
    """去除猜測成分的校正公式：corrected = (raw - 1/n) / (1 - 1/n)，下限 0.0。"""
    return max(0.0, (raw - 1 / n) / (1 - 1 / n))


def _detect_repeated_patterns(evaluations: list[dict]) -> bool:
    """同一 misconception pattern 字串出現 >= 2 次 → 代表學生卡住同一錯誤，應強制換框架重教。"""
    patterns: list[str] = []
    for ev in evaluations:
        for m in ev.get("misconception_patterns", []):
            if isinstance(m, dict) and m.get("pattern"):
                patterns.append(m["pattern"])
    return len(patterns) != len(set(patterns))


class ProgressManagerAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        payload = ctx.task_payload
        evaluations: list[dict] = payload["evaluations"]
        pass_threshold: float = payload.get("pass_threshold", 0.75)
        max_attempts: int = payload.get("max_attempts", 3)
        total_stages: int = payload.get("total_stages", 1)
        current_stage_id: int = payload.get("current_stage_id", 0)
        question_mode: str = payload.get("question_mode", "short_answer")

        raw_attempt = payload.get("current_attempt")
        try:
            attempts = int(raw_attempt) if raw_attempt is not None else len(evaluations)
        except (TypeError, ValueError):
            attempts = len(evaluations)
        attempts = max(1, attempts)
        raw_scores = [e.get("score", 0.0) for e in evaluations]

        # 選擇題套猜測校正，使分數與簡答題具可比性
        if question_mode == "multiple_choice":
            scores = [correct_mc_score(s) for s in raw_scores]
        else:
            scores = raw_scores

        best_score = max(scores) if scores else 0.0
        latest_score = scores[-1] if scores else 0.0

        # 收集所有 misconception_patterns，診斷嚴重程度與重複模式
        all_misconceptions: list[dict] = []
        for ev in evaluations:
            all_misconceptions.extend(ev.get("misconception_patterns", []))
        high_severity = [m for m in all_misconceptions if m.get("severity") == "high"]
        repeated_patterns = _detect_repeated_patterns(evaluations)

        if best_score >= pass_threshold:
            decision = "advance"
            next_stage = current_stage_id + 1 if current_stage_id + 1 < total_stages else None
            message = f"很好！你已理解這個階段（校正得分：{best_score:.0%}），讓我們繼續。"
        elif high_severity:
            # 高嚴重度根本誤解 → 立即換框架重教，不繼續重試同框架
            decision = "reteach"
            next_stage = None
            concept = high_severity[0].get("concept", "這個概念")
            message = f"我注意到你在「{concept}」上有根本性的誤解，讓我換個完全不同的角度重新解釋。"
        elif repeated_patterns:
            # 同一錯誤重複出現 → 換框架重教
            decision = "reteach"
            next_stage = None
            message = "我注意到你在同一個概念上重複出現相同的錯誤，讓我換個完全不同的比喻框架來解釋。"
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
            "high_severity_misconceptions": high_severity[:3],
            "repeated_patterns_detected": repeated_patterns,
        }
