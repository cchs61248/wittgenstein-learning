import json
from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import LLMMessage, MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils import extract_json


class ContentSplitterAgent(BaseAgent):
    def _extract_json_candidate(self, text: str) -> str:
        return extract_json(text)

    def _normalize_splitter_output(
        self,
        data: dict[str, Any],
        max_stages: int,
        db_chunks: dict[str, dict],
    ) -> dict[str, Any]:
        """
        正規化 LLM 輸出：
        - 驗證 source_chunk_ids 均存在於後端 chunks
        - 由後端回填真實 quote（LLM 不生成 quote）
        - 移除無效 chunk_id 引用
        """
        stages_raw = data.get("stages")
        stages: list[dict[str, Any]] = stages_raw if isinstance(stages_raw, list) else []
        chunk_roles: dict[str, str] = data.get("chunk_roles") or {}
        normalized_stages: list[dict[str, Any]] = []

        for idx, s in enumerate(stages[:max_stages]):
            if not isinstance(s, dict):
                continue

            raw_node_id = s.get("node_id")
            if raw_node_id:
                node_id = str(raw_node_id)
            else:
                chapter = (idx // 3) + 1
                section = (idx % 3) + 1
                node_id = f"{chapter}.{section}"

            # 驗證並回填 source_chunks（後端真實原文）
            raw_ids = s.get("source_chunk_ids") or []
            valid_ids = [cid for cid in raw_ids if cid in db_chunks]
            source_chunks = [
                {
                    "chunk_id": cid,
                    "quote": db_chunks[cid]["text"],
                    "note": f"order_index={db_chunks[cid]['order_index']}",
                }
                for cid in valid_ids
            ]

            # fallback：若 LLM 沒回傳有效 chunk_ids，用順序推斷
            if not source_chunks and db_chunks:
                sorted_chunks = sorted(db_chunks.values(), key=lambda c: c["order_index"])
                chunk_count = max(2, len(sorted_chunks) // max(len(stages), 1))
                start = idx * chunk_count
                fallback_chunks = sorted_chunks[start: start + chunk_count]
                source_chunks = [
                    {"chunk_id": c["chunk_id"], "quote": c["text"], "note": "fallback"}
                    for c in fallback_chunks
                ]
                valid_ids = [c["chunk_id"] for c in fallback_chunks]

            normalized_stages.append(
                {
                    "stage_id": int(s.get("stage_id", idx + 1)),
                    "node_id": node_id,
                    "title": str(s.get("title", f"階段 {idx + 1}")),
                    "source_chunk_ids": valid_ids,
                    "source_chunks": source_chunks,
                    "key_concepts": [
                        str(c)
                        for c in (s.get("key_concepts") or [])
                        if isinstance(c, (str, int, float))
                    ],
                    "prerequisites": [
                        str(c)
                        for c in (s.get("prerequisites") or [])
                        if isinstance(c, (str, int, float))
                    ],
                    "estimated_questions": int(s.get("estimated_questions", 2) or 2),
                    "teaching_goal": str(s.get("teaching_goal", "")),
                }
            )

        if not normalized_stages:
            raise ValueError("內容切割結果缺少有效 stages")

        return {
            "stages": normalized_stages,
            "chunk_roles": chunk_roles,
            "summary": str(data.get("summary", "")),
        }

    async def _parse_or_repair_json(
        self,
        raw_text: str,
        max_stages: int,
        db_chunks: dict[str, dict],
    ) -> dict[str, Any]:
        candidate = self._extract_json_candidate(raw_text)

        for attempt in range(3):
            try:
                parsed = json.loads(candidate)
                if not isinstance(parsed, dict):
                    raise ValueError("回傳 JSON 不是物件")
                return self._normalize_splitter_output(parsed, max_stages=max_stages, db_chunks=db_chunks)
            except Exception as e:
                if attempt == 2:
                    raise
                repair_system = (
                    "你是 JSON 修復器。只輸出合法 JSON，不要任何額外文字。"
                    "請確保字串內換行使用 \\n 且所有欄位間有逗號。"
                )
                repair_user = (
                    "請將下列內容修正為合法 JSON，保持原本語意與欄位。\n"
                    "必要欄位：stages(array), chunk_roles(object), summary(string)。\n"
                    "stage 欄位：stage_id, node_id, title, source_chunk_ids, "
                    "key_concepts, prerequisites, estimated_questions, teaching_goal。\n"
                    f"目前錯誤：{e}\n\n"
                    f"{candidate}"
                )
                repaired = await self.llm.chat(
                    [LLMMessage(role=MessageRole.USER, content=repair_user)],
                    system_prompt=repair_system,
                )
                candidate = self._extract_json_candidate(repaired.content)

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        source_chunks: list[dict] = payload.get("source_chunks", [])
        max_stages: int = payload.get("max_stages", 30)
        target_depth: str = payload.get("target_depth", "intermediate")

        # 建立 chunk_id → chunk dict 供後端回填
        db_chunks: dict[str, dict] = {c["chunk_id"]: c for c in source_chunks}

        # 組裝 chunks 呈現給 LLM（只傳 id + text，不傳 source truth 以外的欄位）
        chunks_text = "\n\n".join(
            f"[{c['chunk_id']}]\n{c['text']}"
            for c in sorted(source_chunks, key=lambda x: x.get("order_index", 0))
        )

        system = SYSTEM_PROMPTS["content_splitter"].format(max_stages=max_stages)
        user_msg = (
            f"目標難度：{target_depth}\n\n"
            f"以下是教材的分段內容，請根據語義關係組合成學習階段：\n\n"
            f"{chunks_text}"
        )
        self._messages.append(LLMMessage(role=MessageRole.USER, content=user_msg))

        response = await self.llm.chat(self._messages, system_prompt=system)
        self._reset()
        return await self._parse_or_repair_json(response.content, max_stages=max_stages, db_chunks=db_chunks)
