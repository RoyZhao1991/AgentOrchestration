"""Webhook endpoint validation and idempotent delivery helpers."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse
from uuid import uuid4
import ipaddress


class WebhookEndpointRejected(ValueError):
    """Raised when a webhook endpoint is unsafe or unavailable."""


@dataclass(frozen=True)
class WebhookEndpoint:
    id: str
    workspace_id: str
    target_url: str
    allow_localhost: bool = False
    disabled: bool = False


@dataclass(frozen=True)
class WebhookDeliveryRecord:
    id: str
    workspace_id: str
    endpoint_id: str
    event_id: str
    target_url: str
    payload: Dict[str, Any]
    status: str


class WebhookEndpointValidator:
    @classmethod
    def validate(cls, target_url: str, allow_localhost: bool = False) -> str:
        parsed = urlparse(target_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise WebhookEndpointRejected(
                "Webhook endpoint must be an HTTP URL",
            )

        host = parsed.hostname
        if not host:
            raise WebhookEndpointRejected("Webhook endpoint host is required")

        if not allow_localhost and cls._is_localhost(host):
            raise WebhookEndpointRejected(
                "Localhost webhook endpoints require explicit allow_localhost",
            )

        return target_url

    @staticmethod
    def _is_localhost(host: str) -> bool:
        normalized = host.strip().lower().rstrip(".")
        if normalized == "localhost":
            return True

        try:
            return ipaddress.ip_address(normalized).is_loopback
        except ValueError:
            return False


class WebhookRegistry:
    def __init__(self):
        self._endpoints: Dict[str, WebhookEndpoint] = {}
        self._deliveries: Dict[
            Tuple[str, str, str],
            WebhookDeliveryRecord,
        ] = {}

    def register_endpoint(
        self,
        workspace_id: str,
        target_url: str,
        allow_localhost: bool = False,
    ) -> WebhookEndpoint:
        WebhookEndpointValidator.validate(
            target_url,
            allow_localhost=allow_localhost,
        )
        endpoint = WebhookEndpoint(
            id=str(uuid4()),
            workspace_id=workspace_id,
            target_url=target_url,
            allow_localhost=allow_localhost,
        )
        self._endpoints[endpoint.id] = endpoint
        return endpoint

    def disable_endpoint(self, endpoint_id: str) -> bool:
        endpoint = self._endpoints.get(endpoint_id)
        if endpoint is None:
            return False

        self._endpoints[endpoint_id] = WebhookEndpoint(
            id=endpoint.id,
            workspace_id=endpoint.workspace_id,
            target_url=endpoint.target_url,
            allow_localhost=endpoint.allow_localhost,
            disabled=True,
        )
        return True

    def deliver(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_id: str,
        payload: Dict[str, Any],
        callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> WebhookDeliveryRecord:
        endpoint = self._endpoint_for_workspace(workspace_id, endpoint_id)
        if endpoint.disabled:
            raise WebhookEndpointRejected("Webhook endpoint is disabled")

        key = (workspace_id, endpoint_id, event_id)
        existing = self._deliveries.get(key)
        if existing is not None:
            return existing

        public_payload = self._public_payload(payload)
        record = WebhookDeliveryRecord(
            id=str(uuid4()),
            workspace_id=workspace_id,
            endpoint_id=endpoint_id,
            event_id=event_id,
            target_url=endpoint.target_url,
            payload=public_payload,
            status="delivered",
        )
        self._deliveries[key] = record

        if callback is not None:
            callback(endpoint.target_url, dict(public_payload))

        return record

    def delivery_count(self) -> int:
        return len(self._deliveries)

    def endpoint_count(self) -> int:
        return len(self._endpoints)

    def _endpoint_for_workspace(
        self,
        workspace_id: str,
        endpoint_id: str,
    ) -> WebhookEndpoint:
        endpoint = self._endpoints.get(endpoint_id)
        if endpoint is None or endpoint.workspace_id != workspace_id:
            raise WebhookEndpointRejected("Webhook endpoint is unavailable")
        return endpoint

    def _public_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value
            for key, value in payload.items()
            if not key.startswith("_") and not key.startswith("internal_")
        }
