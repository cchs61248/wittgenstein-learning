"""StageConsolidatorAgent — P0b-2 全局協調 stages（chunks ≥ 30）.

per-source split 後攤平 + jaccard 合併 + normalize 之後，每章的 splitter 仍
可能採用不同 prefix 命名（「借錢外掛」「借錢工具解析」「借錢炒股迷思」其實
講同類事）。本 agent 一次拿全部 stages，做：

1. 統一 prefix 命名（同類用相同前綴）
2. 同類 stages 連續排序（避免跳號）
3. 跨章節同主題合併（jaccard 抓不到的語意合併）
4. title 字數 ≤ 20、編號（一）（二）必須連續

**硬約束**：不可新增/移除任何 chunk_id、不可移除 key_concepts。只 reassign
chunk_ids 到 stage、rename title、reorder。

若 LLM 輸出無效或破壞 chunk 完整性，fallback 沿用原 stages。
"""
from __future__ import annotations

import json
from typing import Any

from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..llm.cache_context import llm_cache_context
from ..utils import extract_json
from ..utils.prompt_templates import SYSTEM_PROMPTS


def _stage_chunks_set(stages: list[dict]) -> set[str]:
    out: set[str] = set()
    for s in stages or []:
        for cid in s.get("source_chunk_ids") or []:
            if isinstance(cid, str):
                out.add(cid)
    return out


def _is_valid_consolidation(
    original: list[dict], consolidated: list[dict]
) -> tuple[bool, str]:
    """Verify consolidator output preserves chunk coverage."""
    if not isinstance(consolidated, list) or not consolidated:
        return False, "empty or non-list output"
    orig_chunks = _stage_chunks_set(original)
    new_chunks = _stage_chunks_set(consolidated)
    missing = orig_chunks - new_chunks
    extra = new_chunks - orig_chunks
    if missing:
        return False, f"dropped {len(missing)} chunks: {sorted(missing)[:5]}"
    if extra:
        return False, f"fabricated {len(extra)} chunks: {sorted(extra)[:5]}"
    for s in consolidated:
        if not isinstance(s, dict):
            return False, "non-dict stage"
        if not s.get("title"):
            return False, "stage missing title"
        if not s.get("source_chunk_ids"):
            return False, f"stage '{s.get('title')}' has no chunks"
    return True, ""


def _payload_for_llm(stages: list[dict]) -> list[dict]:
    """Strip stages down to the fields the consolidator needs to reason about.

    `first_chunk_id` lets the LLM honour rule F (global reading order).
    """
    out: list[dict] = []
    for s in stages:
        cids = list(s.get("source_chunk_ids") or [])
        first = min(cids) if cids else ""
        out.append({
            "title": s.get("title") or "",
            "key_concepts": list(s.get("key_concepts") or []),
            "source_chunk_ids": cids,
            "first_chunk_id": first,
            "teaching_goal": s.get("teaching_goal") or "",
            "node_id": s.get("node_id") or "",
        })
    return out


class StageConsolidatorAgent(BaseAgent):
    """One-shot global rename + reorder + merge for per-source-split stages."""

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        with llm_cache_context(agent_name="StageConsolidatorAgent"):
            self._reset()
            payload = ctx.task_payload
            stages: list[dict] = payload.get("stages") or []
            outline: dict | None = payload.get("required_outline")
            sources_manifest: list[dict] = payload.get("sources_manifest") or []

            t0 = self._log_start(
                ctx, stages=len(stages), outline=bool(outline),
                sources=len(sources_manifest),
            )

            if len(stages) <= 1:
                self._log_end(ctx, t0, {"stages_kept": len(stages), "skipped": True})
                return {"stages": stages, "skipped": True, "reason": "≤1 stage"}

            user_msg = {
                "stages": _payload_for_llm(stages),
                "sources_manifest": sources_manifest,
            }
            if outline:
                user_msg["required_outline"] = {
                    "required_stage_titles": outline.get("required_stage_titles") or [],
                    "named_cases": outline.get("named_cases") or [],
                    "framework_sections": outline.get("framework_sections") or [],
                    "summary_sections": outline.get("summary_sections") or [],
                }

            self._add_message(
                MessageRole.USER,
                json.dumps(user_msg, ensure_ascii=False),
            )
            try:
                response = await self.llm.chat(
                    self._messages, system_prompt=SYSTEM_PROMPTS["stage_consolidator"]
                )
                self._reset()
                data = json.loads(extract_json(response.content))
            except Exception as e:
                self._log.warning(
                    "StageConsolidatorAgent LLM/parse failed  session=%s  err=%s — keeping original",
                    ctx.session_id, e,
                )
                self._log_end(ctx, t0, {"stages_kept": len(stages), "fallback": True})
                return {"stages": stages, "fallback": True, "reason": str(e)}

            consolidated = data.get("consolidated_stages") if isinstance(data, dict) else None
            ok, reason = _is_valid_consolidation(stages, consolidated or [])
            if not ok:
                self._log.warning(
                    "StageConsolidatorAgent validation failed  session=%s  reason=%s — keeping original",
                    ctx.session_id, reason,
                )
                self._log_end(ctx, t0, {"stages_kept": len(stages), "fallback": True})
                return {"stages": stages, "fallback": True, "reason": reason}

            # Re-attach extra fields that LLM may have dropped (estimated_questions, prerequisites)
            orig_by_title = {(s.get("title") or "").strip(): s for s in stages}
            for new_s in consolidated:
                # Inherit prerequisites / estimated_questions / chunk_roles from
                # the first original stage that contributed to this consolidated stage.
                if not new_s.get("estimated_questions"):
                    new_s["estimated_questions"] = 3
                if "prerequisites" not in new_s:
                    new_s["prerequisites"] = []
                # If LLM's title matches an original title, inherit teaching_goal as fallback
                t = (new_s.get("title") or "").strip()
                if not new_s.get("teaching_goal") and t in orig_by_title:
                    new_s["teaching_goal"] = orig_by_title[t].get("teaching_goal") or ""

            self._log_end(ctx, t0, {
                "stages_before": len(stages),
                "stages_after": len(consolidated),
            })
            return {"stages": consolidated, "fallback": False}
