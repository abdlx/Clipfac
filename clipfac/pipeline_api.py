"""Analyze -> immutable plan -> render, without downloading or paying for analysis twice."""
import hashlib

from fastapi import APIRouter, HTTPException, Request
from clipfac import plans, store
from clipfac.api import client
from clipfac.auth import request_owner
from clipfac.models import ProductionRequest, SubmitAnalysis, DeliveryRetry, editorial_brief
from clipfac.presets import get_preset
from clipfac.quality import shortlist
from clipfac.stages import read_analysis

router = APIRouter(prefix="/api/clipfac", tags=["Clipfac stages"])


async def submit(request, key, operation, arguments, payload, brief):
    owner = request_owner(request)
    try:
        record, created = store.reserve(owner, key, {"operation": operation, "arguments": arguments}, brief)
    except store.Conflict as exc:
        raise HTTPException(409, str(exc))
    if not created:
        return {**record, "replayed": True}
    try:
        async with client(request) as api:
            response = await api.post("/api/process", json=payload)
        result = response.json()
        state = "queued" if response.is_success and result.get("job_id") else "blocked"
        store.finish(record["id"], state, result)
    except Exception:
        store.finish(record["id"], "uncertain", {"error": "Submission interrupted; reconcile before resubmitting."})
        raise HTTPException(503, "Submission uncertain; reuse the same key to inspect the record")
    return store.get(record["id"], owner)


def safe_arguments(body):
    arguments = body.model_dump(exclude={"idempotency_key", "webhook_secret"})
    secret = getattr(body, "webhook_secret", None)
    if secret:
        arguments["webhook_secret_digest"] = hashlib.sha256(secret.encode()).hexdigest()
    return arguments


@router.post("/analyses")
async def analyze(body: SubmitAnalysis, request: Request):
    direction = editorial_brief(body)
    payload = {
        "acknowledged": True, "clipfac_analysis_only": True,
        "clipfac_auto_style": body.style == "auto",
        "clipfac_style": body.style if body.style != "auto" else "podcast",
        "clipfac_brief": direction, "clipfac_max_clips": body.max_clips,
        "clip_min_seconds": body.min_seconds, "clip_max_seconds": body.max_seconds,
        "force_low_quality": body.force_low_quality, "layouts": ["none"],
        "webhook_url": body.webhook_url, "webhook_secret": body.webhook_secret,
    }
    if body.source_url:
        payload["url"] = body.source_url
    else:
        payload["upload_id"] = body.upload_id
    return await submit(request, body.idempotency_key, "analysis",
                        safe_arguments(body), payload, direction)


def artifact(request, analysis_job_id):
    namespace = request.app.state.clipfac_namespace
    try:
        return read_analysis(namespace["OUTPUT_DIR"], analysis_job_id)
    except (OSError, ValueError):
        raise HTTPException(404, "Analysis unavailable, unfinished or expired")


@router.get("/analyses/{analysis_job_id}")
async def analysis(analysis_job_id: str, request: Request):
    result = dict(artifact(request, analysis_job_id))
    transcript = result.pop("transcript", {})
    result["transcript_summary"] = {"language": transcript.get("language"),
                                     "segments": len(transcript.get("segments", []))}
    if result.get("contact_sheet"):
        result["contact_sheet_url"] = f"/videos/{analysis_job_id}/{result['contact_sheet']}"
    return result


@router.get("/analyses/{analysis_job_id}/transcript")
async def transcript(analysis_job_id: str, request: Request, start_ms: int = 0, end_ms: int = 300000):
    from clipfac.models import TranscriptWindow
    from pydantic import ValidationError
    try:
        window = TranscriptWindow(analysis_job_id=analysis_job_id, start_ms=start_ms, end_ms=end_ms)
    except ValidationError:
        raise HTTPException(422, "Read a valid transcript window of at most five minutes")
    result = artifact(request, analysis_job_id)["transcript"]
    segments = [segment for segment in result.get("segments", [])
                if segment.get("end", 0) * 1000 > window.start_ms
                and segment.get("start", 0) * 1000 < window.end_ms]
    return {"language": result.get("language"), "segments": segments[:500],
            "start_ms": start_ms, "end_ms": end_ms, "truncated": len(segments) > 500}


def candidate_texts(data):
    result = {}
    for index, clip in enumerate(data["candidates"]):
        start, end = clip.get("start", 0), clip.get("end", 0)
        words = []
        for segment in data["transcript"].get("segments", []):
            if segment.get("words"):
                words.extend(str(word.get("word", "")) for word in segment["words"]
                             if word.get("end", 0) > start and word.get("start", 0) < end)
            elif segment.get("end", 0) > start and segment.get("start", 0) < end:
                words.append(segment.get("text", ""))
        result[index] = " ".join(words)
    return result


