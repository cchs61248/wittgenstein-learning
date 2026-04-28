import json
from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS


class QuestionGeneratorAgent(BaseAgent):
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
            f"內容摘要：{stage['content'][:800]}"
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
            q["answer_mode"] = q.get("answer_mode") or question_mode
            if q["answer_mode"] != "multiple_choice":
                q["options"] = []
                q["correct_option_id"] = None
        return data
