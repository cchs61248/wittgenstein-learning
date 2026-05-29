"""Phase 1 refinement: fold interior orphan chunks into the neighbouring stage.

sess_vtfl3q4il（一致性雜湊）: chunk_0008 被 splitter 漏抓，夾在 stage [5,6,7] 與下一個
stage [9] 之間，孤兒救援把它變成一個空泛的「章節總結與補充內容」filler stage，切斷敘事。
中間孤兒應折進前一個內容 stage，而不是新建 filler。
"""
import unittest

from backend.utils.small_curriculum import fold_interior_orphan_chunks


def _src(n: int) -> list[dict]:
    return [
        {"chunk_id": f"chunk_{i:04d}", "order_index": i, "text": f"段落{i}", "source_id": "s"}
        for i in range(n)
    ]


def _stage(node: str, orders: list[int]) -> dict:
    return {
        "stage_id": int(node.split(".")[1]),
        "node_id": node,
        "title": f"節點 {node}",
        "key_concepts": ["概念"],
        "source_chunk_ids": [f"chunk_{o:04d}" for o in orders],
    }


class TestFoldInteriorOrphan(unittest.TestCase):
    def test_interior_orphan_folds_into_preceding_stage(self):
        src = _src(11)  # chunks 0..10
        stages = [
            _stage("1.1", [0, 1, 2]),
            _stage("1.2", [3, 4, 5, 6, 7]),
            _stage("1.3", [9, 10]),
        ]  # chunk_0008 是唯一孤兒，interior（1.3 有 chunk 排在它後面）
        out = fold_interior_orphan_chunks(stages, src)
        self.assertEqual(len(out), 3, "不應新增 filler stage")
        s12 = next(s for s in out if s["node_id"] == "1.2")
        self.assertEqual(
            s12["source_chunk_ids"],
            ["chunk_0003", "chunk_0004", "chunk_0005", "chunk_0006", "chunk_0007", "chunk_0008"],
        )

    def test_orphan_within_stage_span_folds_into_that_stage(self):
        # 真實情況：merge_singleton 先把薄 stage [9] 折進前一個 → 1.2=[5,6,7,9]，
        # 此時 chunk_0008 落在 1.2 的 span [5..9] 之內，應折回 1.2，而不是 max≤8 的 1.1。
        src = _src(11)  # 0..10
        stages = [
            _stage("1.1", [0, 1, 2, 3, 4]),
            _stage("1.2", [5, 6, 7, 9]),
            _stage("1.3", [10]),
        ]  # chunk_0008 是唯一孤兒，落在 1.2 的 [5..9] 區間內
        out = fold_interior_orphan_chunks(stages, src)
        s12 = next(s for s in out if s["node_id"] == "1.2")
        self.assertEqual(
            s12["source_chunk_ids"],
            ["chunk_0005", "chunk_0006", "chunk_0007", "chunk_0008", "chunk_0009"],
        )
        s11 = next(s for s in out if s["node_id"] == "1.1")
        self.assertNotIn("chunk_0008", s11["source_chunk_ids"])

    def test_trailing_orphan_left_untouched(self):
        src = _src(11)  # 0..10
        stages = [
            _stage("1.1", [0, 1, 2, 3, 4]),
            _stage("1.2", [5, 6, 7, 8]),
        ]  # chunk_0009 / chunk_0010 是 trailing 孤兒（沒有 stage 排在它們之後）
        out = fold_interior_orphan_chunks(stages, src)
        covered = {c for s in out for c in s["source_chunk_ids"]}
        self.assertNotIn("chunk_0009", covered)
        self.assertNotIn("chunk_0010", covered)
        self.assertEqual(len(out), 2)

    def test_leading_orphan_folds_into_following_stage(self):
        src = _src(6)  # 0..5
        stages = [
            _stage("1.1", [1, 2]),
            _stage("1.2", [3, 4, 5]),
        ]  # chunk_0000 孤兒，無前置內容 stage → 折進後一個
        out = fold_interior_orphan_chunks(stages, src)
        s11 = next(s for s in out if s["node_id"] == "1.1")
        self.assertIn("chunk_0000", s11["source_chunk_ids"])
        self.assertEqual(len(out), 2)

    def test_intro_stage_not_used_as_fold_target(self):
        src = _src(7)  # 0..6
        stages = [
            {"stage_id": 1, "node_id": "1.1", "title": "導論與總覽",
             "key_concepts": ["k"], "source_chunk_ids": ["chunk_0000", "chunk_0001"]},
            _stage("1.2", [2, 3]),
            _stage("1.3", [5, 6]),
        ]  # chunk_0004 孤兒，前置 max≤4 的非 intro 內容 stage = 1.2
        out = fold_interior_orphan_chunks(stages, src)
        s12 = next(s for s in out if s["node_id"] == "1.2")
        self.assertIn("chunk_0004", s12["source_chunk_ids"])
        s11 = next(s for s in out if s["node_id"] == "1.1")
        self.assertNotIn("chunk_0004", s11["source_chunk_ids"])

    def test_no_orphans_returns_unchanged(self):
        src = _src(4)
        stages = [_stage("1.1", [0, 1]), _stage("1.2", [2, 3])]
        out = fold_interior_orphan_chunks(stages, src)
        self.assertEqual(out, stages)


if __name__ == "__main__":
    unittest.main()
