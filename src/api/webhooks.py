"""Webhook security and idempotent delivery helpers."""

import hashlib
import hmac
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Tuple
from urllib.parse import urlparse


INTERNAL_PAYLOAD_KEYS = {
    "debug",
    "execution_id",
    "hostname",
    "internal",
    "internal_metadata",
    "internal_run_id",
    "password",
    "secret",
    "stack_trace",
    "token",
    "trace_id",
}


class WebhookSecurityError(ValueError):
    """Raised when webhook trust checks reject a request or delivery."""


class EndpointStatus(Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True)
class WebhookEndpoint:
    endpoint_id: str
    workspace_id: str
    url: str
    secret: str
    status: EndpointStatus = EndpointStatus.ACTIVE
    rotation: int = 0


@dataclass(frozen=True)
class DeliveryRecord:
    endpoint_id: str
    workspace_id: str
    event_id: str
    idempotency_key: str
    payload: Mapping[str, Any]
    callback_status: str


class WebhookSecurityGateway:
    """Validates webhook trust before parsing, persistence, or callbacks."""

    def __init__(self, replay_window_seconds: int = 300):
        if replay_window_seconds <= 0:
            raise ValueError("replay_window_seconds must be positive")
        self.replay_window_seconds = replay_window_seconds
        self._endpoints: Dict[str, WebhookEndpoint] = {}
        self._seen_signatures = set()
        self._delivery_records: Dict[str, DeliveryRecord] = {}

    def register_endpoint(
        self,
        endpoint_id: str,
        workspace_id: str,
        url: str,
        secret: str,
    ) -> WebhookEndpoint:
        self._validate_endpoint(endpoint_id, workspace_id, url, secret)
        endpoint = WebhookEndpoint(
            endpoint_id=endpoint_id,
            workspace_id=workspace_id,
            url=url,
            secret=secret,
        )
        self._endpoints[endpoint_id] = endpoint
        return endpoint

    def disable_endpoint(self, endpoint_id: str) -> WebhookEndpoint:
        endpoint = self._endpoints[endpoint_id]
        disabled = WebhookEndpoint(
            endpoint_id=endpoint.endpoint_id,
            workspace_id=endpoint.workspace_id,
            url=endpoint.url,
            secret=endpoint.secret,
            status=EndpointStatus.DISABLED,
            rotation=endpoint.rotation,
        )
        self._endpoints[endpoint_id] = disabled
        return disabled

    def rotate_endpoint_secret(
        self,
        endpoint_id: str,
        new_secret: str,
    ) -> WebhookEndpoint:
        if not new_secret:
            raise WebhookSecurityError("webhook secret is required")
        endpoint = self._endpoints[endpoint_id]
        rotated = WebhookEndpoint(
            endpoint_id=endpoint.endpoint_id,
            workspace_id=endpoint.workspace_id,
            url=endpoint.url,
            secret=new_secret,
            status=endpoint.status,
            rotation=endpoint.rotation + 1,
        )
        self._endpoints[endpoint_id] = rotated
        self._seen_signatures = {
            seen
            for seen in self._seen_signatures
            if not seen.startswith(f"{endpoint_id}:")
        }
        return rotated

    def sign(self, endpoint_id: str, timestamp: int, body: bytes) -> str:
        endpoint = self._endpoints[endpoint_id]
        return self._signature(endpoint.secret, timestamp, body)

    def verify_signature(
        self,
        endpoint_id: str,
        workspace_id: str,
        timestamp: int,
        signature: str,
        body: bytes,
        now: Optional[int] = None,
    ) -> bool:
        endpoint = self._trusted_endpoint(endpoint_id, workspace_id)
        checked_at = int(time.time()) if now is None else now
        if abs(checked_at - timestamp) > self.replay_window_seconds:
            raise WebhookSecurityError(
                "webhook signature outside replay window"
            )

        replay_key = (
            f"{endpoint_id}:{endpoint.rotation}:{timestamp}:{signature}"
        )
        if replay_key in self._seen_signatures:
            raise WebhookSecurityError("webhook signature replay detected")

        expected = self._signature(endpoint.secret, timestamp, body)
        if not hmac.compare_digest(signature, expected):
            raise WebhookSecurityError("webhook signature mismatch")

        self._seen_signatures.add(replay_key)
        return True

    def deliver(
        self,
        endpoint_id: str,
        workspace_id: str,
        event_id: str,
        payload: Mapping[str, Any],
        callback: Optional[Callable[[str, Mapping[str, Any]], None]] = None,
    ) -> DeliveryRecord:
        endpoint = self._trusted_endpoint(endpoint_id, workspace_id)
        idempotency_key = f"{endpoint.endpoint_id}:{event_id}"
        existing = self._delivery_records.get(idempotency_key)
        if existing:
            return existing

        public_payload = self._public_payload(payload)
        if callback:
            callback(endpoint.url, public_payload)
        record = DeliveryRecord(
            endpoint_id=endpoint.endpoint_id,
            workspace_id=endpoint.workspace_id,
            event_id=event_id,
            idempotency_key=idempotency_key,
            payload=public_payload,
            callback_status="delivered",
        )
        self._delivery_records[idempotency_key] = record
        return record

    def delivery_records(self) -> Tuple[DeliveryRecord, ...]:
        return tuple(self._delivery_records.values())

    def _trusted_endpoint(
        self,
        endpoint_id: str,
        workspace_id: str,
    ) -> WebhookEndpoint:
        endpoint = self._endpoints.get(endpoint_id)
        if endpoint is None:
            raise WebhookSecurityError("webhook endpoint is not registered")
        if endpoint.workspace_id != workspace_id:
            raise WebhookSecurityError("webhook endpoint workspace mismatch")
        if endpoint.status != EndpointStatus.ACTIVE:
            raise WebhookSecurityError("webhook endpoint is disabled")
        return endpoint

    def _validate_endpoint(
        self,
        endpoint_id: str,
        workspace_id: str,
        url: str,
        secret: str,
    ) -> None:
        if not endpoint_id or not workspace_id or not secret:
            raise WebhookSecurityError(
                "endpoint id, workspace, and secret are required"
            )
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise WebhookSecurityError("webhook endpoint must use https")
        host = (parsed.hostname or "").lower()
        if host in {"localhost", "127.0.0.1", "::1"}:
            raise WebhookSecurityError(
                "webhook endpoint must be publicly routable"
            )

    def _signature(self, secret: str, timestamp: int, body: bytes) -> str:
        signed = f"{timestamp}.".encode("utf-8") + body
        return hmac.new(
            secret.encode("utf-8"),
            signed,
            hashlib.sha256,
        ).hexdigest()

    def _public_payload(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            key: self._public_value(value)
            for key, value in payload.items()
            if not self._internal_key(key)
        }

    def _public_value(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return self._public_payload(value)
        if isinstance(value, list):
            return [self._public_value(item) for item in value]
        return value

    def _internal_key(self, key: str) -> bool:
        normalized = key.lower()
        return (
            normalized.startswith("_")
            or normalized in INTERNAL_PAYLOAD_KEYS
        )
