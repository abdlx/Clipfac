"""Staged workflow tests without external models, downloads or GPU dependencies."""
import asyncio
import copy
import hashlib
import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

from clipfac import plans, store
from clipfac.models import SubmitAnalysis, TranscriptWindow
from clipfac.stages import ANALYSIS_FILE, read_analysis, source_hash


def analysis_fixture():
    return {
        "schema_version": 1,
        "source": {"filename": "source.mp4", "sha256": hashlib.sha256(b"source").hexdigest(),
                   "duration_seconds": 120, "size_bytes": 6},
        "selection": {"duration": [15, 60], "max_clips": 15},
        "profile": {"style": "podcast", "layout": "none", "fallback": False},
        "transcript": {"language": "en", "segments": [
            {"start": 0, "end": 30, "text": "An approved offer with context."},
            {"start": 60, "end": 90, "text": "A second complete thought."},
        ]},
        "campaign": {},
        "candidates": [
            {"start": 0, "end": 30, "predicted_score": 90, "video_title_for_youtube_short": "First"},
            {"start": 60, "end": 90, "predicted_score": 85, "video_title_for_youtube_short": "Second"},
        ],
        "warnings": [], "contact_sheet": None,
    }


def cut(index=0):
    return {"candidate_index": index, "start_ms": index * 60000,
            "end_ms": index * 60000 + 30000, "title": "An honest title", "score": 90}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFAC_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    monkeypatch.delenv("CLIPFAC_ADMIN_TOKEN", raising=False)
    return tmp_path


def test_auto_style_contract_and_transcript_window():
    body = SubmitAnalysis(source_url="https://example.com/video", confirm_rights=True,
                          idempotency_key="analysis-request")
    assert body.style == "auto"
    with pytest.raises(ValueError):
        TranscriptWindow(analysis_job_id="job", start_ms=0, end_ms=300001)


def test_plan_compiler_is_stable_and_preserves_integer_boundaries(ledger):
    doc = plans.create("dex", "analysis-1", analysis_fixture(), [cut()], "podcast", True)
    first = plans.compile_candidates(doc)
    assert first == plans.compile_candidates(copy.deepcopy(doc))
    assert first["shorts"][0]["start"] == 0
    assert first["shorts"][0]["end"] == 30
    assert doc["digest"] == plans.digest(doc)
    with pytest.raises(ValueError):
        plans.PlanClip(candidate_index=0, start_ms=1.5, end_ms=30000)
    invalid = copy.deepcopy(doc)
    invalid["clips"][0]["end_ms"] = 200000
    with pytest.raises(ValueError):
        plans.compile_candidates(invalid)


def test_revision_conflict_keeps_previous_plan_immutable(ledger):
    original = plans.create("dex", "analysis-1", analysis_fixture(), [cut()], "podcast", True)
    revised_cut = {**cut(), "title": "Revised title"}
    revision = plans.revise("dex", plans.RevisePlan(plan_id=original["id"], expected_revision=1,
                                                   clips=[revised_cut], style="cinematic"))
    assert revision["revision"] == 2 and revision["parent"] == original["id"]
    assert plans.get(original["id"], "dex") == original
    assert plans.get(original["id"], "ezra") is None
    assert plans.latest(original["id"], "dex")["id"] == revision["id"]
    with pytest.raises(store.Conflict):
        plans.revise("dex", plans.RevisePlan(plan_id=original["id"], expected_revision=1, clips=[cut()]))


def test_unknown_candidate_and_too_many_clips_are_rejected(ledger):
    bad = cut(9)
    with pytest.raises(ValueError):
        plans.create("dex", "analysis-1", analysis_fixture(), [bad], "podcast", True)


def test_analysis_reader_rejects_path_traversal(ledger):
    with pytest.raises(ValueError):
        read_analysis(ledger, "../escape")


