import json
from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS


class ContentSplitterAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        raw_content: str = payload["raw_content"]
        max_stages: int = payload.get("max_stages", 8)
        target_depth: str = payload.get("target_depth", "intermediate")

        system = SYSTEM_PROMPTS["content_splitter"].format(max_stages=max_stages)
        user_msg = (
            f"目標難度：{target_depth}\n\n"
            f"學習材料：\n{raw_content}"
        )
        self._add_message(MessageRole.USER, user_msg)

        response = await self.llm.chat(self._messages, system_prompt=system)
        self._reset()

        raw_json = response.content.strip()
        if raw_json.startswith("```"):
            raw_json = raw_json.split("```")[1]
            if raw_json.startswith("json"):
                raw_json = raw_json[4:]
        data = json.loads(raw_json.strip())
        return data
