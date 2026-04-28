import json
from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS


class EvaluatorAgent(BaseAgent):
    def _format_options(self, question: dict[str, Any]) -> tuple[str, str]:
        options = question.get("options") or []
        if not isinstance(options, list) or not options:
            return "（非選擇題或無選項）", "（無）"

        option_lines: list[str] = []
        option_map: dict[str, str] = {}
        for opt in options:
            if not isinstance(opt, dict):
                continue
            opt_id = str(opt.get("id", "")).strip()
            opt_text = str(opt.get("text", "")).strip()
            if not opt_id:
                continue
            option_map[opt_id] = opt_text
            option_lines.append(f"{opt_id}. {opt_text}")

        correct_id = str(question.get("correct_option_id", "")).strip()
        correct_text = option_map.get(correct_id, "")
        correct_line = f"{correct_id}. {correct_text}" if correct_id else "（未提供）"
        return "\n".join(option_lines) if option_lines else "（選項格式不完整）", correct_line

    def _resolve_student_answer(self, question: dict[str, Any], user_answer: str) -> str:
        options = question.get("options") or []
        if not isinstance(options, list) or not options:
            return user_answer
        answer_key = str(user_answer).strip()
        for opt in options:
            if not isinstance(opt, dict):
                continue
            if str(opt.get("id", "")).strip() == answer_key:
                return f"{answer_key}. {str(opt.get('text', '')).strip()}"
        return user_answer

    def _format_source_chunks(self, source_chunks: list[dict]) -> str:
        if not source_chunks:
            return "（無 source_chunks）"
        lines: list[str] = []
        for chunk in source_chunks:
            if not isinstance(chunk, dict):
                continue
            chunk_id = str(chunk.get("chunk_id", "")).strip() or "unknown"
            quote = str(chunk.get("quote", "")).strip()
            if quote:
                lines.append(f"[{chunk_id}] {quote}")
        return "\n".join(lines) if lines else "（source_chunks 格式不完整）"

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        question = payload["question"]
        user_answer: str = payload["user_answer"]
        compressed_history: list[dict] = payload.get("compressed_history", [])
        source_chunks: list[dict] = payload.get("source_chunks", [])

        history_text = ""
        if compressed_history:
            lines = [f"Q: {t['q']}\nA: {t['a']}" for t in compressed_history[-3:]]
            history_text = "\n\n過去問答記錄（最近3輪）：\n" + "\n---\n".join(lines)
        options_text, correct_option_text = self._format_options(question)
        resolved_student_answer = self._resolve_student_answer(question, user_answer)

        self._add_message(
            MessageRole.USER,
            f"問題：{question['text']}\n"
            f"問題類型：{question.get('type', 'understand')}\n"
            f"作答模式：{question.get('answer_mode', 'short_answer')}\n"
            f"選項列表：\n{options_text}\n"
            f"正確選項（評分參考，不可直接洩漏）：{correct_option_text}\n"
            f"題目證據 chunk IDs：{', '.join(question.get('evidence_chunk_ids', []))}\n"
            f"要測試的概念：{', '.join(question.get('key_concepts_tested', []))}\n"
            f"評分參考要點（不公開）：{', '.join(question.get('expected_answer_hints', []))}\n"
            f"教材來源（評分只能依此判斷）：\n{self._format_source_chunks(source_chunks)}\n"
            f"\n學生回答：{resolved_student_answer}"
            f"{history_text}",
        )

        response = await self.llm.chat(self._messages, system_prompt=SYSTEM_PROMPTS["evaluator"])
        self._reset()

        raw_json = response.content.strip()
        if raw_json.startswith("```"):
            raw_json = raw_json.split("```")[1]
            if raw_json.startswith("json"):
                raw_json = raw_json[4:]
        data = json.loads(raw_json.strip())
        if isinstance(data.get("feedback"), str):
            data["feedback"] = data["feedback"].replace("\\n", "\n")
        return data
