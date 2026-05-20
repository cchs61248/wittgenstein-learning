import json
import math
from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import LLMMessage, MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils import extract_json


def _format_distribution_quota(
    key_concepts: list[str], num_questions: int
) -> str:
    """為 stage 有 ≥ 2 個概念時生成「每概念配額」訊息段。

    單一概念上限 = ceil(num_questions / len(key_concepts)) + 1（避免過度集中）。
    補強節點（key_concepts 只 1 個）不注入，避免 noise。
    """
    if len(key_concepts) < 2:
        return ""
    per_max = math.ceil(num_questions / len(key_concepts)) + 1
    base = num_questions // len(key_concepts)
    remainder = num_questions - base * len(key_concepts)
    suggested = [base + 1 if i < remainder else base for i in range(len(key_concepts))]
    suggestion = "/".join(str(n) for n in suggested)
    quota_lines = [f"- {c}：至少 1 題、最多 {per_max} 題" for c in key_concepts]
    return (
        "\n\n【本階段關鍵概念與配額】\n"
        + "\n".join(quota_lines)
        + f"\n\n總題數 {num_questions} → 請盡量均勻分配（建議 {suggestion}）。"
        "\nQG 仍自由決定題目內容、難度、選項，但每概念至少 1 題、不可超過上限。"
    )


def _check_distribution_violations(
    questions: list[dict], key_concepts: list[str], num_questions: int
) -> list[tuple[str, int, int]]:
    """檢查 QG 回傳的題目分布是否違反 quota。

    回傳違規清單 [(concept, actual_count, max_allowed)]。
    補強節點（key_concepts < 2）不檢查。
    """
    if len(key_concepts) < 2:
        return []
    per_max = math.ceil(num_questions / len(key_concepts)) + 1
    counter: dict[str, int] = {}
    for q in questions:
        for kc in q.get("key_concepts_tested") or []:
            counter[kc] = counter.get(kc, 0) + 1
    return [(c, n, per_max) for c, n in counter.items() if n > per_max]


