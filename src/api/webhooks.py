"""Webhook registration and fanout controls."""

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from uuid import uuid4

INTERNAL_FIELD_NAMES = {
    "internal_metadata",
    "internal_run_id",
    "private_context",
    "queue_slot",
    "raw_payload",
    "retry_state",
    "runner_token",
    "secret",
    "token",
    "worker_pid",
}
INTERNAL_FIELD_PREFIXES = ("_", "internal_", "private_")


class WebhookError(ValueError):
    """Raised when webhook registration or delivery is invalid."""


@dataclass
class WebhookEndpoint:
    id: str
    workspace_id: str
    url: str
    event_types: Tuple[str, ...]
    rate_limit_per_minute: int = 60
    enabled: bool = True
    version: int = 1
    disabled_reason: Optional[str] = None
    created_at: float = field(default_factory=time.time)


@dataclass
class WebhookDeliveryRecord:
    delivery_id: str
    endpoint_id: str
    workspace_id: str
    event_id: str
    event_type: str
    status: str
    payload: Dict[str, Any]
    attempts: int
    reason: Optional[str] = None
    retry_after: Optional[float] = None
    timestamp: float = field(default_factory=time.time)


class WebhookFanoutDispatcher:
    def __init__(self, clock: Optional[Callable[[], float]] = None):
        self._clock = clock or time.time
        self._endpoints: Dict[str, WebhookEndpoint] = {}
        self._delivery_records: Dict[
            Tuple[str, str],
            WebhookDeliveryRecord,
        ] = {}
        self._rate_windows: Dict[str, List[float]] = {}

    def register_endpoint(
        self,
        workspace_id: str,
        url: str,
        event_types: List[str],
        rate_limit_per_minute: int = 60,
        endpoint_id: Optional[str] = None,
    ) -> WebhookEndpoint:
        self._validate_workspace(workspace_id)
        self._validate_url(url)
        events = self._validate_event_types(event_types)
        if rate_limit_per_minute < 1:
            raise WebhookError("rate_limit_per_minute must be positive")

        endpoint = WebhookEndpoint(
            id=endpoint_id or str(uuid4()),
            workspace_id=workspace_id,
            url=url,
            event_types=events,
            rate_limit_per_minute=rate_limit_per_minute,
            created_at=self._clock(),
        )
        self._endpoints[endpoint.id] = endpoint
        self._rate_windows.setdefault(endpoint.id, [])
        return endpoint

    def disable_endpoint(
        self,
        endpoint_id: str,
        workspace_id: str,
        reason: str = "disabled",
    ) -> None:
        endpoint = self._get_scoped_endpoint(endpoint_id, workspace_id)
        endpoint.enabled = False
        endpoint.disabled_reason = reason
        self._rate_windows.pop(endpoint.id, None)

    def rotate_endpoint(
        self,
        endpoint_id: str,
        workspace_id: str,
        new_url: str,
    ) -> WebhookEndpoint:
        endpoint = self._get_scoped_endpoint(endpoint_id, workspace_id)
        self.disable_endpoint(endpoint_id, workspace_id, "rotated")
        return self.register_endpoint(
            workspace_id=workspace_id,
            url=new_url,
            event_types=list(endpoint.event_types),
            rate_limit_per_minute=endpoint.rate_limit_per_minute,
            endpoint_id=str(uuid4()),
        )

    def deliver(
        self,
        workspace_id: str,
        event_id: str,
        event_type: str,
        payload: Dict[str, Any],
        callback: Callable[[WebhookEndpoint, Dict[str, Any]], Any],
    ) -> List[WebhookDeliveryRecord]:
        self._validate_workspace(workspace_id)
        if not event_id:
            raise WebhookError("event_id is required")
        if not event_type:
            raise WebhookError("event_type is required")

        records: List[WebhookDeliveryRecord] = []
        endpoints = [
            endpoint
            for endpoint in self._endpoints.values()
            if endpoint.workspace_id == workspace_id
            and endpoint.enabled
            and event_type in endpoint.event_types
        ]

        for endpoint in sorted(endpoints, key=lambda item: item.id):
            record_key = (endpoint.id, event_id)
            if record_key in self._delivery_records:
                records.append(self._delivery_records[record_key])
                continue

            retry_after = self._reserve_rate_limit(endpoint)
            public_payload = sanitize_public_payload(payload)
            if retry_after is not None:
                record = self._record_delivery(
                    endpoint=endpoint,
                    event_id=event_id,
                    event_type=event_type,
                    status="rate_limited",
                    payload=public_payload,
                    attempts=0,
                    reason="endpoint_rate_limit_exceeded",
                    retry_after=retry_after,
                )
                records.append(record)
                continue

            try:
                callback(endpoint, public_payload)
            except Exception as exc:
                record = self._record_delivery(
                    endpoint=endpoint,
                    event_id=event_id,
                    event_type=event_type,
                    status="failed",
                    payload=public_payload,
                    attempts=1,
                    reason=str(exc),
                )
            else:
                record = self._record_delivery(
                    endpoint=endpoint,
                    event_id=event_id,
                    event_type=event_type,
                    status="delivered",
                    payload=public_payload,
                    attempts=1,
                )
            records.append(record)

        return records

    def _reserve_rate_limit(
        self,
        endpoint: WebhookEndpoint,
    ) -> Optional[float]:
        now = self._clock()
        window_start = now - 60
        window = [
            timestamp
            for timestamp in self._rate_windows.setdefault(endpoint.id, [])
            if timestamp > window_start
        ]
        self._rate_windows[endpoint.id] = window
        if len(window) >= endpoint.rate_limit_per_minute:
            oldest = min(window)
            return max(0.0, 60 - (now - oldest))
        window.append(now)
        return None

    def _record_delivery(
        self,
        endpoint: WebhookEndpoint,
        event_id: str,
        event_type: str,
        status: str,
        payload: Dict[str, Any],
        attempts: int,
        reason: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> WebhookDeliveryRecord:
        record = WebhookDeliveryRecord(
            delivery_id=str(uuid4()),
            endpoint_id=endpoint.id,
            workspace_id=endpoint.workspace_id,
            event_id=event_id,
            event_type=event_type,
            status=status,
            payload=payload,
            attempts=attempts,
            reason=reason,
            retry_after=retry_after,
            timestamp=self._clock(),
        )
        self._delivery_records[(endpoint.id, event_id)] = record
        return record

    def _get_scoped_endpoint(
        self,
        endpoint_id: str,
        workspace_id: str,
    ) -> WebhookEndpoint:
        endpoint = self._endpoints.get(endpoint_id)
        if endpoint is None or endpoint.workspace_id != workspace_id:
            raise WebhookError("endpoint not found for workspace")
        return endpoint

    def _validate_workspace(self, workspace_id: str) -> None:
        if not workspace_id:
            raise WebhookError("workspace_id is required")

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise WebhookError("webhook endpoint must be an HTTPS URL")

    def _validate_event_types(self, event_types: List[str]) -> Tuple[str, ...]:
        events = tuple(event for event in event_types if event)
        if not events:
            raise WebhookError("at least one event type is required")
        return events


def sanitize_public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        public = {}
        for key, child in value.items():
            key_text = str(key)
            if _is_internal_field(key_text):
                continue
            public[key_text] = sanitize_public_payload(child)
        return public
    if isinstance(value, list):
        return [sanitize_public_payload(child) for child in value]
    return value


def _is_internal_field(key: str) -> bool:
    normalized = key.lower()
    return normalized in INTERNAL_FIELD_NAMES or normalized.startswith(
        INTERNAL_FIELD_PREFIXES
    )
