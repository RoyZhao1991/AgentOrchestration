"""Agent Executor — Handles task execution within agent sandboxes."""

import asyncio
from contextlib import suppress
from contextvars import ContextVar, Token
from enum import Enum
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

current_execution_id: ContextVar[Optional[str]] = ContextVar(
    "ao_current_execution_id",
    default=None,
)
current_agent_id: ContextVar[Optional[str]] = ContextVar(
    "ao_current_agent_id",
    default=None,
)
current_task_id: ContextVar[Optional[str]] = ContextVar(
    "ao_current_task_id",
    default=None,
)


class ExecutionStatus(Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {
    ExecutionStatus.SUCCEEDED.value,
    ExecutionStatus.FAILED.value,
    ExecutionStatus.CANCELLED.value,
}


class AgentExecutor:
    def __init__(self, max_concurrent: int = 5):
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._results: Dict[str, Any] = {}

    async def execute(
        self,
        agent_id: str,
        task: Dict[str, Any],
        handler: Callable,
    ) -> str:
        execution_id = str(task.get("execution_id") or uuid4())
        task_id = task.get("id")

        async with self._lock:
            if self._is_terminal(execution_id):
                return execution_id

            task_obj = self._active_tasks.get(execution_id)
            if task_obj is None:
                self._results[execution_id] = self._build_result(
                    execution_id,
                    agent_id,
                    task_id,
                    ExecutionStatus.RUNNING.value,
                    started_at=time.time(),
                )
                task_obj = asyncio.create_task(
                    self._run_lifecycle(execution_id, agent_id, task, handler)
                )
                self._active_tasks[execution_id] = task_obj

        try:
            await asyncio.shield(task_obj)
        except asyncio.CancelledError:
            if task_obj.done() or task_obj.cancelling():
                with suppress(asyncio.CancelledError):
                    await task_obj
            if self._results.get(execution_id, {}).get("status") == (
                ExecutionStatus.CANCELLED.value
            ):
                return execution_id
            raise
        return execution_id

    async def _run_lifecycle(
        self,
        exec_id: str,
        agent_id: str,
        task: Dict,
        handler: Callable,
    ) -> None:
        started_at = time.time()
        task_id = task.get("id")
        try:
            async with self._semaphore:
                result = await self._run_execution(
                    exec_id,
                    agent_id,
                    task,
                    handler,
                )
        except asyncio.CancelledError:
            await self._record_terminal(
                exec_id,
                self._build_result(
                    exec_id,
                    agent_id,
                    task_id,
                    ExecutionStatus.CANCELLED.value,
                    started_at=started_at,
                    duration=time.time() - started_at,
                ),
            )
            raise
        except Exception as e:
            await self._record_terminal(
                exec_id,
                self._build_result(
                    exec_id,
                    agent_id,
                    task_id,
                    ExecutionStatus.FAILED.value,
                    started_at=started_at,
                    duration=time.time() - started_at,
                    error=str(e),
                ),
            )
        else:
            await self._record_terminal(exec_id, result)
        finally:
            async with self._lock:
                if self._active_tasks.get(exec_id) is asyncio.current_task():
                    self._active_tasks.pop(exec_id, None)

    async def _run_execution(
        self,
        exec_id: str,
        agent_id: str,
        task: Dict,
        handler: Callable,
    ) -> Any:
        start = time.time()
        tokens = self._enter_context(exec_id, agent_id, task.get("id"))
        try:
            result = await handler(agent_id, task)
            return self._build_result(
                exec_id,
                agent_id,
                task.get("id"),
                ExecutionStatus.SUCCEEDED.value,
                started_at=start,
                duration=time.time() - start,
                result=result,
            )
        finally:
            self._exit_context(tokens)

    def _enter_context(
        self,
        exec_id: str,
        agent_id: str,
        task_id: Optional[str],
    ) -> List[Tuple[ContextVar, Token]]:
        return [
            (current_execution_id, current_execution_id.set(exec_id)),
            (current_agent_id, current_agent_id.set(agent_id)),
            (current_task_id, current_task_id.set(task_id)),
        ]

    def _exit_context(self, tokens: List[Tuple[ContextVar, Token]]) -> None:
        for context_var, token in reversed(tokens):
            context_var.reset(token)

    def _build_result(
        self,
        exec_id: str,
        agent_id: str,
        task_id: Optional[str],
        status: str,
        started_at: float,
        duration: Optional[float] = None,
        result: Any = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "execution_id": exec_id,
            "agent_id": agent_id,
            "task_id": task_id,
            "status": status,
            "started_at": started_at,
            "timestamp": time.time(),
        }
        if duration is not None:
            payload["duration"] = duration
        if result is not None:
            payload["result"] = result
        if error is not None:
            payload["error"] = error
        return payload

    async def _record_terminal(
        self,
        exec_id: str,
        result: Dict[str, Any],
    ) -> None:
        async with self._lock:
            if self._is_terminal(exec_id):
                return
            self._results[exec_id] = result

    def _is_terminal(self, execution_id: str) -> bool:
        result = self._results.get(execution_id, {})
        return result.get("status") in TERMINAL_STATUSES

    def get_result(self, execution_id: str) -> Optional[Any]:
        return self._results.get(execution_id)

    def cancel(self, execution_id: str) -> bool:
        task = self._active_tasks.get(execution_id)
        if task and not task.done():
            task.cancel()
            current = self._results.get(execution_id, {})
            if not self._is_terminal(execution_id):
                started_at = current.get("started_at", time.time())
                self._results[execution_id] = self._build_result(
                    execution_id,
                    current.get("agent_id"),
                    current.get("task_id"),
                    ExecutionStatus.CANCELLED.value,
                    started_at=started_at,
                    duration=time.time() - started_at,
                )
            self._active_tasks.pop(execution_id, None)
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
