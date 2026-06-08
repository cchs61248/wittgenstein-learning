"""longterm_memory.get_concept_canonical_pool() regression tests。

設計目的：給 ConceptCanonicalizeAgent 提供「同教材歷史概念名 + 排序資訊」。
按 source_signature 隔離（migration 017 延續行為）、按 total_exposures DESC 排序。

對應 spec: docs/superpowers/specs/2026-05-21-canonicalize-agent-design.md § 5
"""
import os
import unittest

from backend.memory import longterm_memory
from backend.db.database import init_db, close_db, get_db


async def _insert_mastery(
    user_id: str, concept_name: str,
    mastery_score: float = 0.5,
    total_exposures: int = 1,
    source_signature: str | None = None,
):
    db = await get_db()
    await db.execute(
        """INSERT INTO concept_mastery
           (user_id, concept_name, mastery_score, total_exposures, source_signature, last_tested)
           VALUES ($1, $2, $3, $4, $5, NOW())""",
        user_id, concept_name, mastery_score, total_exposures, source_signature,
    )


async def _clear_user(user_id: str):
    db = await get_db()
    await db.execute("DELETE FROM concept_mastery WHERE user_id = $1", user_id)


class TestGetConceptCanonicalPool(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db(os.environ["DATABASE_URL"], reset=True)
        db = await get_db()
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES ($1, $2, $3)",
            "u_canon_pool_test", "u@test", "x",
        )
        await _clear_user("u_canon_pool_test")

    async def asyncTearDown(self):
        await close_db()

    async def test_basic_returns_required_fields(self):
        """回傳 list[dict] 含 concept_name / total_exposures / last_tested 三欄。"""
        await _insert_mastery("u_canon_pool_test", "巴菲特神話",
                              total_exposures=3, source_signature="sig_A")
        result = await longterm_memory.get_concept_canonical_pool(
            user_id="u_canon_pool_test", source_signature="sig_A",
        )
        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertIn("concept_name", row)
        self.assertIn("total_exposures", row)
        self.assertIn("last_tested", row)
        self.assertEqual(row["concept_name"], "巴菲特神話")
        self.assertEqual(row["total_exposures"], 3)

    async def test_source_isolation_excludes_other_signatures(self):
        """跨 source_signature 隔離（migration 017 延續行為）。"""
        await _insert_mastery("u_canon_pool_test", "A_concept",
                              total_exposures=1, source_signature="sig_A")
        await _insert_mastery("u_canon_pool_test", "B_concept",
                              total_exposures=1, source_signature="sig_B")
        result = await longterm_memory.get_concept_canonical_pool(
            user_id="u_canon_pool_test", source_signature="sig_A",
        )
        names = [r["concept_name"] for r in result]
        self.assertIn("A_concept", names)
        self.assertNotIn("B_concept", names)

    async def test_source_isolation_excludes_null_signature(self):
        """NULL signature（migration 017 前的舊 record）也排除。"""
        await _insert_mastery("u_canon_pool_test", "legacy_concept",
                              total_exposures=1, source_signature=None)
        await _insert_mastery("u_canon_pool_test", "new_concept",
                              total_exposures=1, source_signature="sig_A")
        result = await longterm_memory.get_concept_canonical_pool(
            user_id="u_canon_pool_test", source_signature="sig_A",
        )
        names = [r["concept_name"] for r in result]
        self.assertNotIn("legacy_concept", names)
        self.assertIn("new_concept", names)

    async def test_orders_by_exposures_desc(self):
        """排序：total_exposures DESC, last_tested DESC。"""
        await _insert_mastery("u_canon_pool_test", "low_exp",
                              total_exposures=1, source_signature="sig_A")
        await _insert_mastery("u_canon_pool_test", "mid_exp",
                              total_exposures=5, source_signature="sig_A")
        await _insert_mastery("u_canon_pool_test", "high_exp",
                              total_exposures=10, source_signature="sig_A")
        result = await longterm_memory.get_concept_canonical_pool(
            user_id="u_canon_pool_test", source_signature="sig_A",
        )
        names = [r["concept_name"] for r in result]
        self.assertEqual(names, ["high_exp", "mid_exp", "low_exp"])

    async def test_limit_truncates(self):
        """limit=80 截前 80 筆（按既有排序）。"""
        for i in range(100):
            await _insert_mastery(
                "u_canon_pool_test", f"concept_{i:03d}",
                total_exposures=100 - i,
                source_signature="sig_A",
            )
        result = await longterm_memory.get_concept_canonical_pool(
            user_id="u_canon_pool_test", source_signature="sig_A", limit=80,
        )
        self.assertEqual(len(result), 80)
        self.assertEqual(result[0]["concept_name"], "concept_000")
        self.assertEqual(result[79]["concept_name"], "concept_079")

    async def test_empty_pool_returns_empty_list(self):
        """無 record 時、回傳 []。"""
        result = await longterm_memory.get_concept_canonical_pool(
            user_id="u_canon_pool_test", source_signature="sig_A",
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
