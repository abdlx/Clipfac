"""Editor ops v1 — deterministic ffmpeg trim / split / merge for rendered clips.

This module is deliberately app.py-free: it only knows about files in a job
directory (``<OUTPUT_DIR>/<job_id>/``) plus the job's ``*_metadata.json``.
It exists for the operations the editor's EDL / rerender path CANNOT express:

- **merge-clips**: concatenate two or more *rendered clip files* into one new
  clip. ``POST /api/clip/rerender`` is per-clip (segments of one source), so
  joining clip 2 onto clip 1 has no EDL spelling. Merging two *segments*
  inside one clip is the opposite: that IS an EDL edit (one segment whose
  end is the second segment's end) and belongs in ClipEditor, not here.
- **trim / split of a rendered clip file**: a file-level cut without going
  back to the source or the canonical reframe. The editor's rerender path
  stays the metadata-authoritative edit path; these endpoints return new
  files and (for merge) append one new clip entry to ``metadata.json`` on
  disk. They do NOT rewrite an existing clip's recipe/start/end — a
  clip-relative trim cannot be mapped back onto source segments without
  re-implementing the rerender — and they do NOT touch app.py's in-memory
  ``jobs`` dict, so a running server only sees the merged clip after a
  reload/restart. Agents should treat the returned filenames as the source
  of truth.

Mount (coordinator adds this; this module never edits app.py itself)::

    # app.py — after the other clipfac routers are installed:
    from clipfac.editor_ops import router as editor_ops_router
    app.include_router(editor_ops_router)

Auth: when mounted inside the Clipfac app, the app-wide ``InstallationAuth``
middleware (installed by ``clipfac.integration.install``) guards these
routes exactly like the other ``/api/*`` routes. Do not mount the router on
a bare public FastAPI app.

All ffmpeg work reuses :mod:`recut` (``run_cut_concat`` /
``concat_command``) and :mod:`ffmpeg_utils` encode args so parts share one
encode profile and the concat demuxer can stream-copy the join.
"""

import asyncio
import glob
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import recut
import subtitles
from clipfac.caption_templates import get_template, get_template_info
from clipfac import supoclip_captions
from ffmpeg_utils import METADATA_SCRUB, QUALITY_FAST, audio_encode_args, video_encode_args

router = APIRouter(prefix="/api/editor", tags=["Clip editor ops"])

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_FFMPEG_TIMEOUT_SECONDS = 600
_MIN_CLIP_SECONDS = 0.5


class EditorOpError(ValueError):
    """Invalid request — safe to surface verbatim as a 400 detail."""


# ---------------------------------------------------------------------------
# pure helpers (no FastAPI, directly unit-testable)
# ---------------------------------------------------------------------------

def probe_clip(path):
    """ffprobe a clip file. Returns {duration, width, height, codec, fps}."""
    if not os.path.isfile(path):
        raise EditorOpError(f"clip file not found: {os.path.basename(path)}")
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height,codec_name,r_frame_rate",
           "-show_entries", "format=duration",
           "-of", "json", path]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.PIPE, timeout=60)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffprobe failed on {os.path.basename(path)}: "
                           f"{e.stderr.decode('utf-8', 'replace')[-200:]}")
    data = json.loads(out.decode("utf-8"))
    stream = (data.get("streams") or [{}])[0]
    duration = float((data.get("format") or {}).get("duration") or 0)
    num, _, den = (stream.get("r_frame_rate") or "30/1").partition("/")
    fps = round(int(num) / int(den or 1), 2) if num else 30.0
    return {
        "duration": round(duration, 3),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "codec": stream.get("codec_name") or "",
        "fps": fps,
    }


def _run_ffmpeg(command):
    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE,
                                timeout=_FFMPEG_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"ffmpeg timed out after {_FFMPEG_TIMEOUT_SECONDS}s "
            f"({' '.join(command[1:6])}...)")
    if result.returncode != 0:
        tail = (result.stderr or b"").decode("utf-8", "replace")[-400:]
        raise RuntimeError(f"ffmpeg failed ({' '.join(command[1:6])}...): {tail}")


