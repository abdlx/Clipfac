"""Production API built on upstream processing; credentials never enter the ledger."""
import asyncio
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request

from clipfac import store
from clipfac.auth import request_owner
from clipfac.models import ClipRef, ProductionRequest, SubmitProduction, editorial_brief
from clipfac.presets import get_preset, list_presets
from clipfac.quality import inspect_media, shortlist

router = APIRouter(prefix="/api/clipfac", tags=["Clipfac"])
_inspection_slot = asyncio.Semaphore(1)


def client(request):
    # Keep auth identical to ordinary API calls. Forward Origin for HttpOnly sessions.
    allowed = {"authorization", "x-api-key", "x-gemini-key", "x-upload-post-key",
               "x-elevenlabs-key", "x-fal-key", "cookie", "origin"}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=request.app),
                             base_url=str(request.base_url).rstrip("/"),
                             headers={k: v for k, v in request.headers.items() if k.lower() in allowed},
                             timeout=180)


@router.get("/presets")
async def presets():
    return {"presets": list_presets(), "automatic_style_selection": False}


@router.post("/brief")
async def brief(body: ProductionRequest):
    return editorial_brief(body)


@router.post("/productions")
async def produce(body: SubmitProduction, request: Request):
    if os.environ.get("BILLING_ENABLED", "").lower() in ("1", "true", "yes"):
        raise HTTPException(409, "Clipfac production ledger currently supports self-host mode only")
    owner = request_owner(request)
    req = ProductionRequest.model_validate(body.model_dump(exclude={"idempotency_key"}))
    try:
        record, created = store.reserve(owner, body.idempotency_key, req.model_dump(), editorial_brief(req))
    except store.Conflict as exc:
        raise HTTPException(409, str(exc))
    if not created:
        return {**record, "replayed": True,
                "submission_notice": "A submitting/uncertain record is never resubmitted automatically. Check the instance before using a new key."}
    preset = get_preset(req.style)
    payload = {"acknowledged": True, "output_format": "vertical",
               "layouts": preset["layouts"], "auto_hook": preset["hook"],
               "auto_hook_style": preset["hook_style"], "captions": req.captions,
               "clip_min_seconds": req.min_seconds, "clip_max_seconds": req.max_seconds,
               "clipfac_style": req.style, "clipfac_brief": editorial_brief(req),
               "clipfac_max_clips": req.max_clips, "force_low_quality": req.force_low_quality}
    if req.source_url:
        payload["url"] = req.source_url
    else:
        payload["upload_id"] = req.upload_id
    try:
        async with client(request) as api:
            response = await api.post("/api/process", json=payload)
        result = response.json()
        state = "queued" if response.is_success and result.get("job_id") else "blocked"
        store.finish(record["id"], state, result)
    except Exception:
        # Do not retry an ambiguous network/process failure and create duplicate work.
        store.finish(record["id"], "uncertain", {"error": "Submission interrupted; reconcile with instance jobs before resubmitting."})
        raise HTTPException(503, "Submission state uncertain. Reuse the same idempotency key to inspect it.")
    return store.get(record["id"], owner)


@router.get("/productions")
async def productions(request: Request):
    return {"productions": store.recent(request_owner(request))}


def clip_texts(namespace, job_id, clips):
    root = Path(namespace["OUTPUT_DIR"]).resolve()
    directory = (root / job_id).resolve()
    if not directory.is_relative_to(root):
        return {}
    matches = sorted(directory.glob("*_metadata.json"))
    if not matches:
        return {}
    try:
        metadata = json.loads(matches[0].read_text())
        segments = (metadata.get("transcript") or {}).get("segments", [])
    except (OSError, ValueError, AttributeError):
        return {}
    result = {}
    for index, clip in enumerate(clips):
        spans = (clip.get("recipe") or {}).get("segments") or [
            {"start": clip.get("start", 0), "end": clip.get("end", 0)}]
        words = []
        for span in spans:
            for segment in segments:
                if segment.get("words"):
                    words.extend(str(w.get("word", "")) for w in segment["words"]
                                 if w.get("end", 0) > span["start"] and w.get("start", 0) < span["end"])
                elif segment.get("end", 0) > span["start"] and segment.get("start", 0) < span["end"]:
                    words.append(str(segment.get("text", "")))
        result[index] = " ".join(words)
    return result


@router.get("/productions/{production_id}")
async def production(production_id: str, request: Request):
    record = store.get(production_id, request_owner(request))
    if not record:
        raise HTTPException(404, "Production not found")
    job_id = (record["response"] or {}).get("job_id")
    if not job_id:
        return record
    async with client(request) as api:
        response = await api.get(f"/api/status/{job_id}")
    if response.status_code == 404:
        return {**record, "state": "unavailable", "notice": "Job artifacts expired or the job is unavailable"}
    if not response.is_success:
        raise HTTPException(response.status_code, "Could not inspect processing job")
    job = response.json()
    record["state"] = job.get("status")
    record["job"] = job
    if job.get("status") == "completed":
        req = ProductionRequest.model_validate(record["request"])
        clips = (job.get("result") or {}).get("clips", [])
        texts = clip_texts(request.app.state.clipfac_namespace, job_id, clips)
        record["selection"] = shortlist(clips, req, texts)
    return record


@router.post("/inspect")
async def inspect(body: ClipRef, request: Request):
    async with client(request) as api:
        response = await api.get(f"/api/status/{body.job_id}")
    if not response.is_success:
        raise HTTPException(response.status_code, "Job unavailable")
    clips = (response.json().get("result") or {}).get("clips") or []
    if body.clip_index >= len(clips):
        raise HTTPException(404, "Clip unavailable")
    namespace = request.app.state.clipfac_namespace
    root = Path(namespace["OUTPUT_DIR"]).resolve()
    url = urlparse(clips[body.clip_index].get("video_url") or "")
    if url.scheme or not url.path.startswith("/videos/"):
        raise HTTPException(409, "Inspection requires a retained local render")
    path = (root / unquote(url.path[len("/videos/"):])).resolve()
    job_dir = (root / body.job_id).resolve()
    if not job_dir.is_relative_to(root) or not path.is_relative_to(job_dir) or not path.is_file():
        raise HTTPException(404, "Retained render unavailable")
    async with _inspection_slot:
        try:
            return await asyncio.to_thread(inspect_media, path)
        except (ValueError, OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(422, f"Inspection failed: {type(exc).__name__}")
