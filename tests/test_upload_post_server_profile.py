"""Upload-Post key + profile resolve server-side, so the editor never asks
the browser for a key. Skipped where the full app cannot be imported."""
import asyncio

import pytest
from fastapi import FastAPI
from starlette.requests import Request

app_module = pytest.importorskip("app")


def _request(stub_app):
    # A bare scope gives base_url "/" and httpx refuses the relative URL;
    # a real request always carries scheme + server.
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "http", "server": ("testserver", 80),
        "headers": [], "app": stub_app,
    })


def test_vault_default_profile_wins(monkeypatch):
    monkeypatch.setenv("CLIPFAC_UPLOAD_PROFILE", "my-profile")
    stub = FastAPI()

    @stub.get("/api/social/user")
    async def _profiles():
        return {"profiles": [{"username": "other-profile", "connected": []}]}

    assert asyncio.run(app_module._server_post_profile(_request(stub))) == "my-profile"


def test_single_connected_profile_is_picked(monkeypatch):
    monkeypatch.delenv("CLIPFAC_UPLOAD_PROFILE", raising=False)
    stub = FastAPI()

    @stub.get("/api/social/user")
    async def _profiles():
        return {"profiles": [{"username": "only-profile", "connected": ["youtube"]}]}

    assert asyncio.run(app_module._server_post_profile(_request(stub))) == "only-profile"


def test_ambiguous_profiles_need_an_explicit_choice(monkeypatch):
    monkeypatch.delenv("CLIPFAC_UPLOAD_PROFILE", raising=False)
    stub = FastAPI()

    @stub.get("/api/social/user")
    async def _profiles():
        return {"profiles": [
            {"username": "one", "connected": []},
            {"username": "two", "connected": []},
        ]}

    assert asyncio.run(app_module._server_post_profile(_request(stub))) is None


def test_missing_profile_error_points_at_provider_connections(monkeypatch):
    monkeypatch.delenv("BILLING_ENABLED", raising=False)
    with pytest.raises(Exception) as error:
        app_module.resolve_post_profile(None, None)
    assert "Provider connections" in str(error.value.detail)
