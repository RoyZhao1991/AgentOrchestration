"""Central authentication and permission checks for protected API routes."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Iterable, Set


READ_SCOPE = "orchestrator:read"
WRITE_SCOPE = "orchestrator:write"
OPERATOR_ROLE = "workspace:operator"


@dataclass(frozen=True)
class Principal:
    token: str
    workspace_id: str
    scopes: Set[str]
    roles: Set[str]
    expires_at: float
    revoked: bool = False


class AuthError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class PermissionService:
    def __init__(self):
        self._principals: Dict[str, Principal] = {}

    def clear(self) -> None:
        self._principals.clear()

    def register_token(
        self,
        token: str,
        workspace_id: str = "default",
        scopes: Iterable[str] = (READ_SCOPE, WRITE_SCOPE),
        roles: Iterable[str] = (OPERATOR_ROLE,),
        expires_at: float = None,
        revoked: bool = False,
    ) -> None:
        self._principals[token] = Principal(
            token=token,
            workspace_id=workspace_id,
            scopes=set(scopes),
            roles=set(roles),
            expires_at=(
                expires_at if expires_at is not None else time.time() + 3600
            ),
            revoked=revoked,
        )

    def authorize_request(
        self,
        authorization: str,
        method: str,
        workspace_id: str = None,
    ) -> Principal:
        principal = self.authenticate(authorization)
        required_scope = self.required_scope(method)
        requested_workspace = workspace_id or principal.workspace_id

        if required_scope not in principal.scopes:
            raise AuthError(403, "Insufficient scope")
        if OPERATOR_ROLE not in principal.roles:
            raise AuthError(403, "Insufficient workspace role")
        if requested_workspace != principal.workspace_id:
            raise AuthError(403, "Workspace access denied")
        return principal

    def authenticate(self, authorization: str) -> Principal:
        if not authorization:
            raise AuthError(401, "Unauthorized")
        if not authorization.startswith("Bearer "):
            raise AuthError(401, "Malformed authorization")

        token = authorization.removeprefix("Bearer ").strip()
        principal = self._principals.get(token)
        if principal is None:
            raise AuthError(401, "Unknown token")
        if principal.revoked:
            raise AuthError(401, "Revoked token")
        if principal.expires_at <= time.time():
            raise AuthError(401, "Expired token")
        return principal

    @staticmethod
    def required_scope(method: str) -> str:
        if method.upper() in {"GET", "HEAD", "OPTIONS"}:
            return READ_SCOPE
        return WRITE_SCOPE


permissions = PermissionService()