def _check_range(start, end, duration, what="range"):
    if not (0 <= start < end):
        raise EditorOpError(f"{what}: need 0 <= start < end, got {start}..{end}")
    if end - start < _MIN_CLIP_SECONDS:
        raise EditorOpError(
            f"{what}: shorter than {_MIN_CLIP_SECONDS}s")
    if end > duration + 0.05:
        raise EditorOpError(
            f"{what}: end {end}s is past the clip duration {duration}s")


def trim_clip(input_path, start, end, output_path, workdir=None):
    """Cut [start, end] out of a rendered clip file. Frame-accurate re-encode
    (same profile recut uses), so the result concats cleanly later."""
    info = probe_clip(input_path)
    _check_range(float(start), float(end), info["duration"], "trim")
    workdir = workdir or os.path.dirname(os.path.abspath(output_path))
    recut.run_cut_concat(input_path,
                         [{"start": round(float(start), 3),
                           "end": round(float(end), 3)}],
                         output_path, workdir)
    return output_path


def split_clip(input_path, at, out_a, out_b, workdir=None):
    """Split a rendered clip file at ``at`` seconds into two files.
    Returns (out_a, out_b)."""
    info = probe_clip(input_path)
    at = float(at)
    if not (0 < at < info["duration"]):
        raise EditorOpError(
            f"split point {at}s is outside the clip (0..{info['duration']}s)")
    if at < _MIN_CLIP_SECONDS or info["duration"] - at < _MIN_CLIP_SECONDS:
        raise EditorOpError(
            f"split would leave a side shorter than {_MIN_CLIP_SECONDS}s")
    workdir = workdir or os.path.dirname(os.path.abspath(out_a))
    trim_clip(input_path, 0, at, out_a, workdir)
    trim_clip(input_path, at, info["duration"], out_b, workdir)
    return out_a, out_b


def _same_stream_params(infos):
    first = infos[0]
    return all(
        (i["width"], i["height"], i["codec"], i["fps"]) ==
        (first["width"], first["height"], first["codec"], first["fps"])
        for i in infos[1:])


def _reencode_to_match(input_path, target, output_path):
    """Re-encode one clip to the target's dims/fps so the concat demuxer can
    stream-copy the join. Used only when inputs don't already match."""
    cmd = ["ffmpeg", "-y", "-i", input_path,
           "-vf", (f"scale={target['width']}:{target['height']}:"
                   "force_original_aspect_ratio=increase,"
                   f"crop={target['width']}:{target['height']},setsar=1"),
           "-r", str(target["fps"]),
           *video_encode_args(QUALITY_FAST),
           *audio_encode_args(),
           *METADATA_SCRUB, "-movflags", "+faststart",
           output_path]
    _run_ffmpeg(cmd)
    return output_path


def merge_clip_files(input_paths, output_path, workdir=None):
    """Concatenate rendered clip files in order. Same dims/codec/fps inputs
    join with a stream copy (no generation loss); mismatched inputs are
    re-encoded to the first clip's profile first. Returns output_path."""
    if len(input_paths) < 2:
        raise EditorOpError("merge needs at least 2 clips")
    if len(input_paths) > 12:
        raise EditorOpError("merge supports at most 12 clips")
    infos = [probe_clip(p) for p in input_paths]
    if any(i["duration"] <= 0 for i in infos):
        raise EditorOpError("cannot merge a clip with unknown duration")
    workdir = workdir or os.path.dirname(os.path.abspath(output_path))
    os.makedirs(workdir, exist_ok=True)
    token = f"{int(time.time())}_{os.getpid()}"
    parts = list(input_paths)
    if not _same_stream_params(infos):
        parts = []
        for n, path in enumerate(input_paths):
            part = os.path.join(workdir, f"temp_merge_part_{token}_{n}.mp4")
            _reencode_to_match(path, infos[0], part)
            parts.append(part)
    list_path = os.path.join(workdir, f"temp_merge_concat_{token}.txt")
    try:
        with open(list_path, "w") as f:
            for part in parts:
                f.write(f"file '{os.path.abspath(part)}'\n")
        _run_ffmpeg(recut.concat_command(list_path, output_path))
    finally:
        for path in ([list_path] + (parts if parts != list(input_paths) else [])):
            try:
                os.remove(path)
            except OSError:
                pass
    return output_path


