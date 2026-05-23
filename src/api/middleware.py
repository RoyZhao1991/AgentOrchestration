"""API middleware components."""

import contextvars
import hashlib
import time
import logging
import uuid
from dataclasses import dataclass
from typing import Callable, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

ALLOWED_REQUEST_ROLES = {"anonymous", "viewer", "operator", "admin", "service"}
MAX_CONTEXT_HEADER_LENGTH = 128
HEADER_SAFE_CHARS = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "._:-"
)

correlation_id_var: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar("correlation_id", default=None)
)
request_id_var: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar("request_id", default=None)
)
tenant_id_var: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar("tenant_id", default=None)
)
role_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_role",
    default="anonymous",
)


@dataclass(frozen=True)
class RequestContext:
    correlation_id: str
    request_id: str
    tenant_id: str
    role: str
    scope_digest: str


def get_request_context() -> Optional[RequestContext]:
    correlation_id = correlation_id_var.get()
    request_id = request_id_var.get()
    tenant_id = tenant_id_var.get()
    role = role_var.get()
    if not correlation_id or not request_id or not tenant_id:
        return None
    return RequestContext(
        correlation_id=correlation_id,
        request_id=request_id,
        tenant_id=tenant_id,
        role=role,
        scope_digest=_scope_digest(tenant_id, role),
    )


def get_correlation_id() -> Optional[str]:
    return correlation_id_var.get()


def get_request_id() -> Optional[str]:
    return request_id_var.get()


def get_tenant_id() -> Optional[str]:
    return tenant_id_var.get()


def get_request_role() -> str:
    return role_var.get()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        context = _context_from_request(request)
        tokens = (
            correlation_id_var.set(context.correlation_id),
            request_id_var.set(context.request_id),
            tenant_id_var.set(context.tenant_id),
            role_var.set(context.role),
        )
        try:
            rejection = _validate_context_headers(request, context)
            if rejection is not None:
                return _with_context_headers(rejection, context, rejected=True)

            try:
                response = await call_next(request)
            except Exception:
                logger.exception(
                    "request failed",
                    extra=_log_context(context),
                )
                response = Response(
                    status_code=500,
                    content="Internal Server Error",
                )
            return _with_context_headers(response, context)
        finally:
            correlation_id_var.reset(tokens[0])
            request_id_var.reset(tokens[1])
            tenant_id_var.reset(tokens[2])
            role_var.reset(tokens[3])


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        is_api_v2 = request.url.path.startswith("/api/v2")
        is_token_route = request.url.path == "/api/v2/auth/token"
        if is_api_v2 and not is_token_route:
            token = request.headers.get("Authorization", "")
            if not token.startswith("Bearer "):
                return Response(status_code=401, content="Unauthorized")
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 100, window: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window
        self._requests = {}

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        if client_ip not in self._requests:
            self._requests[client_ip] = []

        self._requests[client_ip] = [
            timestamp
            for timestamp in self._requests[client_ip]
            if now - timestamp < self.window
        ]

        if len(self._requests[client_ip]) >= self.max_requests:
            return Response(status_code=429, content="Too many requests")

        self._requests[client_ip].append(now)
        return await call_next(request)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start
        context = get_request_context()
        extra = _log_context(context) if context else {}
        logger.info(
            "%s %s %s %.3fs",
            request.method,
            request.url.path,
            response.status_code,
            duration,
            extra=extra,
        )
        return response


def _context_from_request(request: Request) -> RequestContext:
    tenant_id = _safe_header(
        request.headers.get("X-Tenant-ID")
        or request.headers.get("X-Workspace-ID")
        or "anonymous",
        default="anonymous",
    )
    role = _safe_header(
        request.headers.get("X-Role") or "anonymous",
        default="anonymous",
    ).lower()
    if role not in ALLOWED_REQUEST_ROLES:
        role = "anonymous"

    correlation_id = _safe_header(
        request.headers.get("X-Correlation-ID"),
        default=str(uuid.uuid4()),
    )
    request_id = _safe_header(
        request.headers.get("X-Request-ID"),
        default=str(uuid.uuid4()),
    )
    return RequestContext(
        correlation_id=correlation_id,
        request_id=request_id,
        tenant_id=tenant_id,
        role=role,
        scope_digest=_scope_digest(tenant_id, role),
    )


