"""Golden curriculum sources + prompt archetype regression."""
import unittest

from backend.tools.golden_curriculum_sources import GOLDEN_SOURCES, available_sources


class TestGoldenSources(unittest.TestCase):
    def test_golden_ids_unique(self):
        ids = [s.id for s in GOLDEN_SOURCES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_archetypes_documented(self):
        allowed = {
            "tech_handbook",
            "case_series",
            "framework_narrative",
            "parallel_lessons",
            "listicle_rules",
        }
        for spec in GOLDEN_SOURCES:
            self.assertIn(spec.archetype, allowed, spec.id)


class TestNarrativeArchetypePrompt(unittest.TestCase):
    def test_drift_verifier_has_example_i_narrative(self):
        from backend.utils.prompt_templates import SYSTEM_PROMPTS

        prompt = SYSTEM_PROMPTS["drift_verifier"]
        self.assertIn("敘述型", prompt)
        self.assertIn("範例 I（", prompt)
        i_idx = prompt.find("範例 I（")
        section = prompt[i_idx : i_idx + 1200]
        self.assertIn("aligned=true", section)
        self.assertIn("大聲朗讀", section)

    def test_teacher_rule8_narrative_exemption(self):
        from backend.utils.prompt_templates import SYSTEM_PROMPTS

        prompt = SYSTEM_PROMPTS["teacher"]
        self.assertIn("敘述型 / 方法論型教材", prompt)
        self.assertIn("大聲朗讀", prompt)


class TestGoldenChunkProbeIfPresent(unittest.TestCase):
    def test_available_sources_probe(self):
        from backend.tools.live_small_file_curriculum_test import probe_source_chunks

        for spec, path in available_sources():
            chunks, probe = probe_source_chunks(path)
            self.assertGreaterEqual(
                probe.chunk_count,
                spec.min_chunks,
                f"{spec.id}: chunks={probe.chunk_count}",
            )
            if spec.min_section_titles:
                self.assertGreaterEqual(
                    probe.section_title_count,
                    spec.min_section_titles,
                    f"{spec.id}: titles={probe.section_title_count}",
                )
            self.assertEqual(
                probe.toc_chunk_count,
                0,
                f"{spec.id}: toc chunks should be 0",
            )


if __name__ == "__main__":
    unittest.main()
