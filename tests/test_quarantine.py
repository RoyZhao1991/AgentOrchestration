from datetime import datetime, timedelta, timezone

import pytest

from src.common.quarantine import (
    QuarantineAccessError,
    FailedValidationQuarantineStore,
)


def test_quarantine_records_get_expiration_timestamp():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = FailedValidationQuarantineStore(retention=timedelta(hours=2))

    record = store.add(
        {"task": "bad"},
        reason="schema mismatch",
        received_at=now,
    )

    assert record.received_at == now
    assert record.expires_at == now + timedelta(hours=2)


def test_cleanup_removes_expired_validation_failures():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = FailedValidationQuarantineStore(retention=timedelta(hours=1))
    expired = store.add({"task": "old"}, "schema mismatch", received_at=now)
    active = store.add(
        {"task": "new"},
        "schema mismatch",
        received_at=now + timedelta(hours=2),
    )

    removed = store.cleanup_expired(now + timedelta(hours=1, seconds=1))

    assert removed == [expired.id]
    assert store.get(expired.id) is None
    assert store.get(active.id) == active


def test_debug_summary_redacts_sensitive_payload_by_default():
    store = FailedValidationQuarantineStore()
    record = store.add(
        {
            "task": "deploy",
            "api_key": "live-key",
            "nested": {"password": "secret-value", "safe": "visible"},
        },
        "schema mismatch",
    )

    summary = store.debug_summary(record.id)

    assert summary["payload"]["api_key"] == "[REDACTED]"
    assert summary["payload"]["nested"]["password"] == "[REDACTED]"
    assert summary["payload"]["nested"]["safe"] == "visible"
    assert "live-key" not in str(summary)
    assert "secret-value" not in str(summary)


def test_raw_payload_requires_allowed_role():
    store = FailedValidationQuarantineStore(default_access_roles=("security",))
    record = store.add({"token": "raw-token"}, "schema mismatch")

    with pytest.raises(QuarantineAccessError):
        store.raw_payload(record.id, role="viewer")

    assert store.raw_payload(
        record.id,
        role="security",
    ) == {"token": "raw-token"}


def test_debug_summaries_group_safe_record_metadata():
    store = FailedValidationQuarantineStore()
    first = store.add({"secret": "one"}, "schema mismatch")
    second = store.add({"token": "two"}, "missing field")

    summaries = store.debug_summaries()

    assert [summary["id"] for summary in summaries] == [first.id, second.id]
    assert [summary["reason"] for summary in summaries] == [
        "schema mismatch",
        "missing field",
    ]
    assert all(
        "[REDACTED]" in str(summary["payload"])
        for summary in summaries
    )
