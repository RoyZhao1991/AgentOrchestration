import pytest
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Scope

from src.api.middleware import (
    AuthMiddleware,
    RateLimitMiddleware,
    SECURITY_HEADERS,
    SecurityHeadersMiddleware,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def build_request(
    path="/api/v2/agents",
    headers=None,
    client_host="127.0.0.1",
):
    raw_headers = [
        (key.lower().encode(), value.encode())
        for key, value in (headers or {}).items()
    ]
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw_headers,
        "client": (client_host, 52341),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def assert_security_headers(response):
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


@pytest.mark.anyio
async def test_security_headers_apply_to_normal_responses():
    middleware = SecurityHeadersMiddleware(app=None)
    request = build_request(path="/health")

    async def call_next(_request):
        return Response(status_code=200, content="ok")

    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 200
    assert_security_headers(response)


@pytest.mark.anyio
async def test_security_headers_apply_to_rejected_responses():
    security = SecurityHeadersMiddleware(app=None)
    auth = AuthMiddleware(app=None)
    request = build_request(path="/api/v2/agents")

    async def call_next(_request):
        return await auth.dispatch(
            _request,
            lambda __request: Response(status_code=200),
        )

    response = await security.dispatch(request, call_next)

    assert response.status_code == 401
    assert_security_headers(response)


@pytest.mark.anyio
async def test_security_headers_apply_to_rate_limit_responses():
    security = SecurityHeadersMiddleware(app=None)
    rate_limit = RateLimitMiddleware(app=None, max_requests=0)
    request = build_request(path="/api/v2/agents")

    async def call_next(_request):
        return await rate_limit.dispatch(
            _request,
            lambda __request: Response(status_code=200),
        )

    response = await security.dispatch(request, call_next)

    assert response.status_code == 429
    assert_security_headers(response)


@pytest.mark.anyio
async def test_security_headers_apply_to_exception_responses(caplog):
    middleware = SecurityHeadersMiddleware(app=None)
    request = build_request(
        path="/api/v2/agents",
        headers={"Authorization": "Bearer secret-token"},
    )

    async def call_next(_request):
        raise RuntimeError("database password leaked")

    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 500
    assert response.body == b"Internal server error"
    assert "secret-token" not in response.body.decode()
    assert "database password leaked" not in response.body.decode()
    assert_security_headers(response)
    assert "secret-token" not in caplog.text
