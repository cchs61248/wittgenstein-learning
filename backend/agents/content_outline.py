"""ContentOutlineAgent — splitter 前的教材骨架抽取。

方案 C：先抽出具名案例、框架章節與建議 stage 標題，供 ContentSplitter 依骨架切分。
"""
import json
from typing import Any

from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils import extract_json


def normalize_outline(data: dict) -> dict[str, Any]:
    """將 LLM 回傳正規化為穩定 outline 結構。"""
    return {
        "required_stage_titles": list(data.get("required_stage_titles") or []),
        "named_cases": list(data.get("named_cases") or []),
        "framework_sections": list(data.get("framework_sections") or []),
        "summary_sections": list(data.get("summary_sections") or []),
        "must_cover_chunks": list(data.get("must_cover_chunks") or []),
    }


class ContentOutlineAgent(BaseAgent):
    """從 source_chunks 抽取教材骨架與並列具名案例。"""

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        source_chunks: list[dict] = payload.get("source_chunks") or []
        t0 = self._log_start(ctx, chunks=len(source_chunks))

        self._add_message(
            MessageRole.USER,
            f"source_chunks={json.dumps(source_chunks, ensure_ascii=False)}",
        )
        response = await self.llm.chat(
            self._messages, system_prompt=SYSTEM_PROMPTS["content_outline"]
        )
        self._reset()

        data = json.loads(extract_json(response.content))
        result = normalize_outline(data)
        self._log_end(ctx, t0, {
            "named_cases_count": len(result["named_cases"]),
            "required_titles_count": len(result["required_stage_titles"]),
        })
        return result
