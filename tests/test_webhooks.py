from src.api.webhooks import WebhookError, WebhookFanoutDispatcher


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_valid_delivery_is_scoped_and_strips_internal_metadata():
    clock = Clock()
    dispatcher = WebhookFanoutDispatcher(clock=clock)
    endpoint = dispatcher.register_endpoint(
        "workspace-a",
        "https://example.com/a",
        ["run.completed"],
    )
    dispatcher.register_endpoint(
        "workspace-b",
        "https://example.com/b",
        ["run.completed"],
    )
    callbacks = []

    records = dispatcher.deliver(
        "workspace-a",
        "event-1",
        "run.completed",
        {
            "run_id": "public-run",
            "internal_run_id": "private-run",
            "nested": {
                "value": "public",
                "_trace": "private",
                "worker_pid": 123,
            },
        },
        lambda target, payload: callbacks.append((target.id, payload)),
    )

    assert [record.status for record in records] == ["delivered"]
    assert records[0].endpoint_id == endpoint.id
    assert records[0].workspace_id == "workspace-a"
    assert callbacks == [
        (
            endpoint.id,
            {
                "run_id": "public-run",
                "nested": {"value": "public"},
            },
        )
    ]
    assert records[0].payload == callbacks[0][1]


def test_per_endpoint_rate_limit_rejects_before_callback():
    clock = Clock()
    dispatcher = WebhookFanoutDispatcher(clock=clock)
    dispatcher.register_endpoint(
        "workspace-a",
        "https://example.com/a",
        ["run.completed"],
        rate_limit_per_minute=1,
    )
    callbacks = []

    first = dispatcher.deliver(
        "workspace-a",
        "event-1",
        "run.completed",
        {"run_id": "one"},
        lambda target, payload: callbacks.append(payload),
    )
    second = dispatcher.deliver(
        "workspace-a",
        "event-2",
        "run.completed",
        {"run_id": "two"},
        lambda target, payload: callbacks.append(payload),
    )

    assert first[0].status == "delivered"
    assert second[0].status == "rate_limited"
    assert second[0].attempts == 0
    assert second[0].reason == "endpoint_rate_limit_exceeded"
    assert second[0].retry_after == 60.0
    assert callbacks == [{"run_id": "one"}]


def test_retrying_same_event_is_idempotent_and_does_not_refanout():
    dispatcher = WebhookFanoutDispatcher(clock=Clock())
    dispatcher.register_endpoint(
        "workspace-a",
        "https://example.com/a",
        ["run.completed"],
    )
    callbacks = []

    first = dispatcher.deliver(
        "workspace-a",
        "event-1",
        "run.completed",
        {"run_id": "one"},
        lambda target, payload: callbacks.append(payload),
    )
    second = dispatcher.deliver(
        "workspace-a",
        "event-1",
        "run.completed",
        {"run_id": "one", "internal_metadata": "private"},
        lambda target, payload: callbacks.append(payload),
    )

    assert first[0].delivery_id == second[0].delivery_id
    assert first[0].status == "delivered"
    assert second[0].status == "delivered"
    assert callbacks == [{"run_id": "one"}]


def test_disabled_or_rotated_endpoints_do_not_receive_fanout():
    dispatcher = WebhookFanoutDispatcher(clock=Clock())
    old_endpoint = dispatcher.register_endpoint(
        "workspace-a",
        "https://example.com/old",
        ["run.completed"],
    )
    new_endpoint = dispatcher.rotate_endpoint(
        old_endpoint.id,
        "workspace-a",
        "https://example.com/new",
    )
    callbacks = []

    records = dispatcher.deliver(
        "workspace-a",
        "event-1",
        "run.completed",
        {"run_id": "one"},
        lambda target, payload: callbacks.append(target.id),
    )

    assert [record.endpoint_id for record in records] == [new_endpoint.id]
    assert callbacks == [new_endpoint.id]
    assert old_endpoint.enabled is False
    assert old_endpoint.disabled_reason == "rotated"


def test_registration_rejects_malformed_endpoints():
    dispatcher = WebhookFanoutDispatcher(clock=Clock())

    try:
        dispatcher.register_endpoint(
            "workspace-a",
            "http://example.com/insecure",
            ["run.completed"],
        )
    except WebhookError as exc:
        assert "HTTPS URL" in str(exc)
    else:
        raise AssertionError("expected malformed endpoint to be rejected")


def test_endpoint_updates_are_workspace_isolated():
    dispatcher = WebhookFanoutDispatcher(clock=Clock())
    endpoint = dispatcher.register_endpoint(
        "workspace-a",
        "https://example.com/a",
        ["run.completed"],
    )

    try:
        dispatcher.disable_endpoint(endpoint.id, "workspace-b")
    except WebhookError as exc:
        assert "endpoint not found" in str(exc)
    else:
        raise AssertionError("expected cross-workspace disable to be rejected")

    callbacks = []
    records = dispatcher.deliver(
        "workspace-b",
        "event-1",
        "run.completed",
        {"run_id": "one"},
        lambda target, payload: callbacks.append(payload),
    )

    assert records == []
    assert callbacks == []
    assert endpoint.enabled is True
