from datetime import datetime, timedelta, timezone

import pytest

from src.api.api_key_settings import (
    APIKeyAuthorizationReason,
    APIKeyCreationRequest,
    APIKeyPrincipal,
    APIKeySettingsGuard,
    CredentialClient,
)


class TestAPIKeySettingsGuard:
    def setup_method(self):
        self.now = datetime(2026, 5, 22, 9, 30, tzinfo=timezone.utc)
        self.guard = APIKeySettingsGuard()
        self.request = APIKeyCreationRequest(
            workspace_id="workspace-1",
            requested_scopes=frozenset({"agents:run"}),
            privileged=True,
        )

    def principal(self, **overrides):
        values = {
            "principal_id": "user-1",
            "workspace_id": "workspace-1",
            "role": "admin",
            "scopes": frozenset({
                "api_keys:write",
                "api_keys:privileged:create",
            }),
            "client": CredentialClient.BEARER_TOKEN,
            "expires_at": self.now + timedelta(hours=1),
            "session_version": 7,
            "current_session_version": 7,
            "mfa_challenge_id": "mfa-123",
            "mfa_verified_at": self.now - timedelta(minutes=2),
        }
        values.update(overrides)
        return APIKeyPrincipal(**values)

    @pytest.mark.parametrize(
        ("principal_overrides", "expected_reason"),
        [
            ({"revoked": True}, APIKeyAuthorizationReason.REVOKED),
            ({"disabled": True}, APIKeyAuthorizationReason.DISABLED),
            ({"malformed": True}, APIKeyAuthorizationReason.MALFORMED),
            (
                {
                    "expires_at": datetime(
                        2026,
                        5,
                        22,
                        9,
                        0,
                        tzinfo=timezone.utc,
                    )
                },
                APIKeyAuthorizationReason.EXPIRED,
            ),
            (
                {"current_session_version": 8},
                APIKeyAuthorizationReason.STALE_SESSION,
            ),
            (
                {"workspace_id": "workspace-2"},
                APIKeyAuthorizationReason.WRONG_WORKSPACE,
            ),
            (
                {"scopes": frozenset({"api_keys:write"})},
                APIKeyAuthorizationReason.MISSING_SCOPE,
            ),
            ({"role": "viewer"}, APIKeyAuthorizationReason.INSUFFICIENT_ROLE),
        ],
    )
    def test_privileged_key_creation_denies_invalid_principals(
        self,
        principal_overrides,
        expected_reason,
    ):
        decision = self.guard.authorize_creation(
            self.principal(**principal_overrides),
            self.request,
            now=self.now,
        )

        assert not decision.allowed
        assert decision.reason == expected_reason

    def test_anonymous_principal_is_denied(self):
        decision = self.guard.authorize_creation(
            None,
            self.request,
            now=self.now,
        )

        assert not decision.allowed
        assert decision.reason == APIKeyAuthorizationReason.ANONYMOUS

    def test_privileged_key_creation_requires_fresh_mfa_challenge(self):
        missing_mfa = self.guard.authorize_creation(
            self.principal(mfa_challenge_id=None, mfa_verified_at=None),
            self.request,
            now=self.now,
        )
        stale_mfa = self.guard.authorize_creation(
            self.principal(mfa_verified_at=self.now - timedelta(minutes=30)),
            self.request,
            now=self.now,
        )

        assert missing_mfa.reason == APIKeyAuthorizationReason.MFA_REQUIRED
        assert stale_mfa.reason == APIKeyAuthorizationReason.MFA_EXPIRED

    def test_bearer_and_browser_variants_use_same_authorization_path(self):
        bearer = self.principal(client=CredentialClient.BEARER_TOKEN)
        browser = self.principal(client=CredentialClient.BROWSER_SESSION)

        bearer_decision = self.guard.authorize_creation(
            bearer,
            self.request,
            now=self.now,
        )
        browser_decision = self.guard.authorize_creation(
            browser,
            self.request,
            now=self.now,
        )

        assert bearer_decision.allowed
        assert browser_decision.allowed
        assert bearer_decision.reason == browser_decision.reason

    def test_authorized_workspace_admin_creates_privileged_key(self):
        created_key = self.guard.create_key(
            self.principal(),
            self.request,
            now=self.now,
        )

        assert created_key.workspace_id == "workspace-1"
        assert created_key.principal_id == "user-1"
        assert created_key.privileged
        assert created_key.scopes == frozenset({"agents:run"})

    def test_denial_audit_omits_mfa_and_token_material(self):
        decision = self.guard.authorize_creation(
            self.principal(revoked=True, mfa_challenge_id="sensitive-mfa"),
            self.request,
            now=self.now,
        )
        audit_entry = self.guard.audit_log()[-1]

        assert decision.reason == APIKeyAuthorizationReason.REVOKED
        assert audit_entry["reason"] == "revoked"
        assert "mfa" not in audit_entry
        assert "sensitive-mfa" not in str(audit_entry)
