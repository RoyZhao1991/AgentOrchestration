import asyncio
import math

from src.agent.executor import AgentExecutor


async def json_handler(agent_id, task):
    return {
        "agent_id": agent_id,
        "task_id": task["id"],
        "payload": task["payload"],
    }


async def set_handler(_agent_id, _task):
    return {"not-json"}


async def nan_handler(_agent_id, _task):
    return math.nan


class TestAgentExecutor:
    def test_records_json_serializable_tool_result(self):
        executor = AgentExecutor()

        execution_id = asyncio.run(
            executor.execute(
                "agent-1",
                {"id": "task-1", "payload": {"ok": True}},
                json_handler,
            )
        )

        result = executor.get_result(execution_id)
        assert result["result"]["payload"] == {"ok": True}
        assert "error" not in result

    def test_non_json_tool_result_records_terminal_failure(self):
        executor = AgentExecutor()

        execution_id = asyncio.run(
            executor.execute("agent-1", {"id": "task-2"}, set_handler)
        )

        result = executor.get_result(execution_id)
        assert "Tool result is not JSON serializable" in result["error"]
        assert execution_id in executor._terminal_executions
        assert execution_id not in executor._active_tasks

    def test_non_finite_tool_result_records_terminal_failure(self):
        executor = AgentExecutor()

        execution_id = asyncio.run(
            executor.execute("agent-1", {"id": "task-3"}, nan_handler)
        )

        result = executor.get_result(execution_id)
        assert "Tool result is not JSON serializable" in result["error"]
        assert execution_id in executor._terminal_executions

    def test_duplicate_retry_does_not_overwrite_terminal_outcome(self):
        executor = AgentExecutor()
        execution_id = "retry-exec-1"

        recorded = executor._record_terminal_result(
            execution_id,
            {"result": "first"},
        )
        assert recorded
        assert not executor._record_terminal_result(
            execution_id,
            {"error": "retry overwrite"},
        )

        assert executor.get_result(execution_id) == {"result": "first"}
