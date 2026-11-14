"""Face-aware reframing (9:16): YuNet detector ported from SupoClip.

Covers ``clipfac.face_detection`` (YuNet wrapper + Haar fallback, dominant
face, clip scan, spring trajectory) and the ``face_following`` switch in
``reframe_v2`` — above all the fail-soft invariant: a clip with no faces
renders exactly as it did before this change.

The heavy ML stack (torch / mediapipe / ultralytics) is NOT needed here:
``clipfac.face_detection`` imports only stdlib at module level and
``reframe_v2`` is exercised with the same stub-``main`` pattern as
``test_gpu_decode.py``. Real-video checks run against a synthetic clip built
in-test (drawn face + the Tung Tung still from the user's media library when
present) so the suite stays hermetic.
"""

import os
import subprocess
import sys
import types

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reframe_v2
from clipfac import face_detection as fd

try:
    import cv2 as _cv2_available  # noqa: F401
except ImportError:
    _cv2_available = None

needs_cv2 = pytest.mark.skipif(_cv2_available is None, reason="cv2 not installed")

# A real face-like still from the user's media library (AI-generated Tung
# Tung character; YuNet finds its single frontal face). Skipped when absent —
# the drawn-face fixture below is the hermetic positive case.
MEDIA_FACE = os.path.expanduser(
    "~/workspace/user/media_library/image/image.png")


