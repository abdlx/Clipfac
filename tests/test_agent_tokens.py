"""Agent tokens: creation, hashing, scoping, revocation, auth integration."""
import os

import pytest
from fastapi.testclient import TestClient

# NOTE: no module-level os.environ writes here. The admin token and DB path
# are set per-test via the fixture below (monkeypatch auto-undoes them), so
# this module never leaks auth state into the MCP/self-host tests that run
# in the same session and expect no admin token.
ADMIN_TOKEN = "test-admin-token-that-is-long-enough"

from clipfac import agent_tokens  # noqa: E402
from clipfac.auth import required_scope  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_db(tmp_path, monkeypatch):
    db = str(tmp_path / "tokens.sqlite3")
    monkeypatch.setenv("CLIPFAC_DB_PATH", db)
    monkeypatch.setenv("CLIPFAC_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("BILLING_ENABLED", "0")
    yield


def test_create_returns_token_once_and_stores_only_hash():
    record = agent_tokens.create_token("hob Main", ["read", "process"])
    assert record["token"].startswith("cft_")
    assert len(record["token"]) > 30
    assert record["name"] == "hob Main"
    assert record["scopes"] == ["process", "read"]
    # The list endpoint never leaks the value or the hash.
    listed = agent_tokens.list_tokens()
    assert len(listed) == 1
    assert "token" not in listed[0] and "token_hash" not in listed[0]
    assert listed[0]["prefix"] == record["prefix"]
    # A second create with the same name is fine — values differ.
    other = agent_tokens.create_token("hob Main", ["read"])
    assert other["token"] != record["token"]


def test_create_rejects_bad_input():
    with pytest.raises(ValueError, match="name"):
        agent_tokens.create_token("   ", ["read"])
    with pytest.raises(ValueError, match="at least one scope"):
        agent_tokens.create_token("x", [])
    with pytest.raises(ValueError, match="Unknown scope"):
        agent_tokens.create_token("x", ["admin"])
    with pytest.raises(ValueError, match="Unknown scope"):
        agent_tokens.create_token("x", ["read", "delete"])


def test_verify_and_last_used():
    record = agent_tokens.create_token("Claude Code", ["read", "edit"])
    assert agent_tokens.verify_token("cft_nonexistent") is None
    assert agent_tokens.verify_token("wrong-prefix-token") is None
    verified = agent_tokens.verify_token(record["token"])
    assert verified is not None
    assert verified["owner"].startswith("agent:")
    assert verified["scopes"] == {"read", "edit"}
    listed = agent_tokens.list_tokens()
    assert listed[0]["last_used"] is not None


def test_revoke_blocks_verification():
    record = agent_tokens.create_token("temp", ["read"])
    assert agent_tokens.verify_token(record["token"]) is not None
    assert agent_tokens.revoke_token(record["id"]) is True
    assert agent_tokens.revoke_token(record["id"]) is False  # already revoked
    assert agent_tokens.verify_token(record["token"]) is None
    assert agent_tokens.list_tokens() == []
    assert len(agent_tokens.list_tokens(include_revoked=True)) == 1


def test_agent_tokens_path_is_admin_only():
    for method in ("GET", "POST", "DELETE"):
        assert required_scope("/api/clipfac/agent-tokens", method) == "admin"
    assert required_scope("/api/clipfac/agent-tokens/abc", "DELETE") == "admin"


def test_endpoints_end_to_end_with_admin_and_agent_tokens():
    import app as app_module

    client = TestClient(app_module.app, raise_server_exceptions=False)
    admin = {"Authorization": "Bearer test-admin-token-that-is-long-enough"}
    # An agent token must not be able to list tokens.
    created = client.post(
        "/api/clipfac/agent-tokens",
        json={"name": "hob Main", "scopes": ["read", "process"]},
        headers=admin,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    token = body["token"]
    assert token.startswith("cft_")

    agent = {"Authorization": f"Bearer {token}"}
    # The agent token authenticates for reads...
    assert client.get("/api/clipfac/productions", headers=agent).status_code == 200
    # ...but cannot list, mint or revoke tokens.
    assert client.get("/api/clipfac/agent-tokens", headers=agent).status_code == 403
    assert client.post("/api/clipfac/agent-tokens",
                       json={"name": "evil", "scopes": ["read"]},
                       headers=agent).status_code == 403
    # Admin lists, then revokes; the agent token stops working.
    listed = client.get("/api/clipfac/agent-tokens", headers=admin)
    assert listed.status_code == 200
    assert len(listed.json()["tokens"]) == 1
    assert listed.json()["tokens"][0]["name"] == "hob Main"
    revoked = client.delete(f"/api/clipfac/agent-tokens/{body['id']}", headers=admin)
    assert revoked.status_code == 200
    assert client.get("/api/clipfac/productions", headers=agent).status_code == 401


def test_mcp_call_with_agent_token_scopes():
    import app as app_module

    client = TestClient(app_module.app, raise_server_exceptions=False)
    admin = {"Authorization": "Bearer test-admin-token-that-is-long-enough"}
    created = client.post(
        "/api/clipfac/agent-tokens",
        json={"name": "reader", "scopes": ["read"]},
        headers=admin,
    )
    token = created.json()["token"]
    agent = {"Authorization": f"Bearer {token}"}
    # tools/list is a read operation: allowed for a read-scoped token.
    listed = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=agent,
    )
    assert listed.status_code == 200, listed.text
    assert "tools" in listed.json().get("result", {})
    # tools/call on produce_clips needs process scope: denied for read-only.
    denied = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "produce_clips",
                         "arguments": {"source_url": "https://example.com/v.mp4",
                                       "confirm_rights": True,
                                       "idempotency_key": "test-key-123"}}},
        headers=agent,
    )
    assert denied.status_code == 403, denied.text
