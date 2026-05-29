import os
import pytest
from unittest.mock import patch

from backend.utils.small_curriculum import (
    duplicate_title_threshold,
    merge_duplicate_topic_stages,
)


def test_default_when_env_unset():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("STAGE_TITLE_MERGE_THRESHOLD", None)
        assert duplicate_title_threshold() == 0.85


@pytest.mark.parametrize("raw,expected", [
    ("0.70", 0.70),
    ("0.95", 0.95),
    ("1.0", 1.0),
])
def test_env_value_parsed(raw, expected):
    with patch.dict(os.environ, {"STAGE_TITLE_MERGE_THRESHOLD": raw}, clear=False):
        assert duplicate_title_threshold() == expected


@pytest.mark.parametrize("raw", ["", "abc", "-0.1", "0", "1.5"])
def test_invalid_env_falls_back_to_default(raw):
    with patch.dict(os.environ, {"STAGE_TITLE_MERGE_THRESHOLD": raw}, clear=False):
        assert duplicate_title_threshold() == 0.85


def test_merge_uses_env_threshold():
    stages = [
        {"title": "投資理財的基本原則", "source_chunk_ids": ["c1"]},
        {"title": "投資理財的核心原則", "source_chunk_ids": ["c2"]},
    ]
    with patch.dict(os.environ, {"STAGE_TITLE_MERGE_THRESHOLD": "0.99"}, clear=False):
        merged = merge_duplicate_topic_stages(stages)
        assert len(merged) == 2  # 0.99 太嚴格，不合
    with patch.dict(os.environ, {"STAGE_TITLE_MERGE_THRESHOLD": "0.50"}, clear=False):
        merged = merge_duplicate_topic_stages(stages)
        assert len(merged) == 1  # 0.50 很鬆，會合
