"""Provider encryption, masked responses, restart loading and permission boundaries."""
import asyncio
import json
import os
import stat

import httpx
import pytest
from fastapi import FastAPI, Request

from clipfac import vault
from clipfac.auth import InstallationAuth


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFAC_DB_PATH", str(tmp_path / "private.db"))
    monkeypatch.delenv("CLIPFAC_VAULT_KEY", raising=False)
    baseline = {key: None for key in vault.ENV_KEYS}
    monkeypatch.setattr(vault, "_BASE_ENV", baseline)
    # Record every original environment variable for monkeypatch teardown.
    for key in vault.ENV_KEYS:
        monkeypatch.setenv(key, os.environ.get(key, ""))
    return tmp_path


def test_key_is_encrypted_and_never_returned(isolated):
    secret = "sk-test-private-credential"
    result = vault.save("text", vault.ProviderUpdate(
        endpoint="https://provider.example/v1", model="test-model", token=secret))
    assert result["token_configured"]
    assert secret not in json.dumps(vault.public_settings())
    assert secret.encode() not in (isolated / "private.db").read_bytes()
    assert secret.encode() not in (isolated / "private.db-wal").read_bytes() if (isolated / "private.db-wal").exists() else True
    assert stat.S_IMODE((isolated / "clipfac-vault.key").stat().st_mode) == 0o600
    assert os.environ["LLM_API_KEY"] == secret


def test_settings_restore_on_restart_and_preserve_blank_key(isolated):
    vault.save("text", vault.ProviderUpdate(endpoint="https://provider.example/v1", model="first", token="test-secret"))
    os.environ.pop("LLM_API_KEY", None)
    vault.apply_runtime()
    assert os.environ["LLM_API_KEY"] == "test-secret"
    vault.save("text", vault.ProviderUpdate(model="second", token=None))
    assert os.environ["LLM_API_KEY"] == "test-secret"
    assert os.environ["LLM_MODEL"] == "second"


def test_disable_and_delete_have_explicit_environment_behavior(isolated, monkeypatch):
    monkeypatch.setitem(vault._BASE_ENV, "GEMINI_API_KEY", "deployment-secret")
    os.environ["GEMINI_API_KEY"] = "deployment-secret"
    vault.save("gemini", vault.ProviderUpdate(model="test-model", token="saved-secret"))
    assert os.environ["GEMINI_API_KEY"] == "saved-secret"
    vault.save("gemini", vault.ProviderUpdate(enabled=False))
    assert "GEMINI_API_KEY" not in os.environ
    vault.delete("gemini")
    assert os.environ["GEMINI_API_KEY"] == "deployment-secret"


def test_endpoint_cannot_hide_credentials_in_url():
    for endpoint in ("file:///tmp/key", "https://user:pass@host.example/v1", "https://host.example/v1?token=secret"):
        with pytest.raises(ValueError):
            vault.ProviderUpdate(endpoint=endpoint)


def call(app, method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(run())


def test_provider_secret_is_injected_on_server_without_browser_header(isolated):
    vault.save("gemini", vault.ProviderUpdate(token="test-server-key"))
    app = FastAPI()
    app.add_middleware(vault.ProviderHeaders)
    @app.get("/api/test-provider")
    async def probe(request: Request):
        return {"server_received_key": request.headers.get("x-gemini-key") == "test-server-key"}
    assert call(app, "GET", "/api/test-provider").json()["server_received_key"]


def test_reader_agent_cannot_change_provider_settings(isolated, monkeypatch):
    monkeypatch.setenv("CLIPFAC_ADMIN_TOKEN", "admin-" + "a" * 32)
    monkeypatch.setenv("CLIPFAC_AGENT_TOKENS", json.dumps([
        {"token": "reader-" + "r" * 32, "scopes": ["read"]},
    ]))
    app = FastAPI()
    app.include_router(vault.router)
    app.add_middleware(InstallationAuth)
    headers = {"Authorization": "Bearer reader-" + "r" * 32}
    assert call(app, "GET", "/api/clipfac/providers", headers=headers).status_code == 200
    assert call(app, "PUT", "/api/clipfac/providers/gemini", headers=headers,
                json={"token": "attacker-key"}).status_code == 403
