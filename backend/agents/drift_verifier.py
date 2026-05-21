import json
import re
from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils import extract_json


class DriftVerifierAgent(BaseAgent):
    def _extract_cited_chunks(
        self, candidate_text: str, source_chunks: list[dict]
    ) -> list[dict]:
        """從候選文字中提取所有 chunk_id 引用，配對 source_chunks 中的原文。

        同時支援 Markdown 格式 [chunk_0001] 與 JSON 格式 ["chunk_0001"]。
        """
        chunk_map = {
            c["chunk_id"]: c
            for c in source_chunks
            if isinstance(c, dict) and c.get("chunk_id")
        }
        # 直接比對 chunk_id 命名模式，不受括號格式影響
        cited_ids = list(dict.fromkeys(re.findall(r'\bchunk_\w+\b', candidate_text)))
        result = []
        for cid in cited_ids:
            chunk = chunk_map.get(cid)
            if chunk:
                text = (chunk.get("text") or chunk.get("quote") or "").strip()
                result.append({"chunk_id": cid, "text": text[:400], "found": True})
            else:
                result.append({"chunk_id": cid, "text": "", "found": False})
        return result

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        content_type: str = payload.get("content_type", "explanation")
        t0 = self._log_start(ctx, content_type=content_type)

        source_chunks: list[dict] = payload.get("source_chunks", [])
        candidate_text: str = payload.get("candidate_text", "")
        full_explanation: str = payload.get("full_explanation") or ""
        next_stage_concepts: list[str] = payload.get("next_stage_concepts") or []
        forbidden_future_concepts: list[str] = payload.get("forbidden_future_concepts") or []
        stage_kind: str = (payload.get("stage_kind") or "").strip()
        must_reinforce_concepts: list[str] = payload.get("must_reinforce_concepts") or []

        cited_chunks = self._extract_cited_chunks(candidate_text, source_chunks)

        explanation_section = (
            f"\n\nfull_explanation（本次課程已驗證講解，出題對齊基準）：\n{full_explanation}"
            if content_type == "questions" and full_explanation.strip()
            else ""
        )

        next_stage_section = (
            f"\n\nnext_stage_concepts（下一節即將教的概念，本節對應段落只一句帶過、"
            f"不計入 coverage 必要元素）：{json.dumps(next_stage_concepts, ensure_ascii=False)}"
            if next_stage_concepts
            else ""
        )

        forbidden_future_section = (
            f"\n\nforbidden_future_concepts（再下下節以後的概念，source_chunks 中"
            f"語意對應的段落整段豁免、不計入 coverage 必要元素）："
            f"{json.dumps(forbidden_future_concepts, ensure_ascii=False)}"
            if forbidden_future_concepts
            else ""
        )

        remediation_section = ""
        if content_type == "explanation" and stage_kind == "remediation" and must_reinforce_concepts:
            remediation_section = (
                f"\n\nstage_kind=remediation；must_reinforce_concepts（補強模式反向 coverage "
                f"僅檢查以下弱項，其他 chunk 教學必要元素豁免）："
                f"{json.dumps(must_reinforce_concepts, ensure_ascii=False)}"
            )

        self._add_message(
            MessageRole.USER,
            f"content_type={content_type}\n\n"
            f"source_chunks={json.dumps(source_chunks, ensure_ascii=False)}\n\n"
            f"cited_chunks_lookup={json.dumps(cited_chunks, ensure_ascii=False)}\n\n"
            f"candidate_output={candidate_text}"
            f"{explanation_section}"
            f"{next_stage_section}"
            f"{forbidden_future_section}"
            f"{remediation_section}",
        )
        response = await self.llm.chat(self._messages, system_prompt=SYSTEM_PROMPTS["drift_verifier"])
        self._reset()

        data = json.loads(extract_json(response.content))

        claim_checks: list[dict] = data.get("claim_checks") or []
        for cc in cited_chunks:
            if not cc["found"]:
                existing = next(
                    (c for c in claim_checks if c.get("cited_chunk_id") == cc["chunk_id"]),
                    None,
                )
                if not existing:
                    claim_checks.append({
                        "cited_chunk_id": cc["chunk_id"],
                        "claim": "引用了不存在的 chunk_id",
                        "supported": False,
                        "issue": f"chunk_id '{cc['chunk_id']}' 不存在於 source_chunks",
                    })

        # 後端強制：只要任一 claim_check 未通過，aligned 必為 False（不信任 LLM 的 aligned 欄位）
        llm_aligned = bool(data.get("aligned", False))
        has_unsupported = any(not c.get("supported", True) for c in claim_checks)
        aligned = llm_aligned and not has_unsupported

        result = {
            "aligned": aligned,
            "issues": data.get("issues") or [],
            "missing_evidence": data.get("missing_evidence") or [],
            "revision_hint": str(data.get("revision_hint", "")).strip(),
            "claim_checks": claim_checks,
            "unsupported_claims": data.get("unsupported_claims") or [],
        }
        if not aligned:
            self._log.warning(
                "DriftVerifier NOT aligned  session=%s  content_type=%s  "
                "llm_aligned=%s  has_unsupported=%s  issues=%s",
                ctx.session_id, content_type, llm_aligned, has_unsupported,
                result["issues"],
            )
        self._log_end(ctx, t0, {"aligned": aligned, "issues": len(result["issues"])})
        return result
