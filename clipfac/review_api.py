"""Evidence-based visual review and bounded repair plans; never overwrites a render."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from clipfac import plans, reviews, store
from clipfac.api import client, _inspection_slot
from clipfac.auth import request_owner
from clipfac.models import ProductionRequest
from clipfac.pipeline_api import candidate_texts
from clipfac.quality import inspect_media, shortlist
from clipfac.stages import read_analysis, sample_source, source_hash

router = APIRouter(prefix="/api/clipfac", tags=["Clipfac review"])
_review_slots = asyncio.Semaphore(2)


async def render_context(request, reference):
    from urllib.parse import unquote, urlparse
    async with client(request) as api:
        response = await api.get(f"/api/status/{reference.job_id}")
    if not response.is_success:
        raise HTTPException(response.status_code, "Rendered job unavailable")
    clips = (response.json().get("result") or {}).get("clips") or []
    clip = next((item for index, item in enumerate(clips)
                 if item.get("clipfac_render_index", index) == reference.clip_index), None)
    if clip is None:
        raise HTTPException(404, "Clip unavailable")
    url = urlparse(clip.get("video_url") or "")
    root = Path(request.app.state.clipfac_namespace["OUTPUT_DIR"]).resolve()
    directory = (root / reference.job_id).resolve()
    path = (root / unquote(url.path.removeprefix("/videos/"))).resolve()
    if url.scheme or not url.path.startswith("/videos/") or not directory.is_relative_to(root) or not path.is_relative_to(directory) or not path.is_file():
        raise HTTPException(404, "Retained local render unavailable")
    metadata_files = sorted(directory.glob("*_metadata.json"))
    metadata = json.loads(metadata_files[0].read_text()) if metadata_files else {}
    return path, directory, clip, metadata


@router.post("/reviews")
async def review_clip(body: reviews.ReviewRequest, request: Request):
    owner = request_owner(request)
    path, directory, clip, metadata = await render_context(request, body)
    try:
        media_sha = await asyncio.to_thread(source_hash, path)
    except OSError:
        raise HTTPException(409, "Rendered file expired during review")
    record, created = reviews.reserve(owner, body.job_id, body.clip_index, media_sha,
                                      retry_failed=body.retry_failed)
    if not created:
        return {**record, "cached": True}
    technical = None
    try:
        async with _inspection_slot:
            technical = await asyncio.to_thread(inspect_media, path)
            proof_dir = directory / "clipfac-reviews" / f"{body.clip_index}-{media_sha[:16]}"
            proof_dir.mkdir(parents=True, exist_ok=True)
            frames, timestamps = await asyncio.to_thread(
                sample_source, path, technical["duration_seconds"], proof_dir, 8)
        async with client(request) as api:
            response = await api.get(f"/api/clip/{body.job_id}/{body.clip_index}/transcript")
        transcript = response.json().get("captions", [])[:1000] if response.is_success else []
        plan_id = (metadata.get("clipfac_plan") or {}).get("id")
        plan = plans.get(plan_id, owner) if plan_id else None
        context = {"title": clip.get("video_title_for_youtube_short"), "hook": clip.get("viral_hook_text"),
                   "style": (plan or {}).get("style"), "campaign": (plan or {}).get("campaign", {})}
        async with _review_slots:
            visual, usage = await asyncio.to_thread(
                reviews.review_visual, frames, timestamps, transcript, context,
                request.headers.get("x-gemini-key"))
        if any(item.sample_index is not None and item.sample_index >= len(frames) for item in visual.findings):
            raise ValueError("Review cited a frame that was not sampled")
        report = {
            "technical": technical, "visual": visual.model_dump(), "model_usage": usage,
            "sample_timestamps": timestamps,
            "contact_sheet_url": f"/videos/{body.job_id}/clipfac-reviews/{proof_dir.name}/clipfac-contact-sheet.jpg" if frames else None,
            "plan_id": plan_id,
            "candidate_index": clip.get("clipfac_candidate_index"),
            "repair_supported": bool(plan) and not bool(clip.get("recipe")),
            "limitations": ["Sampled frames do not cover every video frame.",
                            "Audio aesthetics and continuous pacing are not assessed by still images.",
                            "Model confidence is not measured accuracy."],
        }
        blockers = any(item.severity in ("major", "blocker") for item in visual.findings)
        report["decision"] = "sampled_checks_passed" if (
            frames and not technical["issues"] and visual.verdict == "pass" and not blockers
            and not visual.manual_review and visual.confidence >= .75
        ) else "review_required"
        stale = await asyncio.to_thread(source_hash, path) != media_sha
        if stale:
            report["decision"] = "stale_render"
        reviews.finish(record["id"], "stale" if stale else "completed", report)
    except Exception as exc:
        reviews.finish(record["id"], "failed", {
            "decision": "review_unavailable", "error_type": type(exc).__name__, "technical": technical,
            "notice": "Review failed. No pass or repair was inferred. Retry explicitly after resolving the provider or media error.",
        })
    return reviews.get(record["id"], owner)


@router.get("/reviews/{review_id}")
async def get_review(review_id: str, request: Request):
    result = reviews.get(review_id, request_owner(request))
    if not result:
        raise HTTPException(404, "Review not found")
    return result


@router.post("/reviews/repair")
async def propose_repair(body: reviews.ReviewRef, request: Request):
    owner = request_owner(request)
    review = reviews.get(body.review_id, owner)
    if not review or review["state"] != "completed":
        raise HTTPException(409, "A completed, current review is required")
    report = review["report"]
    if report.get("decision") != "review_required":
        raise HTTPException(409, "This review does not request repairs")
    visual = reviews.VisualReview.model_validate(report["visual"])
    proposal = visual.repair
    if not proposal:
        raise HTTPException(409, "No supported automatic repair was proposed; manual review is required")
    if not report.get("repair_supported"):
        raise HTTPException(409, "Legacy edits require a new source plan before automatic repair")
    original = plans.get(report.get("plan_id"), owner) if report.get("plan_id") else None
    if not original:
        raise HTTPException(409, "Bounded repairs require a retained immutable source plan")
    candidate_index = report.get("candidate_index")
    chosen = next((clip for clip in original["clips"] if clip["candidate_index"] == candidate_index), None)
    if chosen is None:
        raise HTTPException(409, "Reviewed clip cannot be mapped to its source plan")
    # The reviewed render must still have the same content fingerprint.
    reference = reviews.ReviewRequest(job_id=review["job_id"], clip_index=review["clip_index"])
    path, _, _, _ = await render_context(request, reference)
    if await asyncio.to_thread(source_hash, path) != review["media_sha"]:
        raise HTTPException(409, "Render changed after this review")
    try:
        data = read_analysis(request.app.state.clipfac_namespace["OUTPUT_DIR"], original["analysis_job_id"])
    except (OSError, ValueError):
        raise HTTPException(409, "Source analysis expired")
    revised = dict(chosen)
    revised["start_ms"] += proposal.trim_start_ms
    revised["end_ms"] += proposal.trim_end_ms
    if proposal.hook is not None:
        revised["hook"] = proposal.hook.strip()
    style = proposal.style or original["style"]
    if revised == chosen and style == original["style"]:
        raise HTTPException(409, "Proposal contains no supported change")
    # Enforce campaign phrases again after trimming; a repair cannot remove a
    # required statement or introduce an unsupported hook.
    check = copy.deepcopy(data)
    check["candidates"] = [{"start": revised["start_ms"] / 1000,
                            "end": revised["end_ms"] / 1000, "predicted_score": revised["score"]}]
    text = candidate_texts(check).get(0, "")
    if proposal.hook and " ".join(proposal.hook.casefold().split()) not in " ".join(text.casefold().split()):
        raise HTTPException(422, "Automatic hook repair must be a verbatim quote from the retained transcript")
    low, high = original["duration_bounds_ms"]
    requirements = ProductionRequest(
        source_url="https://retained-source.invalid/video", confirm_rights=True,
        style=style, campaign=original["campaign"], min_seconds=low / 1000,
        max_seconds=high / 1000, minimum_score=0,
    )
    if not shortlist(check["candidates"], requirements, {0: text})["accepted"]:
        raise HTTPException(422, "Repair would violate duration or campaign constraints")
    lineage = hashlib.sha256(json.dumps({
        "source": original["source_sha256"], "campaign": original["campaign"], "candidate": candidate_index,
    }, sort_keys=True).encode()).hexdigest()
    try:
        repair, created = reviews.reserve_repair(owner, body.review_id, lineage)
    except store.Conflict as exc:
        raise HTTPException(409, str(exc))
    if not created:
        return {key: value for key, value in repair.items() if key != "owner"}
    try:
        document = plans.create(owner, original["analysis_job_id"], data, [revised], style, original["captions"])
        reviews.finish_repair(repair["id"], "planned", document["id"])
        return {**repair, "state": "planned", "plan_id": document["id"],
                "notice": "New single-clip repair plan created. Render it with render_edit_plan, then inspect the new result. Original render is unchanged."}
    except Exception as exc:
        reviews.finish_repair(repair["id"], "failed")
        raise HTTPException(422, f"Repair planning failed: {type(exc).__name__}")
