"""content_outline prompt sanity tests（方案 C L1）。"""
import unittest

from backend.utils.prompt_templates import SYSTEM_PROMPTS


class TestContentOutlinePromptSanity(unittest.TestCase):
    def test_prompt_exists(self):
        self.assertIn("content_outline", SYSTEM_PROMPTS)

    def test_prompt_has_named_cases_and_required_titles(self):
        prompt = SYSTEM_PROMPTS["content_outline"]
        self.assertIn("named_cases", prompt)
        self.assertIn("required_stage_titles", prompt)
        self.assertIn("QR Code Generator", prompt)

    def test_prompt_has_must_cover_chunks(self):
        prompt = SYSTEM_PROMPTS["content_outline"]
        self.assertIn("must_cover_chunks", prompt)


if __name__ == "__main__":
    unittest.main()
