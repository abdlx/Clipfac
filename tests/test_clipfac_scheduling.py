"""Scheduled publishing: queue, worker, and REST API."""
import asyncio
import time

import httpx
import pytest
from fastapi import FastAPI

from clipfac import scheduling
from clipfac.scheduling import (cancel_scheduled, due_schedules, list_scheduled,
                                router, run_due_once, schedule_publish,
                                scheduler_loop)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFAC_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("CLIPFAC_REQUIRE_REVIEW", "1")
    monkeypatch.delenv("CLIPFAC_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("UPLOAD_POST_API_KEY", raising=False)
    return tmp_path


def call(app, method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(run())


def body(**overrides):
    payload = {"job_id": "job-1", "clip_index": 0, "platforms": ["youtube"],
               "user_id": "brand-profile", "scheduled_at": time.time() + 3600}
    payload.update(overrides)
    return payload


def test_create_list_cancel(env):
    app = FastAPI()
    app.include_router(router)
    created = call(app, "POST", "/api/clipfac/schedule/", json=body())
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["status"] == "pending" and row["attempts"] == 0
    listed = call(app, "GET", "/api/clipfac/schedule/")
    assert [item["id"] for item in listed.json()["schedules"]] == [row["id"]]
    cancelled = call(app, "DELETE", f"/api/clipfac/schedule/{row['id']}")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "failed"
    assert cancelled.json()["last_error"] == "Cancelled by user"
    assert list_scheduled() == [cancelled.json()]


def test_past_scheduled_at_is_rejected(env):
    app = FastAPI()
    app.include_router(router)
    result = call(app, "POST", "/api/clipfac/schedule/", json=body(scheduled_at=time.time() - 10))
    assert result.status_code == 422
    with pytest.raises(ValueError):
        schedule_publish("job-1", 0, ["youtube"], time.time() - 10)


def test_duplicate_idempotency_key_returns_existing_row(env):
    first = schedule_publish("job-1", 0, ["youtube"], time.time() + 3600,
                             idempotency_key="sched-duplicate-key")
    second = schedule_publish("job-1", 0, ["youtube"], time.time() + 7200,
                              idempotency_key="sched-duplicate-key")
    assert second["id"] == first["id"] and second.get("replayed") is True
    assert len(list_scheduled()) == 1


def test_platform_validation(env):
    with pytest.raises(ValueError):
        schedule_publish("job-1", 0, ["twitch"], time.time() + 60)
    with pytest.raises(ValueError):
        schedule_publish("job-1", 0, ["youtube", "youtube"], time.time() + 60)
    with pytest.raises(ValueError):
        schedule_publish("job-1", 0, [], time.time() + 60)
    app = FastAPI()
    app.include_router(router)
    assert call(app, "POST", "/api/clipfac/schedule/",
                json=body(platforms=["youtube", "tiktok"])).status_code == 201


def test_due_selection(env):
    now = time.time()
    due = schedule_publish("job-1", 0, ["youtube"], now + 60, idempotency_key="sched-due-1")
    future = schedule_publish("job-1", 1, ["youtube"], now + 3600, idempotency_key="sched-due-2")
    assert [row["id"] for row in due_schedules(now + 120)] == [due["id"]]
    assert [row["id"] for row in due_schedules(now + 30)] == []
    assert future["id"] not in [row["id"] for row in due_schedules(now + 120)]
    # A row that already burned its attempts is never due again.
    cancelled = schedule_publish("job-1", 2, ["youtube"], now + 60, idempotency_key="sched-due-3")
    cancel_scheduled(cancelled["id"])
    assert cancelled["id"] not in [row["id"] for row in due_schedules(now + 120)]


def test_cancel_non_pending_is_409(env):
    app = FastAPI()
    app.include_router(router)
    row = schedule_publish("job-1", 0, ["youtube"], time.time() + 60)
    cancel_scheduled(row["id"])
    assert call(app, "DELETE", f"/api/clipfac/schedule/{row['id']}").status_code == 409
    assert call(app, "DELETE", "/api/clipfac/schedule/does-not-exist").status_code == 404


class FakePublishClient:
    """Stands in for the internal HTTP client; records publish calls."""

    def __init__(self, behaviour):
        self.behaviour = behaviour  # fn(body) -> httpx.Response or raises
        self.calls = []

    async def post(self, url, json):
        assert url == "/api/clipfac/publish"
        self.calls.append(json)
        return self.behaviour(json)


def _run(client, now=None):
    return asyncio.run(run_due_once(client, now=now))


def test_run_due_once_success_marks_published(env):
    now = time.time()
    row = schedule_publish("job-1", 0, ["youtube"], now + 60, title="t",
                           idempotency_key="sched-ok-123")
    client = FakePublishClient(lambda body: httpx.Response(
        200, json={"state": "submitted", "response": {"request_id": "upload-1"}}))
    summary = _run(client, now=now + 120)
    assert summary == {"due": 1, "published": 1, "blocked": 0, "failed": 0}
    done = list_scheduled()[0]
    assert done["status"] == "published" and done["published_at"] is not None
    assert done["attempts"] == 1 and done["last_error"] is None
    assert client.calls[0]["idempotency_key"] == "sched-ok-123"
    assert client.calls[0]["platforms"] == ["youtube"]


def test_run_due_once_review_gate_409_marks_blocked(env):
    now = time.time()
    schedule_publish("job-1", 0, ["youtube"], now + 60, idempotency_key="sched-gate-1")
    client = FakePublishClient(lambda body: httpx.Response(
        409, json={"detail": "Current render needs passing technical and sampled visual review before publishing"}))
    summary = _run(client, now=now + 120)
    assert summary == {"due": 1, "published": 0, "blocked": 1, "failed": 0}
    done = list_scheduled()[0]
    assert done["status"] == "blocked"
    assert "review" in done["last_error"]


def test_run_due_once_blocked_publish_state_marks_blocked(env):
    """publish() answers 200 with state=blocked when /api/social/post 4xxs."""
    now = time.time()
    schedule_publish("job-1", 0, ["youtube"], now + 60, idempotency_key="sched-blk-1")
    client = FakePublishClient(lambda body: httpx.Response(
        200, json={"state": "blocked",
                   "response": {"detail": "Upload-Post is not connected — add your Upload-Post key in Provider connections"}}))
    assert _run(client, now=now + 120)["blocked"] == 1
    done = list_scheduled()[0]
    assert done["status"] == "blocked" and "Upload-Post" in done["last_error"]


def test_run_due_once_5xx_and_transport_errors_mark_failed_without_retry(env):
    now = time.time()
    failing = schedule_publish("job-1", 0, ["youtube"], now + 60, idempotency_key="sched-5xx-1")
    erroring = schedule_publish("job-1", 1, ["youtube"], now + 60, idempotency_key="sched-err-1")

    def behaviour(body):
        if body["clip_index"] == 0:
            return httpx.Response(503, json={"detail": "Publishing outcome uncertain"})
        raise RuntimeError("connection reset")

    client = FakePublishClient(behaviour)
    summary = _run(client, now=now + 120)
    assert summary == {"due": 2, "published": 0, "blocked": 0, "failed": 2}
    assert len(client.calls) == 2
    rows = {row["id"]: row for row in list_scheduled()}
    assert rows[failing["id"]]["status"] == "failed"
    assert rows[erroring["id"]]["status"] == "failed"
    assert rows[erroring["id"]]["last_error"] == "RuntimeError: connection reset"
    # A second pass finds nothing due: failed rows are terminal, never retried.
    assert _run(client, now=now + 180) == {"due": 0, "published": 0, "blocked": 0, "failed": 0}
    assert len(client.calls) == 2
    assert rows[failing["id"]]["attempts"] == 1


def test_scheduler_loop_never_crashes_on_a_failing_pass(env, monkeypatch):
    async def run():
        app = FastAPI()
        calls = {"n": 0}

        def boom(now=None, limit=100):
            calls["n"] += 1
            raise RuntimeError("database is gone")

        monkeypatch.setattr(scheduling, "due_schedules", boom)
        task = asyncio.create_task(scheduler_loop(app, interval_seconds=5))
        await asyncio.sleep(0.3)  # first pass fails inside the loop, loop survives
        assert calls["n"] == 1
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


# ---------------------------------------------------------------------------
# End-to-end against the real app paths (real publish endpoint + real
# /api/social/post handler, in-process via ASGI transport — no server, no
# network, no Upload-Post key). The schedule must end in a terminal state
# with a clear error, never lost and never retried in a loop.
# ---------------------------------------------------------------------------

@pytest.fixture
def real_app(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPFAC_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("CLIPFAC_REQUIRE_REVIEW", "1")
    monkeypatch.delenv("CLIPFAC_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("UPLOAD_POST_API_KEY", raising=False)
    import app as app_module
    output_dir = tmp_path / "output"
    monkeypatch.setattr(app_module, "OUTPUT_DIR", str(output_dir))
    job_id = "e2e-job-1"
    job_dir = output_dir / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "clip.mp4").write_bytes(b"e2e-render-bytes")
    app_module.jobs[job_id] = {"status": "completed", "result": {"clips": [
        {"clipfac_render_index": 0, "video_url": f"/videos/{job_id}/clip.mp4"}]}}
    yield app_module, job_id
    app_module.jobs.pop(job_id, None)


def _e2e_run(app_module, now):
    async def run():
        async with scheduling.internal_client(app_module.app) as client:
            return await run_due_once(client, now=now)
    return asyncio.run(run())


def test_e2e_missing_upload_post_key_blocks_schedule(real_app):
    app_module, job_id = real_app
    now = time.time()
    row = schedule_publish(job_id, 0, ["youtube"], now + 60, user_id="brand-profile",
                           idempotency_key="sched-e2e-nokey")
    summary = _e2e_run(app_module, now + 120)
    assert summary == {"due": 1, "published": 0, "blocked": 1, "failed": 0}
    done = list_scheduled()[0]
    assert done["status"] == "blocked" and "Upload-Post is not connected" in done["last_error"]
    assert done["attempts"] == 1
    assert _e2e_run(app_module, now + 180)["due"] == 0  # never retried


def test_e2e_unreviewed_clip_hits_review_gate_before_any_upload(real_app, monkeypatch):
    app_module, job_id = real_app
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "dummy-key-for-gate-check-only")
    now = time.time()
    schedule_publish(job_id, 0, ["youtube"], now + 60, user_id="brand-profile",
                     idempotency_key="sched-e2e-gate1")
    summary = _e2e_run(app_module, now + 120)
    assert summary == {"due": 1, "published": 0, "blocked": 1, "failed": 0}
    done = list_scheduled()[0]
    assert done["status"] == "blocked" and "review" in done["last_error"].lower()
