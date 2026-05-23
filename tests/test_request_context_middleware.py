import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware import (
    LoggingMiddleware,
    RequestContextMiddleware,
    get_request_context,
)


def make_client():
    app = FastAPI()
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/context")
    async def context():
        request_context = get_request_context()
        return {
            "correlation_id": request_context.correlation_id,
            "workspace_id": request_context.workspace_id,
            "active_role": request_context.active_role,
        }

    @app.get("/boom")
    async def boom():
        raise RuntimeError("private-runtime-token")

    return TestClient(app)


def test_request_context_sets_safe_correlation_header_and_logs(caplog):
    client = make_client()
    headers = {
        "X-Correlation-ID": "corr-123",
        "X-Workspace-ID": "workspace-a",
        "X-Active-Role": "owner",
        "Authorization": "Bearer private-token",
    }

    with caplog.at_level(logging.INFO, logger="src.api.middleware"):
        response = client.get("/context", headers=headers)

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "corr-123"
    assert response.json() == {
        "correlation_id": "corr-123",
        "workspace_id": "workspace-a",
        "active_role": "owner",
    }
    assert "corr-123" in caplog.text
    assert "workspace-a" in caplog.text
    assert "private-token" not in caplog.text
    assert "secret" not in caplog.text
    assert get_request_context() is None


def test_reused_correlation_id_is_rejected_across_workspaces():
    client = make_client()
    first_headers = {
        "X-Correlation-ID": "shared-correlation",
        "X-Workspace-ID": "workspace-a",
        "X-Active-Role": "owner",
    }
    second_headers = {
        "X-Correlation-ID": "shared-correlation",
        "X-Workspace-ID": "workspace-b",
        "X-Active-Role": "owner",
    }

    assert client.get("/context", headers=first_headers).status_code == 200
    response = client.get("/context", headers=second_headers)

    assert response.status_code == 409
    assert response.headers["X-Correlation-ID"] == "shared-correlation"
    assert response.text == "Correlation ID scope mismatch"
    assert get_request_context() is None


def test_reused_correlation_id_is_rejected_across_active_roles():
    client = make_client()
    first_headers = {
        "X-Correlation-ID": "role-correlation",
        "X-Workspace-ID": "workspace-a",
        "X-Active-Role": "owner",
    }
    second_headers = {
        "X-Correlation-ID": "role-correlation",
        "X-Workspace-ID": "workspace-a",
        "X-Active-Role": "viewer",
    }

    assert client.get("/context", headers=first_headers).status_code == 200
    response = client.get("/context", headers=second_headers)

    assert response.status_code == 409
    assert response.headers["X-Correlation-ID"] == "role-correlation"
    assert get_request_context() is None


def test_generated_correlation_id_is_scoped_and_returned():
    client = make_client()

    response = client.get(
        "/context",
        headers={
            "X-Workspace-ID": "workspace-a",
            "X-Active-Role": "owner",
        },
    )

    assert response.status_code == 200
    generated_id = response.headers["X-Correlation-ID"]
    assert len(generated_id) == 32
    assert response.json()["correlation_id"] == generated_id
    assert get_request_context() is None


def test_invalid_correlation_id_is_rejected_before_handler_work():
    client = make_client()

    response = client.get(
        "/context",
        headers={"X-Correlation-ID": "bad correlation id"},
    )

    assert response.status_code == 400
    assert response.text == "Invalid correlation ID"
    assert "X-Correlation-ID" not in response.headers
    assert get_request_context() is None


def test_exception_path_returns_sanitized_response_and_clears_context(caplog):
    client = make_client()
    headers = {
        "X-Correlation-ID": "boom-correlation",
        "X-Workspace-ID": "workspace-a",
        "X-Active-Role": "owner",
    }

    with caplog.at_level(logging.ERROR, logger="src.api.middleware"):
        response = client.get("/boom", headers=headers)

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert response.headers["X-Correlation-ID"] == "boom-correlation"
    assert "private-runtime-token" not in response.text
    assert "private-runtime-token" not in caplog.text
    assert get_request_context() is None
