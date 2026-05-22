import pytest

from src.orchestrator.webhooks import (
    WebhookEndpointRejected,
    WebhookEndpointValidator,
    WebhookRegistry,
)


class TestWebhookEndpointValidation:
    def test_rejects_localhost_before_persistence(self):
        registry = WebhookRegistry()

        with pytest.raises(WebhookEndpointRejected, match="Localhost"):
            registry.register_endpoint(
                "workspace-a",
                "http://localhost:9000/hook",
            )

        assert registry.endpoint_count() == 0

    @pytest.mark.parametrize(
        "target_url",
        [
            "http://127.0.0.1:9000/hook",
            "http://[::1]:9000/hook",
            "https://localhost./hook",
        ],
    )
    def test_rejects_loopback_aliases(self, target_url):
        with pytest.raises(WebhookEndpointRejected):
            WebhookEndpointValidator.validate(target_url)

    def test_allows_localhost_when_explicitly_allowed(self):
        registry = WebhookRegistry()

        endpoint = registry.register_endpoint(
            "workspace-a",
            "http://localhost:9000/hook",
            allow_localhost=True,
        )

        assert endpoint.allow_localhost
        assert registry.endpoint_count() == 1


class TestWebhookDelivery:
    def test_valid_delivery_records_public_payload_once(self):
        registry = WebhookRegistry()
        endpoint = registry.register_endpoint(
            "workspace-a",
            "https://example.com/hook",
        )
        callbacks = []

        record = registry.deliver(
            "workspace-a",
            endpoint.id,
            "event-1",
            {
                "event": "run.completed",
                "run_id": "run-1",
                "internal_worker": "worker-7",
                "_trace": "secret-debug",
            },
            callback=lambda url, payload: callbacks.append((url, payload)),
        )

        assert record.status == "delivered"
        assert record.payload == {"event": "run.completed", "run_id": "run-1"}
        assert callbacks == [("https://example.com/hook", record.payload)]

    def test_retry_returns_existing_delivery_without_callback(self):
        registry = WebhookRegistry()
        endpoint = registry.register_endpoint(
            "workspace-a",
            "https://example.com/hook",
        )
        callbacks = []

        first = registry.deliver(
            "workspace-a",
            endpoint.id,
            "event-1",
            {"event": "run.completed"},
            callback=lambda url, payload: callbacks.append((url, payload)),
        )
        second = registry.deliver(
            "workspace-a",
            endpoint.id,
            "event-1",
            {"event": "run.completed"},
            callback=lambda url, payload: callbacks.append((url, payload)),
        )

        assert second == first
        assert registry.delivery_count() == 1
        assert len(callbacks) == 1

    def test_workspace_isolation_rejects_cross_workspace_delivery(self):
        registry = WebhookRegistry()
        endpoint = registry.register_endpoint(
            "workspace-a",
            "https://example.com/hook",
        )

        with pytest.raises(WebhookEndpointRejected, match="unavailable"):
            registry.deliver(
                "workspace-b",
                endpoint.id,
                "event-1",
                {"event": "run.completed"},
            )

        assert registry.delivery_count() == 0

    def test_disabled_endpoint_rejects_without_delivery_record(self):
        registry = WebhookRegistry()
        endpoint = registry.register_endpoint(
            "workspace-a",
            "https://example.com/hook",
        )

        assert registry.disable_endpoint(endpoint.id)
        with pytest.raises(WebhookEndpointRejected, match="disabled"):
            registry.deliver(
                "workspace-a",
                endpoint.id,
                "event-1",
                {"event": "run.completed"},
            )

        assert registry.delivery_count() == 0
