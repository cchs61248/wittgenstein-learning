"""Regression: multiple_choice option 必須帶 `id` key（全系統契約）。

背景：LLM 常把 option key 吐成 `option_id`，前端讀 `opt.id` 會全為 undefined →
所有選項同時 highlight（全選）+ 提交空答案。normalize_question_options 在 QG 產生與
DB 載回時把 key 收斂成 `id`。
"""
import unittest

from backend.utils.option_normalize import normalize_question_options


class TestNormalizeQuestionOptions(unittest.TestCase):
    def test_option_id_alias_becomes_id(self):
        q = {
            "answer_mode": "multiple_choice",
            "correct_option_id": "A",
            "options": [
                {"option_id": "A", "text": "對"},
                {"option_id": "B", "text": "錯"},
            ],
        }
        normalize_question_options(q)
        self.assertEqual([o["id"] for o in q["options"]], ["A", "B"])
        # 殘留別名移除，避免雙 key
        self.assertFalse(any("option_id" in o for o in q["options"]))

    def test_missing_id_falls_back_to_letters(self):
        q = {
            "answer_mode": "multiple_choice",
            "options": [{"text": "甲"}, {"text": "乙"}, {"text": "丙"}],
        }
        normalize_question_options(q)
        self.assertEqual([o["id"] for o in q["options"]], ["A", "B", "C"])

    def test_existing_id_preserved(self):
        q = {"options": [{"id": "X", "text": "a"}, {"id": "Y", "text": "b"}]}
        normalize_question_options(q)
        self.assertEqual([o["id"] for o in q["options"]], ["X", "Y"])

    def test_selected_only_one_matches(self):
        """還原前端行為：點一個選項只有它 match，不會全選。"""
        q = {"options": [{"option_id": "A"}, {"option_id": "B"}, {"option_id": "C"}]}
        normalize_question_options(q)
        selected = q["options"][0]["id"]
        self.assertEqual([o["id"] == selected for o in q["options"]], [True, False, False])

    def test_correct_option_id_coerced_to_str(self):
        q = {"correct_option_id": " A ", "options": [{"id": "A", "text": "a"}]}
        normalize_question_options(q)
        self.assertEqual(q["correct_option_id"], "A")

    def test_non_mc_or_empty_is_noop(self):
        q = {"answer_mode": "short_answer", "options": []}
        self.assertIs(normalize_question_options(q), q)
        self.assertEqual(normalize_question_options({}), {})


if __name__ == "__main__":
    unittest.main()
