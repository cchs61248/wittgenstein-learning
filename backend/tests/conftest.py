import asyncio

import pytest


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
