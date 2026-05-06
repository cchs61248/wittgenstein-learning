import asyncio
from unittest.mock import MagicMock

from backend.orchestrator.learning_orchestrator import LearningOrchestrator


def make_orchestrator() -> LearningOrchestrator:
    return LearningOrchestrator(MagicMock())


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_insert_reteach_stage_preserves_source_stage(monkeypatch):
    stored = {}

    async def store_stages(session_id, stages):
        stored["stages"] = stages

    async def upsert_stage_progress(**kwargs):
        stored["progress"] = kwargs

    monkeypatch.setattr(
        "backend.orchestrator.learning_orchestrator.session_memory.store_stages",
        store_stages,
    )
    monkeypatch.setattr(
        "backend.orchestrator.learning_orchestrator.session_memory.upsert_stage_progress",
        upsert_stage_progress,
    )

    orchestrator = make_orchestrator()
    stages = [{
        "stage_id": 3,
        "node_id": "3",
        "title": "原章",
        "content": "原始內容",
        "key_concepts": ["語言遊戲"],
        "source_chunks": [{"chunk_id": "chunk_0001", "quote": "原文"}],
    }]

    updated, idx = run(orchestrator._insert_reteach_stage("s1", stages, 0, ["語言遊戲"]))

    assert idx == 1
    assert updated[0]["content"] == "原始內容"
    assert updated[1]["kind"] == "reteach"
    assert updated[1]["source_stage_id"] == 3
    assert "重教" in updated[1]["title"]
    assert stored["progress"]["understanding_notes"]["kind"] == "reteach"


def test_count_dynamic_children_by_source_stage():
    orchestrator = make_orchestrator()
    stages = [
        {"stage_id": 3, "kind": "main"},
        {"stage_id": 4, "kind": "reteach", "source_stage_id": 3},
        {"stage_id": 5, "kind": "remediation", "source_stage_id": 3},
        {"stage_id": 6, "kind": "remediation", "source_stage_id": 3},
        {"stage_id": 7, "kind": "reteach", "source_stage_id": 2},
    ]

    assert orchestrator._count_child_stages(stages, 3, "reteach") == 1
    assert orchestrator._count_child_stages(stages, 3, "remediation") == 2
