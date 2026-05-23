import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware import (
    LoggingMiddleware,
    RequestContextMiddleware,
    get_request_context,
)


def _app(calls=None):
    app = FastAPI()
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/context")
    async def context_endpoint():
        if calls is not None:
            calls.append("context")
        context = get_request_context()
        return {
            "correlation_id": context.correlation_id,
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "role": context.role,
            "scope_digest": context.scope_digest,
        }

    @app.get("/boom")
    async def boom_endpoint():
        context = get_request_context()
        assert context.tenant_id == "tenant-a"
        raise RuntimeError("boom")

    return app


def test_normal_request_sets_context_headers_and_resets_state():
    client = TestClient(_app())

    response = client.get(
        "/context",
        headers={
            "X-Correlation-ID": "corr-a",
            "X-Request-ID": "req-a",
            "X-Tenant-ID": "tenant-a",
            "X-Role": "admin",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["correlation_id"] == "corr-a"
    assert payload["request_id"] == "req-a"
    assert payload["tenant_id"] == "tenant-a"
    assert payload["role"] == "admin"
    assert response.headers["X-Correlation-ID"] == "corr-a"
    assert response.headers["X-Request-ID"] == "req-a"
    assert response.headers["X-Context-Scope"] == payload["scope_digest"]
    assert "tenant-a" not in response.headers["X-Context-Scope"]
    assert get_request_context() is None


def test_rejected_workspace_mismatch_does_not_call_handler():
    calls = []
    client = TestClient(_app(calls))

    response = client.get(
        "/context",
        headers={
            "X-Correlation-ID": "corr-b",
            "X-Tenant-ID": "tenant-a",
            "X-Workspace-ID": "tenant-b",
            "X-Role": "viewer",
        },
    )

    assert response.status_code == 403
    assert response.headers["X-Correlation-ID"] == "corr-b"
    assert response.headers["X-Context-Decision"] == "rejected"
    assert calls == []
    assert get_request_context() is None


def test_rejected_role_logs_sanitized_scope(caplog):
    client = TestClient(_app())

    with caplog.at_level(logging.WARNING, logger="src.api.middleware"):
        response = client.get(
            "/context",
            headers={
                "X-Correlation-ID": "corr-c",
                "X-Tenant-ID": "tenant-secret",
                "X-Role": "root",
            },
        )

    assert response.status_code == 403
    assert response.headers["X-Context-Decision"] == "rejected"
    assert "tenant-secret" not in caplog.text
    assert any(
        getattr(record, "context_scope", None)
        for record in caplog.records
    )
    assert get_request_context() is None


def test_exception_path_returns_context_headers_and_resets_state():
    client = TestClient(_app(), raise_server_exceptions=False)

    response = client.get(
        "/boom",
        headers={
            "X-Correlation-ID": "corr-d",
            "X-Request-ID": "req-d",
            "X-Tenant-ID": "tenant-a",
            "X-Role": "operator",
        },
    )

    assert response.status_code == 500
    assert response.headers["X-Correlation-ID"] == "corr-d"
    assert response.headers["X-Request-ID"] == "req-d"
    assert get_request_context() is None


def test_sequential_requests_keep_tenant_context_isolated():
    client = TestClient(_app())

    first = client.get(
        "/context",
        headers={
            "X-Correlation-ID": "same-correlation",
            "X-Tenant-ID": "tenant-a",
            "X-Role": "viewer",
        },
    )
    second = client.get(
        "/context",
        headers={
            "X-Correlation-ID": "same-correlation",
            "X-Tenant-ID": "tenant-b",
            "X-Role": "viewer",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["tenant_id"] == "tenant-a"
    assert second.json()["tenant_id"] == "tenant-b"
    assert (
        first.headers["X-Context-Scope"]
        != second.headers["X-Context-Scope"]
    )
    assert get_request_context() is None
