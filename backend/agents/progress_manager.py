from typing import Any
from .base_agent import BaseAgent, AgentContext

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


def _unique_confused_concepts(
    evaluations: list[dict], stage_key_concepts: list[str] | None = None
) -> list[str]:
    """從評估結果中蒐集去重後的 confused concepts。

    若提供 stage_key_concepts，會把名稱對齊到 canonical 命名（normalize），
    確保動態節點 remediation_focus 不會跨出原 stage 命名空間，
    避免 concept_mastery 跨章節碎片化。
    """
    concepts: list[str] = []
    for ev in evaluations:
        concepts.extend(ev.get("confused_concepts", []))
    deduped = list(dict.fromkeys(c for c in concepts if c))
    if stage_key_concepts:
        from ..utils.concept_normalize import normalize_concepts
        return normalize_concepts(deduped, stage_key_concepts)
    return deduped


def _mastery_state(scores: list[float], confused: list[str], pass_threshold: float) -> str:
    if not scores:
        return "none"
    best_score = max(scores)
    avg_score = sum(scores) / len(scores)
    passed_count = sum(1 for score in scores if score >= pass_threshold)
    if best_score >= pass_threshold and not confused:
        return "complete"
    if best_score < 0.5 and avg_score < 0.5:
        return "none"
    if passed_count > 0 or best_score >= pass_threshold:
        return "partial"
    return "partial" if confused else "none"


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
        stage_kind: str = payload.get("stage_kind") or ("dynamic" if is_dynamic else "main")
        source_reteach_count: int = int(payload.get("source_reteach_count", 0) or 0)
        source_remediation_count: int = int(payload.get("source_remediation_count", 0) or 0)
        max_reteach: int = int(payload.get("max_reteach", 2) or 2)
        max_remediation: int = int(payload.get("max_remediation", 2) or 2)
        stage_key_concepts: list[str] = payload.get("stage_key_concepts") or []

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

        all_misconceptions: list[dict] = []
        for ev in evaluations:
            all_misconceptions.extend(ev.get("misconception_patterns", []))
        high_severity = [m for m in all_misconceptions if m.get("severity") == "high"]
        repeated_patterns = _detect_repeated_patterns(evaluations)
        unique_confused = _unique_confused_concepts(evaluations, stage_key_concepts)
        mastery = _mastery_state(scores, unique_confused, pass_threshold)
        is_child_stage = stage_kind in {"reteach", "remediation"}

        if is_child_stage:
            if mastery == "complete":
                decision = "advance"
                next_stage = None
                message = f"很好！你已掌握這個補充章節（校正得分：{best_score:.0%}），讓我們繼續。"
            elif best_score >= pass_threshold:
                # 補強 stage 已通關（best ≥ threshold）即視為 advance，
                # 不因 evaluator MC wrong path 殘留的 confused 再對同 focus 重複補強。
                decision = "advance"
                next_stage = None
                message = f"很好！補強之後你已通過此節（校正得分：{best_score:.0%}），讓我們繼續。"
            elif high_severity and source_reteach_count < max_reteach:
                # 子章節中仍出現根本性誤解，且重教預算未耗盡 → 升級為重教
                decision = "reteach"
                next_stage = None
                concept = high_severity[0].get("concept", "這個概念")
                message = f"你在「{concept}」上仍有根本性的誤解，我會再用一個新框架重新說明。"
            elif stage_kind == "reteach" and mastery == "none" and source_reteach_count < max_reteach:
                decision = "reteach"
                next_stage = None
                message = "這一輪仍未建立整體理解，我會再插入一個新的重教子章節，用另一個框架重新說明。"
            elif stage_kind == "reteach" and mastery == "none" and source_remediation_count < max_remediation:
                decision = "remediate"
                next_stage = None
                message = "同一章節的重教次數已達上限，我會改插入補強子章節，針對仍卡住的概念練習。"
            elif source_remediation_count < max_remediation:
                decision = "remediate"
                next_stage = None
                message = "你已掌握部分內容，我會針對仍卡住的概念新增補強子章節。"
            else:
                decision = "advance"
                next_stage = None
                message = "你已達到此章補強上限，先繼續前進，後續可再回顧這些概念。"
        elif mastery == "complete":
            decision = "advance"
            next_stage = current_stage_id + 1 if current_stage_id + 1 < total_stages else None
            message = f"很好！你已完全掌握這個階段（校正得分：{best_score:.0%}），讓我們繼續。"
        elif best_score >= pass_threshold and unique_confused:
            # 分數達標但仍有弱點概念，先針對性補強再前進
            decision = "remediate"
            next_stage = None
            message = f"分數不錯（{best_score:.0%}），但還有幾個概念需要釐清，我會針對這些弱點新增補強子章節。"
        elif high_severity:
            decision = "reteach"
            next_stage = None
            concept = high_severity[0].get("concept", "這個概念")
            message = f"我注意到你在「{concept}」上有根本性的誤解，讓我換個完全不同的角度重新解釋。"
        elif repeated_patterns:
            decision = "reteach"
            next_stage = None
            message = "我注意到你在同一個概念上重複出現相同的錯誤，讓我換個完全不同的比喻框架來解釋。"
        elif mastery == "none" and attempts == 1:
            # 首次嘗試全錯，先給一次重試機會再決定是否重教
            decision = "retry"
            next_stage = None
            message = "這次還沒抓到要點，讓我們再試一次，我會調整題目角度。"
        elif mastery == "none":
            decision = "reteach"
            next_stage = None
            message = "你目前對本章整體理解仍未建立，我會插入一個重教子章節，用新的框架重新說明。"
        elif mastery == "partial" and unique_confused and attempts >= max_attempts:
            decision = "remediate"
            next_stage = None
            message = "你已掌握部分內容，我會針對仍卡住的概念新增補強子章節。"
        elif mastery == "partial" and attempts < max_attempts and best_score >= 0.5:
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
