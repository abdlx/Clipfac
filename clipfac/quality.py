"""Conservative selection gates and bounded technical media inspection."""
import json
import math
import re
import subprocess


def shortlist(clips, request, transcript_texts=None):
    """Keep original indexes. Scores are editorial estimates, not view forecasts."""
    accepted, held = [], []
    transcript_texts = transcript_texts or {}
    def score(item):
        try:
            value = float(item.get("predicted_score"))
            return value if math.isfinite(value) else -1
        except (TypeError, ValueError):
            return -1
    ranked = sorted(enumerate(clips), key=lambda pair: (-score(pair[1]), pair[0]))
    ranges = []
    for index, clip in ranked:
        reasons = []
        try:
            start, end = float(clip["start"]), float(clip["end"])
            if not math.isfinite(start + end) or start < 0 or end <= start:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            held.append({"index": index, "reasons": ["invalid_source_range"]})
            continue
        if not request.min_seconds - .5 <= end - start <= request.max_seconds + .5:
            reasons.append("duration_outside_brief")
        if score(clip) < request.minimum_score:
            reasons.append("score_below_threshold" if score(clip) >= 0 else "score_unavailable")
        for lo, hi in ranges:
            overlap = max(0, min(end, hi) - max(start, lo))
            if overlap / min(end - start, hi - lo) >= .5:
                reasons.append("overlaps_stronger_clip")
                break
        text = transcript_texts.get(index)
        campaign = request.campaign
        if campaign.required_phrases or campaign.forbidden_phrases:
            if text is None:
                reasons.append("campaign_transcript_unavailable")
            else:
                normalized = " ".join(text.casefold().split())
                if any(" ".join(p.casefold().split()) not in normalized for p in campaign.required_phrases):
                    reasons.append("required_phrase_missing")
                if any(" ".join(p.casefold().split()) in normalized for p in campaign.forbidden_phrases):
                    reasons.append("forbidden_phrase_present")
        if len(accepted) >= request.max_clips:
            reasons.append("clip_limit")
        if reasons:
            held.append({"index": index, "reasons": reasons})
        else:
            accepted.append({"index": index, "score": score(clip), "clip": clip,
                             "technical_review": "pending", "editorial_review": "pending"})
            ranges.append((start, end))
    return {"accepted": accepted, "held": held, "score_notice": "Model scores are estimates, not guaranteed reach.",
            "review_notice": "Shortlist approval checks duration, overlap and literal transcript phrases only. Inspect media and review editorial quality before publishing."}


def inspect_media(path):
    """No shell, bounded execution; read-only analysis of a server-resolved file."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30)
    if probe.returncode:
        raise ValueError("Media probe failed")
    info = json.loads(probe.stdout)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    issues = []
    duration = float(info.get("format", {}).get("duration") or 0)
    if not video or duration <= 0:
        issues.append("invalid_video")
    if not audio:
        issues.append("missing_audio")
    if video and (int(video.get("width", 0)) < 360 or int(video.get("height", 0)) < 360):
        issues.append("low_resolution")
    # Full decode catches corruption. Long source files are never accepted here:
    # callers resolve rendered clips, not caller-supplied paths.
    scan = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "info", "-i", str(path),
         "-t", "185", "-vf", "blackdetect=d=0.5:pix_th=0.1", "-af", "volumedetect",
         "-f", "null", "-"], capture_output=True, text=True, timeout=180)
    if scan.returncode:
        issues.append("decode_failed")
    black = re.findall(r"black_start:([0-9.]+) black_end:([0-9.]+)", scan.stderr)
    if black:
        issues.append("black_intervals")
    levels = dict(re.findall(r"(mean_volume|max_volume):\s*(-?(?:[0-9.]+|inf)) dB", scan.stderr))
    if levels.get("max_volume") == "-inf":
        issues.append("silent_audio")
    elif levels.get("mean_volume") and float(levels["mean_volume"]) < -40:
        issues.append("quiet_audio")
    return {"status": "needs_review" if issues else "technical_checks_passed",
            "duration_seconds": duration, "width": (video or {}).get("width"),
            "height": (video or {}).get("height"), "audio": bool(audio),
            "audio_levels_db": levels, "black_intervals": black, "issues": issues,
            "not_checked": ["caption_overflow", "caption_accuracy", "subject_framing",
                            "editorial_coherence", "campaign_visual_compliance"]}
