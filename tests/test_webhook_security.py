import pytest

from src.api.webhooks import WebhookSecurityError, WebhookSecurityGateway


def gateway():
    service = WebhookSecurityGateway(replay_window_seconds=60)
    service.register_endpoint(
        endpoint_id="endpoint-1",
        workspace_id="workspace-1",
        url="https://hooks.example.com/orchestrator",
        secret="shared-signing-secret",
    )
    return service


def test_valid_signature_and_delivery_are_recorded_after_trust_checks():
    service = gateway()
    body = b'{"event":"task.completed"}'
    signature = service.sign("endpoint-1", timestamp=1000, body=body)
    callbacks = []

    assert service.verify_signature(
        endpoint_id="endpoint-1",
        workspace_id="workspace-1",
        timestamp=1000,
        signature=signature,
        body=body,
        now=1020,
    )
    record = service.deliver(
        endpoint_id="endpoint-1",
        workspace_id="workspace-1",
        event_id="event-1",
        payload={
            "status": "ok",
            "token": "private",
            "nested": {"secret": "x"},
        },
        callback=lambda url, payload: callbacks.append((url, payload)),
    )

    assert record.callback_status == "delivered"
    assert record.payload == {"status": "ok", "nested": {}}
    assert callbacks == [
        (
            "https://hooks.example.com/orchestrator",
            {"status": "ok", "nested": {}},
        )
    ]


def test_signature_replay_window_is_enforced_before_payload_parsing():
    service = gateway()
    body = b"not-json and should not be parsed"
    signature = service.sign("endpoint-1", timestamp=1000, body=body)

    with pytest.raises(WebhookSecurityError, match="outside replay window"):
        service.verify_signature(
            endpoint_id="endpoint-1",
            workspace_id="workspace-1",
            timestamp=1000,
            signature=signature,
            body=body,
            now=1200,
        )

    assert service.delivery_records() == ()


def test_signature_replay_is_rejected():
    service = gateway()
    body = b'{"event":"task.completed"}'
    signature = service.sign("endpoint-1", timestamp=1000, body=body)
    assert service.verify_signature(
        endpoint_id="endpoint-1",
        workspace_id="workspace-1",
        timestamp=1000,
        signature=signature,
        body=body,
        now=1001,
    )

    with pytest.raises(WebhookSecurityError, match="replay"):
        service.verify_signature(
            endpoint_id="endpoint-1",
            workspace_id="workspace-1",
            timestamp=1000,
            signature=signature,
            body=body,
            now=1002,
        )


def test_delivery_rejects_workspace_mismatch_without_record_or_callback():
    service = gateway()
    callbacks = []

    with pytest.raises(WebhookSecurityError, match="workspace mismatch"):
        service.deliver(
            endpoint_id="endpoint-1",
            workspace_id="workspace-2",
            event_id="event-1",
            payload={"status": "ok"},
            callback=lambda url, payload: callbacks.append((url, payload)),
        )

    assert callbacks == []
    assert service.delivery_records() == ()


def test_delivery_retries_are_idempotent_and_do_not_reinvoke_callback():
    service = gateway()
    callbacks = []

    first = service.deliver(
        endpoint_id="endpoint-1",
        workspace_id="workspace-1",
        event_id="event-1",
        payload={"status": "ok"},
        callback=lambda url, payload: callbacks.append((url, payload)),
    )
    second = service.deliver(
        endpoint_id="endpoint-1",
        workspace_id="workspace-1",
        event_id="event-1",
        payload={"status": "changed"},
        callback=lambda url, payload: callbacks.append((url, payload)),
    )

    assert second == first
    assert second.payload == {"status": "ok"}
    assert len(callbacks) == 1
    assert len(service.delivery_records()) == 1


def test_disabled_endpoint_rejects_signature_and_delivery():
    service = gateway()
    service.disable_endpoint("endpoint-1")
    body = b'{"event":"task.completed"}'
    signature = service.sign("endpoint-1", timestamp=1000, body=body)

    with pytest.raises(WebhookSecurityError, match="disabled"):
        service.verify_signature(
            endpoint_id="endpoint-1",
            workspace_id="workspace-1",
            timestamp=1000,
            signature=signature,
            body=body,
            now=1001,
        )
    with pytest.raises(WebhookSecurityError, match="disabled"):
        service.deliver(
            endpoint_id="endpoint-1",
            workspace_id="workspace-1",
            event_id="event-1",
            payload={"status": "ok"},
        )


def test_rotated_endpoint_rejects_old_signature():
    service = gateway()
    body = b'{"event":"task.completed"}'
    old_signature = service.sign("endpoint-1", timestamp=1000, body=body)
    service.rotate_endpoint_secret("endpoint-1", "replacement-secret")

    with pytest.raises(WebhookSecurityError, match="mismatch"):
        service.verify_signature(
            endpoint_id="endpoint-1",
            workspace_id="workspace-1",
            timestamp=1000,
            signature=old_signature,
            body=body,
            now=1001,
        )


def test_registration_rejects_untrusted_endpoint_before_persistence():
    service = WebhookSecurityGateway()

    with pytest.raises(WebhookSecurityError, match="https"):
        service.register_endpoint(
            "bad",
            "workspace-1",
            "http://example.com",
            "s",
        )
    with pytest.raises(WebhookSecurityError, match="publicly routable"):
        service.register_endpoint(
            "local",
            "workspace-1",
            "https://localhost/hook",
            "s",
        )

    assert service.delivery_records() == ()
