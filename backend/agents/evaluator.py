import json
from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS


class EvaluatorAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        question = payload["question"]
        user_answer: str = payload["user_answer"]
        compressed_history: list[dict] = payload.get("compressed_history", [])

        history_text = ""
        if compressed_history:
            lines = [f"Q: {t['q']}\nA: {t['a']}" for t in compressed_history[-3:]]
            history_text = "\n\n過去問答記錄（最近3輪）：\n" + "\n---\n".join(lines)

        self._add_message(
            MessageRole.USER,
            f"問題：{question['text']}\n"
            f"問題類型：{question.get('type', 'understand')}\n"
            f"要測試的概念：{', '.join(question.get('key_concepts_tested', []))}\n"
            f"評分參考要點（不公開）：{', '.join(question.get('expected_answer_hints', []))}\n"
            f"\n學生回答：{user_answer}"
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
