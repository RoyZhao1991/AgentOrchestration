"""Task Scheduler — Priority-based task queuing and dispatch."""

import hashlib
import heapq
import time
from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4


class PriorityQueue:
    def __init__(self):
        self._queue = []
        self._counter = 0

    def push(self, item: Any, priority: int = 0) -> None:
        heapq.heappush(self._queue, (-priority, self._counter, item))
        self._counter += 1

    def pop(self) -> Optional[Any]:
        if self._queue:
            return heapq.heappop(self._queue)[2]
        return None

    def pop_matching(self, predicate: Callable[[Any], bool]) -> Optional[Any]:
        skipped = []
        item = None

        while self._queue:
            entry = heapq.heappop(self._queue)
            candidate = entry[2]
            if predicate(candidate):
                item = candidate
                break
            skipped.append(entry)

        for entry in skipped:
            heapq.heappush(self._queue, entry)
        return item

    def items(self) -> List[Any]:
        return [entry[2] for entry in self._queue]

    def peek(self) -> Optional[Any]:
        if self._queue:
            return self._queue[0][2]
        return None

    def __len__(self) -> int:
        return len(self._queue)


class TaskScheduler:
    def __init__(
        self,
        resume_backlog_limit: int = 2,
        time_fn: Callable[[], float] = time.time,
    ):
        if resume_backlog_limit < 1:
            raise ValueError("resume_backlog_limit must be positive")

        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, Dict] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._paused_tenants = set()
        self._resume_plans: Dict[str, Dict[str, Any]] = {}
        self._audit_events: List[Dict[str, Any]] = []
        self._resume_backlog_limit = resume_backlog_limit
        self._time_fn = time_fn
        self._max_retries = 3

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = task.get("id") or str(uuid4())
        task["id"] = task_id
        task["enqueued_at"] = self._time_fn()
        task["retries"] = task.get("retries", 0)
        task["priority"] = priority
        task["queue"] = queue

        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)
        return task_id

    def schedule(
        self,
        task: Dict,
        delay: float,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = task.get("id") or str(uuid4())
        task["id"] = task_id
        task["queue"] = queue
        task["priority"] = priority
        self._scheduled[task_id] = {
            "task": task,
            "run_at": self._time_fn() + delay,
            "queue": queue,
            "priority": priority,
        }
        return task_id

    def pause_tenant(self, tenant_id: str, release_id: str = "manual") -> None:
        self._paused_tenants.add(tenant_id)
        self._resume_plans.pop(tenant_id, None)
        self._audit(
            "paused",
            tenant_id,
            release_id,
            reason="tenant_paused",
        )

    def resume_tenant(
        self,
        tenant_id: str,
        release_id: str,
        backlog_limit: Optional[int] = None,
    ) -> bool:
        if tenant_id not in self._paused_tenants:
            self._audit(
                "rejected",
                tenant_id,
                release_id,
                reason="tenant_not_paused",
            )
            return False

        limit = backlog_limit or self._resume_backlog_limit
        if limit < 1:
            raise ValueError("backlog_limit must be positive")

        backlog = self._count_queued_tenant_tasks(tenant_id)
        self._paused_tenants.remove(tenant_id)
        self._resume_plans[tenant_id] = {
            "release_id": release_id,
            "available": min(limit, backlog),
            "limit": limit,
        }
        self._audit(
            "planned",
            tenant_id,
            release_id,
            reason="tenant_resumed",
            backlog=backlog,
            allowed=min(limit, backlog),
        )
        return True

    def advance_resume_window(
        self,
        tenant_id: str,
        release_id: str,
        backlog_limit: Optional[int] = None,
    ) -> bool:
        plan = self._resume_plans.get(tenant_id)
        if plan is None:
            self._audit(
                "rejected",
                tenant_id,
                release_id,
                reason="missing_resume_plan",
            )
            return False

        limit = backlog_limit or plan["limit"]
        if limit < 1:
            raise ValueError("backlog_limit must be positive")

        backlog = self._count_queued_tenant_tasks(tenant_id)
        plan["release_id"] = release_id
        plan["available"] += min(limit, backlog)
        self._audit(
            "advanced",
            tenant_id,
            release_id,
            reason="resume_window_advanced",
            backlog=backlog,
            allowed=min(limit, backlog),
        )
        return True

    @property
    def audit_events(self) -> List[Dict[str, Any]]:
        return [deepcopy(event) for event in self._audit_events]

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
    ) -> Optional[Dict]:
        now = self._time_fn()
        expired = [
            tid
            for tid, record in self._scheduled.items()
            if record["run_at"] <= now
        ]
        for tid in expired:
            record = self._scheduled.pop(tid)
            self.enqueue(
                record["task"],
                record["queue"],
                priority=record["priority"],
            )

        if queue in self._queues and len(self._queues[queue]) > 0:
            task = self._queues[queue].pop_matching(self._can_dispatch)
            if task:
                self._in_flight[task["id"]] = task
                return task
        return None

    def complete(self, task_id: str) -> bool:
        return self._in_flight.pop(task_id, None) is not None

    def fail(self, task_id: str, queue: str = "default") -> bool:
        task = self._in_flight.pop(task_id, None)
        if task:
            task["retries"] += 1
            if task["retries"] < self._max_retries:
                self.enqueue(task, queue, priority=task.get("priority", 0))
                return True
        return False

    def _can_dispatch(self, task: Dict[str, Any]) -> bool:
        tenant_id = task.get("tenant_id")
        if not tenant_id:
            return True

        if tenant_id in self._paused_tenants:
            self._audit(
                "deferred",
                tenant_id,
                task.get("release_id", "unknown"),
                reason="tenant_paused",
            )
            return False

        plan = self._resume_plans.get(tenant_id)
        if not plan:
            return True

        if plan["available"] <= 0:
            self._audit(
                "deferred",
                tenant_id,
                plan["release_id"],
                reason="resume_window_exhausted",
            )
            return False

        plan["available"] -= 1
        self._audit(
            "accepted",
            tenant_id,
            plan["release_id"],
            reason="resume_window_available",
            remaining_window=plan["available"],
        )
        return True

    def _count_queued_tenant_tasks(self, tenant_id: str) -> int:
        return sum(
            1
            for queue in self._queues.values()
            for task in queue.items()
            if task.get("tenant_id") == tenant_id
        )

    def _audit(
        self,
        decision: str,
        tenant_id: str,
        release_id: str,
        **metadata: Any,
    ) -> None:
        event = {
            "decision": decision,
            "tenant_ref": self._tenant_ref(tenant_id),
            "release_id": release_id,
            "at": self._time_fn(),
        }
        event.update(metadata)
        self._audit_events.append(event)

    @staticmethod
    def _tenant_ref(tenant_id: str) -> str:
        digest = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
        return digest[:12]

