"""Scheduled publishing: a queue + worker on top of the existing publish flow.

A schedule names a clip (job_id + clip_index), the platforms, and a UTC
time. When the time comes the worker calls the existing
``POST /api/clipfac/publish`` endpoint through the app's own internal HTTP
client, so the review gate, the idempotency ledger and the Upload-Post
call all behave exactly as they do for an immediate publish. Nothing here
duplicates that flow and nothing here bypasses the review gate.

Ported ideas from SupoClip (``backend/src/services/social_service.py``,
``src/workers/cron_worker.py``): claim due rows, dispatch one at a time,
mark a row failed (never delete it, never retry in the same pass) when the
dispatch itself fails. What does NOT port: SupoClip's Redis/arq cron
worker and its Postgres social_posts table — Clipfac is a single Python
app on SQLite, so the worker is an asyncio task started from the app
lifespan and the queue is a SQLite table beside the productions ledger.

Wiring (coordinator — do NOT edit those files from here):

1. Router mount — add to ``clipfac/integration.py::install`` next to the
   other ``include_router`` calls::

       from clipfac.scheduling import router as scheduling_router
       app.include_router(scheduling_router)

2. Lifespan hook — add to ``app.py::lifespan`` next to the outbox task::

       schedule_task = None
       if not BILLING_ENABLED:
           from clipfac.scheduling import scheduler_loop as _run_schedule_loop
           schedule_task = asyncio.create_task(_run_schedule_loop(app))
       yield
       if schedule_task:
           schedule_task.cancel()
           with suppress(asyncio.CancelledError):
               await schedule_task

   (``suppress`` is already imported in app.py.)

3. MCP tools — add to ``clipfac/mcp.py`` immediately after the ``ROUTES``
   dict, BEFORE the ``TOOL_SCOPES = ...`` line (it is built from ROUTES)::

       from clipfac.scheduling import SCHEDULING_ROUTES
       ROUTES.update(SCHEDULING_ROUTES)

4. Auth scope — ``clipfac/auth.py::required_scope`` falls through to
   ``"admin"`` for unknown POST/DELETE paths, which would lock agent
   tokens with only the ``publish`` scope out of scheduling. Add before
   the final ``return "admin"``::

       if path.startswith("/api/clipfac/schedule"):
           return "publish" if method in ("POST", "DELETE") else "read"
"""
import asyncio
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, field_validator

from clipfac import store
from clipfac.auth import request_owner
from clipfac.models import ClipRef, StrictModel

PLATFORMS = ("tiktok", "instagram", "youtube")
STATUSES = ("pending", "publishing", "published", "blocked", "failed")
MAX_ATTEMPTS = 3
KEY_PATTERN = r"^[A-Za-z0-9._:-]+$"


class SchedulingConflict(ValueError):
    """A schedule exists but cannot move to the requested state."""


