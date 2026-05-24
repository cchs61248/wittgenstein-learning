"""Mock tests for outline A/B tooling (no live LLM)."""
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from backend.tools.live_outline_ab_compare import AbRow


class TestOutlineAbCompare(unittest.TestCase):
    def test_ab_row_fields(self):
        row = AbRow(
            spec_id="api_design",
            label="API Design",
            force_outline=False,
            session_id="sess_x",
            chunk_count=4,
            stage_count=3,
            curriculum_llm_calls=2,
            missing_named_cases=0,
            global_aligned=True,
        )
        self.assertFalse(row.force_outline)
        self.assertEqual(row.curriculum_llm_calls, 2)


class TestOutlineAbPipelineMock(unittest.IsolatedAsyncioTestCase):
    async def test_force_outline_calls_both_branches(self):
        from backend.tools import live_outline_ab_compare as mod
        from backend.tools.golden_curriculum_sources import GoldenSource

        calls: list[bool] = []

        async def fake_run(spec_id, path, label, *, force_outline):
            calls.append(force_outline)
            return AbRow(
                spec_id=spec_id,
                label=label,
                force_outline=force_outline,
                session_id="s",
                chunk_count=1,
                stage_count=1,
                curriculum_llm_calls=1,
                missing_named_cases=0,
                global_aligned=True,
            )

        fake_spec = GoldenSource(
            id="api_design",
            archetype="x",
            label="API",
            candidate_paths=("/tmp/x",),
            full_v2=False,
        )
        with patch.object(mod, "_run_one", side_effect=fake_run), patch(
            "backend.tools.live_outline_ab_compare.available_sources",
            return_value=[(fake_spec, Path("/tmp/x"))],
        ):
            await mod.main_async(["api_design"])

        self.assertEqual(calls, [False, True])


if __name__ == "__main__":
    unittest.main()
