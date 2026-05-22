"""Orchestration Engine — Core execution and coordination logic."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from src.agent import AgentRegistry, AgentStatus
from src.orchestrator.scheduler import TaskScheduler

logger = logging.getLogger(__name__)


class OrchestrationEngine:
    _ALLOWED_LIFECYCLE_TRANSITIONS = {
        None: {"pending", "queued", "running"},
        "pending": {"queued", "running", "failed", "cancelled"},
        "queued": {"running", "failed", "cancelled"},
        "running": {"paused", "completed", "failed", "stopped", "cancelled"},
        "paused": {"running", "stopped", "cancelled"},
        "completed": set(),
        "failed": set(),
        "stopped": set(),
        "terminated": set(),
        "cancelled": set(),
    }

    def __init__(self, max_workers: int = 10, agent_timeout: int = 300):
        self.registry = AgentRegistry()
        self.scheduler = TaskScheduler()
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.agent_timeout = agent_timeout
        self._running = False
        self._event_states: Dict[str, Dict[str, Any]] = {}
        self._event_audit: List[Dict[str, Any]] = []
        self._event_metrics: Dict[str, int] = {
            "event_intake.accepted": 0,
            "event_intake.rejected": 0,
        }
        self._hooks: Dict[str, List[Callable]] = {
            "pre_execute": [],
            "post_execute": [],
            "on_error": [],
            "on_complete": [],
        }

    def register_hook(self, event: str, callback: Callable) -> None:
        if event in self._hooks:
            self._hooks[event].append(callback)

    def ingest_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Validate shared event-bus events before committing state."""
        run_id = event.get("run_id")
        tenant_id = event.get("tenant_id")
        lifecycle = event.get("lifecycle") or event.get("status")

        if not run_id:
            return self._reject_event(None, None, "missing_run_id")
        if not tenant_id:
            return self._reject_event(run_id, None, "missing_tenant")
        if not lifecycle:
            return self._reject_event(run_id, None, "missing_lifecycle")

        lifecycle = str(lifecycle).lower()
        attempt = self._coerce_non_negative_int(event.get("attempt", 0))
        revision = self._coerce_non_negative_int(event.get("revision", 0))
        if attempt is None:
            return self._reject_event(run_id, None, "invalid_attempt")
        if revision is None:
            return self._reject_event(run_id, None, "invalid_revision")

        current = self._event_states.get(run_id)
        if current and current["tenant_id"] != tenant_id:
            return self._reject_event(run_id, current, "tenant_mismatch")

        if current:
            current_attempt = current["attempt"]
            current_revision = current["revision"]
            if attempt < current_attempt:
                return self._reject_event(run_id, current, "stale_attempt")
            if attempt == current_attempt and revision <= current_revision:
                return self._reject_event(run_id, current, "stale_revision")
            if not self._can_transition(current["lifecycle"], lifecycle):
                return self._reject_event(
                    run_id,
                    current,
                    "invalid_lifecycle_transition",
                )
        elif not self._can_transition(None, lifecycle):
            return self._reject_event(
                run_id,
                None,
                "invalid_initial_lifecycle",
            )

        state = {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "agent_id": event.get("agent_id"),
            "lifecycle": lifecycle,
            "attempt": attempt,
            "revision": revision,
        }
        self._event_states[run_id] = state
        self._record_event_decision(run_id, "accepted", lifecycle)
        logger.info("Accepted orchestrator event run_id=%s", run_id)
        return {
            "accepted": True,
            "reason": "accepted",
            "run_id": run_id,
            "lifecycle": lifecycle,
        }

    def event_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        state = self._event_states.get(run_id)
        return dict(state) if state else None

    @property
    def event_audit(self) -> List[Dict[str, Any]]:
        return [dict(record) for record in self._event_audit]

    @property
    def event_metrics(self) -> Dict[str, int]:
        return dict(self._event_metrics)

    def _reject_event(
        self,
        run_id: Optional[str],
        current: Optional[Dict[str, Any]],
        reason: str,
    ) -> Dict[str, Any]:
        lifecycle = current["lifecycle"] if current else None
        self._record_event_decision(run_id, reason, lifecycle)
        logger.warning(
            "Rejected orchestrator event run_id=%s reason=%s",
            run_id,
            reason,
        )
        return {
            "accepted": False,
            "reason": reason,
            "run_id": run_id,
            "current_lifecycle": lifecycle,
        }

    def _record_event_decision(
        self,
        run_id: Optional[str],
        reason: str,
        lifecycle: Optional[str],
    ) -> None:
        metric = (
            "event_intake.accepted"
            if reason == "accepted"
            else "event_intake.rejected"
        )
        self._event_metrics[metric] += 1
        if reason != "accepted":
            key = f"event_intake.rejected.{reason}"
            self._event_metrics[key] = self._event_metrics.get(key, 0) + 1
        self._event_audit.append(
            {
                "run_id": run_id,
                "decision": reason,
                "lifecycle": lifecycle,
            }
        )

    def _can_transition(
        self,
        current: Optional[str],
        lifecycle: str,
    ) -> bool:
        allowed = self._ALLOWED_LIFECYCLE_TRANSITIONS.get(current, set())
        return lifecycle == current or lifecycle in allowed

    def _coerce_non_negative_int(self, value: Any) -> Optional[int]:
        try:
            coerced = int(value)
        except (TypeError, ValueError):
            return None
        return coerced if coerced >= 0 else None

    async def start(self) -> None:
        self._running = True
        logger.info("Orchestration engine started")
        while self._running:
            task = await self.scheduler.dequeue()
            if task:
                asyncio.create_task(self._execute_task(task))
            await asyncio.sleep(0.1)

    def stop(self) -> None:
        self._running = False
        logger.info("Orchestration engine stopped")

    async def _execute_task(self, task: Dict[str, Any]) -> None:
        task_id = task["id"]
        agent_id = task["target_agent"]
        logger.info(f"Executing task {task_id} on agent {agent_id}")

        for hook in self._hooks["pre_execute"]:
            await hook(task)

        try:
            agent = self.registry.get(agent_id)
            if not agent:
                raise ValueError(f"Agent {agent_id} not found")

            self.registry.update_status(agent_id, AgentStatus.RUNNING)
            result = await asyncio.wait_for(
                self._run_agent_task(agent, task),
                timeout=self.agent_timeout,
            )
            self.registry.update_status(agent_id, AgentStatus.PAUSED)

            for hook in self._hooks["post_execute"]:
                await hook(task, result)

            logger.info(f"Task {task_id} completed successfully")

        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")
            for hook in self._hooks["on_error"]:
                await hook(task, e)

    async def _run_agent_task(self, agent: Dict, task: Dict) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            self._execute_in_thread,
            agent,
            task,
        )

    def _execute_in_thread(self, agent: Dict, task: Dict) -> Any:
        return {
            "status": "completed",
            "output": f"Task {task['id']} processed by {agent['name']}",
        }

