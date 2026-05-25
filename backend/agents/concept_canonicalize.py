"""ConceptCanonicalizeAgent — splitter 之後的概念命名標準化層。

設計目的：把跨 session 漂移的 splitter 輸出 map 回歷史 canonical name，
讓 concept_mastery 個人化過濾不因命名字面差異而失效。

對應 spec: docs/superpowers/specs/2026-05-21-canonicalize-agent-design.md
"""
import json
from typing import Any

from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..llm.cache_context import llm_cache_context
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils import extract_json


class ConceptCanonicalizeAgent(BaseAgent):
    """把 splitter 新概念名 map 回同教材歷史 canonical name（三類判定）。"""

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        new_concepts: list[str] = payload.get("new_concepts") or []
        historical_pool: list[dict] = payload.get("historical_pool") or []
        t0 = self._log_start(
            ctx,
            new_count=len(new_concepts),
            historical_count=len(historical_pool),
        )

        # 早退：沒歷史就沒對齊對象，所有 new_concepts 直接標 decision=new，
        # 不必呼叫 LLM。對首次學該教材的 session（multi-source 與 small_file 常見場景）
        # 可省 1 次 LLM call。
        if not historical_pool:
            validated = [
                {"new_name": name, "decision": "new", "canonical": None,
                 "reason": "no historical pool"}
                for name in new_concepts
            ]
            self._log_end(ctx, t0, {"mapped": 0, "new": len(validated), "unsure": 0,
                                    "skipped": True})
            return {"mappings": validated}

        self._add_message(
            MessageRole.USER,
            f"new_concepts={json.dumps(new_concepts, ensure_ascii=False)}\n\n"
            f"historical_pool={json.dumps(historical_pool, ensure_ascii=False)}",
        )
        with llm_cache_context(agent_name="ConceptCanonicalizeAgent"):
            response = await self.llm.chat(
                self._messages, system_prompt=SYSTEM_PROMPTS["concept_canonicalize"]
            )
        self._reset()

        data = json.loads(extract_json(response.content))
        raw_mappings = data.get("mappings") or []

        historical_names = {h["concept_name"] for h in historical_pool}
        valid_decisions = {"mapped", "new", "unsure"}
        by_name = {m.get("new_name"): m for m in raw_mappings if isinstance(m, dict)}

        validated: list[dict] = []
        for name in new_concepts:
            entry = by_name.get(name)
            if not entry:
                validated.append({
                    "new_name": name, "decision": "unsure",
                    "canonical": None, "reason": "LLM 漏掉、fallback",
                })
                self._log.warning(
                    "canonicalize fallback (omitted)  concept=%s", name,
                )
                continue
            decision = entry.get("decision")
            canonical = entry.get("canonical")
            reason = entry.get("reason") or ""
            if decision not in valid_decisions:
                self._log.warning(
                    "canonicalize fallback (invalid decision)  concept=%s  raw=%s",
                    name, decision,
                )
                decision = "unsure"
                canonical = None
            elif decision == "mapped":
                if not canonical:
                    self._log.warning(
                        "canonicalize fallback (empty canonical)  concept=%s", name,
                    )
                    decision = "unsure"
                elif canonical not in historical_names:
                    self._log.warning(
                        "canonicalize fallback (canonical hallucinated)  "
                        "concept=%s  canonical=%s",
                        name, canonical,
                    )
                    decision = "unsure"
                    canonical = None
            validated.append({
                "new_name": name, "decision": decision,
                "canonical": canonical, "reason": reason,
            })

        stats = {"mapped": 0, "new": 0, "unsure": 0}
        for m in validated:
            stats[m["decision"]] = stats.get(m["decision"], 0) + 1
        self._log_end(ctx, t0, stats)

        return {"mappings": validated}
