import asyncio

from src.agent.executor import AgentExecutor, ExecutionState


def test_heartbeat_does_not_revive_completed_execution():
    async def scenario():
        executor = AgentExecutor()
        started = asyncio.Event()

        async def handler(agent_id, task):
            started.set()
            await asyncio.sleep(0)
            return {"ok": True}

        run = asyncio.create_task(
            executor.execute(
                "agent-1",
                {"id": "task-1"},
                handler,
                execution_id="run-1",
            )
        )
        await started.wait()
        assert executor.record_heartbeat("run-1", worker_id="worker-1")
        first_heartbeat = executor.get_heartbeat("run-1")

        execution_id = await run

        assert execution_id == "run-1"
        assert executor.get_state("run-1") is ExecutionState.COMPLETED
        assert not executor.record_heartbeat(
            "run-1",
            worker_id="worker-1",
            status="running",
            details={"attempt": 2},
        )
        assert executor.get_heartbeat("run-1") == first_heartbeat
        assert executor.get_result("run-1")["state"] == "completed"

    asyncio.run(scenario())


def test_failed_execution_keeps_first_terminal_result():
    async def scenario():
        executor = AgentExecutor()

        async def handler(agent_id, task):
            raise RuntimeError("worker failed")

        execution_id = await executor.execute(
            "agent-1",
            {"id": "task-2"},
            handler,
            execution_id="run-2",
        )

        assert execution_id == "run-2"
        assert executor.get_state("run-2") is ExecutionState.FAILED
        first_result = executor.get_result("run-2")
        assert first_result["error"] == "worker failed"
        assert not executor.record_heartbeat("run-2", status="running")
        assert executor.get_result("run-2") == first_result

    asyncio.run(scenario())


def test_cancel_records_terminal_outcome_and_clears_active_work():
    async def scenario():
        executor = AgentExecutor()
        started = asyncio.Event()

        async def handler(agent_id, task):
            started.set()
            await asyncio.sleep(60)

        run = asyncio.create_task(
            executor.execute(
                "agent-1",
                {"id": "task-3"},
                handler,
                execution_id="run-3",
            )
        )
        await started.wait()

        assert executor.record_heartbeat("run-3", worker_id="worker-1")
        first_heartbeat = executor.get_heartbeat("run-3")
        assert not executor.record_heartbeat("run-3", status="completed")
        assert executor.get_heartbeat("run-3") == first_heartbeat
        assert executor.cancel("run-3")
        assert await run == "run-3"

        assert executor.get_state("run-3") is ExecutionState.CANCELLED
        assert not executor.is_active("run-3")
        assert executor.get_result("run-3")["state"] == "cancelled"
        assert not executor.record_heartbeat("run-3", status="running")

    asyncio.run(scenario())
