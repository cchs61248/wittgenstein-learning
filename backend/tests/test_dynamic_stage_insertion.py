import asyncio
from types import SimpleNamespace
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


def test_remediation_transition_marks_child_stage_in_progress_before_emit(monkeypatch):
    statuses: dict[int, str] = {}
    emitted: list[dict] = []
    current_stage_updates: list[int] = []

    async def fake_upsert_stage_progress(**kwargs):
        statuses[int(kwargs["stage_id"])] = kwargs["status"]

    async def fake_get_stage_statuses(session_id):
        return statuses

    async def fake_noop(*args, **kwargs):
        return None

    async def fake_weak_concepts(user_id):
        return "無"

    async def fake_mastery_map(user_id, concepts):
        return {}

    async def fake_update_current_stage(session_id, stage_id):
        current_stage_updates.append(stage_id)

    async def fake_run_stage(*args, **kwargs):
        return None

    async def fake_emit(msg):
        emitted.append(msg)

    monkeypatch.setattr(
        "backend.orchestrator.learning_orchestrator.session_memory.upsert_stage_progress",
        fake_upsert_stage_progress,
    )
    monkeypatch.setattr(
        "backend.orchestrator.learning_orchestrator.session_memory.get_stage_statuses",
        fake_get_stage_statuses,
    )
    monkeypatch.setattr(
        "backend.orchestrator.learning_orchestrator.session_memory.store_stages",
        fake_noop,
    )
    monkeypatch.setattr(
        "backend.orchestrator.learning_orchestrator.session_memory.insert_decision_record",
        fake_noop,
    )
    monkeypatch.setattr(
        "backend.orchestrator.learning_orchestrator.session_memory.update_current_stage",
        fake_update_current_stage,
    )
    monkeypatch.setattr(
        "backend.orchestrator.learning_orchestrator.longterm_memory.get_weak_concepts",
        fake_weak_concepts,
    )
    monkeypatch.setattr(
        "backend.orchestrator.learning_orchestrator.longterm_memory.get_concept_mastery_map",
        fake_mastery_map,
    )

    orchestrator = make_orchestrator()

    async def fake_progress_run(ctx):
        return {
            "decision": "remediate",
            "message": "需要補強",
            "next_stage_id": None,
            "best_score": 0.4,
            "remediation_focus": ["概念A"],
            "high_severity_misconceptions": [],
            "repeated_patterns_detected": False,
        }

    orchestrator.progress.run = fake_progress_run
    orchestrator.run_stage = fake_run_stage

    stages = [
        {
            "stage_id": 1,
            "node_id": "1",
            "title": "原章",
            "content": "原始內容",
            "key_concepts": ["概念A"],
            "source_chunks": [{"chunk_id": "chunk_0001", "quote": "原文"}],
        },
        {
            "stage_id": 2,
            "node_id": "2",
            "title": "下一章",
            "content": "下一章內容",
            "key_concepts": ["概念B"],
            "source_chunks": [],
        },
    ]
    wm = SimpleNamespace(
        stage_evaluations=[{"score": 0.4}],
        current_attempt=1,
        question_mode="multiple_choice",
        stage_turns=[],
        stages=stages,
    )

    run(orchestrator._make_progress_decision("s1", "u1", stages, stages[0], 0, wm, fake_emit))

    session_started = next(msg for msg in emitted if msg["type"] == "session_started")
    child_stage_id = max(stage["stage_id"] for stage in session_started["payload"]["stages"])
    assert session_started["payload"]["stage_statuses"][str(child_stage_id)] == "in_progress"
    assert current_stage_updates == [child_stage_id]
