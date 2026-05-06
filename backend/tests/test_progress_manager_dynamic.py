from unittest.mock import MagicMock

import pytest

from backend.agents.base_agent import AgentContext
from backend.agents.progress_manager import ProgressManagerAgent
from backend.utils.token_counter import TokenCounter


def make_agent() -> ProgressManagerAgent:
    return ProgressManagerAgent(MagicMock(), TokenCounter())


def ctx(payload: dict) -> AgentContext:
    return AgentContext(session_id="s1", user_id="u1", task_payload=payload)


@pytest.mark.asyncio
async def test_remediation_child_partially_mastered_repeats_remediation_when_under_limit():
    agent = make_agent()
    result = await agent.run(ctx({
        "evaluations": [
            {"score": 0.72, "confused_concepts": ["家族相似性"], "misconception_patterns": []},
            {"score": 0.78, "confused_concepts": [], "misconception_patterns": []},
        ],
        "pass_threshold": 0.75,
        "current_stage_id": 11,
        "total_stages": 4,
        "current_attempt": 1,
        "is_dynamic": True,
        "stage_kind": "remediation",
        "source_stage_id": 3,
        "source_reteach_count": 0,
        "source_remediation_count": 1,
    }))

    assert result["decision"] == "remediate"
    assert result["remediation_focus"] == ["家族相似性"]


@pytest.mark.asyncio
async def test_reteach_child_unmastered_reteaches_again_when_under_limit():
    agent = make_agent()
    result = await agent.run(ctx({
        "evaluations": [
            {"score": 0.1, "confused_concepts": ["語言遊戲"], "misconception_patterns": []},
            {"score": 0.2, "confused_concepts": ["規則遵循"], "misconception_patterns": []},
        ],
        "pass_threshold": 0.75,
        "current_stage_id": 12,
        "total_stages": 4,
        "current_attempt": 1,
        "is_dynamic": True,
        "stage_kind": "reteach",
        "source_stage_id": 3,
        "source_reteach_count": 1,
        "source_remediation_count": 0,
    }))

    assert result["decision"] == "reteach"
    assert result["remediation_focus"] == ["語言遊戲", "規則遵循"]


@pytest.mark.asyncio
async def test_reteach_child_unmastered_turns_to_remediation_at_reteach_limit():
    agent = make_agent()
    result = await agent.run(ctx({
        "evaluations": [
            {"score": 0.1, "confused_concepts": ["語言遊戲"], "misconception_patterns": []},
            {"score": 0.2, "confused_concepts": ["規則遵循"], "misconception_patterns": []},
        ],
        "pass_threshold": 0.75,
        "current_stage_id": 13,
        "total_stages": 4,
        "current_attempt": 1,
        "is_dynamic": True,
        "stage_kind": "reteach",
        "source_stage_id": 3,
        "source_reteach_count": 2,
        "source_remediation_count": 0,
    }))

    assert result["decision"] == "remediate"
    assert "重教" in result["message"]


@pytest.mark.asyncio
async def test_remediation_child_advances_at_remediation_limit():
    agent = make_agent()
    result = await agent.run(ctx({
        "evaluations": [
            {"score": 0.5, "confused_concepts": ["語言遊戲"], "misconception_patterns": []},
            {"score": 0.55, "confused_concepts": ["語言遊戲"], "misconception_patterns": []},
        ],
        "pass_threshold": 0.75,
        "current_stage_id": 14,
        "total_stages": 4,
        "current_attempt": 1,
        "is_dynamic": True,
        "stage_kind": "remediation",
        "source_stage_id": 3,
        "source_reteach_count": 0,
        "source_remediation_count": 2,
    }))

    assert result["decision"] == "advance"
    assert result["next_stage_id"] is None
