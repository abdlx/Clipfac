"""Real FFmpeg signals. No external providers, network or ML stack."""
import shutil
import subprocess

import pytest
from clipfac.quality import inspect_media
from clipfac.stages import sample_source

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="FFmpeg tools required for real-media smoke tests")


def make_video(path, picture="testsrc2=size=360x640:rate=12", sound="sine=frequency=440:sample_rate=48000"):
    command = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", picture]
    if sound:
        command += ["-f", "lavfi", "-i", sound]
    command += ["-t", "1.2", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if sound:
        command += ["-c:a", "aac", "-shortest"]
    command += [str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return path


def test_moving_picture_with_sound_passes_implemented_checks(tmp_path):
    media = make_video(tmp_path / "clean.mp4")
    report = inspect_media(media)
    assert report["status"] == "technical_checks_passed", report
    assert report["width"] == 360 and report["height"] == 640
    assert report["audio"] is True
    assert "editorial_coherence" in report["not_checked"]


def test_black_picture_is_flagged(tmp_path):
    media = make_video(tmp_path / "black.mp4", picture="color=black:size=360x640:rate=12")
    assert "black_intervals" in inspect_media(media)["issues"]


def test_actual_digital_silence_is_flagged(tmp_path):
    media = make_video(tmp_path / "silent.mp4", sound="anullsrc=channel_layout=mono:sample_rate=48000")
    assert "silent_audio" in inspect_media(media)["issues"]


def test_missing_audio_is_flagged(tmp_path):
    media = make_video(tmp_path / "no-audio.mp4", sound=None)
    assert "missing_audio" in inspect_media(media)["issues"]


def test_contact_sheet_contains_actual_frames(tmp_path):
    media = make_video(tmp_path / "source.mp4")
    frames, timestamps = sample_source(media, 1.2, tmp_path, count=2)
    assert len(frames) == len(timestamps) == 2
    from PIL import Image
    with Image.open(tmp_path / "clipfac-contact-sheet.jpg") as sheet:
        assert sheet.width == 1024 and sheet.height > 0


def test_corrupt_video_never_gets_a_pass(tmp_path):
    media = tmp_path / "broken.mp4"
    media.write_bytes(b"not-a-video")
    with pytest.raises(ValueError):
        inspect_media(media)
