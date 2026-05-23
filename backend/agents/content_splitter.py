import json
import logging
import re
from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import LLMMessage, MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils import extract_json
from ..utils.small_curriculum import merge_duplicate_topic_stages


_log = logging.getLogger("wl.agents.splitter")

# kc 通常以「案例：/實務：/範例：」前綴 dup 原概念，要 dedupe 前綴版
_KC_CASE_PREFIX_RE = re.compile(r"^(案例|實務|實例|範例|案例分析|案例研究)[：:]")


def _sanitize_key_concepts(raw_kc: list, stage_title: str) -> list[str]:
    """過濾 R10 (d) 違規的 key_concept：
    (a) kc 與 stage title 字面相同 → 刪除（title 是「教學單元名稱」，不是 kc）
    (b) kc 含中文「：」或全形破折號「—」分隔符 → 通常是 title 變種，刪除
    (c) kc 以「案例：/實務：/範例：」開頭 + 同 stage 其他 kc 已含同主題 → 去前綴後 dedupe
    """
    title_norm = stage_title.strip()
    out: list[str] = []
    seen_norm: set[str] = set()
    for c in raw_kc:
        if not isinstance(c, (str, int, float)):
            continue
        cs = str(c).strip()
        if not cs:
            continue
        # (a)
        if cs == title_norm:
            _log.warning("splitter kc title-leak dropped: %r (= stage title)", cs)
            continue
        # (b)
        if "：" in cs or "—" in cs:
            _log.warning("splitter kc title-leak dropped: %r (contains separator)", cs)
            continue
        # (c)
        stripped = _KC_CASE_PREFIX_RE.sub("", cs).strip()
        norm = stripped.lower()
        if norm in seen_norm:
            _log.warning(
                "splitter kc dup dropped: %r (case-prefix variant of existing)", cs
            )
            continue
        seen_norm.add(norm)
        out.append(cs)
    return out


def _format_chunks_with_sources(source_chunks: list[dict]) -> str:
    """
    將 chunks 格式化為 ContentSplitter 的輸入文字。
    若 chunks 帶有 source_label，依來源分組顯示（跨來源聚合提示）；
    否則維持原本的平面格式。
    """
    sorted_chunks = sorted(source_chunks, key=lambda x: x.get("order_index", 0))

    has_sources = any(c.get("source_label") for c in sorted_chunks)
    if not has_sources:
        return "\n\n".join(f"[{c['chunk_id']}]\n{c['text']}" for c in sorted_chunks)

    # 依 source_index 分組（保持插入順序）
    groups: dict[int, list[dict]] = {}
    for c in sorted_chunks:
        idx = c.get("source_index", 0)
        groups.setdefault(idx, []).append(c)

    parts: list[str] = []
    for idx in sorted(groups):
        chunks_in_group = groups[idx]
        label = chunks_in_group[0].get("source_label", f"來源 {idx + 1}")
        header = f"{'=' * 3} 來源 {idx + 1}：{label} {'=' * 3}"
        body = "\n\n".join(f"[{c['chunk_id']}]\n{c['text']}" for c in chunks_in_group)
        parts.append(f"{header}\n\n{body}")

    return "\n\n".join(parts)


