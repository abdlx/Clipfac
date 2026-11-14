"""Editor ops v1 (clipfac.editor_ops): deterministic ffmpeg trim / split /
merge of rendered clips, plus the SupoClip template style burn.

Test clips are generated with ffmpeg testsrc (no -low job is ever run).
"""
import json
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipfac import editor_ops
from clipfac.editor_ops import (
    EditorOpError, merge_clip_files, probe_clip, router, split_clip, trim_clip,
)


def _make_clip(path, duration=6.0, width=360, height=640):
    cmd = ["ffmpeg", "-y", "-f", "lavfi",
           f"-i", f"testsrc=duration={duration}:size={width}x{height}:rate=30",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
           "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
           "-c:a", "aac", "-movflags", "+faststart", str(path)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    return path


def _words(start=0.0, count=12, step=0.5):
    return [{"word": f" word{i}", "start": start + i * step,
             "end": start + (i + 1) * step} for i in range(count)]


@pytest.fixture()
def two_clips(tmp_path):
    a = _make_clip(tmp_path / "a.mp4", duration=4.0)
    b = _make_clip(tmp_path / "b.mp4", duration=3.0)
    return a, b


class TestProbe:
    def test_probe_reports_duration_and_dims(self, tmp_path):
        clip = _make_clip(tmp_path / "c.mp4", duration=5.0)
        info = probe_clip(str(clip))
        assert info["width"] == 360 and info["height"] == 640
        assert abs(info["duration"] - 5.0) < 0.15
        assert info["codec"] == "h264"

    def test_probe_missing_file_raises(self):
        with pytest.raises(EditorOpError):
            probe_clip("/no/such/clip.mp4")


class TestTrim:
    def test_trim_changes_duration_and_keeps_dims(self, tmp_path):
        clip = _make_clip(tmp_path / "c.mp4", duration=6.0)
        out = tmp_path / "trimmed.mp4"
        trim_clip(str(clip), 1.0, 4.0, str(out), str(tmp_path))
        info = probe_clip(str(out))
        assert abs(info["duration"] - 3.0) < 0.2
        assert (info["width"], info["height"]) == (360, 640)

    def test_trim_past_the_end_is_a_400_not_a_crash(self, tmp_path):
        clip = _make_clip(tmp_path / "c.mp4", duration=6.0)
        with pytest.raises(EditorOpError):
            trim_clip(str(clip), 5.0, 9.0, str(tmp_path / "x.mp4"), str(tmp_path))
        with pytest.raises(EditorOpError):
            trim_clip(str(clip), 2.0, 2.2, str(tmp_path / "x.mp4"), str(tmp_path))


class TestSplit:
    def test_split_durations_sum_to_the_whole(self, tmp_path):
        clip = _make_clip(tmp_path / "c.mp4", duration=6.0)
        a, b = split_clip(str(clip), 2.5, str(tmp_path / "a.mp4"),
                          str(tmp_path / "b.mp4"), str(tmp_path))
        assert abs(probe_clip(a)["duration"] - 2.5) < 0.2
        assert abs(probe_clip(b)["duration"] - 3.5) < 0.2

    def test_split_outside_the_clip_is_rejected(self, tmp_path):
        clip = _make_clip(tmp_path / "c.mp4", duration=6.0)
        with pytest.raises(EditorOpError):
            split_clip(str(clip), 0.0, str(tmp_path / "a.mp4"),
                       str(tmp_path / "b.mp4"), str(tmp_path))
        with pytest.raises(EditorOpError):
            split_clip(str(clip), 5.9, str(tmp_path / "a.mp4"),
                       str(tmp_path / "b.mp4"), str(tmp_path))


class TestMerge:
    def test_merge_concatenates_matching_clips(self, tmp_path, two_clips):
        a, b = (str(p) for p in two_clips)
        out = str(tmp_path / "merged.mp4")
        merge_clip_files([a, b], out, str(tmp_path))
        info = probe_clip(out)
        assert abs(info["duration"] - 7.0) < 0.3
        assert (info["width"], info["height"]) == (360, 640)

    def test_merge_mismatched_dims_reencodes_to_the_first(self, tmp_path):
        a = _make_clip(tmp_path / "small.mp4", duration=2.0, width=360, height=640)
        b = _make_clip(tmp_path / "big.mp4", duration=2.0, width=720, height=1280)
        out = str(tmp_path / "merged.mp4")
        merge_clip_files([str(a), str(b)], out, str(tmp_path))
        info = probe_clip(out)
        assert abs(info["duration"] - 4.0) < 0.3
        assert (info["width"], info["height"]) == (360, 640)

    def test_merge_needs_at_least_two(self, tmp_path, two_clips):
        with pytest.raises(EditorOpError):
            merge_clip_files([str(two_clips[0])], str(tmp_path / "m.mp4"),
                             str(tmp_path))


# --- router ---------------------------------------------------------------

@pytest.fixture()
def job_client(tmp_path):
    """A fake job dir with one clip + metadata transcript, behind TestClient."""
    job_dir = tmp_path / "job1"
    job_dir.mkdir()
    clip = _make_clip(job_dir / "job1_clip_1.mp4", duration=6.0)
    metadata = {
        "shorts": [{
            "video_url": "/videos/job1/job1_clip_1.mp4",
            "video_title_for_youtube_short": "Test clip",
            "start": 0, "end": 6,
            "recipe": {"segments": [{"start": 0, "end": 6}]},
        }],
        "transcript": {"language": "en", "segments": [
            {"start": 0, "end": 6, "text": "test words here",
             "words": _words(count=12)}]},
    }
    (job_dir / "job1_metadata.json").write_text(json.dumps(metadata))
    app = FastAPI()
    app.state.clipfac_namespace = {"OUTPUT_DIR": str(tmp_path)}
    app.include_router(router)
    return TestClient(app), job_dir


class TestRouter:
    def test_templates_lists_all_seven(self, job_client):
        client, _ = job_client
        res = client.get("/api/editor/templates")
        assert res.status_code == 200
        ids = [t["id"] for t in res.json()["templates"]]
        assert ids == ["default", "hormozi", "mrbeast", "minimal",
                       "tiktok", "neon", "podcast"]

    def test_trim_endpoint(self, job_client):
        client, job_dir = job_client
        res = client.post("/api/editor/trim", json={
            "job_id": "job1", "clip_index": 0, "start": 1.0, "end": 4.0})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["new_file"].startswith("trimmed_")
        assert abs(body["duration"] - 3.0) < 0.2
        assert (job_dir / body["new_file"]).exists()

    def test_split_endpoint(self, job_client):
        client, job_dir = job_client
        res = client.post("/api/editor/split", json={
            "job_id": "job1", "clip_index": 0, "at": 2.5})
        assert res.status_code == 200, res.text
        body = res.json()
        assert abs(body["part_a"]["duration"] - 2.5) < 0.2
        assert abs(body["part_b"]["duration"] - 3.5) < 0.2

    def test_merge_clips_appends_a_metadata_entry(self, job_client):
        client, job_dir = job_client
        # Second clip in the same job so merge has two inputs.
        _make_clip(job_dir / "job1_clip_2.mp4", duration=3.0)
        meta_path = job_dir / "job1_metadata.json"
        data = json.loads(meta_path.read_text())
        data["shorts"].append({
            "video_url": "/videos/job1/job1_clip_2.mp4",
            "video_title_for_youtube_short": "Second clip",
            "start": 0, "end": 3})
        meta_path.write_text(json.dumps(data))
        res = client.post("/api/editor/merge-clips", json={
            "job_id": "job1", "clip_indices": [0, 1]})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["new_file"].startswith("merged_")
        assert abs(body["duration"] - 9.0) < 0.4
        assert body["clip_index"] == 2
        data = json.loads(meta_path.read_text())
        assert len(data["shorts"]) == 3
        assert data["shorts"][2]["merged_from"] == [0, 1]

    def test_style_endpoint_burns_template_plus_hook(self, job_client):
        client, job_dir = job_client
        res = client.post("/api/editor/style", json={
            "job_id": "job1", "clip_index": 0, "template": "hormozi",
            "hook_title": "POV: 24 kills one mag"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["template"] == "hormozi"
        assert body["new_file"].startswith("templated_")
        assert abs(body["duration"] - 6.0) < 0.3
        assert (job_dir / body["new_file"]).exists()
        data = json.loads((job_dir / "job1_metadata.json").read_text())
        assert data["shorts"][0]["caption_template"] == "hormozi"
        assert data["shorts"][0]["video_url"].endswith(body["new_file"])
        # The .ass the burn used is on disk next to the clip.
        assert list(job_dir.glob("style_0_*.ass"))

    def test_style_endpoint_rejects_unknown_clip(self, job_client):
        client, _ = job_client
        res = client.post("/api/editor/style", json={
            "job_id": "job1", "clip_index": 9, "template": "hormozi"})
        assert res.status_code == 404

    def test_style_endpoint_rejects_bad_job_id(self, job_client):
        client, _ = job_client
        res = client.post("/api/editor/style", json={
            "job_id": "../job1", "clip_index": 0, "template": "hormozi"})
        assert res.status_code == 400