def call(app, method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(run())


def stage_app(ledger):
    from clipfac.pipeline_api import router
    root = ledger / "output"
    directory = root / "analysis-1"
    directory.mkdir(parents=True)
    (directory / ANALYSIS_FILE).write_text(json.dumps(analysis_fixture()))
    app = FastAPI()
    app.state.clipfac_namespace = {"OUTPUT_DIR": str(root)}
    app.include_router(router)
    return app


def test_create_plan_and_read_bounded_transcript(ledger):
    app = stage_app(ledger)
    response = call(app, "POST", "/api/clipfac/plans", json={"analysis_job_id": "analysis-1"})
    assert response.status_code == 200, response.text
    document = response.json()
    assert len(document["clips"]) == 2
    assert document["source_sha256"] == analysis_fixture()["source"]["sha256"]
    assert call(app, "GET", "/api/clipfac/analyses/analysis-1").json().get("transcript") is None
    transcript = call(app, "GET", "/api/clipfac/analyses/analysis-1/transcript?start_ms=0&end_ms=30000")
    assert len(transcript.json()["segments"]) == 1


def test_analysis_submission_marks_analysis_without_requesting_render(ledger):
    app = stage_app(ledger)
    calls = []
    @app.post("/api/process")
    async def process(body: dict):
        calls.append(body)
        return {"job_id": "analysis-1"}
    body = {"source_url": "https://example.com/video", "confirm_rights": True,
            "idempotency_key": "analyze-once-123"}
    first = call(app, "POST", "/api/clipfac/analyses", json=body)
    assert first.status_code == 200, first.text
    assert calls[0]["clipfac_analysis_only"] is True
    assert calls[0]["clipfac_auto_style"] is True
    assert call(app, "POST", "/api/clipfac/analyses", json=body).json()["replayed"]
    assert len(calls) == 1


def test_plan_render_reuses_source_and_requires_exact_revision(ledger):
    app = stage_app(ledger)
    doc = plans.create("local", "analysis-1", analysis_fixture(), [cut()], "podcast", True)
    submissions = []
    @app.post("/api/process")
    async def process(body: dict):
        submissions.append(body)
        return {"job_id": "render-1"}
    body = {"plan_id": doc["id"], "expected_revision": 2, "idempotency_key": "render-plan-123"}
    assert call(app, "POST", "/api/clipfac/plans/render", json=body).status_code == 409
    body["expected_revision"] = 1
    assert call(app, "POST", "/api/clipfac/plans/render", json=body).status_code == 200
    assert submissions[0]["clipfac_plan_id"] == doc["id"]
    assert not submissions[0].get("url")
    assert call(app, "POST", "/api/clipfac/plans/render", json=body).json()["replayed"]
    assert len(submissions) == 1


def test_real_processing_endpoint_accepts_retained_plan_without_model_key(ledger, monkeypatch):
    import app as app_module
    data = analysis_fixture()
    output = ledger / "retained"
    source_dir = output / "analysis-1"
    source_dir.mkdir(parents=True)
    source = source_dir / "source.mp4"
    source.write_bytes(b"source")
    (source_dir / ANALYSIS_FILE).write_text(json.dumps(data))
    document = plans.create("local", "analysis-1", data, [cut()], "podcast", True)
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(output))
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(ledger / "uploads"))
    monkeypatch.setattr(app_module, "_enqueue_job", lambda *args: None)
    async def no_key(_request):
        return None
    monkeypatch.setattr(app_module, "resolve_gemini", no_key)
    response = call(app_module.app, "POST", "/api/process", json={
        "acknowledged": True, "clipfac_plan_id": document["id"],
        "clipfac_style": "podcast", "clipfac_brief": {}, "clipfac_max_clips": 1,
        "layouts": [], "captions": True,
    })
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    try:
        job = app_module.jobs[job_id]
        assert "-u" not in job["cmd"]
        assert "CLIPFAC_RENDER_PLAN_FILE" in job["env"]
        retained = Path(job["output_dir"]) / "clipfac-source.mp4"
        assert source_hash(retained) == document["source_sha256"]
    finally:
        app_module.jobs.pop(job_id, None)


def test_director_honors_explicit_style(monkeypatch):
    import clipfac.director as director
    profile = director.FootageProfile(style="gaming", layout="screencast", confidence=.9,
                                      subject="game", mood="energetic", reasoning="frames",
                                      preserve=["HUD"], avoid=["cropped HUD"])
    monkeypatch.setattr(director, "generate_json", lambda *args: (profile, {"cost_usd": None}))
    chosen = director.choose_direction({"segments": []}, [b"jpg"], {}, requested_style="education")
    assert chosen["style"] == "education"
    assert chosen["mode"] == "multimodal"


def test_analysis_completion_does_not_require_rendered_clips(ledger, monkeypatch):
    import app as app_module
    directory = ledger / "analysis-done"
    directory.mkdir()
    (directory / ANALYSIS_FILE).write_text(json.dumps(analysis_fixture()))
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(ledger))
    class Process:
        returncode = 0
        stdout = io.BytesIO(b"")
        def poll(self):
            return 0
    monkeypatch.setattr(app_module.subprocess, "Popen", lambda *args, **kwargs: Process())
    job = {"cmd": ["python", "main.py"], "env": {"CLIPFAC_ANALYSIS_ONLY": "1"},
           "output_dir": str(directory), "logs": [], "status": "queued"}
    app_module.jobs["analysis-done"] = job
    try:
        asyncio.run(app_module.run_job("analysis-done", job))
        assert job["status"] == "completed"
        assert job["result"]["analysis"]["candidate_count"] == 2
        assert job["result"]["clips"] == []
    finally:
        app_module.jobs.pop("analysis-done", None)



def test_long_source_coverage_scales_without_changing_legacy_defaults(monkeypatch):
    from clip_selection import shortlist_target
    monkeypatch.delenv("CLIPFAC_CANDIDATE_LIMIT", raising=False)
    assert shortlist_target(7200) == 10
    monkeypatch.setenv("CLIPFAC_CANDIDATE_LIMIT", "60")
    assert shortlist_target(7200) == 60
    monkeypatch.setenv("CLIPFAC_CANDIDATE_LIMIT", "100000")
    assert shortlist_target(7200) == 60



def test_analysis_preserves_existing_subtitle_band_for_later_render(tmp_path, monkeypatch):
    import clipfac.stages as stages
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(stages, "sample_source", lambda *args: ([], []))
    monkeypatch.setattr(stages, "choose_direction", lambda *args, **kwargs: {
        "style": "podcast", "layout": "none", "fallback": False})
    band = {"top": .82, "bottom": .96}
    data = stages.analyze_source(source, {"segments": []}, 30, {"shorts": []},
                                  tmp_path, {}, "podcast", source_subtitle_band=band)
    assert data["source_subtitle_band"] == band
    stored = json.loads((tmp_path / stages.ANALYSIS_FILE).read_text())
    assert stored["source_subtitle_band"] == band
