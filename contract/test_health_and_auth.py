"""Promises about reaching the backend and the session token."""
from client import USER_ID

PROTECTED_GETS = (f"/conversations/{USER_ID}", f"/api-keys/{USER_ID}", f"/get-ai-config/{USER_ID}",
                  "/me", "/health/auth", "/mcp/unity/status")


def test_the_backend_answers_its_health_endpoint_without_a_token(api):
    """GET /health answers 200 {"status": "ok", "service": "gamachine"} to anyone."""
    with api.anonymous() as anonymous:
        response = anonymous.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "gamachine"}


def test_the_authenticated_health_endpoint_confirms_a_valid_token(api):
    """GET /health/auth with the session token answers auth "ok"."""
    assert api.ok("GET", "/health/auth")["auth"] == "ok"


def test_requests_without_the_session_token_are_refused(api):
    """A protected endpoint called with no X-Session-Token is refused with a 4xx and returns no data."""
    with api.anonymous() as anonymous:
        for path in PROTECTED_GETS:
            response = anonymous.get(path)
            assert response.status_code in (401, 422), f"{path} -> {response.status_code}"
            assert set(response.json()) == {"detail"}, f"{path} leaked {response.text[:200]}"


def test_requests_with_a_wrong_session_token_are_refused(api):
    """A protected endpoint called with a wrong X-Session-Token answers 401."""
    for path in PROTECTED_GETS:
        response = api.get(path, headers={"X-Session-Token": "not-the-token"})
        assert response.status_code == 401, f"{path} -> {response.status_code}"
        assert set(response.json()) == {"detail"}


def test_the_token_identifies_the_single_local_user(api):
    """There is no login: the session token is the credential and GET /me answers the local user 1."""
    me = api.ok("GET", "/me")
    assert me["user_id"] == USER_ID and me["id"] == USER_ID


def test_another_user_id_is_refused(api):
    """A valid token cannot read another user's data: user 2 answers 403."""
    response = api.get("/conversations/2")
    assert response.status_code == 403


def test_writes_without_a_valid_session_token_are_refused_and_change_nothing(api, tmp_path):
    """POST /api-keys/save, PUT and DELETE /conversations/{id}, POST /save-workspace and POST /mcp/unity/toggle with no or a wrong token are refused (401/403/422) and nothing is written."""
    conv_id = api.new_conversation(title="Token guarded")
    project = tmp_path / "Guarded"
    (project / "Assets").mkdir(parents=True)
    (project / "ProjectSettings").mkdir()
    workspace_before = api.ok("GET", f"/last-workspace/{USER_ID}")
    attempts = (
        ("POST", "/api-keys/save", {"provider_type": "openrouter", "api_key": "sk-guarded-0123456789"}),
        ("PUT", f"/conversations/{conv_id}", {"title": "Renamed without a token"}),
        ("DELETE", f"/conversations/{conv_id}", None),
        ("POST", "/save-workspace", {"user_id": USER_ID, "path": str(project)}),
        ("POST", "/mcp/unity/toggle", {"enabled": False, "workspace_path": str(project)}),
    )
    with api.anonymous() as anonymous:
        for method, path, body in attempts:
            for label, client, headers in (("no token", anonymous, {}),
                                           ("wrong token", anonymous, {"X-Session-Token": "not-the-token"})):
                response = client.request(method, path, json=body, headers=headers)
                assert response.status_code in (401, 403, 422), f"{method} {path} with {label} -> {response.status_code}"
    assert "openrouter" not in api.ok("GET", f"/api-keys/{USER_ID}")["providers_with_keys"]
    listed = {c["id"]: c["title"] for c in api.ok("GET", f"/conversations/{USER_ID}")}
    assert listed.get(conv_id) == "Token guarded"
    assert api.ok("GET", f"/last-workspace/{USER_ID}") == workspace_before
    assert not (project / "UserSettings").exists()
