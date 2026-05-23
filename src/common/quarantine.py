"""Failed validation quarantine store with retention and safe debug views."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


REDACTED = "[REDACTED]"
SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)


class QuarantineAccessError(PermissionError):
    """Raised when raw quarantined payload access is not allowed."""


@dataclass(frozen=True)
class QuarantineRecord:
    id: str
    reason: str
    payload: Mapping[str, Any]
    received_at: datetime
    expires_at: datetime
    access_roles: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def expired(self) -> bool:
        return self.expires_at <= datetime.now(timezone.utc)


class FailedValidationQuarantineStore:
    def __init__(
        self,
        retention: timedelta = timedelta(days=7),
        default_access_roles: Iterable[str] = ("security", "ops"),
    ):
        if retention <= timedelta(0):
            raise ValueError("retention must be positive")
        self.retention = retention
        self.default_access_roles = tuple(default_access_roles)
        self._records: Dict[str, QuarantineRecord] = {}

    def add(
        self,
        payload: Mapping[str, Any],
        reason: str,
        received_at: Optional[datetime] = None,
        retention: Optional[timedelta] = None,
        access_roles: Optional[Iterable[str]] = None,
    ) -> QuarantineRecord:
        if not reason.strip():
            raise ValueError("reason is required")

        effective_retention = retention or self.retention
        if effective_retention <= timedelta(0):
            raise ValueError("retention must be positive")

        timestamp = received_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        record = QuarantineRecord(
            id=str(uuid.uuid4()),
            reason=reason,
            payload=dict(payload),
            received_at=timestamp,
            expires_at=timestamp + effective_retention,
            access_roles=tuple(access_roles or self.default_access_roles),
        )
        self._records[record.id] = record
        return record

    def get(self, record_id: str) -> Optional[QuarantineRecord]:
        return self._records.get(record_id)

    def cleanup_expired(self, now: Optional[datetime] = None) -> List[str]:
        timestamp = now or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        expired_ids = [
            record_id
            for record_id, record in self._records.items()
            if record.expires_at <= timestamp
        ]
        for record_id in expired_ids:
            self._records.pop(record_id, None)
        return expired_ids

    def debug_summary(self, record_id: str) -> Optional[Dict[str, Any]]:
        record = self.get(record_id)
        if record is None:
            return None
        return self._summarize(record)

    def debug_summaries(self) -> List[Dict[str, Any]]:
        return [self._summarize(record) for record in self._records.values()]

    def raw_payload(self, record_id: str, role: str) -> Mapping[str, Any]:
        record = self.get(record_id)
        if record is None:
            raise KeyError(record_id)
        if role not in record.access_roles:
            raise QuarantineAccessError(
                f"role {role!r} cannot access raw quarantined payload"
            )
        return dict(record.payload)

    def _summarize(self, record: QuarantineRecord) -> Dict[str, Any]:
        return {
            "id": record.id,
            "reason": record.reason,
            "received_at": record.received_at.isoformat(),
            "expires_at": record.expires_at.isoformat(),
            "access_roles": list(record.access_roles),
            "payload": _redact(record.payload),
        }


def _redact(value: Any, key: str = "") -> Any:
    if _sensitive_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {child_key: _redact(child_value, child_key)
                for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    return value


def _sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)
