"""Current-byte review gating and idempotent publishing across agent roles."""
import asyncio
import hashlib
import json

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from clipfac import reviews
from clipfac.auth import request_owner
from clipfac.publishing import enforce_review, router


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFAC_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("CLIPFAC_REQUIRE_REVIEW", "1")
    monkeypatch.delenv("CLIPFAC_ADMIN_TOKEN", raising=False)
    return tmp_path


def call(app, method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(run())


def test_gate_requires_current_render_bytes(env):
    path = env / "clip.mp4"
    path.write_bytes(b"first-render")
    request = Request({"type": "http", "headers": []})
    with pytest.raises(HTTPException) as error:
        asyncio.run(enforce_review(request, "job-1", 0, path))
    assert error.value.status_code == 409
    report, _ = reviews.reserve("milo", "job-1", 0, hashlib.sha256(b"first-render").hexdigest())
    reviews.finish(report["id"], "completed", {"decision": "sampled_checks_passed"})
    assert asyncio.run(enforce_review(request, "job-1", 0, path)) == report["id"]
    path.write_bytes(b"changed-render")
    with pytest.raises(HTTPException):
        asyncio.run(enforce_review(request, "job-1", 0, path))


def test_scoped_tokens_share_workspace_artifacts(env, monkeypatch):
    monkeypatch.setenv("CLIPFAC_ADMIN_TOKEN", "admin-" + "a" * 32)
    monkeypatch.setenv("CLIPFAC_AGENT_TOKENS", json.dumps([
        {"token": "dex-" + "d" * 32, "scopes": ["read", "edit"]},
        {"token": "milo-" + "m" * 32, "scopes": ["read"]},
    ]))
    dex = Request({"type": "http", "headers": [(b"authorization", ("Bearer dex-" + "d" * 32).encode())]})
    milo = Request({"type": "http", "headers": [(b"authorization", ("Bearer milo-" + "m" * 32).encode())]})
    assert request_owner(dex) == request_owner(milo) == "local"


def test_publish_retries_do_not_repeat_external_submission(env):
    app = FastAPI()
    app.include_router(router)
    submitted = []
    @app.post("/api/social/post")
    async def post(body: dict):
        submitted.append(body)
        return {"request_id": "upload-123"}
    body = {"job_id": "job-1", "clip_index": 0, "user_id": "brand-profile",
            "platforms": ["youtube"], "idempotency_key": "publish-request-123"}
    first = call(app, "POST", "/api/clipfac/publish", json=body)
    assert first.status_code == 200, first.text
    second = call(app, "POST", "/api/clipfac/publish", json=body)
    assert second.json()["replayed"] and len(submitted) == 1
    assert second.json()["state"] == "submitted"


def test_uncertain_publication_is_never_resent(env):
    app = FastAPI()
    app.include_router(router)
    submitted = []
    @app.post("/api/social/post")
    async def post():
        submitted.append(True)
        raise RuntimeError("lost connection after platform acceptance")
    body = {"job_id": "job-1", "clip_index": 0, "user_id": "brand-profile",
            "platforms": ["youtube"], "idempotency_key": "publish-uncertain"}
    assert call(app, "POST", "/api/clipfac/publish", json=body).status_code == 503
    result = call(app, "POST", "/api/clipfac/publish", json=body)
    assert result.json()["state"] == "uncertain" and len(submitted) == 1


def test_multiple_social_profiles_require_explicit_selection(env):
    app = FastAPI()
    app.include_router(router)
    @app.get("/api/social/user")
    def profiles():
        return {"profiles": [{"username": "one"}, {"username": "two"}]}
    result = call(app, "POST", "/api/clipfac/publish", json={
        "job_id": "job", "clip_index": 0, "platforms": ["youtube"], "idempotency_key": "profile-request",
    })
    assert result.status_code == 400
