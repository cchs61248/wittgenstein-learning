import json
from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import LLMMessage, MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS


class ContentSplitterAgent(BaseAgent):
    def _normalize_source_chunks(self, stage: dict[str, Any], idx: int) -> list[dict[str, str]]:
        raw_chunks = stage.get("source_chunks")
        normalized: list[dict[str, str]] = []
        if isinstance(raw_chunks, list):
            for c_idx, chunk in enumerate(raw_chunks):
                if not isinstance(chunk, dict):
                    continue
                quote = str(chunk.get("quote", "")).strip()
                if not quote:
                    continue
                chunk_id = str(chunk.get("chunk_id") or f"s{idx + 1}_c{c_idx + 1}")
                note = str(chunk.get("note", "")).strip()
                normalized.append({"chunk_id": chunk_id, "quote": quote, "note": note})
        if normalized:
            return normalized

        # 後備：若模型未回 source_chunks，至少保留本 stage 的可追溯引用片段
        fallback_quote = str(stage.get("content", "")).strip()[:500]
        if fallback_quote:
            return [{"chunk_id": f"s{idx + 1}_c1", "quote": fallback_quote, "note": "fallback"}]
        return []

    def _extract_json_candidate(self, text: str) -> str:
        s = text.strip()
        if s.startswith("```"):
            parts = s.split("```")
            if len(parts) >= 2:
                s = parts[1]
                if s.startswith("json"):
                    s = s[4:]
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return s[start : end + 1].strip()
        return s

    def _normalize_splitter_output(self, data: dict[str, Any], max_stages: int) -> dict[str, Any]:
        stages_raw = data.get("stages")
        stages: list[dict[str, Any]] = stages_raw if isinstance(stages_raw, list) else []
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
            normalized_stages.append(
                {
                    "stage_id": int(s.get("stage_id", idx + 1)),
                    "node_id": node_id,
                    "title": str(s.get("title", f"階段 {idx + 1}")),
                    "content": str(s.get("content", "")),
                    "source_chunks": self._normalize_source_chunks(s, idx),
                    "key_concepts": [
                        str(c) for c in (s.get("key_concepts") or []) if isinstance(c, (str, int, float))
                    ],
                    "prerequisites": [
                        str(c) for c in (s.get("prerequisites") or []) if isinstance(c, (str, int, float))
                    ],
                    "estimated_questions": int(s.get("estimated_questions", 2) or 2),
                }
            )

        if not normalized_stages:
            raise ValueError("內容切割結果缺少有效 stages")

        return {
            "stages": normalized_stages,
            "summary": str(data.get("summary", "")),
        }

    async def _parse_or_repair_json(self, raw_text: str, max_stages: int) -> dict[str, Any]:
        candidate = self._extract_json_candidate(raw_text)

        for attempt in range(3):
            try:
                parsed = json.loads(candidate)
                if not isinstance(parsed, dict):
                    raise ValueError("回傳 JSON 不是物件")
                return self._normalize_splitter_output(parsed, max_stages=max_stages)
            except Exception as e:
                if attempt == 2:
                    raise
                repair_system = (
                    "你是 JSON 修復器。只輸出合法 JSON，不要任何額外文字。"
                    "請確保字串內換行使用 \\n 且所有欄位間有逗號。"
                )
                repair_user = (
                    "請將下列內容修正為合法 JSON，保持原本語意與欄位。\n"
                    "必要欄位：stages(array), summary(string)。\n"
                    "stage 欄位：stage_id,title,content,key_concepts,prerequisites,estimated_questions。\n"
                    "stage 欄位中若有 source_chunks，元素需含 chunk_id,quote,note。\n"
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
        raw_content: str = payload.get("raw_content", "")
        provider_file_ref: dict | None = payload.get("provider_file_ref")
        max_stages: int = payload.get("max_stages", 8)
        target_depth: str = payload.get("target_depth", "intermediate")

        system = SYSTEM_PROMPTS["content_splitter"].format(max_stages=max_stages)
        user_msg = (
            f"目標難度：{target_depth}\n\n"
            "請直接閱讀附件檔案並切割學習階段。"
        )
        if raw_content.strip():
            user_msg += f"\n\n補充文字：\n{raw_content}"
        self._messages.append(LLMMessage(role=MessageRole.USER, content=user_msg, attachment=provider_file_ref))

        response = await self.llm.chat(self._messages, system_prompt=system)
        self._reset()
        return await self._parse_or_repair_json(response.content, max_stages=max_stages)