# ---------------------------------------------------------------------------
# job-directory helpers (disk metadata only — see module docstring)
# ---------------------------------------------------------------------------

def _job_dir(request, job_id):
    if not _JOB_ID_RE.match(job_id or ""):
        raise HTTPException(status_code=400, detail="invalid job_id")
    namespace = getattr(request.app.state, "clipfac_namespace", None)
    root = None
    if isinstance(namespace, dict):
        root = namespace.get("OUTPUT_DIR")
    root = root or os.environ.get("CLIPFAC_OUTPUT_DIR") or "output"
    job_dir = os.path.join(root, job_id)
    if not os.path.isdir(job_dir):
        raise HTTPException(status_code=404, detail="Job not found")
    return job_dir


def _load_metadata(job_dir):
    json_files = glob.glob(os.path.join(job_dir, "*_metadata.json"))
    if not json_files:
        raise HTTPException(status_code=404, detail="Metadata not found")
    with open(json_files[0], "r") as f:
        return json_files[0], json.load(f)


def _clip_file(job_dir, data, clip_index):
    clips = data.get("shorts", [])
    if clip_index < 0 or clip_index >= len(clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    filename = (clips[clip_index].get("video_url") or "").split("/")[-1]
    if not filename:
        base = os.path.basename(
            glob.glob(os.path.join(job_dir, "*_metadata.json"))[0]
        ).replace("_metadata.json", "")
        filename = f"{base}_clip_{clip_index + 1}.mp4"
    path = os.path.join(job_dir, filename)
    if not os.path.isfile(path):
        raise HTTPException(
            status_code=404, detail=f"Clip file not found: {filename}")
    return clips[clip_index], filename, path


def _write_metadata_atomic(meta_path, data):
    tmp = f"{meta_path}.tmp-{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, meta_path)


# ---------------------------------------------------------------------------
# router
# ---------------------------------------------------------------------------

class TrimRequest(BaseModel):
    job_id: str
    clip_index: int
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class SplitRequest(BaseModel):
    job_id: str
    clip_index: int
    at: float = Field(gt=0)


class MergeClipsRequest(BaseModel):
    job_id: str
    clip_indices: list[int] = Field(min_length=2, max_length=12)


@router.post("/trim")
async def trim_clip_endpoint(req: TrimRequest, request: Request):
    job_dir = _job_dir(request, req.job_id)
    _, filename, path = _load_clip(request, job_dir, req)
    ts = int(time.time())
    out_name = f"trimmed_{ts}_{filename}"
    out_path = os.path.join(job_dir, out_name)
    try:
        await asyncio.to_thread(
            trim_clip, path, req.start, req.end, out_path, job_dir)
        info = await asyncio.to_thread(probe_clip, out_path)
    except EditorOpError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"success": True, "new_file": out_name,
            "new_video_url": f"/videos/{req.job_id}/{out_name}", **info}


@router.post("/split")
async def split_clip_endpoint(req: SplitRequest, request: Request):
    job_dir = _job_dir(request, req.job_id)
    _, filename, path = _load_clip(request, job_dir, req)
    ts = int(time.time())
    out_a_name = f"split_a_{ts}_{filename}"
    out_b_name = f"split_b_{ts}_{filename}"
    try:
        await asyncio.to_thread(
            split_clip, path, req.at,
            os.path.join(job_dir, out_a_name),
            os.path.join(job_dir, out_b_name), job_dir)
        info_a = await asyncio.to_thread(
            probe_clip, os.path.join(job_dir, out_a_name))
        info_b = await asyncio.to_thread(
            probe_clip, os.path.join(job_dir, out_b_name))
    except EditorOpError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"success": True,
            "part_a": {"new_file": out_a_name,
                       "new_video_url": f"/videos/{req.job_id}/{out_a_name}",
                       **info_a},
            "part_b": {"new_file": out_b_name,
                       "new_video_url": f"/videos/{req.job_id}/{out_b_name}",
                       **info_b}}


