import asyncio
from unittest.mock import MagicMock


from backend.agents.base_agent import AgentContext
from backend.agents.progress_manager import ProgressManagerAgent
from backend.utils.token_counter import TokenCounter


def make_agent() -> ProgressManagerAgent:
    return ProgressManagerAgent(MagicMock(), TokenCounter())


def ctx(payload: dict) -> AgentContext:
    return AgentContext(session_id="s1", user_id="u1", task_payload=payload)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_remediation_child_partially_mastered_repeats_remediation_when_under_limit():
    agent = make_agent()
    result = run(agent.run(ctx({
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
    })))

    assert result["decision"] == "remediate"
    assert result["remediation_focus"] == ["家族相似性"]


def test_reteach_child_unmastered_reteaches_again_when_under_limit():
    agent = make_agent()
    result = run(agent.run(ctx({
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
    })))

    assert result["decision"] == "reteach"
    assert result["remediation_focus"] == ["語言遊戲", "規則遵循"]


def test_reteach_child_unmastered_turns_to_remediation_at_reteach_limit():
    agent = make_agent()
    result = run(agent.run(ctx({
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
    })))

    assert result["decision"] == "remediate"
    assert "重教" in result["message"]


def test_remediation_child_advances_at_remediation_limit():
    agent = make_agent()
    result = run(agent.run(ctx({
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
    })))

    assert result["decision"] == "advance"
    assert result["next_stage_id"] is None


def test_main_stage_with_source_stage_id_and_zero_scores_reteaches():
    agent = make_agent()
    result = run(agent.run(ctx({
        "evaluations": [
            {"score": 0.0, "confused_concepts": ["全表掃描"], "misconception_patterns": []},
            {
                "score": 0.0,
                "confused_concepts": ["倒排索引方向性"],
                "misconception_patterns": [
                    {
                        "concept": "倒排索引方向性",
                        "pattern": "完全未嘗試作答，無法判斷是否有任何基礎概念",
                        "severity": "high",
                    }
                ],
            },
            {"score": 0.0, "confused_concepts": ["文字分析"], "misconception_patterns": []},
        ],
        "pass_threshold": 0.75,
        "current_stage_id": 1,
        "total_stages": 8,
        "current_attempt": 1,
        "is_dynamic": False,
        "stage_kind": "main",
        "source_stage_id": 1,
        "source_reteach_count": 0,
        "source_remediation_count": 0,
    })))

    assert result["decision"] == "reteach"
    assert result["best_score"] == 0.0


def test_dynamic_main_stage_with_source_stage_id_and_zero_scores_reteaches():
    agent = make_agent()
    result = run(agent.run(ctx({
        "evaluations": [
            {
                "score": 0.0,
                "confused_concepts": ["語言遊戲"],
                "misconception_patterns": [
                    {
                        "concept": "語言遊戲",
                        "pattern": "完全無法連結例子與核心概念",
                        "severity": "high",
                    }
                ],
            },
            {"score": 0.0, "confused_concepts": ["規則遵循"], "misconception_patterns": []},
        ],
        "pass_threshold": 0.75,
        "current_stage_id": 7,
        "total_stages": 8,
        "current_attempt": 1,
        "is_dynamic": True,
        "stage_kind": "main",
        "source_stage_id": 7,
        "source_reteach_count": 0,
        "source_remediation_count": 0,
    })))

    assert result["decision"] == "reteach"
    assert result["best_score"] == 0.0


def test_main_stage_all_zero_scores_first_attempt_retries():
    """首次嘗試全錯（無 high_severity）→ retry，先給一次補救機會。"""
    agent = make_agent()
    result = run(agent.run(ctx({
        "evaluations": [
            {"score": 0.0, "confused_concepts": ["同步處理"], "misconception_patterns": []},
            {"score": 0.0, "confused_concepts": ["Job ID 回傳"], "misconception_patterns": []},
            {"score": 0.0, "confused_concepts": ["Job Queue"], "misconception_patterns": []},
            {"score": 0.0, "confused_concepts": ["Worker Pool"], "misconception_patterns": []},
            {"score": 0.0, "confused_concepts": ["獨立擴展能力"], "misconception_patterns": []},
        ],
        "pass_threshold": 0.75,
        "current_stage_id": 1,
        "total_stages": 9,
        "current_attempt": 1,
        "is_dynamic": False,
        "stage_kind": "main",
        "source_stage_id": 1,
        "source_reteach_count": 0,
        "source_remediation_count": 0,
    })))

    assert result["decision"] == "retry", "首次全錯應給 retry 機會，不立即 reteach"
    assert result["best_score"] == 0.0


def test_main_stage_all_zero_scores_second_attempt_reteaches():
    """第二次仍全錯（無 high_severity）→ reteach，確認無法靠自己翻轉。"""
    agent = make_agent()
    result = run(agent.run(ctx({
        "evaluations": [
            {"score": 0.0, "confused_concepts": ["同步處理"], "misconception_patterns": []},
            {"score": 0.0, "confused_concepts": ["Job Queue"], "misconception_patterns": []},
        ],
        "pass_threshold": 0.75,
        "current_stage_id": 1,
        "total_stages": 9,
        "current_attempt": 2,
        "is_dynamic": False,
        "stage_kind": "main",
        "source_stage_id": 1,
        "source_reteach_count": 0,
        "source_remediation_count": 0,
    })))

    assert result["decision"] == "reteach", "第二次全錯應觸發 reteach"


def test_main_stage_partial_near_threshold_retries():
    agent = make_agent()
    result = run(agent.run(ctx({
        "evaluations": [
            {"score": 0.55, "confused_concepts": ["同步處理"], "misconception_patterns": []},
            {"score": 0.62, "confused_concepts": ["timeout 限制"], "misconception_patterns": []},
        ],
        "pass_threshold": 0.75,
        "current_stage_id": 2,
        "total_stages": 9,
        "current_attempt": 1,
        "is_dynamic": False,
        "stage_kind": "main",
        "source_stage_id": 2,
        "source_reteach_count": 0,
        "source_remediation_count": 0,
    })))

    assert result["decision"] == "retry"


def test_main_stage_partial_with_clear_gaps_remediates_after_retry_limit():
    agent = make_agent()
    result = run(agent.run(ctx({
        "evaluations": [
            {"score": 0.58, "confused_concepts": ["Job Queue"], "misconception_patterns": []},
            {"score": 0.6, "confused_concepts": ["Worker Pool"], "misconception_patterns": []},
        ],
        "pass_threshold": 0.75,
        "current_stage_id": 2,
        "total_stages": 9,
        "current_attempt": 3,
        "max_attempts": 3,
        "is_dynamic": False,
        "stage_kind": "main",
        "source_stage_id": 2,
        "source_reteach_count": 0,
        "source_remediation_count": 0,
    })))

    assert result["decision"] == "remediate"