@router.post("/plans")
async def create_plan(body: plans.CreatePlan, request: Request):
    data = artifact(request, body.analysis_job_id)
    style = body.style or data["profile"]["style"]
    band = data.get("selection", {}).get("duration", [15, 60])
    requirements = ProductionRequest(
        source_url="https://retained-source.invalid/video", confirm_rights=True, style=style,
        campaign=data.get("campaign", {}), min_seconds=band[0], max_seconds=band[1],
        max_clips=data.get("selection", {}).get("max_clips", 15), minimum_score=body.minimum_score,
    )
    reviewed = shortlist(data["candidates"], requirements, candidate_texts(data))
    allowed = {item["index"] for item in reviewed["accepted"]}
    chosen = body.candidate_indexes if body.candidate_indexes is not None else sorted(allowed)
    if not chosen or len(chosen) != len(set(chosen)) or any(index not in allowed for index in chosen):
        raise HTTPException(422, {"message": "Select distinct candidates that pass the brief", "selection": reviewed})
    cuts = []
    for index in chosen:
        clip = data["candidates"][index]
        cuts.append({
            "candidate_index": index, "start_ms": round(clip["start"] * 1000),
            "end_ms": round(clip["end"] * 1000),
            "title": str(clip.get("video_title_for_youtube_short") or clip.get("title") or "")[:200],
            "hook": str(clip.get("viral_hook_text") or "")[:160],
            "score": round(clip.get("predicted_score", 0)),
            "youtube_description": str(clip.get("video_description_for_youtube_short") or "")[:2000],
            "instagram_description": str(clip.get("video_description_for_instagram") or "")[:2000],
            "tiktok_description": str(clip.get("video_description_for_tiktok") or "")[:2000],
        })
    try:
        return plans.create(request_owner(request), body.analysis_job_id, data, cuts, style, body.captions)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str, request: Request):
    result = plans.get(plan_id, request_owner(request))
    if not result:
        raise HTTPException(404, "Plan not found")
    return {**result, "latest_plan_id": plans.latest(plan_id, request_owner(request))["id"],
            "storyboard_url": f"/videos/{result['analysis_job_id']}/clipfac-contact-sheet.jpg"}


@router.post("/plans/revise")
async def revise_plan(body: plans.RevisePlan, request: Request):
    previous = plans.get(body.plan_id, request_owner(request))
    if not previous:
        raise HTTPException(404, "Plan not found")
    data = artifact(request, previous["analysis_job_id"])
    checked = {**data, "candidates": [{
        "start": cut.start_ms / 1000, "end": cut.end_ms / 1000, "predicted_score": cut.score,
    } for cut in body.clips]}
    low, high = previous["duration_bounds_ms"]
    requirements = ProductionRequest(
        source_url="https://retained-source.invalid/video", confirm_rights=True,
        style=body.style or previous["style"], campaign=previous["campaign"],
        min_seconds=low / 1000, max_seconds=high / 1000,
        max_clips=previous.get("max_clips", 15), minimum_score=0,
    )
    checked_selection = shortlist(checked["candidates"], requirements, candidate_texts(checked))
    if len(checked_selection["accepted"]) != len(body.clips):
        raise HTTPException(422, {"message": "Revision violates duration, overlap or campaign constraints",
                                  "selection": checked_selection})
    try:
        return plans.revise(request_owner(request), body)
    except KeyError:
        raise HTTPException(404, "Plan not found")
    except store.Conflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/plans/render")
async def render_plan(body: plans.RenderPlan, request: Request):
    document = plans.get(body.plan_id, request_owner(request))
    if not document:
        raise HTTPException(404, "Plan not found")
    if body.expected_revision != document["revision"]:
        raise HTTPException(409, "Plan revision mismatch")
    if document["preset"] != get_preset(document["style"]):
        raise HTTPException(409, "Preset changed since this plan was authored. Revise the plan to pin current settings.")
    preset = document["preset"]
    payload = {
        "acknowledged": True, "clipfac_plan_id": body.plan_id,
        "clipfac_style": document["style"], "clipfac_brief": {
            "campaign": document["campaign"], "direction": preset["direction"]},
        "clipfac_max_clips": len(document["clips"]),
        "auto_hook": preset["hook"], "auto_hook_style": preset["hook_style"],
        "captions": document["captions"],
        "layouts": [document["layout"]] if document["layout"] != "none" else preset["layouts"],
        "output_format": "vertical", "webhook_url": body.webhook_url, "webhook_secret": body.webhook_secret,
    }
    return await submit(request, body.idempotency_key, "render_plan",
                        safe_arguments(body), payload,
                        {"plan_id": document["id"], "revision": document["revision"], "digest": document["digest"]})



@router.get("/webhooks/{job_id}")
async def webhook_deliveries(job_id: str):
    from clipfac.models import AnalysisRef
    from clipfac import outbox
    from pydantic import ValidationError
    try:
        AnalysisRef(analysis_job_id=job_id)
    except ValidationError:
        raise HTTPException(422, "Invalid job id")
    return {"deliveries": outbox.deliveries(job_id)}


@router.post("/webhooks/retry")
async def retry_webhook(body: DeliveryRetry):
    from clipfac import outbox
    if not outbox.retry(body.delivery_id):
        raise HTTPException(409, "Delivery is unavailable or not in dead-letter state")
    return {"delivery_id": body.delivery_id, "state": "pending"}
