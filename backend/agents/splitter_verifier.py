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


def normalize_verifier_result(data: dict) -> dict[str, Any]:
    """將 LLM 回傳正規化為 verifier + repair_plan 結構。"""
    missing_specs: list[dict] = []
    for item in data.get("missing_stage_specs") or []:
        if isinstance(item, dict):
            missing_specs.append({
                "title_hint": str(item.get("title_hint", "")).strip(),
                "must_cover_concepts": list(item.get("must_cover_concepts") or []),
                "source_chunk_ids": list(item.get("source_chunk_ids") or []),
            })

    forbidden: list[dict] = []
    for item in data.get("forbidden_mixes") or []:
        if isinstance(item, dict):
            forbidden.append({
                "stage_title_hint": str(
                    item.get("stage_title_hint") or item.get("stage_title_pattern") or ""
                ).strip(),
                "forbidden_concepts": list(item.get("forbidden_concepts") or []),
            })

    repair_text = str(data.get("repair_plan") or "").strip()
    required_titles = list(data.get("required_stage_titles") or [])

    return {
        "aligned": bool(data.get("aligned", False)),
        "missing_options": list(data.get("missing_options") or []),
        "issue_chunk_ids": list(data.get("issue_chunk_ids") or []),
        "reason": str(data.get("reason", "")).strip(),
        "required_stage_titles": required_titles,
        "missing_stage_specs": missing_specs,
        "forbidden_mixes": forbidden,
        "repair_plan": repair_text,
        "repair_plan_struct": {
            "required_stage_titles": required_titles,
            "missing_stage_specs": missing_specs,
            "forbidden_mixes": forbidden,
            "summary": repair_text,
        },
    }


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
        if isinstance(data, list):
            data = next((x for x in data if isinstance(x, dict)), {})
        if not isinstance(data, dict):
            data = {}
        result = normalize_verifier_result(data)
        self._log_end(ctx, t0, {
            "aligned": result["aligned"],
            "missing_count": len(result["missing_options"]),
            "repair_titles_count": len(result["required_stage_titles"]),
        })
        return result
