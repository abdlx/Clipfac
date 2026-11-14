"""Performance loop v1: record what posted clips actually did, feed the
winners back into clip selection.

Ported idea from SupoClip (backend/src/repositories/social_repository.py +
src/services/social_service.py): SupoClip keeps metrics as JSONB on its
``social_posts`` rows plus a ``social_post_metrics`` snapshot history, then
aggregates by hook_type / clip-length bucket / provider and formats a
prompt-ready ``format_performance_context`` that its AI prompt includes as a
tie-breaker section (``_performance_history_section`` in ai.py), gated behind
a minimum measured-posts config.

What ported here, adapted to Clipfac's single SQLite DB and single
installation (no per-user scoping, no Postgres/JSONB, no metrics-refresh
worker):
- One row per (job, clip, platform) with views/likes, upserted on re-record
  (SupoClip's latest-snapshot-wins rule, simplified: latest record wins).
- Top performers ranked by views (SupoClip's ``get_top_posts``).
- A compact prompt-ready feedback block (SupoClip's
  ``format_performance_context``), capped so it can never bloat the prompt.

What did NOT port (deliberately, v1 scope):
- Per-hook-type / per-duration-bucket aggregates and the min-posts
  personalization gate: with one installation and few posted clips so far,
  top-winners alone is the signal. Aggregates can be added once there are
  enough measured posts to make a bucket mean something.
- Automatic metrics refresh from the platforms: Clipfac's publish path goes
  through Upload-Post, whose cached analytics already exist in
  ``social_metrics.py`` (``summarise`` / ``row_views``) — a future step can
  call ``record_performance(..., source="upload_post")`` from that data
  instead of re-pulling per-platform APIs the way SupoClip does.
- Clip traits (title/hook/time range/predicted score) are NOT stored in the
  performance table itself: they live in the analysis artifact
  (``clipfac.stages.read_analysis`` candidates). ``record_performance``
  accepts them as optional arguments so a caller that already has the clip
  in hand (publish flow, dashboard, agent) can snapshot them alongside the
  numbers; ``feedback_text`` falls back to ``job_id``/clip index when a row
  has no trait snapshot.
"""
import os
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from clipfac.auth import request_owner
from clipfac.models import ClipRef, StrictModel

# Prompt budget: the feedback block must never bloat the selection prompt.
FEEDBACK_MAX_CHARS = 600
FEEDBACK_MAX_CLIPS = 5

PLATFORMS = ("tiktok", "instagram", "youtube")
SOURCES = ("manual", "upload_post")

_SCHEMA = """CREATE TABLE IF NOT EXISTS clip_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    clip_index INTEGER NOT NULL,
    platform TEXT NOT NULL,
    post_url TEXT,
    views INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    recorded_at REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    title TEXT,
    hook TEXT,
    start_s REAL,
    end_s REAL,
    predicted_score INTEGER,
    UNIQUE(job_id, clip_index, platform))"""


def _db_path():
    return Path(os.environ.get("CLIPFAC_DB_PATH", "data/clipfac.sqlite3"))


def _connect():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def ensure_schema():
    """Create ``clip_performance`` idempotently (same pattern as store.py)."""
    with _connect() as db:
        db.execute(_SCHEMA)
        db.commit()


def _row_to_dict(row):
    if row is None:
        return None
    data = dict(row)
    for key in ("views", "likes"):
        data[key] = int(data[key] or 0)
    return data