# 2019-04-25T08:37:12 update

# 2019-06-04T16:40:00 update

# 2019-07-11T12:01:28 update

# 2019-08-02T12:20:21 update

# 2019-08-23T10:38:50 update

# 2019-10-31T13:55:52 update

# 2019-11-04T20:12:32 update

# 2019-12-13T12:22:36 update

# 2020-02-01T10:32:37 update

# 2020-02-26T09:44:38 update

# 2020-03-09T19:00:55 update

# 2020-05-01T18:40:34 update

# 2020-05-12T15:10:31 update

# 2020-06-30T13:24:19 update

# 2020-09-22T16:00:45 update

# 2020-10-20T10:52:48 update

# 2020-10-21T12:18:08 update

# 2020-11-06T12:35:01 update

# 2020-12-09T08:09:33 update

# 2021-01-07T08:20:36 update

# 2021-10-02T15:23:16 update

# 2021-10-06T16:14:57 update

# 2021-10-06T09:27:41 update

# 2021-11-19T08:37:40 update

# 2022-03-01T16:39:54 update

# 2022-05-26T13:43:07 update

# 2022-06-02T10:50:58 update

# 2022-06-14T10:46:48 update

# 2022-07-31T16:44:34 update

# 2022-08-30T18:20:12 update

# 2022-11-04T14:47:03 update

# 2022-12-06T10:36:49 update

# 2022-12-22T13:21:12 update

# 2022-12-26T12:24:50 update

# 2023-03-09T08:09:55 update

# 2023-05-01T10:07:37 update

# 2023-06-08T14:32:15 update

# 2023-07-14T17:24:18 update

# 2023-12-14T08:38:31 update

# 2024-02-20T13:43:58 update

# 2024-03-24T08:52:42 update

# 2024-03-28T15:27:17 update

# 2024-03-29T18:10:33 update

# 2024-04-15T20:18:31 update

# 2024-05-27T13:11:52 update

# 2024-05-27T16:42:56 update

# 2024-06-20T13:03:45 update

# 2024-06-28T12:32:58 update

# 2024-07-10T14:10:16 update

# 2024-07-26T14:18:59 update

# 2024-08-12T08:21:05 update

# 2024-08-21T16:58:40 update

# 2024-09-27T19:54:30 update

# 2024-10-21T13:47:42 update

# 2024-11-11T09:19:27 update

# 2024-12-24T08:23:41 update

# 2025-02-14T10:35:15 update

# 2025-03-31T18:09:40 update

# 2025-06-21T17:32:49 update

# 2025-07-21T16:52:28 update

# 2025-08-20T19:45:16 update

# 2025-11-04T18:54:24 update

# 2025-12-09T20:17:36 update

# 2026-01-12T15:42:32 update

# 2026-01-23T14:41:20 update

# 2026-03-18T14:43:07 update

# 2026-04-13T11:43:19 update
