import pytest

from src.api.webhooks import (
    WebhookManager,
    WebhookValidationError,
    shape_public_event_payload,
)


def test_valid_delivery_filters_internal_metadata_before_callback():
    manager = WebhookManager()
    endpoint = manager.register_endpoint(
        workspace_id="workspace-a",
        url="https://hooks.example.com/agent-events",
        event_types=["run.completed"],
    )
    callbacks = []

    record = manager.deliver(
        workspace_id="workspace-a",
        endpoint_id=endpoint.id,
        event_type="run.completed",
        event_id="evt-1",
        payload={
            "run_id": "run-public-1",
            "status": "completed",
            "internal_run_id": "run-internal-1",
            "metadata": {
                "customer": "acme",
                "worker_host": "worker-7",
                "_debug": "scheduler-state",
            },
            "steps": [
                {
                    "name": "notify",
                    "status": "ok",
                    "retry_token": "retry-secret",
                }
            ],
        },
        sender=lambda url, payload: callbacks.append((url, payload)),
    )

    assert record.status == "delivered"
    assert callbacks == [
        (
            "https://hooks.example.com/agent-events",
            {
                "run_id": "run-public-1",
                "status": "completed",
                "metadata": {"customer": "acme"},
                "steps": [{"name": "notify", "status": "ok"}],
            },
        )
    ]
    assert record.payload == callbacks[0][1]


def test_rejected_endpoint_is_not_persisted_or_delivered():
    manager = WebhookManager()

    with pytest.raises(WebhookValidationError, match="https"):
        manager.register_endpoint(
            workspace_id="workspace-a",
            url="http://localhost/webhook",
            event_types=["run.completed"],
        )

    assert manager.endpoint_count() == 0
    assert manager.delivery_count() == 0


def test_disabled_endpoint_rejects_delivery_before_callback_or_record():
    manager = WebhookManager()
    endpoint = manager.register_endpoint(
        workspace_id="workspace-a",
        url="https://hooks.example.com/agent-events",
        event_types=["run.completed"],
    )
    callbacks = []

    manager.disable_endpoint("workspace-a", endpoint.id)

    with pytest.raises(WebhookValidationError, match="disabled"):
        manager.deliver(
            workspace_id="workspace-a",
            endpoint_id=endpoint.id,
            event_type="run.completed",
            event_id="evt-1",
            payload={"run_id": "run-public-1"},
            sender=lambda url, payload: callbacks.append((url, payload)),
        )

    assert callbacks == []
    assert manager.delivery_count() == 0


def test_retries_are_idempotent_and_do_not_duplicate_callbacks():
    manager = WebhookManager()
    endpoint = manager.register_endpoint(
        workspace_id="workspace-a",
        url="https://hooks.example.com/agent-events",
        event_types=["run.completed"],
    )
    callbacks = []

    first = manager.deliver(
        workspace_id="workspace-a",
        endpoint_id=endpoint.id,
        event_type="run.completed",
        event_id="evt-1",
        payload={"run_id": "run-public-1", "trace_id": "trace-secret"},
        sender=lambda url, payload: callbacks.append(payload),
    )
    retry = manager.deliver(
        workspace_id="workspace-a",
        endpoint_id=endpoint.id,
        event_type="run.completed",
        event_id="evt-1",
        payload={"run_id": "run-public-1", "trace_id": "changed-secret"},
        sender=lambda url, payload: callbacks.append(payload),
    )

    assert retry.delivery_id == first.delivery_id
    assert retry.payload == {"run_id": "run-public-1"}
    assert callbacks == [{"run_id": "run-public-1"}]
    assert manager.delivery_count() == 1


def test_workspace_isolation_and_rotated_endpoints_are_enforced():
    manager = WebhookManager()
    endpoint = manager.register_endpoint(
        workspace_id="workspace-a",
        url="https://hooks.example.com/agent-events",
        event_types=["run.completed"],
        secret_version="v1",
    )
    callbacks = []

    with pytest.raises(WebhookValidationError, match="workspace mismatch"):
        manager.deliver(
            workspace_id="workspace-b",
            endpoint_id=endpoint.id,
            event_type="run.completed",
            event_id="evt-1",
            payload={"run_id": "run-public-1"},
            sender=lambda url, payload: callbacks.append(payload),
        )

    rotated = manager.rotate_endpoint(
        workspace_id="workspace-a",
        endpoint_id=endpoint.id,
        url="https://hooks2.example.com/agent-events",
        secret_version="v2",
    )

    with pytest.raises(WebhookValidationError, match="disabled"):
        manager.deliver(
            workspace_id="workspace-a",
            endpoint_id=endpoint.id,
            event_type="run.completed",
            event_id="evt-2",
            payload={"run_id": "run-public-2"},
            sender=lambda url, payload: callbacks.append(payload),
        )

    record = manager.deliver(
        workspace_id="workspace-a",
        endpoint_id=rotated.id,
        event_type="run.completed",
        event_id="evt-2",
        payload={"run_id": "run-public-2"},
        sender=lambda url, payload: callbacks.append((url, payload)),
    )

    assert record.status == "delivered"
    assert rotated.secret_version == "v2"
    assert rotated.replaces_endpoint_id == endpoint.id
    assert callbacks == [
        (
            "https://hooks2.example.com/agent-events",
            {"run_id": "run-public-2"},
        )
    ]


def test_payload_shaping_can_be_used_before_logging_or_serialization():
    assert shape_public_event_payload(
        {
            "event": "run.completed",
            "tenant_id": "tenant-secret",
            "result": {"ok": True, "private_metadata": {"node": "internal"}},
        }
    ) == {"event": "run.completed", "result": {"ok": True}}
