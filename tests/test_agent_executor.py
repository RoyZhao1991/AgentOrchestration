import asyncio

import pytest

from src.agent.executor import AgentExecutor


async def return_task_id(agent_id, task):
    return {"agent_id": agent_id, "task_id": task["id"]}


async def raise_failure(_agent_id, _task):
    raise RuntimeError("boom")


class TestAgentExecutor:
    def test_results_are_limited_by_max_results(self):
        executor = AgentExecutor(max_results=2)

        execution_ids = [
            asyncio.run(
                executor.execute(
                    "agent-1",
                    {"id": f"task-{index}"},
                    return_task_id,
                )
            )
            for index in range(3)
        ]

        assert executor.get_result(execution_ids[0]) is None
        second_result = executor.get_result(execution_ids[1])
        third_result = executor.get_result(execution_ids[2])
        assert second_result["result"]["task_id"] == "task-1"
        assert third_result["result"]["task_id"] == "task-2"
        assert list(executor._results.keys()) == execution_ids[1:]

    def test_result_ttl_expires_old_entries(self):
        executor = AgentExecutor(result_ttl_seconds=5)
        execution_id = asyncio.run(
            executor.execute("agent-1", {"id": "task-ttl"}, return_task_id)
        )

        executor._result_timestamps[execution_id] -= 10

        assert executor.get_result(execution_id) is None
        assert execution_id not in executor._result_timestamps

    def test_failed_results_are_bounded_too(self):
        executor = AgentExecutor(max_results=1)

        failed_id = asyncio.run(
            executor.execute("agent-1", {"id": "bad"}, raise_failure)
        )
        success_id = asyncio.run(
            executor.execute("agent-1", {"id": "good"}, return_task_id)
        )

        assert executor.get_result(failed_id) is None
        assert executor.get_result(success_id)["result"]["task_id"] == "good"

    @pytest.mark.parametrize("max_results", [0, -1, 1.5, "5", True])
    def test_max_results_must_be_positive_integer(self, max_results):
        with pytest.raises(
            ValueError,
            match="max_results must be a positive integer",
        ):
            AgentExecutor(max_results=max_results)

    @pytest.mark.parametrize("result_ttl_seconds", [0, -1, "5", True])
    def test_result_ttl_must_be_positive_when_set(self, result_ttl_seconds):
        with pytest.raises(
            ValueError,
            match="result_ttl_seconds must be positive",
        ):
            AgentExecutor(result_ttl_seconds=result_ttl_seconds)
