import json
import time
from typing import Any, AsyncGenerator
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils import extract_json


_INTENT_START_MARKER = "<<INTENT_JSON>>"
_INTENT_END_MARKER = "<<END_INTENT>>"


class TeacherAgent(BaseAgent):
    def _format_source_chunks(self, stage: dict[str, Any]) -> str:
        chunks = stage.get("source_chunks") or []
        if not isinstance(chunks, list) or not chunks:
            return "（無 source_chunks，可用內容僅限學習材料段落）"
        lines: list[str] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            chunk_id = str(chunk.get("chunk_id", "")).strip() or "unknown"
            quote = str(chunk.get("quote", "") or chunk.get("text", "")).strip()
            note = str(chunk.get("note", "")).strip()
            if not quote:
                continue
            lines.append(f"[{chunk_id}] {quote}" + (f"（{note}）" if note else ""))
        return "\n".join(lines) if lines else "（source_chunks 格式不完整）"

    def _format_allowed_evidence(self, allowed_chunks: list[dict]) -> str:
        if not allowed_chunks:
            return ""
        lines = []
        for c in allowed_chunks:
            chunk_id = c.get("chunk_id", "unknown")
            text = (c.get("text") or c.get("quote") or "").strip()
            if text:
                lines.append(f"[{chunk_id}] {text}")
        return "\n".join(lines)

    def _build_prompt_params(self, payload: dict) -> dict:
        """從 payload 組裝 teacher system prompt 的格式參數。"""
        user_profile_summary = payload.get("user_profile_summary", "尚無資料")
        adaptive_ctx = payload.get("adaptive_context") or {}
        learner_state = adaptive_ctx.get("learner_state", {})
        requirements = adaptive_ctx.get("next_lesson_requirements", {})

        mastery_map: dict = learner_state.get("mastery_map", {})
        misconceptions: list = learner_state.get("misconceptions", [])
        recent_qa: list = learner_state.get("recent_qa_summary", [])
        must_reinforce: list = requirements.get("must_reinforce", [])
        forbidden_future: list = requirements.get("forbidden_future_concepts", [])

        if mastery_map:
            mastery_summary = "、".join(f"{c}={v:.0%}" for c, v in mastery_map.items())
        else:
            mastery_summary = payload.get("weak_concepts", "無")

        if misconceptions:
            parts = [
                f"「{m['concept']}」：{m['pattern']}"
                for m in misconceptions[:3]
                if m.get("concept") and m.get("pattern")
            ]
            misconceptions_text = "；".join(parts) if parts else "無"
        else:
            misconceptions_text = "無"

        if recent_qa:
            qa_parts = [
                f"{r.get('question_text', '')[:25]}…（{r.get('score', 0):.0%}）"
                for r in recent_qa[-3:]
                if r.get("question_text")
            ]
            recent_qa_text = "；".join(qa_parts) if qa_parts else "無"
        else:
            recent_qa_text = "無"

        must_reinforce_text = "、".join(must_reinforce) if must_reinforce else "無"
        forbidden_future_text = "、".join(forbidden_future[:5]) if forbidden_future else "無"

        selection_reason = requirements.get("selection_reason") or {}
        if selection_reason:
            sr_reason = selection_reason.get("reason", "")
            sr_targets = "、".join(selection_reason.get("target_concepts", [])[:3])
            selection_reason_text = (
                f"{sr_reason}（重點概念：{sr_targets}）" if sr_targets else sr_reason
            ) or "無"
        else:
            selection_reason_text = "無"

        return {
            "user_profile_summary": user_profile_summary,
            "mastery_summary": mastery_summary,
            "misconceptions_text": misconceptions_text,
            "recent_qa_text": recent_qa_text,
            "must_reinforce_text": must_reinforce_text,
            "forbidden_future_text": forbidden_future_text,
            "selection_reason_text": selection_reason_text,
        }

    async def stream_explanation(
        self, ctx: AgentContext
    ) -> AsyncGenerator[str, None]:
        self._reset()
        payload = ctx.task_payload
        stage = payload["stage"]
        prev_stage_title: str | None = payload.get("prev_stage_title")

        self._log.info(
            "TeacherAgent stream_explanation START  session=%s  stage_id=%s  title=%s",
            ctx.session_id, stage.get("stage_id", "?"), stage.get("title", "")[:40],
        )
        t0 = time.perf_counter()

        prompt_params = self._build_prompt_params(payload)
        system = SYSTEM_PROMPTS["teacher"].format(**prompt_params)

        allowed_evidence = (payload.get("adaptive_context") or {}).get("allowed_evidence", [])
        evidence_text = self._format_allowed_evidence(allowed_evidence) or self._format_source_chunks(stage)

        prev_note = f"前一節點：「{prev_stage_title}」" if prev_stage_title else "本節是第一個節點"
        self._add_message(
            MessageRole.USER,
            f"節點 {stage.get('node_id', stage['stage_id'])}：{stage['title']}\n\n"
            f"{prev_note}\n\n"
            f"學習材料：\n{stage.get('content', '')}\n\n"
            f"關鍵概念：{', '.join(stage.get('key_concepts', []))}\n\n"
            f"source_chunks（請在敘述後標記 chunk_id）：\n{evidence_text}",
        )

        total_chars = 0
        async for chunk in self.llm.stream_chat(self._messages, system_prompt=system):
            total_chars += len(chunk)
            yield chunk
        self._reset()

        elapsed = time.perf_counter() - t0
        self._log.info(
            "TeacherAgent stream_explanation END  session=%s  stage_id=%s  "
            "chars=%d  elapsed=%.2fs",
            ctx.session_id, stage.get("stage_id", "?"), total_chars, elapsed,
        )

    async def stream_explanation_with_intent(
        self, ctx
    ) -> AsyncGenerator[str, None]:
        """
        包裝 stream_explanation：
        - 標記前的純文字 chunks 原樣 yield（前端正常顯示講解）
        - 偵測 _INTENT_START_MARKER 後切換到「累積但不 yield」模式
        - 收到 _INTENT_END_MARKER 後 parse JSON 存到 self.last_intent
        - 標記字串可能跨 chunk 切斷，用 buffer 處理邊界
        - 若 LLM 沒輸出標記區塊，emit 仍正常結束、self.last_intent = None
        """
        self.last_intent = None

        # 為了讓 ctx=None 也能跑（測試），偵測是否要 build messages
        if ctx is not None:
            payload = ctx.task_payload
            stage = payload["stage"]
            prev_stage_title: str | None = payload.get("prev_stage_title")
            prompt_params = self._build_prompt_params(payload)
            system = SYSTEM_PROMPTS["teacher"].format(**prompt_params)

            allowed_evidence = (payload.get("adaptive_context") or {}).get("allowed_evidence", [])
            evidence_text = self._format_allowed_evidence(allowed_evidence) or self._format_source_chunks(stage)
            prev_note = f"前一節點：「{prev_stage_title}」" if prev_stage_title else "本節是第一個節點"

            self._reset()
            self._add_message(
                MessageRole.USER,
                f"節點 {stage.get('node_id', stage['stage_id'])}：{stage['title']}\n\n"
                f"{prev_note}\n\n"
                f"學習材料：\n{stage.get('content', '')}\n\n"
                f"關鍵概念：{', '.join(stage.get('key_concepts', []))}\n\n"
                f"source_chunks（請在敘述後標記 chunk_id）：\n{evidence_text}",
            )
            messages_for_llm = self._messages
            system_for_llm = system
        else:
            # 測試模式：呼叫端已把 _messages 與 _system 直接設好
            messages_for_llm = self._messages
            system_for_llm = getattr(self, "_system", "")

        buffer = ""               # 跨 chunk 字元緩衝
        in_intent_block = False
        intent_buffer = ""

        async for chunk in self.llm.stream_chat(messages_for_llm, system_prompt=system_for_llm):
            buffer += chunk
            while True:
                if not in_intent_block:
                    idx = buffer.find(_INTENT_START_MARKER)
                    if idx == -1:
                        # 沒看到 START — 留住可能的「半個 START」邊界（最後 len(MARKER)-1 字元不送）
                        if len(buffer) >= len(_INTENT_START_MARKER):
                            safe = buffer[:-(len(_INTENT_START_MARKER) - 1)]
                        else:
                            safe = ""
                        if safe:
                            yield safe
                            buffer = buffer[len(safe):]
                        break
                    else:
                        if idx > 0:
                            yield buffer[:idx]
                        buffer = buffer[idx + len(_INTENT_START_MARKER):]
                        in_intent_block = True
                        continue
                else:
                    idx = buffer.find(_INTENT_END_MARKER)
                    if idx == -1:
                        # 累積進 intent_buffer，但留住 END_MARKER 邊界
                        safe_len = max(0, len(buffer) - (len(_INTENT_END_MARKER) - 1))
                        intent_buffer += buffer[:safe_len]
                        buffer = buffer[safe_len:]
                        break
                    else:
                        intent_buffer += buffer[:idx]
                        buffer = buffer[idx + len(_INTENT_END_MARKER):]
                        try:
                            self.last_intent = json.loads(intent_buffer.strip())
                        except Exception:
                            self._log.warning(
                                "TeacherAgent inline intent parse failed → fallback to extract_teaching_intent  "
                                "intent_buffer=%r",
                                intent_buffer[:300],
                                exc_info=True,
                            )
                            self.last_intent = None
                        in_intent_block = False
                        intent_buffer = ""

        # 串流結束：若還有未送的 buffer（不在 intent 區塊裡），全送出
        if not in_intent_block and buffer:
            yield buffer

        if ctx is not None:
            self._reset()

    async def extract_teaching_intent(
        self, explanation_text: str, stage: dict
    ) -> dict:
        """串流結束後，從講解全文中提取教學意圖（non-streaming，供 QuestionGeneratorAgent 使用）。"""
        self._reset()
        key_concepts = stage.get("key_concepts", [])
        self._log.info(
            "TeacherAgent extract_teaching_intent  stage_id=%s",
            stage.get("stage_id", "?"),
        )
        system = (
            "你是教學意圖分析器。從提供的講解文字中提取結構化的教學意圖。\n"
            "只輸出 JSON，不要任何其他文字：\n"
            '{\n'
            '  "reinforced_concepts": ["在講解中重點強調的概念"],\n'
            '  "analogies_used": ["使用的類比描述（一句話）"],\n'
            '  "repair_target": "若有針對特定錯誤修正，描述；若無則為 null",\n'
            '  "main_chunk_ids": ["講解中引用的主要 chunk_id，如 chunk_0001"]\n'
            "}"
        )
        self._add_message(
            MessageRole.USER,
            f"關鍵概念：{', '.join(key_concepts)}\n\n"
            f"講解全文：\n{explanation_text[:3000]}",
        )
        try:
            response = await self.llm.chat(self._messages, system_prompt=system)
            data = json.loads(extract_json(response.content))
            return {
                "reinforced_concepts": [str(c) for c in (data.get("reinforced_concepts") or []) if c],
                "analogies_used": [str(a) for a in (data.get("analogies_used") or []) if a],
                "repair_target": data.get("repair_target") or None,
                "main_chunk_ids": [str(c) for c in (data.get("main_chunk_ids") or []) if c],
            }
        except Exception as e:
            self._log.warning(
                "TeacherAgent extract_teaching_intent parse error → using key_concepts[:2] fallback  "
                "stage_id=%s  error=%s  explanation_head=%r",
                stage.get("stage_id", "?"), e, explanation_text[:200],
                exc_info=True,
            )
            return {
                "reinforced_concepts": key_concepts[:2],
                "analogies_used": [],
                "repair_target": None,
                "main_chunk_ids": [],
            }
        finally:
            self._reset()
