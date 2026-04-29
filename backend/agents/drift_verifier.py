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
        """從候選文字中提取所有 [chunk_id] 引用，配對 source_chunks 中的原文。"""
        chunk_map = {
            c["chunk_id"]: c
            for c in source_chunks
            if isinstance(c, dict) and c.get("chunk_id")
        }
        cited_ids = list(dict.fromkeys(re.findall(r"\[([^\]]+)\]", candidate_text)))
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
        source_chunks: list[dict] = payload.get("source_chunks", [])
        candidate_text: str = payload.get("candidate_text", "")

        # 預先提取引用的 chunk，傳遞給 LLM 做精確的 citation accuracy 驗證
        cited_chunks = self._extract_cited_chunks(candidate_text, source_chunks)

        self._add_message(
            MessageRole.USER,
            f"content_type={content_type}\n\n"
            f"source_chunks={json.dumps(source_chunks, ensure_ascii=False)}\n\n"
            f"cited_chunks_lookup={json.dumps(cited_chunks, ensure_ascii=False)}\n\n"
            f"candidate_output={candidate_text}",
        )
        response = await self.llm.chat(self._messages, system_prompt=SYSTEM_PROMPTS["drift_verifier"])
        self._reset()

        data = json.loads(extract_json(response.content))

        # 後端強制補充：found=False 的 chunk_id 一定是未對齊的（不管 LLM 有沒有偵測到）
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

        return {
            "aligned": bool(data.get("aligned", False)),
            "issues": data.get("issues") or [],
            "missing_evidence": data.get("missing_evidence") or [],
            "revision_hint": str(data.get("revision_hint", "")).strip(),
            "claim_checks": claim_checks,
            "unsupported_claims": data.get("unsupported_claims") or [],
        }
