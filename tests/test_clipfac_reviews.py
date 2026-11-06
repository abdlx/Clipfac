"""Visual review evidence, cache isolation and bounded non-destructive repairs."""
import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from clipfac import plans, reviews, store
from clipfac.stages import ANALYSIS_FILE


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFAC_DB_PATH", str(tmp_path / "reviews.db"))
    monkeypatch.delenv("CLIPFAC_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("CLIPFAC_REPAIR_LIMIT", "2")
    return tmp_path


def call(app, method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(run())


def test_review_is_bound_to_media_hash_and_owner(env):
    first, created = reviews.reserve("dex", "job", 0, "sha-1")
    assert created
    reviews.finish(first["id"], "completed", {"decision": "review_required"})
    cached, created = reviews.reserve("dex", "job", 0, "sha-1")
    assert not created and cached["id"] == first["id"]
    changed, created = reviews.reserve("dex", "job", 0, "sha-2")
    assert created and changed["id"] != first["id"]
    assert reviews.get(first["id"], "milo") is None


def test_failed_review_requires_explicit_retry(env):
    first, _ = reviews.reserve("dex", "job", 0, "sha-1")
    reviews.finish(first["id"], "failed", {"decision": "review_unavailable"})
    assert reviews.reserve("dex", "job", 0, "sha-1")[1] is False
    retried, created = reviews.reserve("dex", "job", 0, "sha-1", retry_failed=True)
    assert created and retried["id"] == first["id"] and retried["state"] == "reviewing"


def test_repair_budget_survives_repeated_calls(env):
    first, created = reviews.reserve_repair("dex", "review-1", "same-source-campaign-candidate")
    assert created and first["attempt"] == 1
    replayed, created = reviews.reserve_repair("dex", "review-1", "same-source-campaign-candidate")
    assert not created and replayed["id"] == first["id"]
    second, created = reviews.reserve_repair("dex", "review-2", "same-source-campaign-candidate")
    assert created and second["attempt"] == 2
    with pytest.raises(store.Conflict):
        reviews.reserve_repair("dex", "review-3", "same-source-campaign-candidate")


def review_app(env, monkeypatch, hook=None, provider_fails=False):
    import clipfac.review_api as api
    output = env / "output"
    analysis_dir = output / "analysis-1"
    render_dir = output / "render-1"
    analysis_dir.mkdir(parents=True)
    render_dir.mkdir()
    analysis = {
        "schema_version": 1,
        "source": {"filename": "source.mp4", "duration_seconds": 90,
                   "sha256": hashlib.sha256(b"source").hexdigest(), "size_bytes": 6},
        "selection": {"duration": [15, 60], "max_clips": 15},
        "profile": {"style": "podcast", "layout": "none"},
        "campaign": {}, "warnings": [], "contact_sheet": None,
        "transcript": {"language": "en", "segments": [{"start": 0, "end": 30, "text": "This is an approved source quote."}]},
        "candidates": [{"start": 0, "end": 30, "predicted_score": 90}],
    }
    (analysis_dir / ANALYSIS_FILE).write_text(json.dumps(analysis))
    original = plans.create("local", "analysis-1", analysis, [{
        "candidate_index": 0, "start_ms": 0, "end_ms": 30000, "score": 90, "title": "Original",
    }], "podcast", True)
    (render_dir / "clip.mp4").write_bytes(b"rendered")
    (render_dir / "test_metadata.json").write_text(json.dumps({"clipfac_plan": {"id": original["id"]}}))
    app = FastAPI()
    app.state.clipfac_namespace = {"OUTPUT_DIR": str(output)}
    app.include_router(api.router)
    @app.get("/api/status/render-1")
    def status():
        return {"status": "completed", "result": {"clips": [{
            "video_url": "/videos/render-1/clip.mp4", "clipfac_candidate_index": 0, "clipfac_render_index": 0,
        }]}}
    @app.get("/api/clip/render-1/0/transcript")
    def transcript():
        return {"captions": [{"text": "An approved quote", "startMs": 0, "endMs": 3000}]}
    monkeypatch.setattr(api, "inspect_media", lambda path: {
        "duration_seconds": 30, "issues": [], "width": 1080, "height": 1920,
        "audio": True, "status": "technical_checks_passed"})
    monkeypatch.setattr(api, "sample_source", lambda *args: ([b"frame"], [1.0]))
    model_calls = []
    def visual(*args):
        model_calls.append(True)
        if provider_fails:
            raise RuntimeError("provider down")
        return reviews.VisualReview(
            verdict="revise", confidence=.9, summary="Use restrained captions.",
            findings=[reviews.Finding(category="caption_readability", severity="major",
                                      description="The text is too emphatic.", sample_index=0)],
            repair=reviews.RepairProposal(style="cinematic", hook=hook),
        ), {"cost_usd": None}
    monkeypatch.setattr(reviews, "review_visual", visual)
    return app, original, model_calls


def test_review_then_repair_creates_new_single_clip_plan(env, monkeypatch):
    app, original, calls = review_app(env, monkeypatch)
    result = call(app, "POST", "/api/clipfac/reviews", json={"job_id": "render-1", "clip_index": 0})
    assert result.status_code == 200, result.text
    report = result.json()
    assert report["report"]["decision"] == "review_required"
    cached = call(app, "POST", "/api/clipfac/reviews", json={"job_id": "render-1", "clip_index": 0})
    assert cached.json()["cached"] and len(calls) == 1
    repair = call(app, "POST", "/api/clipfac/reviews/repair", json={"review_id": report["id"]})
    assert repair.status_code == 200, repair.text
    fixed = plans.get(repair.json()["plan_id"], "local")
    assert fixed["style"] == "cinematic" and len(fixed["clips"]) == 1
    assert plans.get(original["id"], "local") == original
    assert (env / "output/render-1/clip.mp4").read_bytes() == b"rendered"


def test_automatic_hook_cannot_invent_a_claim(env, monkeypatch):
    app, _, _ = review_app(env, monkeypatch, hook="Guaranteed to make you rich")
    report = call(app, "POST", "/api/clipfac/reviews", json={"job_id": "render-1", "clip_index": 0}).json()
    repair = call(app, "POST", "/api/clipfac/reviews/repair", json={"review_id": report["id"]})
    assert repair.status_code == 422
    assert "verbatim" in repair.json()["detail"]


def test_provider_failure_is_never_a_pass(env, monkeypatch):
    app, _, _ = review_app(env, monkeypatch, provider_fails=True)
    report = call(app, "POST", "/api/clipfac/reviews", json={"job_id": "render-1", "clip_index": 0}).json()
    assert report["state"] == "failed"
    assert report["report"]["decision"] == "review_unavailable"
    assert report["report"]["technical"]["status"] == "technical_checks_passed"


def test_changed_render_invalidates_repair(env, monkeypatch):
    app, _, _ = review_app(env, monkeypatch)
    report = call(app, "POST", "/api/clipfac/reviews", json={"job_id": "render-1", "clip_index": 0}).json()
    (env / "output/render-1/clip.mp4").write_bytes(b"edited-after-review")
    result = call(app, "POST", "/api/clipfac/reviews/repair", json={"review_id": report["id"]})
    assert result.status_code == 409
