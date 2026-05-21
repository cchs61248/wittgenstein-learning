"""splitter_verifier prompt sanity tests（L1）。

對應 spec: docs/superpowers/specs/2026-05-21-splitter-verifier-agent-design.md § 4
"""
import unittest

from backend.utils.prompt_templates import SYSTEM_PROMPTS


class TestSplitterVerifierPromptSanity(unittest.TestCase):
    def test_prompt_exists(self):
        self.assertIn("splitter_verifier", SYSTEM_PROMPTS)

    def test_prompt_has_signal_words(self):
        """訊號詞清單（A 數量宣告 + B 列舉編號 + C 對比結構）。"""
        prompt = SYSTEM_PROMPTS["splitter_verifier"]
        self.assertIn("分為 N 種", prompt)
        self.assertIn("（一）/（二）/（三）", prompt)
        self.assertIn("方法 1/2/3", prompt)

    def test_prompt_has_mash_up_rule(self):
        """mash-up 判定規則（標題寫某方案、key_concepts 混進別方案）。"""
        prompt = SYSTEM_PROMPTS["splitter_verifier"]
        self.assertIn("mash-up", prompt)
        # mash-up 描述含「key_concepts 混進其他方案概念」相關字眼
        self.assertTrue(
            "key_concepts" in prompt and "混進" in prompt,
            "prompt 應描述 mash-up = title 與 key_concepts 不一致"
        )

    def test_prompt_has_example_b_bug_case(self):
        """範例 B：本次 sess_u055rzse5 bug case 寫進範例。"""
        prompt = SYSTEM_PROMPTS["splitter_verifier"]
        self.assertIn("借錢外掛（三）", prompt)
        self.assertIn("房屋貸款", prompt)
        self.assertIn("missing_options", prompt)

    def test_prompt_has_output_format_four_fields(self):
        """JSON 輸出格式 4 欄。"""
        prompt = SYSTEM_PROMPTS["splitter_verifier"]
        for field in ["aligned", "missing_options", "issue_chunk_ids", "reason"]:
            self.assertIn(field, prompt, f"output format 缺少 {field} 欄")

    def test_prompt_has_false_positive_guards(self):
        """判定要點：避免誤判（列舉非方案 / 一句帶過 / 多切不算）。"""
        prompt = SYSTEM_PROMPTS["splitter_verifier"]
        # 規則 1：不是所有列舉都是並列方案
        self.assertIn("不是所有列舉都是", prompt)
        # 規則 3：多切是 false negative
        self.assertIn("false negative", prompt)

    def test_prompt_has_repair_plan_fields(self):
        prompt = SYSTEM_PROMPTS["splitter_verifier"]
        self.assertIn("required_stage_titles", prompt)
        self.assertIn("missing_stage_specs", prompt)
        self.assertIn("forbidden_mixes", prompt)
        self.assertIn("repair_plan", prompt)

    def test_prompt_plan_b_parallel_course_cases(self):
        """方案 B：並列課程案例 + API mash-up 範例 E。"""
        prompt = SYSTEM_PROMPTS["splitter_verifier"]
        self.assertIn("並列課程案例", prompt)
        self.assertIn("範例 E", prompt)
        self.assertIn("Webhook 設計要點", prompt)
        self.assertIn("title 與 key_concepts 主題對齊", prompt)


if __name__ == "__main__":
    unittest.main()
