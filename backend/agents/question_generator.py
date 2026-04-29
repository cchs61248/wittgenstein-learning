import json
from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils import extract_json


class QuestionGeneratorAgent(BaseAgent):
    def _format_evidence(self, stage: dict[str, Any], allowed_evidence: list[dict]) -> str:
        """優先用 allowed_evidence（DB source chunks），否則退回 stage.source_chunks。"""
        # 優先：context_builder 提供的真實 source chunks
        if allowed_evidence:
            lines = []
            for c in allowed_evidence:
                chunk_id = str(c.get("chunk_id", "unknown"))
                text = (c.get("text") or c.get("quote") or "").strip()
                if text:
                    lines.append(f"[{chunk_id}] {text}")
            if lines:
                return "\n".join(lines)

        # 退回：stage 內的 source_chunks（舊格式）
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

    def _format_teaching_intent(self, teaching_intent: dict) -> str:
        if not teaching_intent:
            return ""
        reinforced = teaching_intent.get("reinforced_concepts", [])
        analogies = teaching_intent.get("analogies_used", [])
        repair = teaching_intent.get("repair_target")
        if not reinforced and not analogies and not repair:
            return ""
        lines = ["\n【本篇文章的教學意圖（請讓問題與此對齊）】"]
        lines.append(f"補強概念：{', '.join(reinforced) if reinforced else '無'}")
        lines.append(f"使用的類比：{'; '.join(analogies) if analogies else '無'}")
        lines.append(f"修正目標：{repair if repair else '無'}")
        if repair:
            lines.append("→ 至少一題直接測試上述修正目標")
        if analogies:
            lines.append("→ 至少一題能檢驗學生是否理解文章使用的類比框架")
        return "\n".join(lines)

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        stage = payload["stage"]
        num_questions: int = payload.get("num_questions", 2)
        attempt_number: int = payload.get("attempt_number", 1)
        previous_question_ids: list[str] = payload.get("previous_question_ids", [])
        question_mode: str = payload.get("question_mode", "short_answer")
        teaching_intent: dict = payload.get("teaching_intent") or {}
        allowed_evidence: list[dict] = payload.get("allowed_evidence") or []

        system = SYSTEM_PROMPTS["question_generator"].format(
            num_questions=num_questions,
            attempt_number=attempt_number,
            stage_id=stage["stage_id"],
            question_mode=question_mode,
        )
        avoid_note = ""
        if previous_question_ids:
            avoid_note = f"\n\n注意：已問過問題 ID：{previous_question_ids}，請避免重複。"

        evidence_text = self._format_evidence(stage, allowed_evidence)
        teaching_intent_text = self._format_teaching_intent(teaching_intent)

        self._add_message(
            MessageRole.USER,
            f"階段：{stage['title']}\n"
            f"關鍵概念：{', '.join(stage.get('key_concepts', []))}\n"
            f"內容摘要：{stage.get('content', '')[:800]}\n\n"
            f"source_chunks（每題要附 evidence_chunk_ids）：\n{evidence_text}"
            f"{teaching_intent_text}"
            f"{avoid_note}",
        )

        response = await self.llm.chat(self._messages, system_prompt=system)
        self._reset()

        data = json.loads(extract_json(response.content))
        for q in data.get("questions", []):
            q["answer_mode"] = question_mode or q.get("answer_mode") or "short_answer"
            if not isinstance(q.get("evidence_chunk_ids"), list):
                q["evidence_chunk_ids"] = []
            if q["answer_mode"] != "multiple_choice":
                q["options"] = []
                q["correct_option_id"] = None
        return data
