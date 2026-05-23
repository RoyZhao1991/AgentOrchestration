import asyncio
import json

from src.orchestrator.scheduler import TaskScheduler


def run(coro):
    return asyncio.run(coro)


def test_worker_disconnect_reclaims_reserved_task_once_preserving_identity():
    scheduler = TaskScheduler()
    task_id = scheduler.enqueue(
        {"type": "sync", "payload": {"account": "customer-1"}},
        queue="critical",
        priority=9,
    )

    reserved = run(
        scheduler.reserve(
            "worker-a",
            queue="critical",
            lease_seconds=30,
            now=1000.0,
        )
    )
    old_token = reserved["reservation_token"]

    assert reserved["id"] == task_id
    assert scheduler.reclaim_worker("worker-a", now=1005.0) == [task_id]
    assert scheduler.reclaim_worker("worker-a", now=1006.0) == []
    assert not scheduler.complete(task_id, old_token)

    rerouted = run(scheduler.reserve("worker-b", queue="critical", now=1007.0))
    assert rerouted["id"] == task_id
    assert rerouted["priority"] == 9
    assert rerouted["queue"] == "critical"
    assert rerouted["reservation_token"] != old_token


def test_expired_reservation_lease_is_reclaimed():
    scheduler = TaskScheduler()
    task_id = scheduler.enqueue({"type": "index"}, priority=4)

    reserved = run(
        scheduler.reserve("worker-a", lease_seconds=10, now=50.0)
    )

    assert reserved["id"] == task_id
    assert scheduler.reclaim_expired(now=59.9) == []
    assert scheduler.reclaim_expired(now=60.0) == [task_id]

    rerouted = run(scheduler.reserve("worker-b", now=61.0))
    assert rerouted["id"] == task_id
    assert rerouted["reserved_by"] == "worker-b"


def test_stale_completion_after_reclaim_is_rejected_without_losing_job():
    scheduler = TaskScheduler()
    task_id = scheduler.enqueue({"type": "charge"}, priority=8)

    reserved = run(scheduler.reserve("worker-a", now=10.0))
    old_token = reserved["reservation_token"]
    assert scheduler.reclaim_worker("worker-a", now=11.0) == [task_id]

    assert not scheduler.complete(task_id, old_token)
    rerouted = run(scheduler.reserve("worker-b", now=12.0))

    assert rerouted["id"] == task_id
    assert scheduler.complete(task_id, rerouted["reservation_token"])


def test_active_reservation_requires_matching_completion_token():
    scheduler = TaskScheduler()
    task_id = scheduler.enqueue({"type": "report"})

    reserved = run(scheduler.reserve("worker-a", now=10.0))

    assert not scheduler.complete(task_id, "wrong-token")
    assert scheduler.complete(task_id, reserved["reservation_token"])
    assert run(scheduler.reserve("worker-b")) is None


def test_reclaimed_task_keeps_priority_order():
    scheduler = TaskScheduler()
    low_id = scheduler.enqueue({"type": "low"}, priority=1)
    high_id = scheduler.enqueue({"type": "high"}, priority=10)

    high = run(scheduler.reserve("worker-a", now=1.0))
    assert high["id"] == high_id
    assert scheduler.reclaim_worker("worker-a", now=2.0) == [high_id]

    next_task = run(scheduler.reserve("worker-b", now=3.0))
    assert next_task["id"] == high_id

    final_task = run(scheduler.reserve("worker-c", now=4.0))
    assert final_task["id"] == low_id


def test_audit_records_do_not_expose_payload_or_tokens():
    scheduler = TaskScheduler()
    secret = "private-runtime-value"
    task_id = scheduler.enqueue(
        {"type": "private", "payload": {"secret": secret}},
        queue="sensitive",
        priority=6,
    )

    reserved = run(scheduler.reserve("worker-a", queue="sensitive", now=10.0))
    token = reserved["reservation_token"]
    assert scheduler.reclaim_worker("worker-a", now=11.0) == [task_id]
    assert not scheduler.complete(task_id, token)

    audit_text = json.dumps(scheduler.audit_records(), sort_keys=True)
    assert "payload" not in audit_text
    assert secret not in audit_text
    assert token not in audit_text
    assert "sensitive" in audit_text
