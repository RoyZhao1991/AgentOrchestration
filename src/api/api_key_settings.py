"""Authorization guard for API key settings."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, FrozenSet, List, Optional
from uuid import uuid4


class CredentialClient(Enum):
    BEARER_TOKEN = "bearer_token"
    BROWSER_SESSION = "browser_session"


class APIKeyAuthorizationReason(Enum):
    ALLOWED = "allowed"
    ANONYMOUS = "anonymous"
    MALFORMED = "malformed"
    REVOKED = "revoked"
    DISABLED = "disabled"
    EXPIRED = "expired"
    STALE_SESSION = "stale_session"
    WRONG_WORKSPACE = "wrong_workspace"
    MISSING_SCOPE = "missing_scope"
    INSUFFICIENT_ROLE = "insufficient_role"
    MFA_REQUIRED = "mfa_required"
    MFA_EXPIRED = "mfa_expired"


@dataclass(frozen=True)
class APIKeyPrincipal:
    principal_id: str
    workspace_id: str
    role: str
    scopes: FrozenSet[str] = frozenset()
    client: CredentialClient = CredentialClient.BEARER_TOKEN
    revoked: bool = False
    disabled: bool = False
    malformed: bool = False
    expires_at: Optional[datetime] = None
    session_version: int = 1
    current_session_version: int = 1
    mfa_challenge_id: Optional[str] = None
    mfa_verified_at: Optional[datetime] = None


@dataclass(frozen=True)
class APIKeyCreationRequest:
    workspace_id: str
    requested_scopes: FrozenSet[str] = frozenset()
    privileged: bool = True


@dataclass(frozen=True)
class APIKeyAuthorizationDecision:
    allowed: bool
    reason: APIKeyAuthorizationReason
    audit_metadata: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CreatedAPIKey:
    key_id: str
    workspace_id: str
    principal_id: str
    scopes: FrozenSet[str]
    privileged: bool


class APIKeySettingsGuard:
    WRITE_SCOPE = "api_keys:write"
    PRIVILEGED_SCOPE = "api_keys:privileged:create"
    PRIVILEGED_ROLES = frozenset({"owner", "admin"})

    def __init__(self, mfa_max_age: timedelta = timedelta(minutes=10)):
        self.mfa_max_age = mfa_max_age
        self._audit_log: List[Dict[str, object]] = []

    def authorize_creation(
        self,
        principal: Optional[APIKeyPrincipal],
        request: APIKeyCreationRequest,
        now: Optional[datetime] = None,
    ) -> APIKeyAuthorizationDecision:
        now = now or datetime.now(timezone.utc)
        reason = self._denial_reason(principal, request, now)
        allowed = reason == APIKeyAuthorizationReason.ALLOWED
        decision = APIKeyAuthorizationDecision(
            allowed=allowed,
            reason=reason,
            audit_metadata=self._audit_metadata(principal, request, reason),
        )
        self._audit_log.append(dict(decision.audit_metadata))
        return decision

    def create_key(
        self,
        principal: Optional[APIKeyPrincipal],
        request: APIKeyCreationRequest,
        now: Optional[datetime] = None,
    ) -> CreatedAPIKey:
        decision = self.authorize_creation(principal, request, now)
        if not decision.allowed:
            raise PermissionError(decision.reason.value)

        assert principal is not None
        return CreatedAPIKey(
            key_id=str(uuid4()),
            workspace_id=request.workspace_id,
            principal_id=principal.principal_id,
            scopes=request.requested_scopes,
            privileged=request.privileged,
        )

    def audit_log(self) -> List[Dict[str, object]]:
        return [dict(entry) for entry in self._audit_log]

    def _denial_reason(
        self,
        principal: Optional[APIKeyPrincipal],
        request: APIKeyCreationRequest,
        now: datetime,
    ) -> APIKeyAuthorizationReason:
        if principal is None:
            return APIKeyAuthorizationReason.ANONYMOUS
        if principal.malformed:
            return APIKeyAuthorizationReason.MALFORMED
        if principal.revoked:
            return APIKeyAuthorizationReason.REVOKED
        if principal.disabled:
            return APIKeyAuthorizationReason.DISABLED
        if principal.expires_at and principal.expires_at <= now:
            return APIKeyAuthorizationReason.EXPIRED
        if principal.session_version != principal.current_session_version:
            return APIKeyAuthorizationReason.STALE_SESSION
        if principal.workspace_id != request.workspace_id:
            return APIKeyAuthorizationReason.WRONG_WORKSPACE
        if self.WRITE_SCOPE not in principal.scopes:
            return APIKeyAuthorizationReason.MISSING_SCOPE

        if request.privileged:
            if self.PRIVILEGED_SCOPE not in principal.scopes:
                return APIKeyAuthorizationReason.MISSING_SCOPE
            if principal.role not in self.PRIVILEGED_ROLES:
                return APIKeyAuthorizationReason.INSUFFICIENT_ROLE
            if not principal.mfa_challenge_id or not principal.mfa_verified_at:
                return APIKeyAuthorizationReason.MFA_REQUIRED
            if principal.mfa_verified_at + self.mfa_max_age < now:
                return APIKeyAuthorizationReason.MFA_EXPIRED

        return APIKeyAuthorizationReason.ALLOWED

    def _audit_metadata(
        self,
        principal: Optional[APIKeyPrincipal],
        request: APIKeyCreationRequest,
        reason: APIKeyAuthorizationReason,
    ) -> Dict[str, object]:
        return {
            "reason": reason.value,
            "allowed": reason == APIKeyAuthorizationReason.ALLOWED,
            "client": principal.client.value if principal else "anonymous",
            "principal_id": principal.principal_id if principal else None,
            "workspace_id": request.workspace_id,
            "role": principal.role if principal else None,
            "privileged": request.privileged,
            "requested_scope_count": len(request.requested_scopes),
        }