# 2019-04-24T14:55:39 update

# 2019-05-01T16:01:52 update

# 2019-05-27T19:55:55 update

# 2019-06-02T09:38:08 update

# 2019-07-10T15:36:32 update

# 2019-07-22T11:36:40 update

# 2019-08-28T10:50:39 update

# 2019-08-30T14:21:57 update

# 2019-09-12T18:46:28 update

# 2019-10-02T09:55:59 update

# 2019-10-03T16:01:13 update

# 2019-12-03T13:07:37 update

# 2020-01-10T13:47:02 update

# 2020-01-31T13:14:49 update

# 2020-03-11T08:03:44 update

# 2020-03-31T15:51:14 update

# 2020-04-10T11:21:15 update

# 2020-06-08T09:31:33 update

# 2020-06-16T20:32:00 update

# 2020-07-21T18:48:01 update

# 2020-09-29T15:16:08 update

# 2020-11-18T14:09:09 update

# 2020-11-26T18:02:40 update

# 2021-01-07T11:18:24 update

# 2021-04-05T15:49:29 update

# 2021-04-27T11:58:27 update

# 2021-05-17T14:54:17 update

# 2021-06-07T11:46:07 update

# 2021-08-31T14:55:54 update

# 2021-09-10T17:29:34 update

# 2021-09-14T10:27:30 update

# 2021-10-06T14:04:05 update

# 2022-03-15T18:11:19 update

# 2022-09-15T18:32:09 update

# 2022-11-17T08:15:16 update

# 2023-02-17T12:24:53 update

# 2023-04-25T14:26:37 update

# 2023-05-22T09:03:39 update

# 2023-09-06T20:26:58 update

# 2023-11-28T17:54:23 update

# 2023-12-27T15:38:11 update

# 2024-03-12T20:10:32 update

# 2024-04-04T20:43:06 update

# 2024-05-27T12:23:51 update

# 2024-05-27T16:42:42 update

# 2024-07-23T13:27:05 update

# 2024-07-24T19:24:13 update

# 2024-11-03T18:25:58 update

# 2025-04-23T20:03:19 update

# 2026-02-16T17:12:09 update

# 2026-03-12T11:33:28 update