def _drawn_face(w=400, h=400):
    """A cartoon face YuNet/Haar both fire on (verified by hand)."""
    import cv2

    img = np.full((h, w, 3), 255, np.uint8)
    cv2.ellipse(img, (w // 2, h // 2), (90, 110), 0, 0, 360, (200, 180, 140), -1)
    cv2.circle(img, (w // 2 - 35, h // 2 - 30), 14, (30, 30, 30), -1)
    cv2.circle(img, (w // 2 + 35, h // 2 - 30), 14, (30, 30, 30), -1)
    cv2.ellipse(img, (w // 2, h // 2 + 50), (40, 20), 0, 0, 180, (30, 30, 30), 4)
    return img


# --- detector loading -------------------------------------------------------

@needs_cv2
def test_yunet_detector_loads_when_model_present():
    det = fd.YuNetFaceDetector()
    assert det.detect(np.zeros((100, 100, 3), dtype=np.uint8)) == []


@needs_cv2
def test_yunet_detector_missing_model_raises_and_open_returns_none(tmp_path):
    missing = tmp_path / "nope.onnx"
    with pytest.raises(FileNotFoundError):
        fd.YuNetFaceDetector(missing)
    assert fd.open_yunet_detector(missing) is None


def test_importing_the_module_does_not_import_cv2():
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.');"
         "import clipfac.face_detection; print('cv2' in sys.modules)"],
        capture_output=True, text=True, cwd=os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))), timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


def test_get_detector_is_cached_and_resettable():
    fd.reset_detector()
    assert fd.get_detector() is fd.get_detector()
    assert fd.get_detector() is not None
    fd.reset_detector()


# --- detection on frames ----------------------------------------------------

@needs_cv2
def test_detect_returns_box_on_face_frame():
    boxes = fd.detect_candidates(_drawn_face())
    assert len(boxes) >= 1
    box = boxes[0]["box"]
    assert len(box) == 4 and all(isinstance(v, int) for v in box)
    x, y, w, h = box
    assert 0 <= x and 0 <= y and x + w <= 400 and y + h <= 400
    assert boxes[0]["score"] == w * h


@pytest.mark.skipif(not os.path.exists(MEDIA_FACE), reason="media face still absent")
@needs_cv2
def test_detect_returns_box_on_real_face_still():
    import cv2

    frame = cv2.imread(MEDIA_FACE)
    assert frame is not None
    boxes = fd.detect_candidates(frame)
    assert len(boxes) >= 1
    # The face sits upper-middle in the still.
    x, y, w, h = boxes[0]["box"]
    fh, fw = frame.shape[:2]
    assert 0.2 < (x + w / 2) / fw < 0.8
    assert (y + h / 2) / fh < 0.5


@needs_cv2
def test_detect_returns_empty_on_faceless_frames():
    assert fd.detect_candidates(np.zeros((400, 400, 3), dtype=np.uint8)) == []
    rng = np.random.default_rng(0)
    assert fd.detect_candidates(
        rng.integers(0, 255, (400, 400, 3), dtype=np.uint8)) == []


@needs_cv2
def test_haar_fallback_fires_when_yunet_is_absent():
    haar = fd.open_haar_cascade()
    if haar is None:
        pytest.skip("no Haar cascade in this cv2 build")
    boxes = fd._detect_face_boxes(_drawn_face(), None, haar)
    assert len(boxes) >= 1
    assert fd._detect_face_boxes(np.zeros((400, 400, 3), dtype=np.uint8),
                                 None, haar) == []
    # Both detectors gone: still [], never an exception.
    assert fd._detect_face_boxes(_drawn_face(), None, None) == []


@needs_cv2
def test_dominant_face_is_the_largest_box():
    import cv2

    frame = _drawn_face()
    yunet, haar = fd._open_face_detectors()
    got = fd._detect_dominant_face(frame, yunet, haar)
    assert got is not None
    cx_frac, area_frac = got
    assert 0.0 < cx_frac < 1.0
    assert 0.0 < area_frac < 1.0
    assert fd._detect_dominant_face(
        np.zeros((400, 400, 3), dtype=np.uint8), yunet, haar) is None


# --- clip scan ---------------------------------------------------------------

@needs_cv2
def test_detect_faces_in_clip_on_synthetic_video(tmp_path):
    import cv2

    pytest.importorskip("cv2")
    if not __import__("shutil").which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    frame = _drawn_face(640, 360)
    # The clip scan keeps only faces covering 0.5%–30% of the frame
    # (SupoClip's filter); the drawn face fills 41% of a 400x400 frame, so
    # paste it onto a larger canvas instead of shrinking the drawing (Haar
    # needs the features at their drawn size).
    canvas = np.full((640, 640, 3), 255, np.uint8)
    small = _drawn_face()
    canvas[120:520, 120:520] = small
    frame = canvas
    path = tmp_path / "face.mp4"
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                          30, (640, 640))
    for _ in range(60):
        out.write(frame)
    out.release()
    centers = fd.detect_faces_in_clip(path, 0.0, 2.0)
    assert len(centers) >= 1
    for x, y, _area, _conf in centers:
        assert 0 <= x < 640 and 0 <= y < 640
    assert fd.detect_faces_in_clip(tmp_path / "missing.mp4", 0.0, 2.0) == []


# --- spring trajectory (pure functions, ported verbatim) ---------------------

def test_build_crop_trajectory_needs_signal():
    assert fd.build_crop_trajectory([], 1920, 1080) == []
    sparse = [(t / 3, 960.0 if i == 0 else None, 0.05) for i, t in enumerate(range(30))]
    assert fd.build_crop_trajectory(sparse, 1920, 1080) == []
    assert fd.build_crop_trajectory(
        [(t / 3, 960.0, 0.05) for t in range(30)], 1920, 1920) == []


def test_build_crop_trajectory_follows_a_moving_face():
    track = [(t / 3, 400.0 + t * 40.0, 0.05) for t in range(30)]
    keys = fd.build_crop_trajectory(track, 1920, 1080)
    assert len(keys) >= 2
    assert keys[0][0] == 0.0
    assert keys[-1][1] > keys[0][1]  # crop pans right as the face moves right
    assert all(0 <= x <= 1920 - 1080 for _t, x in keys)
    assert fd.trajectory_has_movement(keys, 1080)
    static = [(t / 3, 960.0, 0.05) for t in range(30)]
    assert not fd.trajectory_has_movement(
        fd.build_crop_trajectory(static, 1920, 1080), 1080)


# --- fail-soft trajectory in reframe_v2 --------------------------------------

class _Cameraman:
    def __init__(self):
        self.current_center_x = 50.0
        self.target_center_x = 50.0
        self.scene_starts = 0

    def begin_scene(self):
        self.scene_starts += 1

    def update_target(self, box):
        self.target_center_x = box[0] + box[2] / 2

    def get_crop_box(self, force_snap=False):
        if force_snap:
            self.current_center_x = self.target_center_x
        else:
            self.current_center_x += (self.target_center_x - self.current_center_x) * 0.25
        x = int(self.current_center_x)
        return x, 0, x + 10, 10


class _Tracker:
    def __init__(self):
        self.resets = 0

    def reset(self):
        self.resets += 1

    def get_target(self, candidates, frame_number, width):
        return candidates[0]["box"] if candidates else None


def _frame(n):
    f = np.zeros((4, 4, 3), dtype=np.uint8)
    f[0, 0, 0], f[0, 0, 1] = n % 256, n // 256
    return f


SCENES = [(0, 37), (37, 90), (90, 91), (91, 150), (150, 233)]
STRATS = ["TRACK", "GENERAL", "TRACK", "SPLIT", "TRACK"]
N = 233


@pytest.fixture
def fake_main(monkeypatch):
    seen = []

    def detect_face_candidates(frame):
        n = int(frame[0, 0, 0]) + 256 * int(frame[0, 0, 1])
        seen.append(("face", n))
        if n % 3 == 0:  # no face on some frames
            return []
        return [{"box": [n % 90, 0, 8, 8]}]

    def detect_person_yolo(frame):
        n = int(frame[0, 0, 0]) + 256 * int(frame[0, 0, 1])
        seen.append(("yolo", n))
        return [n % 70, 0, 6, 6]

    m = types.SimpleNamespace(
        DETECT_STRIDE=4, YOLO_FALLBACK_STRIDE=8, SCENE_CUT_RESET=True,
        detect_face_candidates=detect_face_candidates,
        detect_person_yolo=detect_person_yolo)
    monkeypatch.setitem(sys.modules, "main", m)
    return seen


def _dense(seen, orig_w=100, face_following=True):
    frames = iter([_frame(n) for n in range(N)])
    cam, tr = _Cameraman(), _Tracker()
    xs = reframe_v2._trajectory_loop(lambda: next(frames, None), False, None,
                                     SCENES, STRATS, orig_w, 1.0, cam, tr,
                                     face_following=face_following)
    return xs, list(seen)


def test_face_following_off_makes_no_detector_calls(fake_main):
    xs, seen = _dense(fake_main, face_following=False)
    assert seen == []
    assert len(xs) == N
    # No detector, no YOLO: the camera never leaves the centre.
    assert set(xs) <= {50, None}


def test_no_faces_is_identical_to_face_following_off(monkeypatch, fake_main):
    """The fail-soft invariant: a faceless clip renders exactly as before.

    With the detector returning [] on every frame (and YOLO also returning
    None), face-following on must produce the same trajectory as
    face-following off.
    """
    m = sys.modules["main"]
    monkeypatch.setattr(m, "detect_face_candidates", lambda frame: [])
    monkeypatch.setattr(m, "detect_person_yolo", lambda frame: None)
    xs_on, _ = _dense(fake_main, face_following=True)
    fake_main.clear()
    xs_off, _ = _dense(fake_main, face_following=False)
    assert xs_on == xs_off


def test_face_following_on_moves_the_crop_toward_the_face(fake_main):
    xs, _seen = _dense(fake_main, face_following=True)
    track = [x for x in xs if x is not None]
    # Frame 0 is a scene cut, so the YOLO person fallback snaps the camera to
    # its box immediately (target centre 3) — the trajectory does not start
    # centred when a person is found on the first frame.
    assert track[0] == 3
    assert max(track) > 50  # the (right-side) faces pull the camera right
    xs_off, _ = _dense(fake_main, face_following=False)
    assert xs != xs_off  # the switch actually changes something when faces exist