def record_performance(job_id, clip_index, platform, views, likes, post_url=None,
                       source="manual", title=None, hook=None, start_s=None,
                       end_s=None, predicted_score=None):
    """Upsert one clip's performance on one platform.

    Keyed by (job_id, clip_index, platform): re-recording the same clip on
    the same platform replaces the numbers (latest snapshot wins, the way
    SupoClip's summarise keeps only the latest snapshot per post).
    """
    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported social platform: {platform!r}")
    if source not in SOURCES:
        raise ValueError(f"Unknown performance source: {source!r}")
    ensure_schema()
    with _connect() as db:
        db.execute(
            """INSERT INTO clip_performance
               (job_id, clip_index, platform, post_url, views, likes,
                recorded_at, source, title, hook, start_s, end_s, predicted_score)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id, clip_index, platform) DO UPDATE SET
                 post_url=COALESCE(excluded.post_url, clip_performance.post_url),
                 views=excluded.views, likes=excluded.likes,
                 recorded_at=excluded.recorded_at, source=excluded.source,
                 title=COALESCE(excluded.title, clip_performance.title),
                 hook=COALESCE(excluded.hook, clip_performance.hook),
                 start_s=COALESCE(excluded.start_s, clip_performance.start_s),
                 end_s=COALESCE(excluded.end_s, clip_performance.end_s),
                 predicted_score=COALESCE(excluded.predicted_score,
                                          clip_performance.predicted_score)""",
            (job_id, int(clip_index), platform, post_url, int(views), int(likes),
             time.time(), source, title, hook,
             None if start_s is None else float(start_s),
             None if end_s is None else float(end_s),
             None if predicted_score is None else int(predicted_score)))
        db.commit()
        return _row_to_dict(db.execute(
            "SELECT * FROM clip_performance WHERE job_id=? AND clip_index=? AND platform=?",
            (job_id, int(clip_index), platform)).fetchone())


def list_performance(job_id=None, platform=None, limit=50):
    """All recorded rows, newest first; optionally filtered by job/platform."""
    ensure_schema()
    clauses, params = [], []
    if job_id is not None:
        clauses.append("job_id=?")
        params.append(job_id)
    if platform is not None:
        clauses.append("platform=?")
        params.append(platform)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _connect() as db:
        rows = db.execute(
            f"SELECT * FROM clip_performance {where} ORDER BY recorded_at DESC LIMIT ?",
            (*params, max(1, int(limit)))).fetchall()
    return [_row_to_dict(row) for row in rows]


def top_performers(limit=5):
    """Winners ranked by views, best first (SupoClip's get_top_posts)."""
    ensure_schema()
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM clip_performance ORDER BY views DESC, likes DESC LIMIT ?",
            (max(1, int(limit)),)).fetchall()
    return [_row_to_dict(row) for row in rows]


def _fmt_views(views):
    views = int(views or 0)
    if views >= 1_000_000:
        return f"{views / 1_000_000:.1f}M"
    if views >= 1_000:
        return f"{views / 1_000:.1f}k"
    return str(views)


def _clip_label(row):
    """One short line naming a winner's traits, or None if nothing to say."""
    parts = []
    title = (row.get("title") or "").strip()
    hook = (row.get("hook") or "").strip()
    if title:
        parts.append(f'"{title[:60]}"')
    elif hook:
        parts.append(f'hook "{hook[:60]}"')
    start, end = row.get("start_s"), row.get("end_s")
    if start is not None and end is not None:
        parts.append(f"{float(start):.0f}-{float(end):.0f}s")
    score = row.get("predicted_score")
    if score is not None:
        parts.append(f"score {int(score)}")
    if not parts:
        parts.append(f"job {row['job_id']} clip {row['clip_index']}")
    return f"- {' '.join(parts)} — {_fmt_views(row['views'])} views on {row['platform']}"


def feedback_text(limit=FEEDBACK_MAX_CLIPS):
    """Prompt bias from real audience data; "" when there is nothing yet.

    Capped at ``limit`` winners and FEEDBACK_MAX_CHARS so the selection
    prompt never grows unboundedly. When the store is empty this returns "",
    and the detail prompt is unchanged in meaning.
    """
    winners = top_performers(limit=limit)
    if not winners:
        return ""
    lines = ["Past top-performing clips (real audience views):"]
    lines.extend(_clip_label(row) for row in winners)
    lines.append("Bias toward moments like these: same kind of hook, pacing and payoff.")
    text = "\n".join(lines)
    if len(text) > FEEDBACK_MAX_CHARS:
        text = text[:FEEDBACK_MAX_CHARS].rstrip()
    return text


