import json
from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS


class QuestionGeneratorAgent(BaseAgent):
    def _format_source_chunks(self, stage: dict[str, Any]) -> str:
        chunks = stage.get("source_chunks") or []
        if not isinstance(chunks, list) or not chunks:
            return "（無 source_chunks，可用內容僅限內容摘要）"
        lines: list[str] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            chunk_id = str(chunk.get("chunk_id", "")).strip() or "unknown"
            quote = str(chunk.get("quote", "")).strip()
            if not quote:
                continue
            lines.append(f"[{chunk_id}] {quote}")
        return "\n".join(lines) if lines else "（source_chunks 格式不完整）"

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        stage = payload["stage"]
        num_questions: int = payload.get("num_questions", 2)
        attempt_number: int = payload.get("attempt_number", 1)
        previous_question_ids: list[str] = payload.get("previous_question_ids", [])
        question_mode: str = payload.get("question_mode", "short_answer")

        system = SYSTEM_PROMPTS["question_generator"].format(
            num_questions=num_questions,
            attempt_number=attempt_number,
            stage_id=stage["stage_id"],
            question_mode=question_mode,
        )
        avoid_note = ""
        if previous_question_ids:
            avoid_note = f"\n\n注意：已問過問題 ID：{previous_question_ids}，請避免重複。"

        self._add_message(
            MessageRole.USER,
            f"階段：{stage['title']}\n"
            f"關鍵概念：{', '.join(stage.get('key_concepts', []))}\n"
            f"內容摘要：{stage['content'][:800]}\n\n"
            f"source_chunks（每題要附 evidence_chunk_ids）：\n{self._format_source_chunks(stage)}"
            f"{avoid_note}",
        )

        response = await self.llm.chat(self._messages, system_prompt=system)
        self._reset()

        raw_json = response.content.strip()
        if raw_json.startswith("```"):
            raw_json = raw_json.split("```")[1]
            if raw_json.startswith("json"):
                raw_json = raw_json[4:]
        data = json.loads(raw_json.strip())
        for q in data.get("questions", []):
            # question_mode 是 session 級設定，優先權高於 LLM 回傳的 answer_mode
            q["answer_mode"] = question_mode or q.get("answer_mode") or "short_answer"
            if not isinstance(q.get("evidence_chunk_ids"), list):
                q["evidence_chunk_ids"] = []
            if q["answer_mode"] != "multiple_choice":
                q["options"] = []
                q["correct_option_id"] = None
        return data
