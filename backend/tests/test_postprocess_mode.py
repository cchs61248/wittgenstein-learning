"""Phase 1: mode-aware postprocessing — choose_postprocess_mode.

依 n_sources 與 same_material 決定後處理模式：
- 單 source（含 same_material 無論真假）→ preservation，不合併。
- 多 source 且 same_material is True → coordination，只協調不合併。
- 多 source 且 same_material 非 True（False / None legacy）→ 受控合併。
"""
import unittest

from backend.utils.small_curriculum import choose_postprocess_mode


class TestChoosePostprocessMode(unittest.TestCase):
    def test_single_source_same_material_true(self):
        self.assertEqual(
            choose_postprocess_mode(1, True), "single_source_finalize_only"
        )

    def test_single_source_same_material_false_still_preserves(self):
        # 單 source 自動視為同教材，same_material 旗標不影響。
        self.assertEqual(
            choose_postprocess_mode(1, False), "single_source_finalize_only"
        )

    def test_multi_source_same_material_true_coordinates(self):
        self.assertEqual(
            choose_postprocess_mode(2, True), "same_material_coordinate_only"
        )

    def test_multi_source_same_material_false_merges(self):
        self.assertEqual(
            choose_postprocess_mode(2, False),
            "cross_material_merge_and_coordinate",
        )

    def test_multi_source_same_material_none_legacy_merges(self):
        # 18.1 backward-compat：多 source 但旗標缺失 → 保守視為不同教材。
        self.assertEqual(
            choose_postprocess_mode(3, None),
            "cross_material_merge_and_coordinate",
        )


if __name__ == "__main__":
    unittest.main()
