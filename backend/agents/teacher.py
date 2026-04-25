from typing import Any, AsyncGenerator
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS


class TeacherAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        stage = payload["stage"]
        user_profile_summary: str = payload.get("user_profile_summary", "尚無資料")
        weak_concepts: str = payload.get("weak_concepts", "無")

        system = SYSTEM_PROMPTS["teacher"].format(
            user_profile_summary=user_profile_summary,
            weak_concepts=weak_concepts,
        )
        self._add_message(
            MessageRole.USER,
            f"請講解以下學習階段：\n\n"
            f"## {stage['title']}\n\n"
            f"{stage['content']}\n\n"
            f"關鍵概念：{', '.join(stage.get('key_concepts', []))}",
        )

        response = await self.llm.chat(self._messages, system_prompt=system)
        self._reset()
        return {"explanation": response.content}

    async def stream_explanation(
        self, ctx: AgentContext
    ) -> AsyncGenerator[str, None]:
        self._reset()
        payload = ctx.task_payload
        stage = payload["stage"]
        user_profile_summary: str = payload.get("user_profile_summary", "尚無資料")
        weak_concepts: str = payload.get("weak_concepts", "無")
        prev_stage_title: str | None = payload.get("prev_stage_title")

        system = SYSTEM_PROMPTS["teacher"].format(
            user_profile_summary=user_profile_summary,
            weak_concepts=weak_concepts,
        )
        prev_note = f"前一節點：「{prev_stage_title}」" if prev_stage_title else "本節是第一個節點"
        self._add_message(
            MessageRole.USER,
            f"節點 {stage.get('node_id', stage['stage_id'])}：{stage['title']}\n\n"
            f"{prev_note}\n\n"
            f"學習材料：\n{stage['content']}\n\n"
            f"關鍵概念：{', '.join(stage.get('key_concepts', []))}",
        )

        async for chunk in self.llm.stream_chat(self._messages, system_prompt=system):
            yield chunk
        self._reset()
