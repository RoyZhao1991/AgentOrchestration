"""Task Scheduler — Priority-based task queuing and dispatch."""

from dataclasses import dataclass
import heapq
import time
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from src.common.metrics import metrics


@dataclass(frozen=True)
class Reservation:
    task_id: str
    queue: str
    priority: int
    worker_id: str
    reserved_at: float
    lease_seconds: float
    token: str


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
    def __init__(self):
        self._queues: Dict[str, PriorityQueue] = {}
        self._scheduled: Dict[str, Tuple[Dict, float, str, int]] = {}
        self._in_flight: Dict[str, Dict] = {}
        self._reservations: Dict[str, Reservation] = {}
        self._retired_tokens: Dict[str, set] = {}
        self._audit_records: List[Dict[str, Any]] = []
        self._max_retries = 3

    def enqueue(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
    ) -> str:
        task_id = str(uuid4())
        task["id"] = task_id
        task["enqueued_at"] = time.time()
        task["retries"] = 0
        task["queue"] = queue
        task["priority"] = priority

        self._enqueue_existing(task, queue, priority)
        return task_id

    def _enqueue_existing(
        self,
        task: Dict,
        queue: str = "default",
        priority: int = 0,
    ) -> None:
        task["queue"] = queue
        task["priority"] = priority
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
        self._scheduled[task_id] = (task, time.time() + delay, queue, priority)
        return task_id

    async def dequeue(
        self,
        queue: str = "default",
        timeout: float = 1.0,
        now: Optional[float] = None,
    ) -> Optional[Dict]:
        self._drain_scheduled(now)

        task = self._pop_queued(queue)
        if task:
            self._in_flight[task["id"]] = task
            metrics.increment("scheduler.tasks.dequeued")
            return task
        return None

    async def reserve(
        self,
        worker_id: str,
        queue: str = "default",
        lease_seconds: float = 60.0,
        now: Optional[float] = None,
    ) -> Optional[Dict]:
        self._drain_scheduled(now)

        task = self._pop_queued(queue)
        if not task:
            return None

        reserved_at = self._clock(now)
        token = uuid4().hex
        priority = int(task.get("priority", 0))
        reservation = Reservation(
            task_id=task["id"],
            queue=queue,
            priority=priority,
            worker_id=worker_id,
            reserved_at=reserved_at,
            lease_seconds=lease_seconds,
            token=token,
        )
        task["reservation_token"] = token
        task["reserved_by"] = worker_id
        task["reserved_at"] = reserved_at
        task["lease_seconds"] = lease_seconds

        self._in_flight[task["id"]] = task
        self._reservations[task["id"]] = reservation
        self._record_audit("reserved", reservation, "worker_claimed")
        metrics.increment("scheduler.reservations.created")
        return task

    def complete(
        self,
        task_id: str,
        reservation_token: Optional[str] = None,
    ) -> bool:
        reservation = self._reservations.get(task_id)
        if reservation:
            if reservation.token != reservation_token:
                self._record_audit(
                    "completion_rejected",
                    reservation,
                    "stale_or_invalid_reservation_token",
                )
                metrics.increment("scheduler.reservations.stale_complete")
                return False

            self._reservations.pop(task_id, None)
            self._in_flight.pop(task_id, None)
            self._retire_token(task_id, reservation.token)
            self._record_audit("completed", reservation, "reservation_ack")
            metrics.increment("scheduler.reservations.completed")
            return True

        if reservation_token is not None:
            self._record_stale_completion(task_id, reservation_token)
            metrics.increment("scheduler.reservations.stale_complete")
            return False

        return self._in_flight.pop(task_id, None) is not None

    def fail(self, task_id: str, queue: str = "default") -> bool:
        task = self._in_flight.pop(task_id, None)
        if task:
            reservation = self._reservations.pop(task_id, None)
            if reservation:
                self._retire_token(task_id, reservation.token)
                self._clear_reservation_fields(task)
            task["retries"] += 1
            if task["retries"] < self._max_retries:
                self._enqueue_existing(
                    task,
                    queue,
                    priority=task.get("priority", 0),
                )
                return True
        return False

    def reclaim_worker(
        self,
        worker_id: str,
        now: Optional[float] = None,
    ) -> List[str]:
        reclaimed = []
        for task_id, reservation in list(self._reservations.items()):
            if reservation.worker_id == worker_id:
                if self._reclaim(task_id, "worker_disconnected", now):
                    reclaimed.append(task_id)
        return reclaimed

    def reclaim_expired(self, now: Optional[float] = None) -> List[str]:
        checked_at = self._clock(now)
        reclaimed = []
        for task_id, reservation in list(self._reservations.items()):
            expires_at = reservation.reserved_at + reservation.lease_seconds
            if expires_at <= checked_at:
                if self._reclaim(task_id, "reservation_lease_expired", now):
                    reclaimed.append(task_id)
        return reclaimed

    def audit_records(self) -> Tuple[Dict[str, Any], ...]:
        return tuple(dict(record) for record in self._audit_records)

    def _drain_scheduled(self, now: Optional[float] = None) -> None:
        checked_at = self._clock(now)
        expired = [
            task_id
            for task_id, (_, run_at, _, _) in self._scheduled.items()
            if run_at <= checked_at
        ]
        for task_id in expired:
            task, _, queue, priority = self._scheduled.pop(task_id)
            self._enqueue_existing(task, queue, priority)

    def _pop_queued(self, queue: str) -> Optional[Dict]:
        if queue in self._queues and len(self._queues[queue]) > 0:
            return self._queues[queue].pop()
        return None

    def _reclaim(
        self,
        task_id: str,
        reason: str,
        now: Optional[float] = None,
    ) -> bool:
        reservation = self._reservations.pop(task_id, None)
        task = self._in_flight.pop(task_id, None)
        if not reservation or not task:
            return False

        self._retire_token(task_id, reservation.token)
        self._clear_reservation_fields(task)
        self._enqueue_existing(task, reservation.queue, reservation.priority)
        self._record_audit("reclaimed", reservation, reason, now)
        metrics.increment("scheduler.reservations.reclaimed")
        return True

    def _clear_reservation_fields(self, task: Dict) -> None:
        task.pop("reservation_token", None)
        task.pop("reserved_by", None)
        task.pop("reserved_at", None)
        task.pop("lease_seconds", None)

    def _record_audit(
        self,
        action: str,
        reservation: Reservation,
        reason: str,
        now: Optional[float] = None,
    ) -> None:
        self._audit_records.append({
            "action": action,
            "reason": reason,
            "task_id": reservation.task_id,
            "queue": reservation.queue,
            "priority": reservation.priority,
            "worker_id": reservation.worker_id,
            "reserved_at": reservation.reserved_at,
            "recorded_at": self._clock(now),
        })

    def _record_stale_completion(
        self,
        task_id: str,
        reservation_token: str,
    ) -> None:
        self._audit_records.append({
            "action": "completion_rejected",
            "reason": "no_active_reservation_for_token",
            "task_id": task_id,
            "token_retired": reservation_token in self._retired_tokens.get(
                task_id,
                set(),
            ),
            "recorded_at": self._clock(),
        })

    def _retire_token(self, task_id: str, token: str) -> None:
        self._retired_tokens.setdefault(task_id, set()).add(token)

    def _clock(self, now: Optional[float] = None) -> float:
        return time.time() if now is None else now

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
