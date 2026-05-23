import asyncio
import os

import pytest

_LIVE_MARKERS = frozenset({"llm_live", "curriculum_live"})


def pytest_collection_modifyitems(config, items):
    """Skip live LLM tests unless RUN_LLM_TESTS=1 (even if -m filter is overridden)."""
    if os.getenv("RUN_LLM_TESTS") == "1":
        return
    reason = (
        "Live LLM tests require RUN_LLM_TESTS=1. "
        "Example: RUN_LLM_TESTS=1 pytest -m llm_live backend/tests/test_reducer_go_nogo_live.py"
    )
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if _LIVE_MARKERS.intersection(item.keywords):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def event_loop_for_sync_unittest_tests():
    """Existing unittest-style tests call asyncio.get_event_loop() directly."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield
    finally:
        loop.close()
        asyncio.set_event_loop(None)