@router.post("/merge-clips")
async def merge_clips_endpoint(req: MergeClipsRequest, request: Request):
    job_dir = _job_dir(request, req.job_id)
    meta_path, data = _load_metadata(job_dir)
    clips = data.get("shorts", [])
    if any(i < 0 or i >= len(clips) for i in req.clip_indices):
        raise HTTPException(status_code=404, detail="Clip not found")
    if len(set(req.clip_indices)) != len(req.clip_indices):
        raise HTTPException(
            status_code=400, detail="clip_indices must be distinct")
    paths = []
    for i in req.clip_indices:
        _, _, path = _clip_file(job_dir, data, i)
        paths.append(path)
    ts = int(time.time())
    out_name = f"merged_{ts}.mp4"
    out_path = os.path.join(job_dir, out_name)
    try:
        await asyncio.to_thread(merge_clip_files, paths, out_path, job_dir)
        info = await asyncio.to_thread(probe_clip, out_path)
    except EditorOpError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Append-only metadata update (disk): the merged clip is a NEW clip, so
    # no existing clip's recipe is rewritten. app.py's in-memory jobs dict
    # does not see it until reload — see the module docstring.
    first = dict(clips[req.clip_indices[0]])
    last = clips[req.clip_indices[-1]]
    first.update({
        "video_url": f"/videos/{req.job_id}/{out_name}",
        "video_title_for_youtube_short":
            (first.get("video_title_for_youtube_short") or "Merged clip")
            + " (merged)",
        "start": first.get("start", 0),
        "end": last.get("end", first.get("start", 0)),
        "merged_from": list(req.clip_indices),
    })
    first.pop("recipe", None)
    first.pop("crop_overrides", None)
    first.pop("layout_ranges", None)
    clips.append(first)
    data["shorts"] = clips
    try:
        await asyncio.to_thread(_write_metadata_atomic, meta_path, data)
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"merged file written but metadata "
            f"update failed: {e}")
    return {"success": True, "new_file": out_name,
            "new_video_url": f"/videos/{req.job_id}/{out_name}",
            "clip_index": len(clips) - 1, **info}


def _strip_derived(output_dir, filename):
    """Walk ``subtitled_<ts>_`` / ``hooked_<ts>_`` / ``templated_<ts>_`` (and
    ``wm_``) prefixes back to the clean file, if it is still on disk.

    Same fail-safe contract as app.py's _strip_burned_captions: the name is
    returned unchanged when there is nothing to strip or the underlying file
    is gone, so a style burn never stacks a second caption layer on top of
    the first — it always burns from the clean base.
    """
    while True:
        m = re.match(r"^(?:subtitled_\d+_|hooked_\d+_|hook_|templated_\d+_|wm_)(.+)$",
                     filename)
        if not m or not os.path.exists(os.path.join(output_dir, m.group(1))):
            return filename
        filename = m.group(1)


def _clip_words(data, clip):
    """Clip-relative words from the job transcript: recipe-aware (multi-segment
    recuts via recut.virtual_transcript) or the flat clip range otherwise."""
    transcript = data.get("transcript") or {}
    if not transcript:
        return []
    segments = (clip.get("recipe") or {}).get("segments")
    if segments:
        sub = recut.virtual_transcript(transcript, segments)
        words = []
        for seg in sub.get("segments", []):
            words.extend(seg.get("words", []))
        return words
    start = float(clip.get("start") or 0)
    end = float(clip.get("end") or 0)
    words = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            if w.get("end", 0) > start and w.get("start", 0) < end:
                words.append({
                    "word": str(w.get("word", "") or "").strip(),
                    "start": max(0.0, float(w.get("start", 0)) - start),
                    "end": max(0.0, float(w.get("end", 0)) - start),
                })
    return words


