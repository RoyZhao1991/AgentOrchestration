import asyncio

from src.agent.executor import (
    AgentExecutor,
    ExecutionStatus,
    current_agent_id,
    current_execution_id,
    current_task_id,
)


def test_nested_execution_restores_outer_contextvars():
    executor = AgentExecutor(max_concurrent=2)
    seen = {}

    async def inner_handler(agent_id, task):
        seen["inner"] = (
            current_execution_id.get(),
            current_agent_id.get(),
            current_task_id.get(),
        )
        current_execution_id.set("mutated-inner-execution")
        current_agent_id.set("mutated-inner-agent")
        current_task_id.set("mutated-inner-task")
        return {"agent_id": agent_id, "task_id": task["id"]}

    async def outer_handler(agent_id, task):
        seen["outer_before"] = (
            current_execution_id.get(),
            current_agent_id.get(),
            current_task_id.get(),
        )
        nested_id = await executor.execute(
            "inner-agent",
            {"id": "inner-task", "execution_id": "inner-exec"},
            inner_handler,
        )
        seen["outer_after"] = (
            current_execution_id.get(),
            current_agent_id.get(),
            current_task_id.get(),
        )
        return {
            "nested_id": nested_id,
            "agent_id": agent_id,
            "task_id": task["id"],
        }

    async def run():
        return await executor.execute(
            "outer-agent",
            {"id": "outer-task", "execution_id": "outer-exec"},
            outer_handler,
        )

    execution_id = asyncio.run(run())

    assert execution_id == "outer-exec"
    assert seen["outer_before"] == ("outer-exec", "outer-agent", "outer-task")
    assert seen["inner"] == ("inner-exec", "inner-agent", "inner-task")
    assert seen["outer_after"] == seen["outer_before"]

    outer_result = executor.get_result("outer-exec")
    inner_result = executor.get_result("inner-exec")
    assert outer_result["status"] == ExecutionStatus.SUCCEEDED.value
    assert outer_result["result"]["nested_id"] == "inner-exec"
    assert inner_result["status"] == ExecutionStatus.SUCCEEDED.value


def test_retry_with_same_execution_id_does_not_duplicate_work():
    executor = AgentExecutor()
    calls = {"count": 0}

    async def handler(agent_id, task):
        calls["count"] += 1
        return {"agent_id": agent_id, "task_id": task["id"]}

    async def run():
        first = await executor.execute(
            "agent-a",
            {"id": "retry-task", "execution_id": "retry-exec"},
            handler,
        )
        second = await executor.execute(
            "agent-a",
            {"id": "retry-task", "execution_id": "retry-exec"},
            handler,
        )
        return first, second

    first_id, second_id = asyncio.run(run())

    assert first_id == "retry-exec"
    assert second_id == "retry-exec"
    assert calls["count"] == 1
    assert executor.get_result("retry-exec")["status"] == (
        ExecutionStatus.SUCCEEDED.value
    )


def test_concurrent_duplicate_execution_shares_terminal_result():
    executor = AgentExecutor(max_concurrent=2)
    calls = {"count": 0}

    async def handler(agent_id, task):
        calls["count"] += 1
        await asyncio.sleep(0.01)
        return {"agent_id": agent_id, "task_id": task["id"]}

    async def run():
        return await asyncio.gather(
            executor.execute(
                "agent-a",
                {"id": "shared-task", "execution_id": "shared-exec"},
                handler,
            ),
            executor.execute(
                "agent-a",
                {"id": "shared-task", "execution_id": "shared-exec"},
                handler,
            ),
        )

    execution_ids = asyncio.run(run())

    assert execution_ids == ["shared-exec", "shared-exec"]
    assert calls["count"] == 1
    assert executor.get_result("shared-exec")["status"] == (
        ExecutionStatus.SUCCEEDED.value
    )


def test_handler_failure_records_one_terminal_outcome():
    executor = AgentExecutor()
    calls = {"count": 0}

    async def handler(agent_id, task):
        calls["count"] += 1
        raise RuntimeError("boom")

    async def run():
        first = await executor.execute(
            "agent-a",
            {"id": "fail-task", "execution_id": "fail-exec"},
            handler,
        )
        second = await executor.execute(
            "agent-a",
            {"id": "fail-task", "execution_id": "fail-exec"},
            handler,
        )
        return first, second

    first_id, second_id = asyncio.run(run())
    result = executor.get_result("fail-exec")

    assert first_id == "fail-exec"
    assert second_id == "fail-exec"
    assert calls["count"] == 1
    assert result["status"] == ExecutionStatus.FAILED.value
    assert result["error"] == "boom"


def test_cancel_records_terminal_outcome_and_cleans_active_task():
    executor = AgentExecutor()

    async def handler(agent_id, task):
        await asyncio.sleep(60)
        return {"agent_id": agent_id, "task_id": task["id"]}

    async def run():
        execution_task = asyncio.create_task(
            executor.execute(
                "agent-a",
                {"id": "cancel-task", "execution_id": "cancel-exec"},
                handler,
            )
        )
        await asyncio.sleep(0)
        assert executor.cancel("cancel-exec") is True
        return await execution_task

    execution_id = asyncio.run(run())
    result = executor.get_result("cancel-exec")

    assert execution_id == "cancel-exec"
    assert result["status"] == ExecutionStatus.CANCELLED.value
    assert result["execution_id"] == "cancel-exec"
    assert executor.cancel("cancel-exec") is False
