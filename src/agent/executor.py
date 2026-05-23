"""Agent Executor — Handles task execution within agent sandboxes."""

import asyncio
import time
from enum import Enum
from threading import RLock
from typing import Any, Callable, Dict, Optional
from uuid import uuid4


class ExecutionState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = {
    ExecutionState.COMPLETED,
    ExecutionState.FAILED,
    ExecutionState.CANCELLED,
}
TERMINAL_HEARTBEAT_STATUSES = {state.value for state in TERMINAL_STATES}


class AgentExecutor:
    def __init__(self, max_concurrent: int = 5):
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._results: Dict[str, Any] = {}
        self._states: Dict[str, ExecutionState] = {}
        self._heartbeats: Dict[str, Dict[str, Any]] = {}
        self._lock = RLock()

    async def execute(
        self,
        agent_id: str,
        task: Dict[str, Any],
        handler: Callable,
        execution_id: Optional[str] = None,
    ) -> str:
        execution_id = execution_id or str(uuid4())
        self._set_state(execution_id, ExecutionState.PENDING)
        async with self._semaphore:
            task_obj = asyncio.create_task(
                self._run_execution(execution_id, agent_id, task, handler)
            )
            with self._lock:
                self._active_tasks[execution_id] = task_obj
                self._states[execution_id] = ExecutionState.RUNNING
            try:
                result = await task_obj
                self._record_terminal_result(
                    execution_id,
                    ExecutionState.COMPLETED,
                    result,
                )
            except asyncio.CancelledError:
                self._record_terminal_result(
                    execution_id,
                    ExecutionState.CANCELLED,
                    self._terminal_payload(
                        execution_id,
                        agent_id,
                        task,
                        ExecutionState.CANCELLED,
                        {"error": "execution cancelled"},
                    ),
                )
            except Exception as e:
                self._record_terminal_result(
                    execution_id,
                    ExecutionState.FAILED,
                    self._terminal_payload(
                        execution_id,
                        agent_id,
                        task,
                        ExecutionState.FAILED,
                        {"error": str(e)},
                    ),
                )
            finally:
                with self._lock:
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
            "state": ExecutionState.COMPLETED.value,
            "result": result,
            "duration": duration,
            "timestamp": time.time(),
        }

    def record_heartbeat(
        self,
        execution_id: str,
        worker_id: Optional[str] = None,
        status: str = "running",
        details: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> bool:
        with self._lock:
            state = self._states.get(execution_id)
            if state is None or state in TERMINAL_STATES:
                return False
            if execution_id not in self._active_tasks:
                return False
            if status.lower() in TERMINAL_HEARTBEAT_STATUSES:
                return False
            self._heartbeats[execution_id] = {
                "execution_id": execution_id,
                "worker_id": worker_id,
                "status": status,
                "details": dict(details or {}),
                "timestamp": (
                    timestamp if timestamp is not None else time.time()
                ),
            }
            return True

    def get_heartbeat(self, execution_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            heartbeat = self._heartbeats.get(execution_id)
            return dict(heartbeat) if heartbeat else None

    def get_state(self, execution_id: str) -> Optional[ExecutionState]:
        with self._lock:
            return self._states.get(execution_id)

    def is_active(self, execution_id: str) -> bool:
        with self._lock:
            return execution_id in self._active_tasks

    def get_result(self, execution_id: str) -> Optional[Any]:
        with self._lock:
            result = self._results.get(execution_id)
            return dict(result) if isinstance(result, dict) else result

    def cancel(self, execution_id: str) -> bool:
        with self._lock:
            task = self._active_tasks.get(execution_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def shutdown(self) -> None:
        with self._lock:
            tasks = list(self._active_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _set_state(self, execution_id: str, state: ExecutionState) -> None:
        with self._lock:
            self._states[execution_id] = state

    def _record_terminal_result(
        self,
        execution_id: str,
        state: ExecutionState,
        result: Dict[str, Any],
    ) -> bool:
        if state not in TERMINAL_STATES:
            raise ValueError("terminal result must use a terminal state")
        with self._lock:
            existing_state = self._states.get(execution_id)
            if existing_state in TERMINAL_STATES:
                return False
            stored = dict(result)
            stored["state"] = state.value
            self._states[execution_id] = state
            self._results[execution_id] = stored
            return True

    def _terminal_payload(
        self,
        execution_id: str,
        agent_id: str,
        task: Dict[str, Any],
        state: ExecutionState,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        record = {
            "execution_id": execution_id,
            "agent_id": agent_id,
            "task_id": task.get("id"),
            "state": state.value,
            "timestamp": time.time(),
        }
        record.update(payload)
        return record

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
