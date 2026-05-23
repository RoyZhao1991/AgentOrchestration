"""Webhook endpoint registration and safe public delivery."""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple
from urllib.parse import urlparse


INTERNAL_ONLY_FIELDS = {
    "authorization",
    "callback_token",
    "debug_context",
    "executor_host",
    "headers",
    "internal_metadata",
    "internal_run_id",
    "internal_task_id",
    "private_metadata",
    "raw_payload",
    "retry_token",
    "secret",
    "secrets",
    "span_id",
    "tenant_id",
    "trace_id",
    "worker_host",
    "worker_pid",
    "workspace_internal_id",
}


class WebhookValidationError(ValueError):
    """Raised when a webhook endpoint or delivery is unsafe."""


@dataclass(frozen=True)
class WebhookEndpoint:
    id: str
    workspace_id: str
    url: str
    event_types: Tuple[str, ...]
    secret_version: str
    enabled: bool = True
    disabled_reason: Optional[str] = None
    replaces_endpoint_id: Optional[str] = None


@dataclass(frozen=True)
class WebhookDeliveryResponse:
    status_code: int


@dataclass
class WebhookDeliveryRecord:
    delivery_id: str
    endpoint_id: str
    workspace_id: str
    event_type: str
    event_id: str
    status: str
    payload: Dict[str, Any]
    attempts: int = 1
    response_status: Optional[int] = None
    error: Optional[str] = None


WebhookSender = Callable[[str, Dict[str, Any]], Optional[object]]