# ----------------------------------------------------------------------
# REST API (mirrors the MCP tools; same auth pattern as publishing.py)
# ----------------------------------------------------------------------
router = APIRouter(prefix="/api/clipfac/performance", tags=["Clip performance"])


class RecordPerformanceRequest(ClipRef):
    platform: str = Field(pattern=r"^(tiktok|instagram|youtube)$")
    views: int = Field(ge=0)
    likes: int = Field(default=0, ge=0)
    post_url: str | None = Field(default=None, max_length=500)
    source: str = Field(default="manual", pattern=r"^(manual|upload_post)$")
    title: str | None = Field(default=None, max_length=200)
    hook: str | None = Field(default=None, max_length=160)
    start_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, ge=0)
    predicted_score: int | None = Field(default=None, ge=0, le=100)


@router.post("/record")
async def record_endpoint(body: RecordPerformanceRequest, request: Request):
    request_owner(request)
    return record_performance(
        body.job_id, body.clip_index, body.platform, body.views, body.likes,
        post_url=body.post_url, source=body.source, title=body.title,
        hook=body.hook, start_s=body.start_s, end_s=body.end_s,
        predicted_score=body.predicted_score)


@router.get("/list")
async def list_endpoint(request: Request, job_id: str | None = None,
                        platform: str | None = None, limit: int = 50):
    request_owner(request)
    if platform is not None and platform not in PLATFORMS:
        raise HTTPException(422, "Unsupported social platform")
    return {"performance": list_performance(job_id=job_id, platform=platform, limit=limit)}


@router.get("/feedback")
async def feedback_endpoint(request: Request):
    request_owner(request)
    winners = top_performers()
    return {"feedback": feedback_text(), "top_performers": winners}


# ----------------------------------------------------------------------
# MCP tools (ROUTES-style dict, same shape as clipfac/mcp.py).
# NOT registered here: the coordinator adds these entries to
# clipfac/mcp.py's ROUTES (see the registration snippet in the report).
# ----------------------------------------------------------------------
class PerformanceRef(StrictModel):
    job_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")
    clip_index: int = Field(ge=0, le=1000)


class PerformanceRecordRef(PerformanceRef):
    platform: str = Field(pattern=r"^(tiktok|instagram|youtube)$")
    views: int = Field(ge=0)
    likes: int = Field(default=0, ge=0)
    post_url: str | None = Field(default=None, max_length=500)
    source: str = Field(default="manual", pattern=r"^(manual|upload_post)$")
    title: str | None = Field(default=None, max_length=200)
    hook: str | None = Field(default=None, max_length=160)
    start_s: float | None = Field(default=None, ge=0)
    end_s: float | None = Field(default=None, ge=0)
    predicted_score: int | None = Field(default=None, ge=0, le=100)


class PerformanceListRef(StrictModel):
    job_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,64}$")
    platform: str | None = Field(default=None, pattern=r"^(tiktok|instagram|youtube)$")
    limit: int = Field(default=50, ge=1, le=500)


MCP_ROUTES = {
    "record_clip_performance": ("edit", "POST", "/api/clipfac/performance/record",
                                PerformanceRecordRef,
                                "Record real audience views/likes for a published clip, keyed by job and clip index. Re-recording the same clip on the same platform replaces the numbers. Include the clip's title, hook, time range and predicted score when known so future selection can learn from it."),
    "get_performance_feedback": ("read", "GET", "/api/clipfac/performance/feedback", None,
                                 "Get the prompt-ready bias from the best-performing posted clips so far, plus the top performers ranked by views. Empty feedback when no performance has been recorded yet."),
    "list_clip_performance": ("read", "GET", "/api/clipfac/performance/list",
                              PerformanceListRef,
                              "List recorded clip performance, newest first. Filter by job_id and platform when given."),
}