def _load_clip(request, job_dir, req):
    meta_path, data = _load_metadata(job_dir)
    return _clip_file(job_dir, data, req.clip_index)


class StyleRequest(BaseModel):
    job_id: str
    clip_index: int
    # SupoClip template id (default/hormozi/mrbeast/minimal/tiktok/neon/
    # podcast); unknown ids fall back to "default" in get_template.
    template: str = "default"
    hook_title: Optional[str] = None
    include_captions: bool = True
    input_filename: Optional[str] = None


@router.get("/templates")
async def list_templates():
    """The 7 SupoClip caption templates (ported verbatim) for the editor's
    template picker."""
    return {"templates": get_template_info()}


@router.post("/style")
async def style_clip_endpoint(req: StyleRequest, request: Request):
    """Burn a SupoClip caption template (+ optional hook title) onto a clip.

    One ffmpeg burn does both: the ported ASS builder writes a single .ass
    with the word-synced karaoke captions in the template's styling and the
    hook title card on top, and Clipfac's subtitles.burn_subtitles burns it.
    The burn always starts from the clean base file (derived prefixes are
    stripped first), so re-styling never stacks caption layers. Metadata's
    video_url is updated in place, like /api/subtitle; the chosen template
    is recorded on the clip as ``caption_template``.
    """
    job_dir = _job_dir(request, req.job_id)
    meta_path, data = _load_metadata(job_dir)
    clip, filename, _path = _clip_file(job_dir, data, req.clip_index)
    if req.input_filename:
        filename = os.path.basename(req.input_filename)
    filename = _strip_derived(job_dir, filename)
    clean_path = os.path.join(job_dir, filename)
    if not os.path.isfile(clean_path):
        raise HTTPException(
            status_code=404, detail=f"Clip file not found: {filename}")

    template = get_template(req.template or "default")
    hook_title = (req.hook_title or "").strip() or None
    words = _clip_words(data, clip) if req.include_captions else []
    if req.include_captions and not words and not hook_title:
        raise HTTPException(
            status_code=400,
            detail="No transcript words for this clip range; pass a hook_title "
                   "to burn a title-only card.")

    ts = int(time.time())
    ass_name = f"style_{req.clip_index}_{ts}.ass"
    ass_path = os.path.join(job_dir, ass_name)
    out_name = f"templated_{ts}_{filename}"
    out_path = os.path.join(job_dir, out_name)
    try:
        info = await asyncio.to_thread(probe_clip, clean_path)
        built = await asyncio.to_thread(
            supoclip_captions.build_assemblyai_ass_subtitles,
            info["width"], info["height"], Path(ass_path),
            words, info["duration"], caption_template=req.template or "default",
            hook_title=hook_title, include_captions=req.include_captions,
        )
        if not built:
            raise HTTPException(
                status_code=400, detail="No words or hook title to burn.")
        await asyncio.to_thread(
            subtitles.burn_subtitles, clean_path, ass_path, out_path)
        out_info = await asyncio.to_thread(probe_clip, out_path)
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    clips = data.get("shorts", [])
    clips[req.clip_index]["video_url"] = f"/videos/{req.job_id}/{out_name}"
    clips[req.clip_index]["caption_template"] = req.template or "default"
    if hook_title:
        clips[req.clip_index]["auto_hook"] = {
            "text": hook_title, "template": req.template or "default"}
    data["shorts"] = clips
    try:
        await asyncio.to_thread(_write_metadata_atomic, meta_path, data)
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"styled file written but metadata "
            f"update failed: {e}")
    return {"success": True, "new_file": out_name,
            "new_video_url": f"/videos/{req.job_id}/{out_name}",
            "template": req.template or "default",
            "hook_title": hook_title, **out_info}