def shape_public_event_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a public event payload without internal-only metadata."""

    def scrub(value: Any) -> Any:
        if isinstance(value, Mapping):
            shaped = {}
            for key, nested in value.items():
                if _is_internal_key(str(key)):
                    continue
                shaped[key] = scrub(nested)
            return shaped
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, tuple):
            return [scrub(item) for item in value]
        return value

    return scrub(payload)


class WebhookManager:
    """Stores workspace-scoped webhook endpoints and idempotent deliveries."""

    def __init__(self) -> None:
        self._endpoints: Dict[str, WebhookEndpoint] = {}
        self._deliveries: Dict[str, WebhookDeliveryRecord] = {}

    def register_endpoint(
        self,
        workspace_id: str,
        url: str,
        event_types: Iterable[str],
        secret_version: str = "v1",
    ) -> WebhookEndpoint:
        workspace_id = _require_non_blank(workspace_id, "workspace_id")
        safe_url = _validate_endpoint_url(url)
        events = _normalize_events(event_types)
        endpoint = WebhookEndpoint(
            id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            url=safe_url,
            event_types=events,
            secret_version=_require_non_blank(
                secret_version,
                "secret_version",
            ),
        )
        self._endpoints[endpoint.id] = endpoint
        return endpoint

    def rotate_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
        url: str,
        secret_version: str,
    ) -> WebhookEndpoint:
        current = self._require_endpoint(
            workspace_id=workspace_id,
            endpoint_id=endpoint_id,
        )
        self._disable_endpoint(
            current,
            reason="rotated",
        )
        rotated = self.register_endpoint(
            workspace_id=workspace_id,
            url=url,
            event_types=current.event_types,
            secret_version=secret_version,
        )
        self._endpoints[rotated.id] = replace(
            rotated,
            replaces_endpoint_id=endpoint_id,
        )
        return self._endpoints[rotated.id]

    def disable_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
        reason: str = "manual",
    ) -> None:
        endpoint = self._require_endpoint(
            workspace_id=workspace_id,
            endpoint_id=endpoint_id,
        )
        self._disable_endpoint(endpoint, reason=reason)

    def deliver(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_type: str,
        event_id: str,
        payload: Mapping[str, Any],
        sender: Optional[WebhookSender] = None,
    ) -> WebhookDeliveryRecord:
        workspace_id = _require_non_blank(workspace_id, "workspace_id")
        event_type = _require_non_blank(event_type, "event_type")
        event_id = _require_non_blank(event_id, "event_id")
        delivery_key = self._delivery_key(endpoint_id, event_type, event_id)
        if delivery_key in self._deliveries:
            record = self._deliveries[delivery_key]
            if record.workspace_id != workspace_id:
                raise WebhookValidationError(
                    "webhook endpoint workspace mismatch"
                )
            return record

        endpoint = self._require_endpoint(
            workspace_id=workspace_id,
            endpoint_id=endpoint_id,
            event_type=event_type,
        )

        public_payload = shape_public_event_payload(payload)
        record = WebhookDeliveryRecord(
            delivery_id=str(uuid.uuid4()),
            endpoint_id=endpoint.id,
            workspace_id=endpoint.workspace_id,
            event_type=event_type,
            event_id=event_id,
            status="pending",
            payload=public_payload,
        )
        self._deliveries[delivery_key] = record

        try:
            response = sender(endpoint.url, public_payload) if sender else None
            status_code = _coerce_status_code(response)
        except Exception as exc:
            record.status = "failed"
            record.error = str(exc)
            return record

        record.response_status = status_code
        if status_code == 410:
            record.status = "gone"
            record.error = "endpoint returned 410 Gone"
            self._disable_endpoint(endpoint, reason="http_410_gone")
            return record
        if 200 <= status_code < 300:
            record.status = "delivered"
            return record

        record.status = "failed"
        record.error = f"endpoint returned HTTP {status_code}"
        return record

    def get_delivery(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_type: str,
        event_id: str,
    ) -> Optional[WebhookDeliveryRecord]:
        key = self._delivery_key(endpoint_id, event_type, event_id)
        record = self._deliveries.get(key)
        if record is None or record.workspace_id != workspace_id:
            return None
        return record

    def get_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
    ) -> Optional[WebhookEndpoint]:
        endpoint = self._endpoints.get(endpoint_id)
        if endpoint is None or endpoint.workspace_id != workspace_id:
            return None
        return endpoint

    def endpoint_count(self) -> int:
        return len(self._endpoints)

    def delivery_count(self) -> int:
        return len(self._deliveries)

    def _require_endpoint(
        self,
        workspace_id: str,
        endpoint_id: str,
        event_type: Optional[str] = None,
    ) -> WebhookEndpoint:
        workspace_id = _require_non_blank(workspace_id, "workspace_id")
        endpoint = self._endpoints.get(endpoint_id)
        if endpoint is None:
            raise WebhookValidationError("webhook endpoint not found")
        if endpoint.workspace_id != workspace_id:
            raise WebhookValidationError("webhook endpoint workspace mismatch")
        if not endpoint.enabled:
            raise WebhookValidationError("webhook endpoint is disabled")
        if event_type is not None:
            safe_event = _require_non_blank(event_type, "event_type")
            if safe_event not in endpoint.event_types:
                raise WebhookValidationError("webhook endpoint is not scoped")
        return endpoint

    def _disable_endpoint(
        self,
        endpoint: WebhookEndpoint,
        reason: str,
    ) -> None:
        self._endpoints[endpoint.id] = replace(
            endpoint,
            enabled=False,
            disabled_reason=_require_non_blank(reason, "reason"),
        )

    @staticmethod
    def _delivery_key(
        endpoint_id: str,
        event_type: str,
        event_id: str,
    ) -> str:
        return f"{endpoint_id}:{event_type}:{event_id}"


def _coerce_status_code(response: Optional[object]) -> int:
    if response is None:
        return 200
    if isinstance(response, WebhookDeliveryResponse):
        return response.status_code
    if isinstance(response, int):
        return response
    if isinstance(response, Mapping) and "status_code" in response:
        return int(response["status_code"])
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        return int(status_code)
    raise WebhookValidationError("sender response status_code is required")


def _is_internal_key(key: str) -> bool:
    return key.startswith("_") or key.lower() in INTERNAL_ONLY_FIELDS


def _normalize_events(event_types: Iterable[str]) -> Tuple[str, ...]:
    events = tuple(
        _require_non_blank(event_type, "event_type")
        for event_type in event_types
    )
    if not events:
        raise WebhookValidationError("at least one event_type is required")
    return events


def _require_non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WebhookValidationError(f"{field_name} is required")
    return value.strip()


def _validate_endpoint_url(url: str) -> str:
    safe_url = _require_non_blank(url, "url")
    parsed = urlparse(safe_url)
    if parsed.scheme != "https":
        raise WebhookValidationError("webhook endpoints must use https")
    if not parsed.netloc:
        raise WebhookValidationError("webhook endpoint host is required")
    hostname = parsed.hostname or ""
    if hostname.lower() == "localhost" or hostname.endswith(".local"):
        raise WebhookValidationError("webhook endpoint host is not public")
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return safe_url
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        raise WebhookValidationError("webhook endpoint host is not public")
    return safe_url