def ensure_schema():
    """Create the schedule table idempotently (same DB pattern as store.py)."""
    with store.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS scheduled_publishes (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, clip_index INTEGER NOT NULL,
            platforms TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
            user_id TEXT, scheduled_at REAL NOT NULL, timezone TEXT,
            idempotency_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
            attempts INTEGER NOT NULL, last_error TEXT,
            created_at REAL NOT NULL, published_at REAL)""")
        db.commit()


def _decode(row):
    if row is None:
        return None
    data = dict(row)
    data["platforms"] = json.loads(data["platforms"])
    return data


def _get(schedule_id):
    ensure_schema()
    with store.connect() as db:
        return _decode(db.execute(
            "SELECT * FROM scheduled_publishes WHERE id=?", (schedule_id,)).fetchone())


def _set_status(schedule_id, status, last_error=None, published_at=None):
    with store.connect() as db:
        db.execute("UPDATE scheduled_publishes SET status=?, last_error=?, published_at=? WHERE id=?",
                   (status, last_error, published_at, schedule_id))
        db.commit()
    return _get(schedule_id)


def schedule_publish(job_id, clip_index, platforms, scheduled_at, title="", description="",
                     user_id=None, timezone=None, idempotency_key=None, now=None):
    """Create a schedule, or return the existing row for a duplicate key.

    ``scheduled_at`` is UTC epoch seconds and must be in the future.
    ``platforms`` must be a distinct subset of {tiktok, instagram, youtube}.
    """
    now = time.time() if now is None else now
    if clip_index < 0:
        raise ValueError("clip_index must be >= 0")
    platforms = list(platforms)
    if not platforms or len(set(platforms)) != len(platforms):
        raise ValueError("platforms must be distinct and non-empty")
    if any(platform not in PLATFORMS for platform in platforms):
        raise ValueError(f"platforms must be a subset of {sorted(PLATFORMS)}")
    if scheduled_at <= now:
        raise ValueError("scheduled_at must be in the future")
    if idempotency_key is None:
        idempotency_key = "sched-" + uuid.uuid4().hex
    if len(idempotency_key) < 8 or not re.match(KEY_PATTERN, idempotency_key):
        raise ValueError("idempotency_key must be >= 8 chars of [A-Za-z0-9._:-]")
    ensure_schema()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM scheduled_publishes WHERE idempotency_key=?",
                              (idempotency_key,)).fetchone()
        if existing:
            db.commit()
            return {**_decode(existing), "replayed": True}
        identity = str(uuid.uuid4())
        db.execute("INSERT INTO scheduled_publishes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (identity, job_id, clip_index, json.dumps(platforms), title or "",
                    description or "", user_id, float(scheduled_at), timezone,
                    idempotency_key, "pending", 0, None, now, None))
        db.commit()
        return _get(identity)


def list_scheduled(status=None, limit=100):
    """Schedules soonest first; optionally filtered to one status."""
    ensure_schema()
    with store.connect() as db:
        if status is not None:
            if status not in STATUSES:
                raise ValueError(f"Unknown status: {status}")
            rows = db.execute("SELECT * FROM scheduled_publishes WHERE status=? "
                              "ORDER BY scheduled_at ASC LIMIT ?", (status, limit))
        else:
            rows = db.execute("SELECT * FROM scheduled_publishes ORDER BY scheduled_at ASC LIMIT ?",
                              (limit,))
        return [_decode(row) for row in rows]


def cancel_scheduled(schedule_id):
    """Cancel a pending schedule. Raises KeyError (missing) or
    SchedulingConflict (already past pending — a 409 for the API)."""
    row = _get(schedule_id)
    if row is None:
        raise KeyError(schedule_id)
    if row["status"] != "pending":
        raise SchedulingConflict(
            f"Only pending schedules can be cancelled (status is {row['status']})")
    return _set_status(schedule_id, "failed", last_error="Cancelled by user")


def due_schedules(now=None, limit=100):
    """Pending schedules whose time has come and that still have attempts."""
    now = time.time() if now is None else now
    ensure_schema()
    with store.connect() as db:
        rows = db.execute("""SELECT * FROM scheduled_publishes
            WHERE status='pending' AND scheduled_at<=? AND attempts<?
            ORDER BY scheduled_at ASC LIMIT ?""", (now, MAX_ATTEMPTS, limit))
        return [_decode(row) for row in rows]


def _internal_headers():
    """Auth for the worker's own calls: the installation admin token when
    one is configured (same process, no credential leaves the server)."""
    token = os.environ.get("CLIPFAC_ADMIN_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def internal_client(app):
    """httpx client that calls this app's own endpoints in-process."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://localhost",
                             headers=_internal_headers(), timeout=180)


def _publish_error(response):
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, dict):
        detail = detail.get("message") or str(detail)
    return str(detail or f"HTTP {response.status_code}")


def _outcome_error(body):
    """A readable error from a publish ledger record."""
    response = body.get("response") or {}
    if isinstance(response, dict):
        return str(response.get("detail") or response.get("error") or response)
    return str(response)


async def _publish_one(client, row):
    """Run one due schedule through the existing publish flow.

    Returns the terminal (status, last_error). Any failure — missing
    Upload-Post key, review-gate 409, 5xx, transport error — lands the row
    in a terminal state here; it is never retried in the same pass.
    """
    body = {"job_id": row["job_id"], "clip_index": row["clip_index"],
            "platforms": row["platforms"], "title": row["title"],
            "description": row["description"], "user_id": row["user_id"],
            "idempotency_key": row["idempotency_key"]}
    try:
        response = await client.post("/api/clipfac/publish", json=body)
    except Exception as exc:  # transport / timeout / client error
        return "failed", f"{type(exc).__name__}: {exc}"
    if response.status_code == 409:
        return "blocked", _publish_error(response)
    if not response.is_success:
        return "failed", _publish_error(response)
    try:
        record = response.json()
    except ValueError:
        return "failed", "Publish endpoint returned a non-JSON response"
    state = record.get("state")
    if state == "submitted":
        return "published", None
    if state == "blocked":
        return "blocked", _outcome_error(record)
    if state == "uncertain":
        return "failed", _outcome_error(record) or "Publishing outcome uncertain"
    return "failed", f"Unexpected publish state: {state!r}"


