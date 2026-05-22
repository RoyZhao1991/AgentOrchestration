import logging

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from src.api.middleware import (
    AGENT_CONTEXT_CLEARED_HEADER,
    AgentContextMiddleware,
    get_request_agent_context,
)


async def context_endpoint(request):
    return JSONResponse({"context": get_request_agent_context()})


async def failing_endpoint(request):
    raise RuntimeError("handler failed")


class RejectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        return Response(status_code=403, content="Rejected")


def build_app(routes, reject=False):
    app = Starlette(routes=routes)
    if reject:
        app.add_middleware(RejectMiddleware)
    app.add_middleware(AgentContextMiddleware)
    return app


class TestAgentContextMiddleware:
    def test_clears_agent_context_after_normal_request(self):
        client = TestClient(
            build_app([Route("/context", context_endpoint)])
        )

        response = client.get("/context", headers={"X-Agent-ID": "agent-a"})

        assert response.status_code == 200
        assert response.json()["context"] == {"agent_id": "agent-a"}
        assert response.headers[AGENT_CONTEXT_CLEARED_HEADER] == "true"
        assert get_request_agent_context() is None

    def test_clears_agent_context_after_rejected_request(self):
        client = TestClient(
            build_app([Route("/context", context_endpoint)], reject=True)
        )

        response = client.get("/context", headers={"X-Agent-ID": "agent-b"})

        assert response.status_code == 403
        assert response.headers[AGENT_CONTEXT_CLEARED_HEADER] == "true"
        assert get_request_agent_context() is None

    def test_clears_agent_context_after_exception_path(self):
        client = TestClient(
            build_app([Route("/boom", failing_endpoint)]),
            raise_server_exceptions=False,
        )

        response = client.get("/boom", headers={"X-Agent-ID": "agent-c"})

        assert response.status_code == 500
        assert get_request_agent_context() is None

    def test_context_does_not_leak_between_requests(self):
        client = TestClient(
            build_app([Route("/context", context_endpoint)])
        )

        first = client.get("/context", headers={"X-Agent-ID": "agent-a"})
        second = client.get("/context")

        assert first.json()["context"] == {"agent_id": "agent-a"}
        assert second.json()["context"] is None
        assert get_request_agent_context() is None

    def test_agent_context_logs_are_sanitized(self, caplog):
        client = TestClient(
            build_app([Route("/context", context_endpoint)])
        )

        with caplog.at_level(logging.INFO, logger="src.api.middleware"):
            client.get(
                "/context",
                headers={
                    "X-Agent-ID": "agent-secret",
                    "Authorization": "Bearer private-token",
                },
            )

        log_text = caplog.text
        assert "Cleared request-local agent context" in log_text
        assert "agent-secret" not in log_text
        assert "private-token" not in log_text
