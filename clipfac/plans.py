"""Immutable edit plans with integer timelines and optimistic revision control."""
import hashlib
import json
import time
import uuid
from typing import Literal
from pydantic import Field, model_validator

from clipfac import store
from clipfac.models import StrictModel, Style
from clipfac.presets import get_preset


class PlanClip(StrictModel):
    candidate_index: int = Field(ge=0, le=1000)
    start_ms: int = Field(ge=0, strict=True)
    end_ms: int = Field(gt=0, strict=True)
    title: str = Field(default="", max_length=200)
    hook: str = Field(default="", max_length=160)
    score: int = Field(default=0, ge=0, le=100)
    youtube_description: str = Field(default="", max_length=2000)
    instagram_description: str = Field(default="", max_length=2000)
    tiktok_description: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("Each cut needs a positive duration")
        return self


class CreatePlan(StrictModel):
    analysis_job_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")
    candidate_indexes: list[int] | None = Field(default=None, max_length=60)
    style: Style | None = None
    minimum_score: float = Field(default=70, ge=0, le=100)
    captions: bool = True


class RevisePlan(StrictModel):
    plan_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")
    expected_revision: int = Field(ge=1)
    clips: list[PlanClip] = Field(min_length=1, max_length=60)
    style: Style | None = None
    captions: bool | None = None


class PlanRef(StrictModel):
    plan_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")


class RenderPlan(PlanRef):
    webhook_url: str | None = Field(default=None, max_length=4096)
    webhook_secret: str | None = Field(default=None, max_length=512)
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    quality: Literal["final"] = "final"


def setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS edit_plans (
        id TEXT PRIMARY KEY, root TEXT NOT NULL, parent TEXT, revision INTEGER NOT NULL,
        owner TEXT NOT NULL, document TEXT NOT NULL, created REAL NOT NULL,
        UNIQUE(root, revision))""")
    db.execute("CREATE TABLE IF NOT EXISTS plan_heads (root TEXT PRIMARY KEY, head TEXT NOT NULL)")
    db.commit()


def digest(document):
    # Identity and timestamps are outside the render contract.
    contract = {key: value for key, value in document.items()
                if key not in ("id", "root", "parent", "revision", "created", "digest")}
    return hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_cuts(document):
    duration = document["duration_ms"]
    low, high = document["duration_bounds_ms"]
    clips = [PlanClip.model_validate(item).model_dump() for item in document["clips"]]
    if len(clips) > document.get("max_clips", 15):
        raise ValueError("Too many clips for this source plan")
    if any(item["candidate_index"] >= document.get("candidate_count", 1001) for item in clips):
        raise ValueError("Unknown source candidate")
    if not clips:
        raise ValueError("No clips meet the plan requirements")
    if len({item["candidate_index"] for item in clips}) != len(clips):
        raise ValueError("A candidate cannot appear twice in a plan")
    for item in clips:
        if item["end_ms"] > duration:
            raise ValueError("Cut exceeds retained source duration")
        if not low <= item["end_ms"] - item["start_ms"] <= high:
            raise ValueError("Cut duration is outside the campaign brief")
    document["clips"] = clips
    return document


def create(owner, analysis_id, analysis, clips, style, captions):
    source = analysis["source"]
    selection = analysis.get("selection", {})
    band = selection.get("duration", [5, 180])
    document = {
        "schema_version": 1, "analysis_job_id": analysis_id,
        "source_sha256": source["sha256"], "source_filename": source["filename"],
        "duration_ms": round(source["duration_seconds"] * 1000),
        "duration_bounds_ms": [round(band[0] * 1000), round(band[1] * 1000)],
        "style": style, "preset": get_preset(style), "captions": captions,
        "candidate_count": len(analysis["candidates"]), "max_clips": selection.get("max_clips", 15),
        "layout": analysis["profile"]["layout"], "campaign": analysis.get("campaign", {}),
        "clips": clips,
    }
    validate_cuts(document)
    identity = str(uuid.uuid4())
    document.update(id=identity, root=identity, parent=None, revision=1, created=time.time())
    document["digest"] = digest(document)
    with store.connect() as db:
        setup(db)
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO edit_plans VALUES (?,?,?,?,?,?,?)",
                   (identity, identity, None, 1, owner, json.dumps(document), document["created"]))
        db.execute("INSERT INTO plan_heads VALUES (?,?)", (identity, identity))
        db.commit()
    return document


def get(plan_id, owner):
    with store.connect() as db:
        setup(db)
        row = db.execute("SELECT document FROM edit_plans WHERE id=? AND owner=?", (plan_id, owner)).fetchone()
        return json.loads(row[0]) if row else None


def revise(owner, request):
    with store.connect() as db:
        setup(db)
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT document FROM edit_plans WHERE id=? AND owner=?", (request.plan_id, owner)).fetchone()
        if not row:
            raise KeyError("Plan not found")
        previous = json.loads(row[0])
        head = db.execute("SELECT head FROM plan_heads WHERE root=?", (previous["root"],)).fetchone()
        if not head or head[0] != request.plan_id or previous["revision"] != request.expected_revision:
            raise store.Conflict("Plan changed. Read the current head before revising.")
        document = dict(previous)
        document["clips"] = [item.model_dump() for item in request.clips]
        if request.style:
            document.update(style=request.style, preset=get_preset(request.style))
        if request.captions is not None:
            document["captions"] = request.captions
        validate_cuts(document)
        identity = str(uuid.uuid4())
        document.update(id=identity, parent=previous["id"], revision=previous["revision"] + 1, created=time.time())
        document["digest"] = digest(document)
        db.execute("INSERT INTO edit_plans VALUES (?,?,?,?,?,?,?)",
                   (identity, document["root"], previous["id"], document["revision"],
                    owner, json.dumps(document), document["created"]))
        db.execute("UPDATE plan_heads SET head=? WHERE root=? AND head=?", (identity, document["root"], previous["id"]))
        db.commit()
    return document


def latest(plan_id, owner):
    with store.connect() as db:
        setup(db)
        row = db.execute("""SELECT p.document FROM edit_plans requested
            JOIN plan_heads h ON h.root=requested.root
            JOIN edit_plans p ON p.id=h.head
            WHERE requested.id=? AND requested.owner=?""", (plan_id, owner)).fetchone()
        return json.loads(row[0]) if row else None


def compile_candidates(document):
    """Stable compiler: no model calls, no generated FFmpeg or shell text."""
    validate_cuts(dict(document))
    return {"shorts": [{
        "start": clip["start_ms"] / 1000, "end": clip["end_ms"] / 1000,
        "video_title_for_youtube_short": clip["title"], "viral_hook_text": clip["hook"],
        "video_description_for_youtube_short": clip["youtube_description"],
        "video_description_for_instagram": clip["instagram_description"],
        "video_description_for_tiktok": clip["tiktok_description"],
        "predicted_score": clip["score"], "clipfac_candidate_index": clip["candidate_index"],
    } for clip in document["clips"]]}
