import json
from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils import extract_json


class DriftVerifierAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        content_type: str = payload.get("content_type", "explanation")
        source_chunks: list[dict] = payload.get("source_chunks", [])
        candidate_text: str = payload.get("candidate_text", "")

        self._add_message(
            MessageRole.USER,
            f"content_type={content_type}\n\n"
            f"source_chunks={json.dumps(source_chunks, ensure_ascii=False)}\n\n"
            f"candidate_output={candidate_text}",
        )
        response = await self.llm.chat(self._messages, system_prompt=SYSTEM_PROMPTS["drift_verifier"])
        self._reset()

        data = json.loads(extract_json(response.content))
        return {
            "aligned": bool(data.get("aligned", False)),
            "issues": data.get("issues") or [],
            "missing_evidence": data.get("missing_evidence") or [],
            "revision_hint": str(data.get("revision_hint", "")).strip(),
        }
