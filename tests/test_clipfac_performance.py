"""Performance loop v1: record/upsert, ranking, prompt feedback, router."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI

import gemini_worker
from clipfac import performance
from clipfac.performance import (
    FEEDBACK_MAX_CHARS,
    ensure_schema,
    feedback_text,
    list_performance,
    record_performance,
    top_performers,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFAC_DB_PATH", str(tmp_path / "perf.db"))
    monkeypatch.delenv("CLIPFAC_ADMIN_TOKEN", raising=False)
    return tmp_path


def _seed_winner(env):
    record_performance("job-a", 0, "tiktok", 1200, 80, post_url="https://tiktok.example/v1",
                       title="The one email that almost ruined me",
                       hook="Stop doing this. Seriously.",
                       start_s=12.5, end_s=41.0, predicted_score=88)
    record_performance("job-a", 1, "youtube", 300, 12, title="Quiet inventory tour",
                       start_s=100.0, end_s=130.0, predicted_score=55)
    record_performance("job-b", 0, "instagram", 5400, 210,
                       title="97% of people miss this",
                       hook="97% of people miss this.",
                       start_s=5.0, end_s=28.0, predicted_score=91)


def test_ensure_schema_is_idempotent(env):
    ensure_schema()
    ensure_schema()
    assert list_performance() == []


def test_record_and_upsert_replaces_numbers(env):
    first = record_performance("job-a", 0, "tiktok", 100, 5)
    assert first["views"] == 100 and first["likes"] == 5
    assert first["source"] == "manual"
    again = record_performance("job-a", 0, "tiktok", 250, 9,
                               post_url="https://tiktok.example/v1", source="upload_post",
                               title="Kept title")
    assert again["id"] == first["id"]
    assert again["views"] == 250 and again["likes"] == 9
    assert again["source"] == "upload_post"
    assert again["post_url"] == "https://tiktok.example/v1"
    assert len(list_performance()) == 1
    # Same clip on another platform is a separate row.
    record_performance("job-a", 0, "youtube", 40, 1)
    assert len(list_performance()) == 2


def test_upsert_keeps_traits_when_re_record_omits_them(env):
    record_performance("job-a", 0, "tiktok", 100, 5, title="Hooky title",
                       hook="Stop this.", start_s=1.0, end_s=20.0, predicted_score=80)
    updated = record_performance("job-a", 0, "tiktok", 200, 6)
    assert updated["title"] == "Hooky title"
    assert updated["hook"] == "Stop this."
    assert updated["start_s"] == 1.0 and updated["end_s"] == 20.0
    assert updated["predicted_score"] == 80


def test_record_rejects_unknown_platform_and_source(env):
    with pytest.raises(ValueError):
        record_performance("job-a", 0, "facebook", 1, 0)
    with pytest.raises(ValueError):
        record_performance("job-a", 0, "tiktok", 1, 0, source="scraped")


def test_list_filters_by_job_and_platform(env):
    _seed_winner(env)
    assert len(list_performance()) == 3
    assert len(list_performance(job_id="job-a")) == 2
    assert len(list_performance(platform="instagram")) == 1
    assert list_performance(platform="instagram")[0]["job_id"] == "job-b"
    assert len(list_performance(job_id="job-a", platform="youtube")) == 1


def test_top_performers_ranked_by_views(env):
    _seed_winner(env)
    winners = top_performers(limit=5)
    assert [w["job_id"] for w in winners] == ["job-b", "job-a", "job-a"]
    assert winners[0]["views"] == 5400
    assert top_performers(limit=1)[0]["job_id"] == "job-b"


def test_feedback_empty_when_no_data(env):
    assert feedback_text() == ""


def test_feedback_names_the_winner_and_its_traits(env):
    _seed_winner(env)
    text = feedback_text()
    assert "97% of people miss this" in text
    assert "5-28s" in text
    assert "score 91" in text
    assert "5.4k views" in text
    assert "instagram" in text
    assert "Bias toward moments like these" in text
    assert len(text) <= FEEDBACK_MAX_CHARS


def test_feedback_capped_at_five_winners(env):
    for index in range(8):
        record_performance(f"job-{index}", 0, "tiktok", 1000 + index, 0,
                           title=f"Clip {index}")
    text = feedback_text()
    assert text.count("Clip ") == 5
    assert len(text) <= FEEDBACK_MAX_CHARS


def test_feedback_falls_back_to_job_and_clip_without_traits(env):
    record_performance("job-x", 3, "youtube", 999, 0)
    text = feedback_text()
    assert "job job-x clip 3" in text
    assert "999 views" in text


def _detail_prompt(**overrides):
    fmt = {
        "video_duration": 600,
        "language": "English",
        "windows_json": "[]",
        "min_clips": 6,
        "max_clips": 20,
        "min_secs": 15,
        "max_secs": 60,
        "performance_feedback": "",
    }
    fmt.update(overrides)
    return gemini_worker.DETAIL_PROMPT_TEMPLATE.format(**fmt)


def test_prompt_unchanged_when_store_is_empty(env):
    assert feedback_text() == ""
    prompt = _detail_prompt(performance_feedback=feedback_text())
    assert "Past top-performing" not in prompt
    assert "Bias toward moments" not in prompt
    # The score template never carries the feedback placeholder at all.
    assert "{performance_feedback}" not in gemini_worker.SCORE_PROMPT_TEMPLATE
    assert "performance_feedback" not in gemini_worker.SCORE_PROMPT_TEMPLATE


def test_prompt_carries_feedback_when_winners_exist(env):
    _seed_winner(env)
    prompt = _detail_prompt(performance_feedback=feedback_text())
    assert "97% of people miss this" in prompt
    assert "5.4k views" in prompt


def _client():
    app = FastAPI()
    app.include_router(performance.router)
    return app


def _call(app, method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(run())


def test_router_record_list_feedback(env):
    app = _client()
    response = _call(app, "POST", "/api/clipfac/performance/record", json={
        "job_id": "job-a", "clip_index": 0, "platform": "tiktok",
        "views": 1200, "likes": 80, "title": "The one email that almost ruined me",
        "hook": "Stop doing this.", "start_s": 12.5, "end_s": 41.0,
        "predicted_score": 88})
    assert response.status_code == 200, response.text
    assert response.json()["views"] == 1200
    # Upsert through the endpoint replaces the numbers.
    response = _call(app, "POST", "/api/clipfac/performance/record", json={
        "job_id": "job-a", "clip_index": 0, "platform": "tiktok",
        "views": 1500, "likes": 90})
    assert response.status_code == 200
    assert response.json()["views"] == 1500
    assert response.json()["title"] == "The one email that almost ruined me"

    response = _call(app, "GET", "/api/clipfac/performance/list",
                     params={"job_id": "job-a"})
    assert response.status_code == 200
    assert len(response.json()["performance"]) == 1

    response = _call(app, "GET", "/api/clipfac/performance/feedback")
    assert response.status_code == 200
    body = response.json()
    assert "The one email that almost ruined me" in body["feedback"]
    assert body["top_performers"][0]["views"] == 1500


def test_router_rejects_bad_platform(env):
    app = _client()
    response = _call(app, "POST", "/api/clipfac/performance/record", json={
        "job_id": "job-a", "clip_index": 0, "platform": "facebook", "views": 1})
    assert response.status_code == 422
    response = _call(app, "GET", "/api/clipfac/performance/list",
                     params={"platform": "facebook"})
    assert response.status_code == 422


def test_router_feedback_empty_store(env):
    app = _client()
    response = _call(app, "GET", "/api/clipfac/performance/feedback")
    assert response.status_code == 200
    assert response.json() == {"feedback": "", "top_performers": []}


def test_mcp_routes_shape():
    routes = performance.MCP_ROUTES
    assert set(routes) == {"record_clip_performance", "get_performance_feedback",
                           "list_clip_performance"}
    for name, (scope, method, endpoint, model, description) in routes.items():
        assert scope in ("read", "edit")
        assert endpoint.startswith("/api/clipfac/performance/")
        assert description
    assert routes["record_clip_performance"][1] == "POST"
    assert routes["get_performance_feedback"][1] == "GET"
    assert routes["list_clip_performance"][1] == "GET"
    # The record model accepts the full trait snapshot; the feedback tool
    # takes no arguments, matching mcp.py's no-model GET handling.
    assert routes["get_performance_feedback"][3] is None
