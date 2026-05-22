"""Agent Executor — Handles task execution within agent sandboxes."""

import asyncio
import time
from collections import OrderedDict
from numbers import Real
from typing import Any, Callable, Dict, Optional
from uuid import uuid4


class AgentExecutor:
    def __init__(
        self,
        max_concurrent: int = 5,
        max_results: int = 1000,
        result_ttl_seconds: Optional[float] = None,
    ):
        invalid_max_results = (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or max_results <= 0
        )
        if invalid_max_results:
            raise ValueError("max_results must be a positive integer")
        if (
            result_ttl_seconds is not None
            and (
                isinstance(result_ttl_seconds, bool)
                or not isinstance(result_ttl_seconds, Real)
                or result_ttl_seconds <= 0
            )
        ):
            raise ValueError("result_ttl_seconds must be positive")

        self.max_concurrent = max_concurrent
        self.max_results = max_results
        self.result_ttl_seconds = result_ttl_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._results: OrderedDict[str, Any] = OrderedDict()
        self._result_timestamps: Dict[str, float] = {}

    async def execute(
        self,
        agent_id: str,
        task: Dict[str, Any],
        handler: Callable,
    ) -> str:
        execution_id = str(uuid4())
        async with self._semaphore:
            task_obj = asyncio.create_task(
                self._run_execution(execution_id, agent_id, task, handler)
            )
            self._active_tasks[execution_id] = task_obj
            try:
                result = await task_obj
                self._store_result(execution_id, result)
            except Exception as e:
                self._store_result(
                    execution_id,
                    {"error": str(e), "timestamp": time.time()},
                )
            finally:
                self._active_tasks.pop(execution_id, None)
        return execution_id

    async def _run_execution(
        self,
        exec_id: str,
        agent_id: str,
        task: Dict,
        handler: Callable,
    ) -> Any:
        start = time.time()
        result = await handler(agent_id, task)
        duration = time.time() - start
        return {
            "execution_id": exec_id,
            "agent_id": agent_id,
            "task_id": task.get("id"),
            "result": result,
            "duration": duration,
            "timestamp": time.time(),
        }

    def get_result(self, execution_id: str) -> Optional[Any]:
        self._cleanup_results()
        return self._results.get(execution_id)

    def _store_result(self, execution_id: str, result: Any) -> None:
        now = time.time()
        self._cleanup_results(now=now)
        self._results[execution_id] = result
        self._result_timestamps[execution_id] = now
        self._trim_results()

    def _cleanup_results(self, now: Optional[float] = None) -> None:
        if self.result_ttl_seconds is None:
            return

        now = now if now is not None else time.time()
        expired_ids = [
            execution_id
            for execution_id, stored_at in self._result_timestamps.items()
            if now - stored_at > self.result_ttl_seconds
        ]
        for execution_id in expired_ids:
            self._results.pop(execution_id, None)
            self._result_timestamps.pop(execution_id, None)

    def _trim_results(self) -> None:
        while len(self._results) > self.max_results:
            execution_id, _ = self._results.popitem(last=False)
            self._result_timestamps.pop(execution_id, None)

    def cancel(self, execution_id: str) -> bool:
        task = self._active_tasks.get(execution_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def shutdown(self) -> None:
        for task in self._active_tasks.values():
            task.cancel()
        if self._active_tasks:
            await asyncio.gather(
                *self._active_tasks.values(),
                return_exceptions=True,
            )

# 2019-01-31T14:19:34 update

# 2019-02-14T17:08:55 update

# 2019-03-28T08:28:18 update

# 2019-10-22T14:08:13 update

# 2020-01-02T11:41:47 update

# 2020-05-27T15:54:00 update

# 2020-06-03T11:28:30 update

# 2020-06-30T11:26:45 update

# 2020-07-22T16:27:48 update

# 2020-10-26T10:21:42 update

# 2020-12-11T08:18:01 update

# 2021-01-19T19:48:39 update

# 2021-02-11T20:31:28 update

# 2021-03-03T17:36:43 update

# 2021-04-13T12:11:10 update

# 2021-10-01T12:48:15 update

# 2021-10-11T13:15:06 update

# 2022-03-04T17:29:10 update

# 2022-09-21T15:04:04 update

# 2022-09-26T11:39:01 update

# 2022-10-07T14:50:58 update

# 2022-10-31T11:14:41 update

# 2022-12-20T14:55:07 update

# 2023-03-06T20:32:35 update

# 2023-05-23T12:22:34 update

# 2023-06-02T12:50:51 update

# 2023-06-27T14:46:58 update

# 2023-09-21T08:31:49 update

# 2023-10-05T20:34:46 update

# 2023-10-10T19:50:50 update

# 2023-12-14T16:01:46 update

# 2024-01-08T09:32:16 update

# 2024-01-25T19:20:00 update

# 2024-04-15T17:45:27 update

# 2024-04-30T12:53:42 update

# 2024-08-20T11:11:27 update

# 2024-09-13T13:17:03 update

# 2024-11-01T12:39:45 update

# 2024-12-17T19:48:45 update

# 2025-01-10T20:46:40 update

# 2025-01-12T17:49:15 update

# 2025-01-24T17:40:56 update

# 2025-01-31T10:14:08 update

# 2025-03-07T08:51:21 update

# 2025-05-19T17:28:46 update

# 2025-07-04T17:58:43 update

# 2025-09-02T09:44:39 update

# 2025-09-22T20:20:24 update

# 2026-01-02T20:01:17 update

# 2026-03-17T08:56:59 update
