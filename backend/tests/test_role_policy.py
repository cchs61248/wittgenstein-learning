"""role_policy 單元測試（不需 DB）。"""
import unittest

from backend.auth.role_policy import validate_provider_for_role


class TestValidateProviderForRole(unittest.TestCase):
    def test_admin_no_restriction(self):
        self.assertIsNone(validate_provider_for_role("admin", "claude", "claude-sonnet-4-6"))
        self.assertIsNone(validate_provider_for_role("admin", "openai", "gpt-5.4"))

    def test_user_no_restriction(self):
        self.assertIsNone(validate_provider_for_role("user", "claude", None))

    def test_editor_monica_allowed_model(self):
        self.assertIsNone(
            validate_provider_for_role("editor", "monica", "claude-4.6-sonnet")
        )
        self.assertIsNone(
            validate_provider_for_role("editor", "monica", "gemini-3-flash")
        )

    def test_editor_monica_no_model_ok(self):
        self.assertIsNone(validate_provider_for_role("editor", "monica", None))

    def test_editor_rejects_non_monica_provider(self):
        err = validate_provider_for_role("editor", "claude", "claude-4.6-sonnet")
        self.assertIsNotNone(err)
        self.assertIn("Monica", err)

    def test_editor_rejects_disallowed_model(self):
        err = validate_provider_for_role("editor", "monica", "gpt-5.5")
        self.assertIsNotNone(err)
        self.assertIn("Claude 4.6 Sonnet", err)


if __name__ == "__main__":
    unittest.main()
