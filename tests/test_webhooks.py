from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.webhooks import router, webhook_store


def make_client():
    webhook_store.clear()
    app = FastAPI()
    app.include_router(router, prefix="/api/v2")
    return TestClient(app)


def test_subscription_normalizes_url_before_duplicate_check():
    client = make_client()
    first = client.post(
        "/api/v2/webhooks/subscriptions",
        json={
            "workspace_id": "workspace-a",
            "url": "HTTPS://Example.COM:443/hooks/payments/?b=2&a=1#ignored",
            "event_types": ["claim.paid"],
        },
    )
    assert first.status_code == 200

    duplicate = client.post(
        "/api/v2/webhooks/subscriptions",
        json={
            "workspace_id": "workspace-a",
            "url": "https://example.com/hooks/payments?a=1&b=2",
            "event_types": ["claim.paid"],
        },
    )

    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == first.json()["id"]
    subscriptions = client.get(
        "/api/v2/webhooks/subscriptions",
        params={"workspace_id": "workspace-a"},
    ).json()["subscriptions"]
    assert len(subscriptions) == 1
    assert "normalized_url" not in subscriptions[0]
    assert "callback_secret" not in subscriptions[0]


def test_subscription_duplicate_checks_are_scoped_by_workspace():
    client = make_client()
    workspace_a = client.post(
        "/api/v2/webhooks/subscriptions",
        json={
            "workspace_id": "workspace-a",
            "url": "https://example.com/hooks/payments",
            "event_types": ["claim.paid"],
        },
    ).json()
    workspace_b = client.post(
        "/api/v2/webhooks/subscriptions",
        json={
            "workspace_id": "workspace-b",
            "url": "https://example.com/hooks/payments/",
            "event_types": ["claim.paid"],
        },
    ).json()

    assert workspace_a["id"] != workspace_b["id"]


def test_delivery_is_workspace_scoped_and_hides_internal_fields():
    client = make_client()
    workspace_a = client.post(
        "/api/v2/webhooks/subscriptions",
        json={
            "workspace_id": "workspace-a",
            "url": "https://a.example.com/hooks",
            "event_types": ["claim.paid"],
        },
    ).json()
    client.post(
        "/api/v2/webhooks/subscriptions",
        json={
            "workspace_id": "workspace-b",
            "url": "https://b.example.com/hooks",
            "event_types": ["claim.paid"],
        },
    )

    delivery = client.post(
        "/api/v2/webhooks/deliveries",
        json={
            "workspace_id": "workspace-a",
            "event_type": "claim.paid",
            "idempotency_key": "evt-1",
            "payload": {"amount": 100, "internal_trace": "hidden"},
        },
    )

    assert delivery.status_code == 200
    body = delivery.json()
    assert body["workspace_id"] == "workspace-a"
    assert body["subscription_id"] == workspace_a["id"]
    assert body["status"] == "delivered"
    assert "payload" not in body
    assert "callback_url" not in body
    assert "internal_trace" not in body


def test_delivery_rejects_disabled_subscription():
    client = make_client()
    client.post(
        "/api/v2/webhooks/subscriptions",
        json={
            "workspace_id": "workspace-a",
            "url": "https://example.com/hooks",
            "event_types": ["claim.paid"],
            "enabled": False,
        },
    )

    delivery = client.post(
        "/api/v2/webhooks/deliveries",
        json={
            "workspace_id": "workspace-a",
            "event_type": "claim.paid",
            "payload": {"amount": 100},
        },
    )

    assert delivery.status_code == 422
    assert "no enabled subscription" in delivery.json()["detail"]


def test_delivery_and_retry_are_idempotent_and_reject_rotated_endpoint():
    client = make_client()
    subscription = client.post(
        "/api/v2/webhooks/subscriptions",
        json={
            "workspace_id": "workspace-a",
            "url": "https://example.com/hooks",
            "event_types": ["claim.paid"],
        },
    ).json()
    delivery = client.post(
        "/api/v2/webhooks/deliveries",
        json={
            "workspace_id": "workspace-a",
            "event_type": "claim.paid",
            "idempotency_key": "evt-1",
            "payload": {"amount": 100},
        },
    ).json()
    duplicate_delivery = client.post(
        "/api/v2/webhooks/deliveries",
        json={
            "workspace_id": "workspace-a",
            "event_type": "claim.paid",
            "idempotency_key": "evt-1",
            "payload": {"amount": 999},
        },
    ).json()
    assert duplicate_delivery["id"] == delivery["id"]

    retry = client.post(
        f"/api/v2/webhooks/deliveries/{delivery['id']}/retry",
        json={"workspace_id": "workspace-a"},
    ).json()
    duplicate_retry = client.post(
        f"/api/v2/webhooks/deliveries/{delivery['id']}/retry",
        json={"workspace_id": "workspace-a"},
    ).json()
    assert duplicate_retry["retry_id"] == retry["retry_id"]
    assert duplicate_retry["attempt"] == 2

    rotated_delivery = client.post(
        "/api/v2/webhooks/deliveries",
        json={
            "workspace_id": "workspace-a",
            "event_type": "claim.paid",
            "idempotency_key": "evt-2",
            "payload": {"amount": 200},
        },
    ).json()
    client.patch(
        f"/api/v2/webhooks/subscriptions/{subscription['id']}",
        json={
            "workspace_id": "workspace-a",
            "url": "https://example.com/hooks-v2",
        },
    )
    rotated_retry = client.post(
        f"/api/v2/webhooks/deliveries/{rotated_delivery['id']}/retry",
        json={"workspace_id": "workspace-a"},
    )

    assert rotated_retry.status_code == 409
    assert "rotated" in rotated_retry.json()["detail"]
