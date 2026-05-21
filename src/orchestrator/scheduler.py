"""Task Scheduler — Priority-based task queuing and dispatch."""

import heapq
import time
from typing import Any, Dict, List, Optional
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

    def peek(self) -> Optional[Any]:
        if self._queue:
            return self._queue[0][2]
        return None

    def __len__(self) -> int:
        return len(self._queue)


class TaskScheduler:
    def __init__(self, reservation_timeout: float = 30.0):
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, float] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._audit_events: List[Dict] = []
        self._max_retries = 3
        self._reservation_timeout = reservation_timeout

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = task.get("id") or str(uuid4())
        task["id"] = task_id
        task["enqueued_at"] = time.time()
        task.setdefault("retries", 0)
        task["queue"] = queue
        task["priority"] = priority
        self._clear_reservation(task)

        self._enqueue_ready(task, queue, priority)
        return task_id

    def _enqueue_ready(self, task: Dict, queue: str, priority: int) -> None:
        if queue not in self._queues:
            self._queues[queue] = PriorityQueue()
        self._queues[queue].push(task, priority)

    def schedule(
        self,
        task: Dict,
        delay: float,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        task["queue"] = queue
        task["priority"] = priority
        self._scheduled[task_id] = time.time() + delay
        return task_id

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
        worker_id: Optional[str] = None,
        reservation_timeout: Optional[float] = None,
        now: Optional[float] = None,
    ) -> Optional[Dict]:
        now = now if now is not None else time.time()
        self.reclaim_abandoned_reservations(queue=queue, now=now)
        expired = [tid for tid, t in self._scheduled.items() if t <= now]
        for tid in expired:
            task = self._scheduled.pop(tid)
            if task:
                self.enqueue(task, queue)

        if queue in self._queues and len(self._queues[queue]) > 0:
            task = self._queues[queue].pop()
            if task:
                self._reserve_task(
                    task,
                    queue=queue,
                    worker_id=worker_id,
                    now=now,
                    reservation_timeout=reservation_timeout,
                )
                self._in_flight[task["id"]] = task
                return task
        return None

    def complete(
        self,
        task_id: str,
        reservation_token: Optional[str] = None,
    ) -> bool:
        task = self._in_flight.get(task_id)
        if not task:
            return False
        if not self._reservation_matches(task, reservation_token):
            self._audit("completion_rejected", task, "stale_reservation")
            return False

        self._in_flight.pop(task_id)
        self._audit("completed", task, "worker_ack")
        return True

    def fail(
        self,
        task_id: str,
        queue: str = "default",
        reservation_token: Optional[str] = None,
    ) -> bool:
        task = self._in_flight.get(task_id)
        if not task:
            return False
        if not self._reservation_matches(task, reservation_token):
            self._audit("failure_rejected", task, "stale_reservation")
            return False

        self._in_flight.pop(task_id)
        task["retries"] += 1
        if task["retries"] < self._max_retries:
            self.enqueue(
                task,
                queue=task.get("queue", queue),
                priority=task.get("priority", 0),
            )
            self._audit("retry_requeued", task, "worker_failure")
            return True
        return False

    def worker_disconnected(
        self,
        worker_id: str,
        queue: str = "default",
        now: Optional[float] = None,
    ) -> int:
        now = now if now is not None else time.time()
        reclaimed = 0

        for task_id, task in list(self._in_flight.items()):
            if task.get("reserved_by") != worker_id:
                continue

            if self._requeue_reserved_task(
                task_id,
                task,
                queue=task.get("queue", queue),
                now=now,
                reason="worker_disconnected",
            ):
                reclaimed += 1

        return reclaimed

    def reclaim_abandoned_reservations(
        self,
        queue: Optional[str] = None,
        now: Optional[float] = None,
    ) -> int:
        now = now if now is not None else time.time()
        reclaimed = 0

        for task_id, task in list(self._in_flight.items()):
            expires_at = task.get("reservation_expires_at")
            if expires_at is None or expires_at > now:
                continue
            if queue is not None and task.get("queue") != queue:
                continue

            if self._requeue_reserved_task(
                task_id,
                task,
                queue=task.get("queue", queue or "default"),
                now=now,
                reason="reservation_expired",
            ):
                reclaimed += 1

        return reclaimed

    def audit_events(self) -> List[Dict]:
        return list(self._audit_events)

    def _reserve_task(
        self,
        task: Dict,
        queue: str,
        worker_id: Optional[str],
        now: float,
        reservation_timeout: Optional[float],
    ) -> None:
        timeout = reservation_timeout or self._reservation_timeout
        task["queue"] = queue
        task["reserved_by"] = worker_id
        task["reserved_at"] = now
        task["reservation_expires_at"] = now + timeout
        task["reservation_token"] = str(uuid4())
        self._audit("reserved", task, "worker_claim")

    def _requeue_reserved_task(
        self,
        task_id: str,
        task: Dict,
        queue: str,
        now: float,
        reason: str,
    ) -> bool:
        if self._in_flight.pop(task_id, None) is None:
            return False

        task["reservation_reclaims"] = task.get("reservation_reclaims", 0) + 1
        task["reclaimed_at"] = now
        task["enqueued_at"] = now
        worker_id = task.get("reserved_by")
        self._clear_reservation(task)
        self._enqueue_ready(task, queue, task.get("priority", 0))
        self._audit("reservation_requeued", task, reason, worker_id=worker_id)
        return True

    def _clear_reservation(self, task: Dict) -> None:
        task.pop("reserved_by", None)
        task.pop("reserved_at", None)
        task.pop("reservation_expires_at", None)
        task.pop("reservation_token", None)

    def _reservation_matches(
        self,
        task: Dict,
        reservation_token: Optional[str],
    ) -> bool:
        if reservation_token is None:
            return True
        return task.get("reservation_token") == reservation_token

    def _audit(
        self,
        action: str,
        task: Dict,
        reason: str,
        worker_id: Optional[str] = None,
    ) -> None:
        self._audit_events.append(
            {
                "action": action,
                "task_id": task.get("id"),
                "queue": task.get("queue", "default"),
                "worker_id": worker_id
                if worker_id is not None
                else task.get("reserved_by"),
                "reason": reason,
            },
        )

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
