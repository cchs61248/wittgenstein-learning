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
        t0 = self._log_start(
            ctx,
            stage_id=payload.get("current_stage_id", "?"),
            attempt=payload.get("current_attempt", "?"),
            evals=len(payload.get("evaluations", [])),
        )

        evaluations: list[dict] = payload["evaluations"]
        pass_threshold: float = payload.get("pass_threshold", 0.75)
        max_attempts: int = payload.get("max_attempts", 3)
        total_stages: int = payload.get("total_stages", 1)
        current_stage_id: int = payload.get("current_stage_id", 0)
        question_mode: str = payload.get("question_mode", "short_answer")
        is_dynamic: bool = payload.get("is_dynamic", False)
        remediate_count: int = payload.get("remediate_count", 0)
        max_remediate: int = 2

        raw_attempt = payload.get("current_attempt")
        try:
            attempts = int(raw_attempt) if raw_attempt is not None else len(evaluations)
        except (TypeError, ValueError):
            attempts = len(evaluations)
        attempts = max(1, attempts)
        raw_scores = [e.get("score", 0.0) for e in evaluations]

        if question_mode == "multiple_choice":
            scores = [correct_mc_score(s) for s in raw_scores]
        else:
            scores = raw_scores

        best_score = max(scores) if scores else 0.0
        latest_score = scores[-1] if scores else 0.0

        # 動態補強子節點：完成即前進，避免無限子節點
        if is_dynamic:
            result = {
                "decision": "advance",
                "message": "補強練習完成，繼續前進！",
                "next_stage_id": None,
                "best_score": best_score,
                "remediation_focus": None,
                "high_severity_misconceptions": [],
                "repeated_patterns_detected": False,
            }
            self._log_end(ctx, t0, {"decision": "advance(dynamic)", "best_score": best_score})
            return result

        # 超過最大補強次數：強制前進
        if remediate_count >= max_remediate:
            result = {
                "decision": "advance",
                "message": f"你已完成 {remediate_count} 次補強練習，讓我們繼續前進。",
                "next_stage_id": None,
                "best_score": best_score,
                "remediation_focus": None,
                "high_severity_misconceptions": [],
                "repeated_patterns_detected": False,
            }
            self._log_end(ctx, t0, {"decision": "advance(max_remediate)", "best_score": best_score})
            return result

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
            decision = "reteach"
            next_stage = None
            concept = high_severity[0].get("concept", "這個概念")
            message = f"我注意到你在「{concept}」上有根本性的誤解，讓我換個完全不同的角度重新解釋。"
        elif repeated_patterns:
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

        result = {
            "decision": decision,
            "message": message,
            "next_stage_id": next_stage,
            "best_score": best_score,
            "remediation_focus": unique_confused[:3] if unique_confused else None,
            "high_severity_misconceptions": high_severity[:3],
            "repeated_patterns_detected": repeated_patterns,
        }
        self._log_end(ctx, t0, {
            "decision": decision,
            "best_score": round(best_score, 3),
            "attempts": attempts,
            "high_severity": len(high_severity),
            "repeated": repeated_patterns,
        })
        return result
