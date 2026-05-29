"""P6: A vs B trade-off exemption in the shared parallel-options block.

Real case sess_lfue6ajjp: a CAP-theorem short article. splitter put CP+AP
into one ATM-case stage (correct — it's a trade-off), but splitter_verifier
(gemini-3-flash) reported aligned=false claiming a missing parallel option.
The exemption tells both splitter and verifier that同一決策的兩面 belong in
one stage.
"""
import unittest

from backend.utils.prompt_templates import SYSTEM_PROMPTS


class TestTradeoffExemptionInPrompts(unittest.TestCase):
    def test_splitter_has_tradeoff_exemption(self):
        p = SYSTEM_PROMPTS["content_splitter"]
        self.assertIn("取捨對比豁免", p)
        self.assertIn("同一決策的兩面", p)

    def test_verifier_has_tradeoff_exemption(self):
        """verifier shares _PARALLEL_OPTIONS_BLOCK so it must see the rule too."""
        p = SYSTEM_PROMPTS["splitter_verifier"]
        self.assertIn("取捨對比豁免", p)
        self.assertIn("verifier 不可判 false", p)

    def test_exemption_covers_title_and_kc(self):
        """The exemption must instruct: title covers both sides, kc not one-sided."""
        p = SYSTEM_PROMPTS["content_splitter"]
        self.assertIn("title 必須涵蓋兩面", p)
        self.assertIn("key_concepts 必須兩面都收", p)


if __name__ == "__main__":
    unittest.main()
