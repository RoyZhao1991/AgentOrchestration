from fastapi.testclient import TestClient

from src.api.server import create_app
from src.api.webhooks import webhook_registry


def setup_function():
    webhook_registry.clear()


def client():
    return TestClient(create_app())


def headers():
    return {"Authorization": "Bearer test-token"}


def register_subscription(
    workspace_id="workspace-a",
    filters=None,
):
    return client().post(
        "/api/v2/webhook-subscriptions",
        params={
            "workspace_id": workspace_id,
            "callback_url": "https://hooks.example.test/callback",
        },
        json=filters or {"event_type": "agent.started"},
        headers=headers(),
    )


def deliver(workspace_id, delivery_id, event):
    return client().post(
        f"/api/v2/webhook-deliveries/{delivery_id}",
        params={"workspace_id": workspace_id},
        json=event,
        headers=headers(),
    )


def retry(workspace_id, delivery_id):
    return client().post(
        f"/api/v2/webhook-deliveries/{delivery_id}/retry",
        params={"workspace_id": workspace_id},
        headers=headers(),
    )


def rotate(subscription_id, filters):
    return client().patch(
        f"/api/v2/webhook-subscriptions/{subscription_id}",
        params={
            "workspace_id": "workspace-a",
            "callback_url": "https://hooks.example.test/rotated",
        },
        json=filters,
        headers=headers(),
    )


def test_rejects_subscription_filters_with_unknown_fields():
    response = register_subscription(
        filters={"event_type": "agent.started", "secret": "leak"}
    )

    assert response.status_code == 400
    assert "unsupported filter fields: secret" in response.text


def test_valid_delivery_matches_allowed_subscription_filter():
    subscription = register_subscription().json()["subscription"]

    response = deliver(
        "workspace-a",
        "delivery-1",
        {"event_type": "agent.started", "payload": {"secret": "redact"}},
    )

    assert response.status_code == 200
    deliveries = response.json()["deliveries"]
    assert deliveries == [
        {
            "delivery_id": "delivery-1",
            "workspace_id": "workspace-a",
            "event_type": "agent.started",
            "status": "queued",
            "attempts": 1,
            "subscription_id": subscription["id"],
        }
    ]
    assert "payload" not in deliveries[0]
    assert "callback_url" not in deliveries[0]


def test_valid_delivery_matches_collection_filter():
    subscription = register_subscription(
        filters={"event_type": ["agent.started", "agent.resumed"]}
    ).json()["subscription"]

    response = deliver(
        "workspace-a",
        "delivery-collection",
        {"event_type": "agent.resumed"},
    )

    record = response.json()["deliveries"][0]
    assert response.status_code == 200
    assert record["status"] == "queued"
    assert record["subscription_id"] == subscription["id"]


def test_rejected_delivery_is_idempotent_and_sanitized():
    register_subscription(filters={"event_type": "agent.started"})

    first = deliver(
        "workspace-a",
        "delivery-2",
        {"event_type": "agent.stopped", "payload": {"token": "hidden"}},
    )
    second = retry("workspace-a", "delivery-2")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    record = first.json()["deliveries"][0]
    assert record["status"] == "rejected"
    assert record["reason"] == "no_matching_subscription"
    assert "payload" not in record
    assert "callback_url" not in record


def test_workspace_isolation_prevents_cross_workspace_delivery():
    register_subscription("workspace-a", {"event_type": "agent.started"})

    response = deliver(
        "workspace-b",
        "delivery-3",
        {"event_type": "agent.started"},
    )

    record = response.json()["deliveries"][0]
    assert record["workspace_id"] == "workspace-b"
    assert record["status"] == "rejected"
    assert "subscription_id" not in record


def test_disabled_subscription_does_not_receive_matching_delivery():
    subscription = register_subscription().json()["subscription"]
    disabled = client().post(
        f"/api/v2/webhook-subscriptions/{subscription['id']}/disable",
        params={"workspace_id": "workspace-a"},
        headers=headers(),
    )

    response = deliver(
        "workspace-a",
        "delivery-4",
        {"event_type": "agent.started"},
    )

    assert disabled.status_code == 200
    assert response.json()["deliveries"][0]["status"] == "rejected"


def test_rotated_subscription_updates_filters_without_exposing_callback():
    subscription = register_subscription(
        filters={"event_type": "agent.started"}
    ).json()["subscription"]

    rotated = rotate(
        subscription["id"],
        {"event_type": "agent.resumed", "status": "ok"},
    )
    response = deliver(
        "workspace-a",
        "delivery-rotated",
        {"event_type": "agent.resumed", "status": "ok"},
    )

    assert rotated.status_code == 200
    updated = rotated.json()["subscription"]
    assert updated["id"] == subscription["id"]
    assert updated["version"] == subscription["version"] + 1
    assert "callback_url" not in updated
    assert response.json()["deliveries"][0]["status"] == "queued"
