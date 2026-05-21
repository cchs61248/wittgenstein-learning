"""concept_canonicalize prompt sanity tests（L1）。

對應 spec: docs/superpowers/specs/2026-05-21-canonicalize-agent-design.md § 4
"""
import unittest

from backend.utils.prompt_templates import SYSTEM_PROMPTS


class TestCanonicalizePromptSanity(unittest.TestCase):
    def test_prompt_exists(self):
        self.assertIn("concept_canonicalize", SYSTEM_PROMPTS)

    def test_prompt_has_three_decisions(self):
        prompt = SYSTEM_PROMPTS["concept_canonicalize"]
        self.assertIn('"mapped"', prompt)
        self.assertIn('"new"', prompt)
        self.assertIn('"unsure"', prompt)

    def test_prompt_has_hierarchy_rule(self):
        """規則 2 抽象層次必須匹配（最關鍵的避免誤判規則）。"""
        prompt = SYSTEM_PROMPTS["concept_canonicalize"]
        self.assertIn("抽象層次必須匹配", prompt)
        self.assertIn("股票質押", prompt)
        self.assertIn("元大證金質押", prompt)

    def test_prompt_has_total_exposures_priority(self):
        """規則 5 多歷史名衝突優先選 total_exposures 高者。"""
        prompt = SYSTEM_PROMPTS["concept_canonicalize"]
        self.assertIn("優先選 total_exposures", prompt)

    def test_prompt_has_example_d_unsure(self):
        """範例 D：醫師年薪天花板 vs 醫師執照的保障 → unsure（不誤映射）。"""
        prompt = SYSTEM_PROMPTS["concept_canonicalize"]
        self.assertIn("範例 D", prompt)
        self.assertIn("醫師年薪天花板", prompt)
        self.assertIn("醫師執照的保障", prompt)
        self.assertIn("unsure", prompt)

    def test_prompt_has_output_format_constraint(self):
        """強約束：mappings 長度必須 = new_concepts 長度。"""
        prompt = SYSTEM_PROMPTS["concept_canonicalize"]
        self.assertIn("mappings", prompt)
        self.assertIn("每個", prompt)


if __name__ == "__main__":
    unittest.main()