class ContentSplitterAgent(BaseAgent):
    def _extract_json_candidate(self, text: str) -> str:
        return extract_json(text)

    def _normalize_splitter_output(
        self,
        data: dict[str, Any],
        max_stages: int,
        db_chunks: dict[str, dict],
        preserve_thin_stages: bool = False,
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

            stage_title = str(s.get("title", f"階段 {idx + 1}"))
            normalized_stages.append(
                {
                    "stage_id": int(s.get("stage_id", idx + 1)),
                    "node_id": node_id,
                    "title": stage_title,
                    "source_chunk_ids": valid_ids,
                    "source_chunks": source_chunks,
                    "key_concepts": _sanitize_key_concepts(
                        s.get("key_concepts") or [], stage_title
                    ),
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

        if preserve_thin_stages:
            # 方案 C：outline / repair_plan 已明確要求具名案例拆開。
            # 這時單一 chunk 支撐多個案例 stage 是合理的；只合併多來源產生的同主題重複 stage。
            normalized_stages = merge_duplicate_topic_stages(normalized_stages)
        else:
            normalized_stages = self._merge_thin_stages(normalized_stages)

        return {
            "stages": normalized_stages,
            "chunk_roles": chunk_roles,
            "summary": str(data.get("summary", "")),
        }

    def _renumber_stages(self, stages: list[dict]) -> list[dict]:
        result = stages
        for j, s in enumerate(result):
            s["stage_id"] = j + 1
            chapter = (j // 3) + 1
            section = (j % 3) + 1
            s["node_id"] = f"{chapter}.{section}"
        return result

    def _merge_thin_stages(self, stages: list[dict]) -> list[dict]:
        """將 source_chunk_ids < 2 的小型 stage 合併至後繼 stage（最後一個合往前）。"""
        if len(stages) <= 1:
            result = stages
        else:
            result = self._merge_thin_stages_body(stages)
        return self._renumber_stages(result)

    def _stage_merge_key(self, stage: dict) -> str:
        return " ".join(str(stage.get("title", "")).lower().split())

    def _merge_stage_into(self, target: dict, incoming: dict) -> None:
        existing_ids = set(target.get("source_chunk_ids") or [])
        new_ids = [
            cid for cid in (incoming.get("source_chunk_ids") or [])
            if cid not in existing_ids
        ]
        target["source_chunk_ids"] = list(target.get("source_chunk_ids") or []) + new_ids

        existing_chunk_ids = {
            sc.get("chunk_id") for sc in (target.get("source_chunks") or [])
        }
        target["source_chunks"] = list(target.get("source_chunks") or []) + [
            sc for sc in (incoming.get("source_chunks") or [])
            if sc.get("chunk_id") not in existing_chunk_ids
        ]
        target["key_concepts"] = list(dict.fromkeys(
            list(target.get("key_concepts") or []) +
            list(incoming.get("key_concepts") or [])
        ))
        target["prerequisites"] = list(dict.fromkeys(
            list(target.get("prerequisites") or []) +
            list(incoming.get("prerequisites") or [])
        ))
        target["estimated_questions"] = max(
            int(target.get("estimated_questions", 2) or 2),
            int(incoming.get("estimated_questions", 2) or 2),
        )

    def _merge_duplicate_topic_stages(self, stages: list[dict]) -> list[dict]:
        """合併同標題重複 stage；保留不同具名案例的單 chunk stage。"""
        result: list[dict] = []
        by_key: dict[str, dict] = {}
        for stage in stages:
            key = self._stage_merge_key(stage)
            if key and key in by_key:
                self._merge_stage_into(by_key[key], stage)
                continue
            result.append(stage)
            if key:
                by_key[key] = stage
        return self._renumber_stages(result)

    def _merge_thin_stages_body(self, stages: list[dict]) -> list[dict]:
        result: list[dict] = []
        i = 0
        while i < len(stages):
            cur = stages[i]
            if len(cur.get("source_chunk_ids", [])) < 2 and i + 1 < len(stages):
                nxt = stages[i + 1]
                existing = set(nxt["source_chunk_ids"])
                new_ids = [c for c in cur["source_chunk_ids"] if c not in existing]
                nxt["source_chunk_ids"] = new_ids + nxt["source_chunk_ids"]
                nxt["source_chunks"] = [
                    sc for sc in cur["source_chunks"] if sc["chunk_id"] in new_ids
                ] + nxt["source_chunks"]
                nxt["key_concepts"] = list(dict.fromkeys(
                    cur["key_concepts"] + nxt["key_concepts"]
                ))
                i += 1
                continue
            result.append(cur)
            i += 1

        # 最後一個 stage 若仍只有 1 chunk，合往前一個
        if len(result) >= 2 and len(result[-1].get("source_chunk_ids", [])) < 2:
            last = result.pop()
            prev = result[-1]
            existing = set(prev["source_chunk_ids"])
            new_ids = [c for c in last["source_chunk_ids"] if c not in existing]
            prev["source_chunk_ids"] = prev["source_chunk_ids"] + new_ids
            prev["source_chunks"] = prev["source_chunks"] + [
                sc for sc in last["source_chunks"] if sc["chunk_id"] in new_ids
            ]
            prev["key_concepts"] = list(dict.fromkeys(
                prev["key_concepts"] + last["key_concepts"]
            ))

        return result

    async def _parse_or_repair_json(
        self,
        raw_text: str,
        max_stages: int,
        db_chunks: dict[str, dict],
        preserve_thin_stages: bool = False,
    ) -> dict[str, Any]:
        candidate = self._extract_json_candidate(raw_text)

        for attempt in range(3):
            try:
                parsed = json.loads(candidate)
                if not isinstance(parsed, dict):
                    raise ValueError("回傳 JSON 不是物件")
                return self._normalize_splitter_output(
                    parsed,
                    max_stages=max_stages,
                    db_chunks=db_chunks,
                    preserve_thin_stages=preserve_thin_stages,
                )
            except Exception as e:
                if attempt == 2:
                    raise
                self._log.warning(
                    "ContentSplitterAgent JSON repair attempt=%d  error=%s", attempt + 1, e
                )
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
        t0 = self._log_start(
            ctx,
            chunks=len(ctx.task_payload.get("source_chunks", [])),
            target_depth=ctx.task_payload.get("target_depth", "?"),
        )

        payload = ctx.task_payload
        source_chunks: list[dict] = payload.get("source_chunks", [])
        max_stages: int = payload.get("max_stages", 30)
        target_depth: str = payload.get("target_depth", "intermediate")
        previous_attempt_missed: list[str] = payload.get("previous_attempt_missed") or []
        issue_chunk_ids: list[str] = payload.get("issue_chunk_ids") or []
        verifier_reason: str = (payload.get("verifier_reason") or "").strip()
        required_outline: dict | None = payload.get("required_outline")
        repair_plan_struct: dict | None = payload.get("repair_plan_struct")
        must_cover_topics: list[str] = payload.get("must_cover_topics") or []

        db_chunks: dict[str, dict] = {c["chunk_id"]: c for c in source_chunks}

        chunks_text = _format_chunks_with_sources(source_chunks)

        system = SYSTEM_PROMPTS["content_splitter"].format(max_stages=max_stages)

        outline_section = ""
        if required_outline:
            outline_section = (
                "\n\n【教材骨架 required_outline】本輪切分必須遵守："
                f"\n{json.dumps(required_outline, ensure_ascii=False)}"
            )

        must_cover_section = ""
        if must_cover_topics:
            cleaned = [str(t).strip() for t in must_cover_topics if str(t).strip()]
            if cleaned:
                must_cover_section = (
                    "\n\n【強約束 — 本 region 必須教到的核心概念（來自 MacroRegionPlanner tier-3）】"
                    "\n以下每個概念必須出現在至少一個 stage 的 key_concepts 內（字面 exact match）；"
                    "概念數量 ≥ 4 個時，原則上每個概念對應獨立 stage，禁止 mash-up 壓縮："
                    f"\n{json.dumps(cleaned, ensure_ascii=False)}"
                )

        retry_hint_section = ""
        if repair_plan_struct or previous_attempt_missed or verifier_reason:
            parts = ["\n\n【重試提示】上一輪切分未通過 SplitterVerifier，本輪必須修正："]
            if repair_plan_struct:
                parts.append(
                    f"\n  repair_plan_struct={json.dumps(repair_plan_struct, ensure_ascii=False)}"
                )
            if previous_attempt_missed:
                parts.append(
                    f"\n  previous_attempt_missed={json.dumps(previous_attempt_missed, ensure_ascii=False)}"
                )
            if issue_chunk_ids:
                parts.append(
                    f"\n  issue_chunk_ids={json.dumps(issue_chunk_ids, ensure_ascii=False)}"
                )
            if verifier_reason:
                parts.append(f"\n  verifier_reason={verifier_reason}")
            retry_hint_section = "".join(parts)

        user_msg = (
            f"目標難度：{target_depth}\n\n"
            f"以下是教材的分段內容，請根據語義關係組合成學習階段：\n\n"
            f"{chunks_text}"
            f"{outline_section}"
            f"{must_cover_section}"
            f"{retry_hint_section}"
        )
        self._messages.append(LLMMessage(role=MessageRole.USER, content=user_msg))

        response = await self.llm.chat(self._messages, system_prompt=system)
        self._reset()
        result = await self._parse_or_repair_json(
            response.content,
            max_stages=max_stages,
            db_chunks=db_chunks,
            preserve_thin_stages=bool(required_outline or repair_plan_struct),
        )

        self._log_end(ctx, t0, {"stages_count": len(result.get("stages", []))})
        return result
