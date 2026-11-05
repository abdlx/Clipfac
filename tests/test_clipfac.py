"""Production contracts, retries, scoped access and quality gate regressions."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from clipfac import store
from clipfac.auth import InstallationAuth, router as auth_router
from clipfac.integration import install
from clipfac.models import ProductionRequest, editorial_brief
from clipfac.presets import PRESETS, get_preset, render_caption_style
from clipfac.quality import shortlist


def request(**changes):
    return ProductionRequest(source_url="https://example.com/video.mp4", confirm_rights=True, **changes)


@pytest.mark.parametrize("changes", [
    {"upload_id": "abc"}, {"source_url": "file:///tmp/a"}, {"confirm_rights": False},
    {"min_seconds": float("nan")}, {"min_seconds": 40, "max_seconds": 42},
    {"max_clips": 16}, {"unexpected": True},
])
def test_strict_contract(changes):
    body = {"source_url": "https://example.com/v", "confirm_rights": True, **changes}
    with pytest.raises(ValidationError):
        ProductionRequest.model_validate(body)


def test_campaign_and_presets_are_isolated(monkeypatch):
    body = request(campaign={"name": "Launch", "required_phrases": ["approved offer"]})
    assert editorial_brief(body)["campaign"]["required_phrases"] == ["approved offer"]
    assert len(PRESETS) == 6
    original = {"font_size": 44, "uppercase": True}
    assert render_caption_style(original, "podcast")["uppercase"] is False
    assert original["uppercase"] is True
    changed = get_preset("gaming")
    changed["caption"]["font_size"] = 999
    assert get_preset("gaming")["caption"]["font_size"] == 40
    import subtitles
    monkeypatch.setenv("CLIPFAC_STYLE", "fitness")
    assert subtitles.automatic_caption_style()["highlight_color"] == "#FFB86B"
    monkeypatch.delenv("CLIPFAC_STYLE")
    assert subtitles.automatic_caption_style() == subtitles.AUTO_CAPTION_STYLE


def test_idempotency_persists_and_isolates_owners(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFAC_DB_PATH", str(tmp_path / "ledger.db"))
    body = request().model_dump()
    first, created = store.reserve("dex", "key-12345678", body, {})
    assert created
    again, created = store.reserve("dex", "key-12345678", body, {})
    assert not created and again["id"] == first["id"]
    with pytest.raises(store.Conflict):
        store.reserve("dex", "key-12345678", {**body, "style": "gaming"}, {})
    other, created = store.reserve("ezra", "key-12345678", body, {})
    assert created and other["id"] != first["id"]
    assert store.get(first["id"], "ezra") is None
    store.finish(first["id"], "queued", {"job_id": "job-1"})
    assert store.get(first["id"], "dex")["response"]["job_id"] == "job-1"


def test_shortlist_preserves_indexes_and_holds_overlaps():
    clips = [
        {"start": 0, "end": 30, "predicted_score": 75},
        {"start": 5, "end": 35, "predicted_score": 92},
        {"start": 50, "end": 80, "predicted_score": 85},
        {"start": 100, "end": 130, "predicted_score": 40},
        {"start": 150, "end": 180},
    ]
    result = shortlist(clips, request())
    assert [c["index"] for c in result["accepted"]] == [1, 2]
    assert "overlaps_stronger_clip" in result["held"][0]["reasons"]
    result = shortlist(clips[:1], request(campaign={"required_phrases": ["offer"]}), {0: "nothing here"})
    assert result["accepted"] == []
    assert result["held"][0]["reasons"] == ["required_phrase_missing"]
    result = shortlist(clips[:1], request(campaign={"forbidden_phrases": ["fake"]}), {0: "A FAKE claim"})
    assert result["held"][0]["reasons"] == ["forbidden_phrase_present"]


def call(app, method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(run())


def auth_app(monkeypatch):
    monkeypatch.setenv("CLIPFAC_ADMIN_TOKEN", "admin-" + "a" * 32)
    monkeypatch.setenv("CLIPFAC_AGENT_TOKENS", json.dumps([
        {"token": "reader-" + "r" * 32, "scopes": ["read"]},
        {"token": "editor-" + "e" * 32, "scopes": ["read", "process", "edit"]},
    ]))
    app = FastAPI()
    app.add_middleware(InstallationAuth)
    app.include_router(auth_router)
    @app.get("/api/clipfac/presets")
    def presets():
        return {"ok": True}
    @app.post("/api/social/post")
    def post():
        return {"ok": True}
    @app.post("/mcp")
    def mcp():
        return {"ok": True}
    return app


def test_scope_guards_both_rest_and_mcp(monkeypatch):
    app = auth_app(monkeypatch)
    assert call(app, "GET", "/api/clipfac/presets").status_code == 401
    headers = {"Authorization": "Bearer editor-" + "e" * 32}
    assert call(app, "GET", "/api/clipfac/presets", headers=headers).status_code == 200
    assert call(app, "POST", "/api/social/post", headers=headers).status_code == 403
    for tool, status in [("publish_clip", 403), ("produce_clips", 200)]:
        assert call(app, "POST", "/mcp", headers=headers, json={
            "jsonrpc": "2.0", "method": "tools/call", "params": {"name": tool},
        }).status_code == status


def test_cookie_session_requires_same_origin_for_writes(monkeypatch):
    app = auth_app(monkeypatch)
    response = call(app, "POST", "/api/clipfac/session", headers={"Authorization": "Bearer admin-" + "a" * 32})
    cookie = response.headers["set-cookie"].split(";")[0]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert call(app, "GET", "/api/clipfac/presets", headers={"Cookie": cookie}).status_code == 200
    for origin, status in [("", 403), ("http://evil.test", 403), ("http://test", 200)]:
        headers = {"Cookie": cookie}
        if origin:
            headers["Origin"] = origin
        assert call(app, "POST", "/api/social/post", headers=headers).status_code == status


def test_missing_production_token_fails_closed(monkeypatch):
    monkeypatch.delenv("CLIPFAC_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("CLIPFAC_REQUIRE_AUTH", "1")
    with pytest.raises(ValueError, match="CLIPFAC_ADMIN_TOKEN"):
        install(FastAPI(), {})


@pytest.mark.parametrize("interrupted", [False, True])
def test_submission_is_never_duplicated(tmp_path, monkeypatch, interrupted):
    from clipfac.api import router
    monkeypatch.delenv("CLIPFAC_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("CLIPFAC_DB_PATH", str(tmp_path / "ledger.db"))
    app = FastAPI()
    app.include_router(router)
    submitted = []
    @app.post("/api/process")
    async def process(body: dict):
        submitted.append(body)
        if interrupted:
            raise RuntimeError("lost connection after acceptance")
        return {"job_id": "test-job", "status": "queued"}
    body = {**request(style="gaming").model_dump(), "idempotency_key": "same-request-123"}
    first = call(app, "POST", "/api/clipfac/productions", json=body)
    assert first.status_code == (503 if interrupted else 200)
    again = call(app, "POST", "/api/clipfac/productions", json=body)
    assert again.json()["replayed"] is True
    assert len(submitted) == 1
    assert submitted[0]["clipfac_style"] == "gaming"
    assert submitted[0]["layouts"] == ["screencast"]
    assert "target_clips" not in submitted[0]
    if interrupted:
        assert again.json()["state"] == "uncertain"


def test_media_inspection_detects_silence(monkeypatch):
    from clipfac.quality import inspect_media
    import clipfac.quality as quality
    results = iter([
        SimpleNamespace(returncode=0, stdout=json.dumps({"streams": [
            {"codec_type": "video", "width": 1080, "height": 1920}, {"codec_type": "audio"}],
            "format": {"duration": "30"}})),
        SimpleNamespace(returncode=0, stderr="mean_volume: -inf dB\nmax_volume: -inf dB"),
    ])
    monkeypatch.setattr(quality.subprocess, "run", lambda *args, **kwargs: next(results))
    report = inspect_media(Path("/render.mp4"))
    assert "silent_audio" in report["issues"]
    assert "editorial_coherence" in report["not_checked"]
