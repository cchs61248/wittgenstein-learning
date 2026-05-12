"""
補充測試：覆蓋 2026-05-06 新增的進度決策規則

場景：
- 高分有弱點 → 補強（不得直接 advance）
- 重教子章節 partial 掌握 → 補強
"""
import asyncio
from unittest.mock import MagicMock

from backend.agents.base_agent import AgentContext
from backend.agents.progress_manager import ProgressManagerAgent
from backend.utils.token_counter import TokenCounter


def make_agent() -> ProgressManagerAgent:
    return ProgressManagerAgent(MagicMock(), TokenCounter())


def ctx(payload: dict) -> AgentContext:
    defaults = {
        "pass_threshold": 0.75,
        "max_attempts": 3,
        "max_reteach": 2,
        "max_remediation": 2,
        "total_stages": 5,
        "current_stage_id": 1,
        "current_attempt": 1,
        "is_dynamic": False,
        "stage_kind": "main",
        "source_reteach_count": 0,
        "source_remediation_count": 0,
    }
    defaults.update(payload)
    return AgentContext(session_id="s", user_id="u", task_payload=defaults)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── 高分有弱點 → 補強 ──────────────────────────────────────────────────────────

def test_high_score_with_confused_concepts_remediates():
    """best_score ≥ 0.75 但仍有混淆概念 → remediate，不得直接 advance。"""
    agent = make_agent()
    result = run(agent.run(ctx({
        "evaluations": [
            {"score": 0.80, "confused_concepts": ["條件獨立"], "misconception_patterns": []},
            {"score": 0.76, "confused_concepts": ["貝氏定理方向"], "misconception_patterns": []},
        ],
    })))
    assert result["decision"] == "remediate", "高分但有弱點應補強，不應直接 advance"
    assert "條件獨立" in (result["remediation_focus"] or [])


def test_high_score_no_confused_advances():
    """best_score ≥ 0.75 且無混淆概念 → advance（確認不誤觸 remediate）。"""
    agent = make_agent()
    result = run(agent.run(ctx({
        "evaluations": [
            {"score": 0.90, "confused_concepts": [], "misconception_patterns": []},
        ],
    })))
    assert result["decision"] == "advance"


def test_high_score_borderline_with_confused_remediates():
    """best_score 剛好達門檻（0.75）且仍有混淆 → remediate，不應視為完全掌握。"""
    agent = make_agent()
    result = run(agent.run(ctx({
        "evaluations": [
            {"score": 0.75, "confused_concepts": ["邊界條件"], "misconception_patterns": []},
        ],
    })))
    assert result["decision"] == "remediate"


# ── 重教子章節 partial 掌握 → 補強 ────────────────────────────────────────────

def test_reteach_child_partial_mastery_remediates():
    """重教子章節中部分掌握（仍有混淆概念）→ 補強，不再重教。"""
    agent = make_agent()
    result = run(agent.run(ctx({
        "evaluations": [
            {"score": 0.65, "confused_concepts": ["語言遊戲"], "misconception_patterns": []},
            {"score": 0.70, "confused_concepts": [], "misconception_patterns": []},
        ],
        "stage_kind": "reteach",
        "is_dynamic": True,
        "source_reteach_count": 1,
        "source_remediation_count": 0,
        "current_attempt": 1,
    })))
    assert result["decision"] == "remediate", "重教後部分掌握應轉補強，不再重教"
    assert result["remediation_focus"] == ["語言遊戲"]


def test_reteach_child_complete_mastery_advances():
    """重教子章節完全掌握（best ≥ 0.75，無混淆）→ advance。"""
    agent = make_agent()
    result = run(agent.run(ctx({
        "evaluations": [
            {"score": 0.85, "confused_concepts": [], "misconception_patterns": []},
        ],
        "stage_kind": "reteach",
        "is_dynamic": True,
        "source_reteach_count": 1,
        "source_remediation_count": 0,
        "current_attempt": 1,
    })))
    assert result["decision"] == "advance"
