"""Task Scheduler — Priority-based task queuing and dispatch."""

import asyncio
import heapq
import time
from collections import defaultdict
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Tuple
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
        skipped: List[Tuple[int, int, Any]] = []
        matched = None

        while self._queue:
            entry = heapq.heappop(self._queue)
            item = entry[2]
            if predicate(item):
                matched = item
                break
            skipped.append(entry)

        for entry in skipped:
            heapq.heappush(self._queue, entry)

        return matched

    def peek(self) -> Optional[Any]:
        if self._queue:
            return self._queue[0][2]
        return None

    def __len__(self) -> int:
        return len(self._queue)


class TaskScheduler:
    _ACTIVE_STATES = {"queued", "scheduled", "in_flight"}
    _DEFAULT_PRIORITY_BUDGETS = {
        "urgent": 1,
        "high": 4,
        "default": 16,
        "low": 8,
    }

    def __init__(
        self,
        priority_budgets: Optional[Dict[str, int]] = None,
        audit_limit: int = 100,
    ):
        self._lock = RLock()
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, Dict] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._task_states: Dict[str, str] = {}
        self._task_priority_classes: Dict[str, str] = {}
        self._in_flight_by_priority: Dict[str, int] = defaultdict(int)
        self._priority_budgets = dict(self._DEFAULT_PRIORITY_BUDGETS)
        if priority_budgets:
            self._priority_budgets.update(priority_budgets)
        self._audit_limit = audit_limit
        self._audit_log: List[Dict[str, Any]] = []
        self._max_retries = 3

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        with self._lock:
            requested_id = task.get("id")
            current_state = self._task_states.get(requested_id)
            if requested_id and current_state in self._ACTIVE_STATES:
                self._record_audit(
                    "reject",
                    requested_id,
                    current_state,
                    current_state,
                    "duplicate_active_task",
                    queue,
                    self._priority_class_for(task, priority),
                )
                raise ValueError(f"task {requested_id} is already active")

            task_id = str(uuid4())
            task["id"] = task_id
            task["enqueued_at"] = time.time()
            task["retries"] = 0
            return self._queue_task(task, queue, priority, "enqueue")

    def schedule(
        self,
        task: Dict,
        delay: float,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        with self._lock:
            requested_id = task.get("id")
            current_state = self._task_states.get(requested_id)
            if requested_id and current_state in self._ACTIVE_STATES:
                self._record_audit(
                    "reject",
                    requested_id,
                    current_state,
                    current_state,
                    "duplicate_active_task",
                    queue,
                    self._priority_class_for(task, priority),
                )
                raise ValueError(f"task {requested_id} is already active")

            task_id = str(uuid4())
            priority_class = self._priority_class_for(task, priority)
            task["id"] = task_id
            task["priority"] = priority
            task["priority_class"] = priority_class
            task["retries"] = task.get("retries", 0)
            self._scheduled[task_id] = {
                "task": task,
                "ready_at": time.time() + delay,
                "queue": queue,
                "priority": priority,
            }
            self._task_states[task_id] = "scheduled"
            self._task_priority_classes[task_id] = priority_class
            self._record_audit(
                "transition",
                task_id,
                None,
                "scheduled",
                "schedule",
                queue,
                priority_class,
            )
            return task_id

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
    ) -> Optional[Dict]:
        deadline = time.time() + timeout

        while True:
            with self._lock:
                self._release_due_scheduled(queue)

                if queue in self._queues and len(self._queues[queue]) > 0:
                    task = self._queues[queue].pop_matching(self._can_dispatch)
                    if task:
                        task_id = task["id"]
                        priority_class = self._task_priority_classes[task_id]
                        transitioned = self._transition_task(
                            task_id,
                            "queued",
                            "in_flight",
                            "dequeue",
                            queue,
                        )
                        if transitioned:
                            self._in_flight[task_id] = task
                            self._in_flight_by_priority[priority_class] += 1
                            return task

                if time.time() >= deadline:
                    return None
            await asyncio.sleep(0.01)
        return None

    def complete(self, task_id: str) -> bool:
        with self._lock:
            task = self._in_flight.pop(task_id, None)
            if task:
                transitioned = self._transition_task(
                    task_id,
                    "in_flight",
                    "completed",
                    "complete",
                    None,
                )
                if transitioned:
                    self._release_priority_budget(task_id)
                    return True

            self._record_audit(
                "reject",
                task_id,
                self._task_states.get(task_id),
                "completed",
                "stale_complete",
                None,
                self._task_priority_classes.get(task_id),
            )
            return False

    def fail(self, task_id: str, queue: str = "default") -> bool:
        with self._lock:
            task = self._in_flight.pop(task_id, None)
            if not task:
                self._record_audit(
                    "reject",
                    task_id,
                    self._task_states.get(task_id),
                    "failed",
                    "stale_fail",
                    queue,
                    self._task_priority_classes.get(task_id),
                )
                return False

            task["retries"] += 1
            self._release_priority_budget(task_id)
            if task["retries"] < self._max_retries:
                self._queue_task(task, queue, task.get("priority", 0), "retry")
                return True

            return self._transition_task(
                task_id,
                "in_flight",
                "failed",
                "max_retries",
                queue,
            )

    def task_state(self, task_id: str) -> Optional[str]:
        with self._lock:
            return self._task_states.get(task_id)

    def audit_log(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._audit_log]

    def in_flight_count(self, priority_class: str) -> int:
        with self._lock:
            return self._in_flight_by_priority[priority_class]

    def _queue_task(
        self,
        task: Dict,
        queue: str,
        priority: int,
        reason: str,
    ) -> str:
        task_id = task["id"]
        priority_class = self._priority_class_for(task, priority)
        task["priority"] = priority
        task["priority_class"] = priority_class

        current_state = self._task_states.get(task_id)
        if current_state not in (None, "scheduled", "in_flight"):
            self._record_audit(
                "reject",
                task_id,
                current_state,
                "queued",
                "invalid_queue_transition",
                queue,
                priority_class,
            )
            raise ValueError(
                f"task {task_id} cannot transition from "
                f"{current_state} to queued"
            )

        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)
        self._task_states[task_id] = "queued"
        self._task_priority_classes[task_id] = priority_class
        self._record_audit(
            "transition",
            task_id,
            current_state,
            "queued",
            reason,
            queue,
            priority_class,
        )
        return task_id

    def _release_due_scheduled(self, queue: str) -> None:
        now = time.time()
        expired = [
            tid for tid, scheduled in self._scheduled.items()
            if scheduled["ready_at"] <= now and scheduled["queue"] == queue
        ]
        for task_id in expired:
            scheduled = self._scheduled.pop(task_id)
            self._queue_task(
                scheduled["task"],
                scheduled["queue"],
                scheduled["priority"],
                "schedule_due",
            )

    def _can_dispatch(self, task: Dict) -> bool:
        task_id = task["id"]
        priority_class = self._task_priority_classes.get(
            task_id,
            self._priority_class_for(task, task.get("priority", 0)),
        )
        if self._task_states.get(task_id) != "queued":
            self._record_audit(
                "reject",
                task_id,
                self._task_states.get(task_id),
                "in_flight",
                "stale_dispatch",
                None,
                priority_class,
            )
            return False

        limit = self._priority_budgets.get(priority_class)
        if (
            limit is not None
            and self._in_flight_by_priority[priority_class] >= limit
        ):
            self._record_audit(
                "defer",
                task_id,
                "queued",
                "queued",
                "priority_budget_exhausted",
                None,
                priority_class,
            )
            return False
        return True

    def _transition_task(
        self,
        task_id: str,
        expected_state: str,
        next_state: str,
        reason: str,
        queue: Optional[str],
    ) -> bool:
        current_state = self._task_states.get(task_id)
        priority_class = self._task_priority_classes.get(task_id)
        if current_state != expected_state:
            self._record_audit(
                "reject",
                task_id,
                current_state,
                next_state,
                reason,
                queue,
                priority_class,
            )
            return False

        self._task_states[task_id] = next_state
        self._record_audit(
            "transition",
            task_id,
            current_state,
            next_state,
            reason,
            queue,
            priority_class,
        )
        return True

    def _release_priority_budget(self, task_id: str) -> None:
        priority_class = self._task_priority_classes.get(task_id)
        if priority_class and self._in_flight_by_priority[priority_class] > 0:
            self._in_flight_by_priority[priority_class] -= 1

    def _priority_class_for(self, task: Dict, priority: int) -> str:
        explicit = (
            task.get("priority_class")
            or task.get("lane")
            or task.get("workflow_lane")
        )
        if explicit:
            return str(explicit).strip().lower() or "default"
        if priority >= 100:
            return "urgent"
        if priority >= 10:
            return "high"
        if priority < 0:
            return "low"
        return "default"

    def _record_audit(
        self,
        action: str,
        task_id: Optional[str],
        from_state: Optional[str],
        to_state: Optional[str],
        reason: str,
        queue: Optional[str],
        priority_class: Optional[str],
    ) -> None:
        entry = {
            "timestamp": time.time(),
            "action": action,
            "task_id": task_id,
            "from_state": from_state,
            "to_state": to_state,
            "reason": reason,
            "queue": queue,
            "priority_class": priority_class,
        }
        self._audit_log.append(entry)
        if len(self._audit_log) > self._audit_limit:
            self._audit_log = self._audit_log[-self._audit_limit:]

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
