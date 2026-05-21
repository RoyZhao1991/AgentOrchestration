import asyncio
import time

import pytest

from src.sdk.decorators import task


def test_task_decorator_supports_sync_function_return_values():
    @task(name="sync_lookup", timeout=1)
    def sync_lookup(agent_id, payload):
        return {
            "agent_id": agent_id,
            "value": payload["value"] * 2,
        }

    result = asyncio.run(sync_lookup("agent-1", {"value": 21}))

    assert result == {"agent_id": "agent-1", "value": 42}
    assert sync_lookup.__task_config__ == {
        "name": "sync_lookup",
        "retries": 0,
        "timeout": 1,
    }


def test_task_decorator_preserves_async_function_behavior():
    @task(timeout=1)
    async def async_lookup(value):
        await asyncio.sleep(0)
        return value + 1

    assert asyncio.run(async_lookup(41)) == 42


def test_task_decorator_times_out_sync_handlers():
    @task(name="slow_sync", timeout=0.01)
    def slow_sync_handler():
        time.sleep(0.05)
        return "late"

    with pytest.raises(
        TimeoutError,
        match="Task slow_sync timed out after 0.01s",
    ):
        asyncio.run(slow_sync_handler())
