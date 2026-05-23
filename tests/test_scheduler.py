import asyncio

from src.orchestrator.scheduler import TaskScheduler


class FakeClock:
    def __init__(self):
        self.monotonic_now = 100.0
        self.wall_now = 1000.0

    def monotonic(self):
        return self.monotonic_now

    def wall(self):
        return self.wall_now

    def advance_monotonic(self, seconds):
        self.monotonic_now += seconds

    def adjust_wall(self, seconds):
        self.wall_now += seconds


class TestTaskScheduler:
    def setup_method(self):
        self.scheduler = TaskScheduler()

    def test_enqueue_task(self):
        task_id = self.scheduler.enqueue({"type": "test", "payload": {}})
        assert task_id is not None

    def test_dequeue_task(self):
        self.scheduler.enqueue({"type": "test", "payload": {"data": 1}})
        task = asyncio.run(self.scheduler.dequeue())
        assert task is not None
        assert task["type"] == "test"

    def test_enqueue_multiple_priorities(self):
        self.scheduler.enqueue({"type": "low"}, priority=1)
        self.scheduler.enqueue({"type": "high"}, priority=10)
        task = asyncio.run(self.scheduler.dequeue())
        assert task["type"] == "high"

    def test_complete_task(self):
        self.scheduler.enqueue({"type": "test"})
        task = asyncio.run(self.scheduler.dequeue())
        assert self.scheduler.complete(task["id"])

    def test_fail_task_with_retry(self):
        self.scheduler.enqueue({"type": "test"})
        task = asyncio.run(self.scheduler.dequeue())
        assert self.scheduler.fail(task["id"])

    def test_scheduled_tasks_use_monotonic_time(self):
        clock = FakeClock()
        scheduler = TaskScheduler(clock=clock.monotonic, wall_clock=clock.wall)

        scheduler.schedule({"type": "scheduled"}, delay=10)
        clock.adjust_wall(3600)
        assert asyncio.run(scheduler.dequeue()) is None

        clock.advance_monotonic(10)
        task = asyncio.run(scheduler.dequeue())
        assert task["type"] == "scheduled"

    def test_heartbeats_survive_backward_wall_clock_adjustment(self):
        clock = FakeClock()
        scheduler = TaskScheduler(
            clock=clock.monotonic,
            wall_clock=clock.wall,
            heartbeat_timeout=30,
        )

        accepted = scheduler.record_heartbeat(
            "worker-1",
            task_id="task-1",
            state="running",
        )
        clock.adjust_wall(-900)
        clock.advance_monotonic(20)

        assert accepted["status"] == "accepted"
        assert scheduler.stale_heartbeats() == []
        assert scheduler.get_heartbeat("worker-1")["task_id"] == "task-1"

        clock.advance_monotonic(11)
        expired = scheduler.expire_stale_heartbeats()
        assert [audit["status"] for audit in expired] == ["expired"]
        assert scheduler.get_heartbeat("worker-1") is None

    def test_non_monotonic_heartbeat_is_rejected_without_state_change(self):
        clock = FakeClock()
        scheduler = TaskScheduler(clock=clock.monotonic, wall_clock=clock.wall)

        scheduler.record_heartbeat("worker-1", task_id="task-1")
        clock.advance_monotonic(-1)
        rejected = scheduler.record_heartbeat("worker-1", task_id="task-2")

        assert rejected["status"] == "rejected"
        assert rejected["reason"] == "non_monotonic_heartbeat"
        assert scheduler.get_heartbeat("worker-1")["task_id"] == "task-1"
        assert scheduler.heartbeat_audit()[-1]["status"] == "rejected"

# 2019-01-09T19:07:03 update

# 2019-02-18T12:30:02 update

# 2019-04-11T16:04:51 update

# 2019-04-17T16:25:46 update

# 2019-05-24T19:32:13 update

# 2019-07-02T12:54:25 update

# 2019-07-03T20:37:00 update

# 2019-08-21T19:37:17 update

# 2019-10-18T10:30:31 update

# 2019-10-25T09:01:38 update

# 2019-10-29T12:59:34 update

# 2019-11-05T10:07:06 update

# 2019-11-11T10:43:52 update

# 2020-01-17T13:40:02 update

# 2020-02-07T14:06:34 update

# 2020-04-03T08:53:40 update

# 2020-04-06T19:36:29 update

# 2020-05-12T11:51:05 update

# 2020-08-17T08:37:15 update

# 2020-09-15T10:39:38 update

# 2020-10-06T11:26:19 update

# 2020-10-21T13:32:43 update

# 2020-12-14T18:18:36 update

# 2020-12-23T17:15:03 update

# 2021-01-25T16:29:00 update

# 2021-02-23T11:23:50 update

# 2021-03-19T12:21:19 update

# 2021-07-29T18:48:25 update

# 2021-08-25T12:46:58 update

# 2021-09-09T16:27:13 update

# 2021-12-16T12:05:30 update

# 2022-05-07T14:05:12 update

# 2022-07-18T20:52:29 update

# 2022-07-31T18:42:26 update

# 2022-09-09T13:10:08 update

# 2023-01-04T15:16:57 update

# 2023-01-17T14:49:04 update

# 2023-02-15T13:51:30 update

# 2023-03-08T09:15:53 update

# 2023-03-23T16:32:20 update

# 2023-03-28T09:32:01 update

# 2023-05-05T17:28:22 update

# 2023-06-01T08:13:52 update

# 2023-06-20T09:58:10 update

# 2023-07-04T16:14:34 update

# 2023-07-17T20:49:40 update

# 2023-12-26T11:49:18 update

# 2024-05-27T11:00:06 update

# 2024-07-04T08:53:03 update

# 2024-07-18T16:19:02 update

# 2024-08-07T09:35:35 update

# 2024-08-22T14:32:14 update

# 2025-05-20T14:19:23 update

# 2025-07-17T17:54:48 update

# 2025-07-28T13:06:30 update

# 2025-12-22T19:05:25 update

# 2026-01-08T18:43:02 update

# 2026-01-12T16:53:28 update

# 2026-04-16T16:58:23 update