class QuestionGeneratorAgent(BaseAgent):
    async def _parse_or_repair_json(self, raw_text: str) -> dict[str, Any]:
        """解析 LLM 回傳的 JSON；失敗時最多重試兩次請 LLM 修復格式。"""
        candidate = extract_json(raw_text)
        for attempt in range(3):
            try:
                parsed = json.loads(candidate)
                if not isinstance(parsed, dict) or not isinstance(parsed.get("questions"), list):
                    raise ValueError("回傳 JSON 缺少 questions 陣列")
                return parsed
            except Exception as e:
                if attempt == 2:
                    raise
                self._log.warning(
                    "QuestionGeneratorAgent JSON repair attempt=%d  error=%s",
                    attempt + 1, e,
                )
                repair_system = (
                    "你是 JSON 修復器。只輸出合法 JSON，不要任何額外文字。"
                    "請確保字串內換行使用 \\n 且所有欄位間有逗號。"
                )
                repair_user = (
                    "請將下列內容修正為合法 JSON，保持原本語意與欄位。\n"
                    "必要欄位：questions(array)；每題：question_id, text, type, "
                    "answer_mode, options, correct_option_id, difficulty, "
                    "evidence_chunk_ids, key_concepts_tested, expected_answer_hints。\n"
                    f"目前錯誤：{e}\n\n"
                    f"{candidate}"
                )
                repaired = await self.llm.chat(
                    [LLMMessage(role=MessageRole.USER, content=repair_user)],
                    system_prompt=repair_system,
                )
                candidate = extract_json(repaired.content)
        raise RuntimeError("QuestionGeneratorAgent JSON repair exhausted")

    def _format_evidence(self, stage: dict[str, Any], allowed_evidence: list[dict]) -> str:
        """優先用 allowed_evidence（DB source chunks），否則退回 stage.source_chunks。"""
        if allowed_evidence:
            lines = []
            for c in allowed_evidence:
                chunk_id = str(c.get("chunk_id", "unknown"))
                text = (c.get("text") or c.get("quote") or "").strip()
                if text:
                    lines.append(f"[{chunk_id}] {text}")
            if lines:
                return "\n".join(lines)

        chunks = stage.get("source_chunks") or []
        if not isinstance(chunks, list) or not chunks:
            return "（無 source_chunks，可用內容僅限內容摘要）"
        lines = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            chunk_id = str(chunk.get("chunk_id", "")).strip() or "unknown"
            quote = (chunk.get("quote") or chunk.get("text") or "").strip()
            if quote:
                lines.append(f"[{chunk_id}] {quote}")
        return "\n".join(lines) if lines else "（source_chunks 格式不完整）"

    def _format_full_explanation(self, full_explanation: str) -> str:
        if not full_explanation or not full_explanation.strip():
            return ""
        return (
            "\n\n【本次講解全文（出題範圍限制）】\n"
            "以下是本次課程的完整講解內容。"
            "問題只能測試此講解中明確出現的概念與知識點；"
            "若 source_chunks 中有內容但未在以下講解中提及，請勿針對該內容出題。\n\n"
            + full_explanation
        )

    def _format_mastered_concepts(
        self, mastery_map: dict, key_concepts: list[str],
        must_reinforce: list[str] | None = None, threshold: float = 0.8,
    ) -> str:
        """從 mastery_map 過濾出已掌握概念清單（mastery>=threshold），
        format 成 prompt 訊息段落。沒有已掌握概念時回傳空字串。

        補強情境訊號衝突修正：若某概念雖 mastery>=threshold 但同時被列入
        must_reinforce（補強對象），代表「答對但有 misconception」需要再教，
        不應該被「禁止出題」——從 mastered 清單中扣除。
        """
        if not mastery_map:
            return ""
        reinforce_set = set(must_reinforce or [])
        mastered = [
            c for c, m in mastery_map.items()
            if isinstance(m, (int, float)) and m >= threshold
            and c not in reinforce_set
        ]
        if not mastered:
            return ""
        return (
            "\n\n【已掌握概念清單（請避免針對這些出題）】\n"
            f"以下概念學生 mastery>={threshold:.1f}，已穩定掌握，"
            "不要單純複問這些概念；可作為干擾項素材或與未掌握概念組合應用，"
            "但**不要把這些概念當作主要 key_concepts_tested**：\n"
            + "、".join(mastered)
        )

    def _format_teaching_intent(self, teaching_intent: dict) -> str:
        if not teaching_intent:
            return ""
        reinforced = teaching_intent.get("reinforced_concepts", [])
        analogies = teaching_intent.get("analogies_used", [])
        repair = teaching_intent.get("repair_target")
        if not reinforced and not analogies and not repair:
            return ""
        lines = ["\n【本篇講解的教學意圖（請讓問題與此對齊）】"]
        lines.append(f"補強概念：{', '.join(reinforced) if reinforced else '無'}")
        if analogies:
            lines.append(
                f"教師使用的類比（僅供理解教學側重，這些類比是教師自創的說明工具，"
                f"不存在於 source_chunks，禁止把類比細節當成題目素材）："
                f"{'; '.join(analogies)}"
            )
        lines.append(f"修正目標：{repair if repair else '無'}")
        if repair:
            lines.append("→ 至少一題直接測試上述修正目標")
        if reinforced:
            lines.append("→ 問題應測試學生是否理解補強概念的核心原理（依據 source_chunks），而非測試類比的情境細節")
        return "\n".join(lines)

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        stage = payload["stage"]
        t0 = self._log_start(
            ctx,
            stage_id=stage.get("stage_id", "?"),
            attempt=payload.get("attempt_number", 1),
            mode=payload.get("question_mode", "short_answer"),
            n=payload.get("num_questions", 2),
        )

        num_questions: int = payload.get("num_questions", 2)
        attempt_number: int = payload.get("attempt_number", 1)
        previous_question_ids: list[str] = payload.get("previous_question_ids", [])
        previous_question_texts: list[str] = payload.get("previous_question_texts", [])
        question_mode: str = payload.get("question_mode", "short_answer")
        teaching_intent: dict = payload.get("teaching_intent") or {}
        allowed_evidence: list[dict] = payload.get("allowed_evidence") or []
        full_explanation: str = payload.get("full_explanation") or ""
        mastery_map: dict = payload.get("mastery_map") or {}
        must_reinforce: list[str] = payload.get("must_reinforce") or []

        system = SYSTEM_PROMPTS["question_generator"].format(
            num_questions=num_questions,
            attempt_number=attempt_number,
            stage_id=stage["stage_id"],
            question_mode=question_mode,
        )
        avoid_note = ""
        if previous_question_ids:
            avoid_note = f"\n\n注意：已問過問題 ID：{previous_question_ids}，請避免重複。"
        if previous_question_texts:
            avoid_note += "\n已問過的題目文字（請完全避免相同或語意相似的提問）：\n" + "\n".join(f"- {t}" for t in previous_question_texts)

        evidence_text = self._format_evidence(stage, allowed_evidence)
        teaching_intent_text = self._format_teaching_intent(teaching_intent)
        full_explanation_text = self._format_full_explanation(full_explanation)
        mastered_text = self._format_mastered_concepts(
            mastery_map, stage.get("key_concepts", []), must_reinforce=must_reinforce,
        )
        quota_text = _format_distribution_quota(
            stage.get("key_concepts", []), num_questions
        )

        self._add_message(
            MessageRole.USER,
            f"階段：{stage['title']}\n"
            f"關鍵概念：{', '.join(stage.get('key_concepts', []))}\n"
            f"內容摘要：{stage.get('content', '')[:800]}\n\n"
            f"source_chunks（每題要附 evidence_chunk_ids）：\n{evidence_text}"
            f"{teaching_intent_text}"
            f"{full_explanation_text}"
            f"{mastered_text}"
            f"{quota_text}"
            f"{avoid_note}",
        )

        response = await self.llm.chat(self._messages, system_prompt=system)
        self._reset()

        data = await self._parse_or_repair_json(response.content)
        for q in data.get("questions", []):
            q["answer_mode"] = question_mode or q.get("answer_mode") or "short_answer"
            if not isinstance(q.get("evidence_chunk_ids"), list):
                q["evidence_chunk_ids"] = []
            if q["answer_mode"] != "multiple_choice":
                q["options"] = []
                q["correct_option_id"] = None

        violations = _check_distribution_violations(
            data.get("questions", []), stage.get("key_concepts", []), num_questions
        )
        if violations:
            self._log.warning(
                "QG distribution violation  session=%s  stage_id=%s  violations=%s",
                ctx.session_id, stage.get("stage_id"),
                [f"{c}={n}>max{m}" for c, n, m in violations],
            )

        self._log_end(ctx, t0, {"questions_count": len(data.get("questions", []))})
        return data
