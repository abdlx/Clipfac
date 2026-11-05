"""Review-gated publication with persistent idempotency."""
import asyncio
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from clipfac import reviews, store
from clipfac.api import client
from clipfac.auth import principal, request_owner
from clipfac.models import ClipRef
from clipfac.stages import source_hash

router = APIRouter(prefix="/api/clipfac", tags=["Reviewed publishing"])


class PublishRequest(ClipRef):
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    user_id: str | None = Field(default=None, max_length=200)
    platforms: list[str] = Field(min_length=1, max_length=3)
    title: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=5000)
    scheduled_date: str | None = Field(default=None, max_length=100)
    timezone: str | None = Field(default=None, max_length=100)


async def enforce_review(request, job_id, clip_index, file_path):
    if os.environ.get("CLIPFAC_REQUIRE_REVIEW", "0") != "1":
        return None
    caller = principal(request)
    if request.headers.get("x-clipfac-review-override") == "1" and caller and "admin" in caller[1]:
        return "explicit_admin_override"
    try:
        fingerprint = await asyncio.to_thread(source_hash, file_path)
    except OSError:
        raise HTTPException(409, "Rendered media unavailable")
    review_id = reviews.passing_review(job_id, clip_index, fingerprint)
    if not review_id:
        raise HTTPException(409, "Current render needs passing technical and sampled visual review before publishing")
    return review_id


@router.post("/publish")
async def publish(body: PublishRequest, request: Request):
    if any(platform not in ("tiktok", "instagram", "youtube") for platform in body.platforms):
        raise HTTPException(422, "Unsupported social platform")
    if len(set(body.platforms)) != len(body.platforms):
        raise HTTPException(422, "Social platforms must be distinct")
    arguments = body.model_dump(exclude={"idempotency_key"})
    if not arguments.get("user_id"):
        profile = os.environ.get("CLIPFAC_UPLOAD_PROFILE")
        if not profile:
            async with client(request) as api:
                response = await api.get("/api/social/user")
            candidates = response.json().get("profiles", []) if response.is_success else []
            if len(candidates) != 1:
                raise HTTPException(400, "Choose an explicit connected Upload-Post profile with user_id")
            profile = candidates[0]["username"]
        arguments["user_id"] = profile
    try:
        record, created = store.reserve(request_owner(request), "publish:" + body.idempotency_key,
                                        {"operation": "publish", "arguments": arguments}, {})
    except store.Conflict as exc:
        raise HTTPException(409, str(exc))
    if not created:
        return {**record, "replayed": True}
    try:
        async with client(request) as api:
            response = await api.post("/api/social/post", json=arguments)
        result = response.json()
        state = "submitted" if response.is_success else ("blocked" if response.status_code < 500 else "uncertain")
        store.finish(record["id"], state, result)
    except Exception:
        store.finish(record["id"], "uncertain", {"error": "Publishing outcome unknown. Reconcile with platform before retrying."})
        raise HTTPException(503, "Publishing outcome uncertain; reuse the same key to inspect it")
    return store.get(record["id"], request_owner(request))
