import asyncio

import pytest

from src.agent.executor import AgentExecutor


@pytest.mark.parametrize("max_concurrent", [0, -1, True, "5"])
def test_executor_rejects_invalid_max_concurrent(max_concurrent):
    with pytest.raises(ValueError, match="positive integer"):
        AgentExecutor(max_concurrent=max_concurrent)


def test_executor_accepts_positive_max_concurrent():
    executor = AgentExecutor(max_concurrent=2)

    async def handler(agent_id, task):
        return {
            "agent_id": agent_id,
            "task_id": task["id"],
        }

    execution_id = asyncio.run(
        executor.execute(
            "agent-1",
            {"id": "job_one"},
            handler,
        )
    )

    result = executor.get_result(execution_id)
    assert executor.max_concurrent == 2
    assert result["agent_id"] == "agent-1"
    assert result["task_id"] == "job_one"
    assert result["result"] == {
        "agent_id": "agent-1",
        "task_id": "job_one",
    }
