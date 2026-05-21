"""SplitterVerifierAgent — splitter 之後的並列方案完整性驗證。

設計目的：偵測 splitter「漏切並列方案 / mash-up」失敗（如 sess_u055rzse5
stage 9（一）信貸 + stage 10（三）股票質押、（二）房貸 mash-up 進 stage 10）。
判 false 時由 orchestrator bounded reroll splitter 1 次。

對應 spec: docs/superpowers/specs/2026-05-21-splitter-verifier-agent-design.md
"""
import json
from typing import Any

from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils import extract_json


class SplitterVerifierAgent(BaseAgent):
    """驗證 splitter 是否漏切教材原文宣告的並列方案。"""

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        source_chunks: list[dict] = payload.get("source_chunks") or []
        stages: list[dict] = payload.get("stages") or []
        t0 = self._log_start(
            ctx,
            chunks=len(source_chunks),
            stages=len(stages),
        )

        # 組裝 user message
        self._add_message(
            MessageRole.USER,
            f"source_chunks={json.dumps(source_chunks, ensure_ascii=False)}\n\n"
            f"stages={json.dumps(stages, ensure_ascii=False)}",
        )
        response = await self.llm.chat(
            self._messages, system_prompt=SYSTEM_PROMPTS["splitter_verifier"]
        )
        self._reset()

        data = json.loads(extract_json(response.content))

        result = {
            "aligned": bool(data.get("aligned", True)),
            "missing_options": list(data.get("missing_options") or []),
            "issue_chunk_ids": list(data.get("issue_chunk_ids") or []),
            "reason": str(data.get("reason", "")).strip(),
        }
        self._log_end(ctx, t0, {
            "aligned": result["aligned"],
            "missing_count": len(result["missing_options"]),
        })
        return result
