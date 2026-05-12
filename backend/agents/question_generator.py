import json
from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import LLMMessage, MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils import extract_json


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

        self._add_message(
            MessageRole.USER,
            f"階段：{stage['title']}\n"
            f"關鍵概念：{', '.join(stage.get('key_concepts', []))}\n"
            f"內容摘要：{stage.get('content', '')[:800]}\n\n"
            f"source_chunks（每題要附 evidence_chunk_ids）：\n{evidence_text}"
            f"{teaching_intent_text}"
            f"{full_explanation_text}"
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

        self._log_end(ctx, t0, {"questions_count": len(data.get("questions", []))})
        return data
