"""Analysis artifacts and reproducible source identity, separate from render metadata."""
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

from clipfac.director import choose_direction

ANALYSIS_FILE = ".clipfac-analysis.json"
ID = re.compile(r"[A-Za-z0-9-]{1,64}")


def source_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_analysis(output_root, job_id):
    if not ID.fullmatch(job_id):
        raise ValueError("Invalid analysis job id")
    root = Path(output_root).resolve()
    path = (root / job_id / ANALYSIS_FILE).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid analysis path")
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported analysis schema")
    return data


def sample_source(path, duration, output_dir, count=8):
    """Bounded stills; timestamps are recorded beside a real contact sheet."""
    count = min(12, max(1, count))
    frames, times = [], []
    for i in range(count):
        timestamp = max(0, min(duration - .05, duration * (i + .5) / count))
        result = subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{timestamp:.3f}", "-i", str(path),
            "-frames:v", "1", "-vf", "scale=1024:-2", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-",
        ], capture_output=True, timeout=45)
        if result.returncode == 0 and result.stdout:
            frames.append(result.stdout)
            times.append(round(timestamp, 3))
    if frames:
        import io
        from PIL import Image, ImageDraw
        sheet = Image.new("RGB", (1024, 2 * math.ceil(len(frames) / 4) * 90), "#151A22")
        draw = ImageDraw.Draw(sheet)
        for i, (frame, timestamp) in enumerate(zip(frames, times)):
            image = Image.open(io.BytesIO(frame)).convert("RGB")
            image.thumbnail((248, 144))
            x, y = (i % 4) * 256 + 4, (i // 4) * 180 + 4
            sheet.paste(image, (x, y))
            draw.text((x + 4, y + 148), f"{timestamp:.1f}s", fill="white")
        sheet.save(Path(output_dir) / "clipfac-contact-sheet.jpg", quality=88)
    return frames, times


def analyze_source(path, transcript, duration, candidates, output_dir, brief, requested_style):
    from jsonfile import write_json_atomic
    from clipfac.presets import get_preset
    frames, times = sample_source(path, duration, output_dir)
    warnings = []
    try:
        profile = choose_direction(transcript, frames, brief.get("campaign", {}),
                                   requested_style=requested_style)
    except Exception as exc:
        # Failed inference never masquerades as a confident AI decision.
        chosen = requested_style if requested_style != "auto" else "podcast"
        profile = {"style": chosen, "layout": "none", "confidence": 0,
                   "subject": "", "mood": "", "reasoning": "Director unavailable; explicit fallback requires review.",
                   "preserve": [], "avoid": [], "fallback": True, "mode": "fallback",
                   "error_type": type(exc).__name__, "usage": None}
        warnings.append("Automatic direction failed; inspect footage before rendering.")
    data = {
        "schema_version": 1,
        "source": {"filename": Path(path).name, "sha256": source_hash(path),
                   "size_bytes": Path(path).stat().st_size, "duration_seconds": duration},
        "profile": profile, "preset": get_preset(profile["style"]),
        "campaign": brief.get("campaign", {}),
        "selection": brief.get("selection", {}),
        "transcript": transcript or {"language": "none", "segments": []},
        "candidates": candidates.get("shorts", []),
        "selection_cost": candidates.get("cost_analysis"),
        "sample_timestamps": times,
        "contact_sheet": "clipfac-contact-sheet.jpg" if frames else None,
        "warnings": warnings,
    }
    write_json_atomic(str(Path(output_dir) / ANALYSIS_FILE), data, indent=2)
    return data
