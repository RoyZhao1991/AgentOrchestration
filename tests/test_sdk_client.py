from urllib.error import URLError

import pytest

from src.sdk.client import OrchestratorClient, SDKRequestError


def test_register_agent_transport_error_includes_safe_request_context(
    monkeypatch,
):
    def fail_request(request):
        raise URLError("connection refused")

    monkeypatch.setattr("src.sdk.client.urlopen", fail_request)
    client = OrchestratorClient(
        base_url="https://example.com",
        api_key="super-secret-token",
    )

    with pytest.raises(SDKRequestError) as error:
        client.register_agent("worker", "worker.processor")

    message = str(error.value)
    assert "POST /agents failed" in message
    assert "connection refused" in message
    assert "super-secret-token" not in message
    assert "Authorization" not in message
    assert "https://example.com" not in message
    assert error.value.method == "POST"
    assert error.value.path == "/agents"


def test_http_error_response_includes_safe_context(
    monkeypatch,
):
    from urllib.error import HTTPError

    def fail_request(request):
        raise HTTPError(
            url=request.full_url,
            code=503,
            msg="Service Unavailable",
            hdrs={"Authorization": "Bearer leaked"},
            fp=None,
        )

    monkeypatch.setattr("src.sdk.client.urlopen", fail_request)
    client = OrchestratorClient(
        base_url="https://example.com",
        api_key="super-secret-token",
    )

    response = client.register_agent("worker", "worker.processor")

    assert response == {
        "error": 503,
        "message": "Service Unavailable",
        "method": "POST",
        "path": "/agents",
    }
