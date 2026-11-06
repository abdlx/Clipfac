"""Self-host /api/me: the dashboard's refreshMe() wipes the saved token on ANY
error, so a missing route silently logs the user out mid-job."""
import asyncio

import httpx
import pytest

app_module = pytest.importorskip("app")


def _call(method, path, headers=None):
    async def run():
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_module.app),
                base_url="http://test") as client:
            return await client.request(method, path, headers=headers or {})
    return asyncio.run(run())


def test_me_returns_self_host_identity(monkeypatch):
    monkeypatch.setenv("CLIPFAC_ADMIN_TOKEN", "admin-" + "a" * 32)
    resp = _call("GET", "/api/me", {"Authorization": "Bearer admin-" + "a" * 32})
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["id"] == "local"
    assert data["plan"] == "free"
    assert data["entitled"] is False


def test_me_requires_installation_token(monkeypatch):
    monkeypatch.setenv("CLIPFAC_ADMIN_TOKEN", "admin-" + "a" * 32)
    assert _call("GET", "/api/me").status_code == 401