async def run_due_once(client, now=None):
    """One worker pass: publish every due schedule exactly once.

    ``client`` is an httpx-like client (use :func:`internal_client` for the
    live app, or a fake in tests). Returns a summary of the pass.
    """
    now = time.time() if now is None else now
    summary = {"due": 0, "published": 0, "blocked": 0, "failed": 0}
    for row in due_schedules(now):
        summary["due"] += 1
        with store.connect() as db:
            db.execute("UPDATE scheduled_publishes SET status='publishing', attempts=attempts+1 "
                       "WHERE id=? AND status='pending'", (row["id"],))
            db.commit()
        try:
            status, error = await _publish_one(client, row)
        except Exception as exc:  # a bug in one row must not kill the pass
            status, error = "failed", f"{type(exc).__name__}: {exc}"
        _set_status(row["id"], status, last_error=error,
                    published_at=now if status == "published" else None)
        summary[status] += 1
    return summary


async def scheduler_loop(app, interval_seconds=30):
    """Background worker: repeatedly run due schedules, forever.

    Fail-safe by design — any exception in one pass is logged and the loop
    keeps going; only cancellation (app shutdown) stops it.
    """
    interval = max(5.0, float(interval_seconds))
    while True:
        try:
            async with internal_client(app) as client:
                await run_due_once(client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[clipfac-scheduling] pass failed: {type(exc).__name__}: {exc}", flush=True)
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

def _parse_scheduled_at(value):
    """UTC epoch seconds, or an ISO-8601 timestamp (offset required, else UTC)."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("scheduled_at must be UTC epoch seconds or an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


class SchedulePublishRequest(ClipRef):
    platforms: list[str] = Field(min_length=1, max_length=3)
    title: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=5000)
    user_id: str | None = Field(default=None, max_length=200)
    scheduled_at: float | str
    timezone: str | None = Field(default=None, max_length=100)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128,
                                        pattern=KEY_PATTERN)

    @field_validator("platforms")
    @classmethod
    def _platforms(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("platforms must be distinct")
        if any(platform not in PLATFORMS for platform in values):
            raise ValueError(f"platforms must be a subset of {sorted(PLATFORMS)}")
        return values

    @field_validator("scheduled_at")
    @classmethod
    def _scheduled_at(cls, value):
        return _parse_scheduled_at(value)


class ScheduleCancelRef(StrictModel):
    schedule_id: str = Field(min_length=1, max_length=64)


router = APIRouter(prefix="/api/clipfac/schedule", tags=["Scheduled publishing"])


@router.post("/")
@router.post("")
async def create_schedule(body: SchedulePublishRequest, request: Request, response: Response):
    request_owner(request)  # same auth pattern as publishing.py
    try:
        row = schedule_publish(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    response.status_code = 200 if row.pop("replayed", False) else 201
    return row


@router.get("/")
@router.get("")
async def list_schedules(request: Request, status: str | None = None, limit: int = 100):
    request_owner(request)
    try:
        return {"schedules": list_scheduled(status=status, limit=max(1, min(limit, 500)))}
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.delete("/{schedule_id}")
async def cancel_schedule(schedule_id: str, request: Request):
    request_owner(request)
    try:
        return cancel_scheduled(schedule_id)
    except KeyError:
        raise HTTPException(404, "Schedule not found")
    except SchedulingConflict as exc:
        raise HTTPException(409, str(exc))


# ---------------------------------------------------------------------------
# MCP tools (registered by the coordinator — see module docstring)
# ---------------------------------------------------------------------------

SCHEDULING_ROUTES = {
    "schedule_publish": ("publish", "POST", "/api/clipfac/schedule", SchedulePublishRequest,
                         "Schedule a clip to publish at a UTC time. The worker calls the existing review-gated publish flow when the time comes; nothing publishes without passing review. scheduled_at is UTC epoch seconds or an ISO-8601 timestamp. Reuse idempotency_key to replay safely."),
    "list_scheduled_publishes": ("read", "GET", "/api/clipfac/schedule", None,
                                 "List scheduled publishes, soonest first, with their status (pending/publishing/published/blocked/failed) and last error."),
    "cancel_scheduled_publish": ("publish", "DELETE", "/api/clipfac/schedule/{schedule_id}", ScheduleCancelRef,
                                 "Cancel a pending scheduled publish. Only pending schedules can be cancelled; published, blocked or failed ones return 409."),
}