def _validate_context_headers(
    request: Request,
    context: RequestContext,
) -> Optional[Response]:
    tenant_id = request.headers.get("X-Tenant-ID")
    workspace_id = request.headers.get("X-Workspace-ID")
    if tenant_id and workspace_id and tenant_id != workspace_id:
        logger.warning(
            "rejected request context mismatch",
            extra=_log_context(context),
        )
        return Response(
            status_code=403,
            content="Tenant and workspace context mismatch",
        )

    role = request.headers.get("X-Role")
    if role and role.lower() not in ALLOWED_REQUEST_ROLES:
        logger.warning(
            "rejected unsupported request role",
            extra=_log_context(context),
        )
        return Response(status_code=403, content="Unsupported request role")
    return None


def _with_context_headers(
    response: Response,
    context: RequestContext,
    rejected: bool = False,
) -> Response:
    response.headers["X-Correlation-ID"] = context.correlation_id
    response.headers["X-Request-ID"] = context.request_id
    response.headers["X-Context-Scope"] = context.scope_digest
    if rejected:
        response.headers["X-Context-Decision"] = "rejected"
    return response


def _safe_header(value: Optional[str], default: str) -> str:
    if value is None:
        return default
    stripped = value.strip()
    if not stripped or len(stripped) > MAX_CONTEXT_HEADER_LENGTH:
        return default
    if any(character not in HEADER_SAFE_CHARS for character in stripped):
        return default
    return stripped


def _scope_digest(tenant_id: str, role: str) -> str:
    scope = f"{tenant_id}:{role}".encode("utf-8")
    return hashlib.sha256(scope).hexdigest()[:16]


def _log_context(context: RequestContext) -> dict:
    return {
        "correlation_id": context.correlation_id,
        "request_id": context.request_id,
        "context_scope": context.scope_digest,
        "request_role": context.role,
    }

# 2019-03-01T18:35:19 update

# 2019-04-03T13:22:05 update

# 2019-04-30T17:18:49 update

# 2019-08-20T09:29:03 update

# 2019-08-30T15:52:06 update

# 2019-11-23T16:58:42 update

# 2020-02-18T10:04:07 update

# 2020-04-21T17:35:30 update

# 2020-05-22T11:10:34 update

# 2020-07-02T12:31:26 update

# 2020-07-05T13:52:59 update

# 2020-08-21T20:36:45 update

# 2021-01-19T09:17:15 update

# 2021-01-29T11:34:24 update

# 2021-02-04T15:21:21 update

# 2021-04-19T19:23:15 update

# 2021-05-20T16:50:15 update

# 2021-06-22T19:23:44 update

# 2021-09-09T13:44:55 update

# 2021-09-16T09:30:20 update

# 2021-10-14T20:42:33 update

# 2021-12-28T16:39:14 update

# 2022-01-26T19:07:27 update

# 2022-01-28T08:03:41 update

# 2022-03-23T12:17:02 update

# 2022-04-06T12:12:27 update

# 2022-04-21T14:53:01 update

# 2022-06-30T08:37:32 update

# 2022-07-06T10:44:45 update

# 2022-11-02T11:12:47 update

# 2022-11-15T20:54:21 update

# 2022-11-23T14:13:34 update

# 2023-01-26T10:03:44 update

# 2023-02-09T17:08:10 update

# 2023-02-16T10:04:00 update

# 2023-03-14T11:52:03 update

# 2023-04-10T12:42:07 update

# 2023-04-26T10:43:39 update

# 2023-06-27T08:18:07 update

# 2023-08-30T15:30:40 update

# 2023-08-30T14:10:05 update

# 2023-10-09T18:32:46 update

# 2023-11-21T20:35:55 update

# 2024-03-07T19:17:39 update

# 2024-04-01T18:06:19 update

# 2024-07-18T15:37:34 update

# 2024-07-25T09:21:53 update

# 2024-08-12T14:24:22 update

# 2024-11-18T08:50:54 update

# 2025-04-08T12:43:05 update

# 2025-06-03T08:10:47 update

# 2025-06-12T08:37:52 update

# 2025-06-17T08:36:56 update

# 2025-07-02T18:09:42 update

# 2025-07-22T12:39:21 update

# 2025-10-13T12:13:46 update

# 2025-12-05T09:44:22 update

# 2025-12-22T18:34:47 update

# 2026-01-26T15:36:23 update

# 2026-02-13T12:36:40 update

# 2026-02-26T11:07:15 update

# 2026-03-19T11:00:17 update

# 2026-03-27T12:58:53 update

# 2026-05-12T17:19:36 update
