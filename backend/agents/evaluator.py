import json
from typing import Any
from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS


class EvaluatorAgent(BaseAgent):
    def _format_options(self, question: dict[str, Any]) -> tuple[str, str]:
        options = question.get("options") or []
        if not isinstance(options, list) or not options:
            return "（非選擇題或無選項）", "（無）"

        option_lines: list[str] = []
        option_map: dict[str, str] = {}
        for opt in options:
            if not isinstance(opt, dict):
                continue
            opt_id = str(opt.get("id", "")).strip()
            opt_text = str(opt.get("text", "")).strip()
            if not opt_id:
                continue
            option_map[opt_id] = opt_text
            option_lines.append(f"{opt_id}. {opt_text}")

        correct_id = str(question.get("correct_option_id", "")).strip()
        correct_text = option_map.get(correct_id, "")
        correct_line = f"{correct_id}. {correct_text}" if correct_id else "（未提供）"
        return "\n".join(option_lines) if option_lines else "（選項格式不完整）", correct_line

    def _resolve_student_answer(self, question: dict[str, Any], user_answer: str) -> str:
        options = question.get("options") or []
        if not isinstance(options, list) or not options:
            return user_answer
        answer_key = str(user_answer).strip()
        for opt in options:
            if not isinstance(opt, dict):
                continue
            if str(opt.get("id", "")).strip() == answer_key:
                return f"{answer_key}. {str(opt.get('text', '')).strip()}"
        return user_answer

    def _format_source_chunks(self, source_chunks: list[dict]) -> str:
        if not source_chunks:
            return "（無 source_chunks）"
        lines: list[str] = []
        for chunk in source_chunks:
            if not isinstance(chunk, dict):
                continue
            chunk_id = str(chunk.get("chunk_id", "")).strip() or "unknown"
            quote = str(chunk.get("quote", "")).strip()
            if quote:
                lines.append(f"[{chunk_id}] {quote}")
        return "\n".join(lines) if lines else "（source_chunks 格式不完整）"

    def _find_option_text(self, question: dict[str, Any], option_id: str) -> str:
        for opt in (question.get("options") or []):
            if isinstance(opt, dict) and str(opt.get("id", "")).strip() == option_id:
                return str(opt.get("text", "")).strip()
        return ""

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        question = payload["question"]
        user_answer: str = payload["user_answer"]
        compressed_history: list[dict] = payload.get("compressed_history", [])
        source_chunks: list[dict] = payload.get("source_chunks", [])

        # 選擇題特殊處理
        if question.get("answer_mode") == "multiple_choice":
            correct_id = str(question.get("correct_option_id", "")).strip()
            selected_id = str(user_answer).strip()
            if correct_id and selected_id == correct_id:
                # 選對：直接回傳 1.0，不呼叫 LLM
                correct_text = self._find_option_text(question, correct_id)
                label = f"{correct_id}. {correct_text}" if correct_text else correct_id
                return {
                    "score": 1.0,
                    "understood_concepts": question.get("key_concepts_tested", []),
                    "confused_concepts": [],
                    "feedback": f"✅ 答對了！**{label}** 是正確選項。\n\n你對這個概念的掌握很好，繼續保持！",
                    "needs_clarification": False,
                    "clarification_question": None,
                }
            # 選錯：交由 LLM 依相近程度給 0.0–0.6 分
            return await self._score_mc_wrong(question, user_answer, compressed_history, source_chunks)

        # 問答題：原有評分流程
        history_text = ""
        if compressed_history:
            lines = [f"Q: {t['q']}\nA: {t['a']}" for t in compressed_history[-3:]]
            history_text = "\n\n過去問答記錄（最近3輪）：\n" + "\n---\n".join(lines)
        options_text, correct_option_text = self._format_options(question)
        resolved_student_answer = self._resolve_student_answer(question, user_answer)

        self._add_message(
            MessageRole.USER,
            f"問題：{question['text']}\n"
            f"問題類型：{question.get('type', 'understand')}\n"
            f"作答模式：{question.get('answer_mode', 'short_answer')}\n"
            f"選項列表：\n{options_text}\n"
            f"正確選項（評分參考，不可直接洩漏）：{correct_option_text}\n"
            f"題目證據 chunk IDs：{', '.join(question.get('evidence_chunk_ids', []))}\n"
            f"要測試的概念：{', '.join(question.get('key_concepts_tested', []))}\n"
            f"評分參考要點（不公開）：{', '.join(question.get('expected_answer_hints', []))}\n"
            f"教材來源（評分只能依此判斷）：\n{self._format_source_chunks(source_chunks)}\n"
            f"\n學生回答：{resolved_student_answer}"
            f"{history_text}",
        )

        response = await self.llm.chat(self._messages, system_prompt=SYSTEM_PROMPTS["evaluator"])
        self._reset()

        raw_json = response.content.strip()
        if raw_json.startswith("```"):
            raw_json = raw_json.split("```")[1]
            if raw_json.startswith("json"):
                raw_json = raw_json[4:]
        data = json.loads(raw_json.strip())
        if isinstance(data.get("feedback"), str):
            data["feedback"] = data["feedback"].replace("\\n", "\n")
        return data

    async def _score_mc_wrong(
        self,
        question: dict[str, Any],
        user_answer: str,
        compressed_history: list[dict],
        source_chunks: list[dict],
    ) -> dict[str, Any]:
        self._reset()
        options_text, correct_option_text = self._format_options(question)
        resolved_student_answer = self._resolve_student_answer(question, user_answer)

        history_text = ""
        if compressed_history:
            lines = [f"Q: {t['q']}\nA: {t['a']}" for t in compressed_history[-3:]]
            history_text = "\n\n過去問答記錄（最近3輪）：\n" + "\n---\n".join(lines)

        self._add_message(
            MessageRole.USER,
            f"[選擇題 — 學生答錯]\n"
            f"問題：{question['text']}\n"
            f"問題類型：{question.get('type', 'understand')}\n"
            f"選項列表：\n{options_text}\n"
            f"學生選擇：{resolved_student_answer}\n"
            f"正確選項（評分參考，不可直接告知學生）：{correct_option_text}\n"
            f"要測試的概念：{', '.join(question.get('key_concepts_tested', []))}\n"
            f"教材來源（評分只能依此判斷）：\n{self._format_source_chunks(source_chunks)}\n"
            f"{history_text}\n\n"
            f"評分規則（此題為選擇題且學生已答錯）：\n"
            f"1. score 必須在 0.0～0.6 之間（答案錯誤，上限 0.6）\n"
            f"2. 依學生選項與正確選項的概念相近程度給分：\n"
            f"   - 方向正確但細節有誤（例如相近的概念混淆）→ 0.4～0.6\n"
            f"   - 僅部分相關（例如抓到一個關鍵詞但邏輯錯誤）→ 0.2～0.4\n"
            f"   - 概念完全無關或相反 → 0.0～0.2\n"
            f"3. 回饋需解釋所選選項哪裡不正確，並引導學生思考正確方向（不可直接說出正確選項）",
        )

        response = await self.llm.chat(self._messages, system_prompt=SYSTEM_PROMPTS["evaluator"])
        self._reset()

        raw_json = response.content.strip()
        if raw_json.startswith("```"):
            raw_json = raw_json.split("```")[1]
            if raw_json.startswith("json"):
                raw_json = raw_json[4:]
        data = json.loads(raw_json.strip())
        if isinstance(data.get("feedback"), str):
            data["feedback"] = data["feedback"].replace("\\n", "\n")
        # 安全夾緊：確保答錯分數不超過 0.6
        if isinstance(data.get("score"), (int, float)):
            data["score"] = min(float(data["score"]), 0.6)
        return data
