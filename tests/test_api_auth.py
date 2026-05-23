import time

from fastapi.testclient import TestClient

from src.api.auth import READ_SCOPE, WRITE_SCOPE, OPERATOR_ROLE, permissions
from src.api.server import create_app


def setup_function():
    permissions.clear()


def client():
    return TestClient(create_app())


def register_token(
    token,
    workspace_id="workspace-a",
    scopes=(READ_SCOPE, WRITE_SCOPE),
    roles=(OPERATOR_ROLE,),
    expires_in=60,
    revoked=False,
):
    permissions.register_token(
        token,
        workspace_id=workspace_id,
        scopes=scopes,
        roles=roles,
        expires_at=time.time() + expires_in,
        revoked=revoked,
    )


def auth_headers(token="valid-token", workspace_id="workspace-a"):
    return {
        "Authorization": f"Bearer {token}",
        "X-Workspace-ID": workspace_id,
    }


def test_anonymous_trailing_slash_request_is_denied_without_redirect():
    response = client().get("/api/v2/agents/", follow_redirects=False)

    assert response.status_code == 401
    assert response.headers.get("location") is None


def test_malformed_token_is_denied_before_protected_route():
    response = client().get(
        "/api/v2/agents",
        headers={"Authorization": "Token malformed"},
    )

    assert response.status_code == 401


def test_expired_token_is_denied():
    register_token("expired-token", expires_in=-1)

    response = client().get(
        "/api/v2/agents",
        headers=auth_headers("expired-token"),
    )

    assert response.status_code == 401


def test_revoked_token_is_denied():
    register_token("revoked-token", revoked=True)

    response = client().get(
        "/api/v2/agents",
        headers=auth_headers("revoked-token"),
    )

    assert response.status_code == 401


def test_insufficient_scope_is_denied_before_mutation():
    register_token("read-only", scopes=(READ_SCOPE,))

    response = client().post(
        "/api/v2/agents",
        params={"name": "agent-a", "agent_type": "worker.processor"},
        headers=auth_headers("read-only"),
    )

    assert response.status_code == 403


def test_insufficient_workspace_role_is_denied():
    register_token("viewer-token", roles=("workspace:viewer",))

    response = client().post(
        "/api/v2/agents",
        params={"name": "agent-a", "agent_type": "worker.processor"},
        headers=auth_headers("viewer-token"),
    )

    assert response.status_code == 403


def test_wrong_workspace_is_denied():
    register_token("valid-token", workspace_id="workspace-a")

    response = client().get(
        "/api/v2/agents",
        headers=auth_headers("valid-token", workspace_id="workspace-b"),
    )

    assert response.status_code == 403


def test_authorized_operator_can_register_agent():
    register_token("valid-token")

    response = client().post(
        "/api/v2/agents",
        params={"name": "agent-a", "agent_type": "worker.processor"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "registered"
