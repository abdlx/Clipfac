"""YuNet face detection + face-track helpers for the reframe engine.

Ported from SupoClip (``backend/src/media/face_detection.py`` and the face
parts of ``backend/src/media/reframing.py``) on the user's direct order to
steal the code, not re-implement the idea. What changed in the port:

- Verbatim: ``YuNetFaceDetector`` (YuNet wrapper + ``process``/``close``),
  ``open_yunet_detector``, the ``YUNET_*`` constants, ``smooth_values``,
  ``_median_filter`` and ``build_crop_trajectory`` (critically-damped spring
  trajectory) — logic untouched.
- Adapted: ``open_haar_cascade`` returns ``None`` instead of raising when
  OpenCV has no ``CascadeClassifier`` (opencv 5.x dropped it) or the file is
  missing; ``detect_faces_in_clip`` drops SupoClip's OpenCV-DNN branch (those
  model files ship in neither repo); the SupoClip ``common.logger`` becomes
  stdlib ``logging``; ``FACE_PRESENCE_MIN_AREA`` is defined here (0.002, the
  value from SupoClip's ``common.py``).
- New for Clipfac: a lazy singleton (``get_detector``/``reset_detector``) and
  ``detect_candidates``, which returns Clipfac-style ``{'box': [...],
  'score': ...}`` dicts so ``main.detect_face_candidates`` can use YuNet as a
  drop-in replacement for the legacy MediaPipe detector.

Why this exists: Clipfac's reframe trajectory already tracks faces — but
through MediaPipe's legacy ``mp.solutions.face_detection``, which newer
mediapipe wheels no longer ship (SupoClip's own docstring says every render
silently fell back to Haar). YuNet is the supported detector; Haar stays as
the last-resort fallback. ``cv2`` is imported lazily so importing this module
never pays the import cost and never breaks when OpenCV or the model is
missing — every entry point fails soft to ``[]``/``None``.

Not ported: ``detect_optimal_crop_region`` (needs SupoClip's ffprobe helper;
Clipfac's ``delivery_size`` + ``SmoothedCameraman`` already own crop
geometry) and the compositor (``build_vertical_compositor_filter`` etc.) —
Clipfac renders ffmpeg-native in ``reframe_v2``. The live trajectory also
stays ``SmoothedCameraman`` + ``SpeakerTracker``: that pair is measured and
tuned on real footage (jump confirmation, scene-cut snap, speaker hysteresis)
and ``build_crop_trajectory`` is kept here as a utility for offline tracks,
not wired into the render path.
"""

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Faces smaller than this fraction of the frame are noise, not a subject
# (SupoClip common.FACE_PRESENCE_MIN_AREA).
FACE_PRESENCE_MIN_AREA = 0.002

YUNET_MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"

YUNET_SCORE_THRESHOLD = 0.6

YUNET_NMS_THRESHOLD = 0.3

YUNET_TOP_K = 50

# Frames wider than this are downscaled before detection: YuNet's cost grows
# with input size, and talking-head faces stay well above its ~10px minimum.
YUNET_MAX_INPUT_WIDTH = 960

FaceBox = Tuple[float, float, float, float, float]  # (x, y, w, h, score) in px

_CV2 = None
_CV2_TRIED = False


def _cv2():
    """OpenCV, imported on first use (never at module import time)."""
    global _CV2, _CV2_TRIED
    if not _CV2_TRIED:
        _CV2_TRIED = True
        try:
            import cv2 as _imported

            _CV2 = _imported
        except Exception as exc:  # pragma: no cover - import-time environment
            logger.warning("OpenCV unavailable (%s); face detection disabled", exc)
            _CV2 = None
    if _CV2 is None:
        raise ImportError("OpenCV (cv2) is not available")
    return _CV2


# --- YuNet detector (verbatim port of SupoClip face_detection.py) -----------

class YuNetFaceDetector:
    """Thin stateful wrapper that resizes YuNet's input per frame size."""

    def __init__(
        self,
        model_path: Path = YUNET_MODEL_PATH,
        score_threshold: float = YUNET_SCORE_THRESHOLD,
        nms_threshold: float = YUNET_NMS_THRESHOLD,
        top_k: int = YUNET_TOP_K,
        max_input_width: int = YUNET_MAX_INPUT_WIDTH,
    ):
        cv2 = _cv2()
        if not Path(model_path).is_file():
            raise FileNotFoundError(f"YuNet model not found: {model_path}")
        self._detector = cv2.FaceDetectorYN.create(
            str(model_path), "", (320, 320), score_threshold, nms_threshold, top_k
        )
        self._input_size: Optional[Tuple[int, int]] = None
        self._max_input_width = max(64, int(max_input_width))

    def detect(self, frame_bgr) -> List[FaceBox]:
        """Return face boxes in the frame's own pixel coordinates."""
        cv2 = _cv2()
        height, width = frame_bgr.shape[:2]
        if width <= 0 or height <= 0:
            return []
        scale = 1.0
        image = frame_bgr
        if width > self._max_input_width:
            scale = self._max_input_width / float(width)
            image = cv2.resize(
                frame_bgr,
                (self._max_input_width, max(1, int(round(height * scale)))),
                interpolation=cv2.INTER_AREA,
            )
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        size = (image.shape[1], image.shape[0])
        if size != self._input_size:
            self._detector.setInputSize(size)
            self._input_size = size

        _, faces = self._detector.detect(_np().ascontiguousarray(image))
        if faces is None:
            return []
        boxes: List[FaceBox] = []
        for face in faces:
            x, y, w, h = (float(v) / scale for v in face[:4])
            score = float(face[-1])
            # Clip to the frame: YuNet boxes can extend past the edges.
            x0, y0 = max(0.0, x), max(0.0, y)
            x1, y1 = min(float(width), x + w), min(float(height), y + h)
            if x1 - x0 > 1 and y1 - y0 > 1:
                boxes.append((x0, y0, x1 - x0, y1 - y0, score))
        return boxes

    def process(self, frame_rgb) -> SimpleNamespace:
        """MediaPipe ``FaceDetection.process`` compatible result.

        Call sites written against ``mp.solutions`` read
        ``results.detections[i].location_data.relative_bounding_box`` and
        ``.score``; this keeps them working with YuNet unchanged.
        """
        cv2 = _cv2()
        height, width = frame_rgb.shape[:2]
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        detections = []
        for x, y, w, h, score in self.detect(frame_bgr):
            box = SimpleNamespace(
                xmin=x / width, ymin=y / height, width=w / width, height=h / height
            )
            detections.append(
                SimpleNamespace(
                    location_data=SimpleNamespace(relative_bounding_box=box),
                    score=[score],
                )
            )
        return SimpleNamespace(detections=detections or None)

    def close(self) -> None:
        """No resources to release; kept for MediaPipe-style call sites."""


def _np():
    import numpy as np

    return np


def open_yunet_detector(
    model_path: Path = YUNET_MODEL_PATH, **kwargs
) -> Optional[YuNetFaceDetector]:
    """Load YuNet, or return None (callers fall back to Haar)."""
    try:
        return YuNetFaceDetector(model_path, **kwargs)
    except Exception as exc:
        logger.warning("YuNet face detector unavailable (%s); using Haar", exc)
        return None


def open_haar_cascade():
    """Haar cascade, or None when OpenCV has no CascadeClassifier.

    Adapted from SupoClip: the fallback must degrade to None instead of
    raising. The cascade XML ships in the opencv wheel's ``cv2/data`` — but
    the opencv 5.x wheels dropped that data file, so a copy is bundled at
    ``clipfac/models/haarcascade_frontalface_default.xml`` and tried first.
    """
    try:
        cv2 = _cv2()
    except ImportError:
        return None
    create = getattr(cv2, "CascadeClassifier", None)
    if create is None:
        logger.warning("Haar cascade unavailable (no CascadeClassifier in cv2 %s)",
                       getattr(cv2, "__version__", "?"))
        return None
    candidates = [
        Path(__file__).parent / "models"
        / "haarcascade_frontalface_default.xml",
    ]
    _wheel_dir = getattr(cv2.data, "haarcascades", "")
    if _wheel_dir:
        candidates.append(Path(_wheel_dir)
                          / "haarcascade_frontalface_default.xml")
    for path in candidates:
        if not path.is_file():
            continue
        try:
            cascade = create(str(path))
        except Exception as exc:
            logger.warning("Haar cascade unavailable (%s)", exc)
            continue
        if not cascade.empty():
            return cascade
    return None


# --- detector pair + per-frame boxes (ported from reframing.py) -------------

def _open_face_detectors():
    """Initialise the YuNet (preferred) + Haar (fallback) face detectors.

    Returns (face_detector_or_None, haar_or_None). The YuNet wrapper also
    exposes a MediaPipe-style ``process``/``close`` so older call sites keep
    working.
    """
    return open_yunet_detector(), open_haar_cascade()


def _detect_face_boxes(frame_bgr, face_detector, haar) -> List[Tuple[float, float, float, float]]:
    """All face boxes (x, y, w, h) in a frame, largest first. Frame-pixel coordinates."""
    h, w = frame_bgr.shape[:2]
    frame_area = float(max(1, w * h))
    boxes: List[Tuple[float, float, float, float, float]] = []

    if face_detector is not None:
        try:
            for fx, fy, fw, fh, conf in face_detector.detect(frame_bgr):
                if fw > 10 and fh > 10:
                    boxes.append((float(fw * fh * conf), float(fx), float(fy), float(fw), float(fh)))
        except Exception as exc:
            logger.debug("YuNet detection failed on frame: %s", exc)

    if not boxes and haar is not None:
        try:
            cv2 = _cv2()
            # Haar at full HD resolution is ~10x the cost for no extra faces:
            # SupoClip's analyze pass always ran at proc_width 480, so its
            # Haar never saw a full-res frame. Detect on a <=640px copy and
            # scale the boxes back; the render path already hands us <=640px
            # frames, so this only ever changes the offline clip scan.
            haar_scale = 1.0
            gray_src = frame_bgr
            if w > 640:
                haar_scale = 640.0 / w
                gray_src = cv2.resize(
                    frame_bgr, (640, max(1, int(round(h * haar_scale)))),
                    interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(gray_src, cv2.COLOR_BGR2GRAY)
            sw, sh = gray.shape[1], gray.shape[0]
            min_side = max(14, int(sw * 0.04))
            for (fx, fy, fw, fh) in haar.detectMultiScale(
                gray,
                scaleFactor=1.2,  # coarser scale steps -> ~2x faster
                minNeighbors=3,
                minSize=(min_side, min_side),
                maxSize=(int(sw * 0.7), int(sh * 0.7)),
            ):
                boxes.append((float(fw * fh),
                              float(fx / haar_scale), float(fy / haar_scale),
                              float(fw / haar_scale), float(fh / haar_scale)))
        except Exception:
            pass

    boxes.sort(key=lambda item: item[0], reverse=True)
    kept: List[Tuple[float, float, float, float]] = []
    for _, fx, fy, fw, fh in boxes:
        if (fw * fh) / frame_area < FACE_PRESENCE_MIN_AREA:
            continue
        cx, cy = fx + fw / 2, fy + fh / 2
        if any(k[0] <= cx <= k[0] + k[2] and k[1] <= cy <= k[1] + k[3] for k in kept):
            continue
        kept.append((fx, fy, fw, fh))
        if len(kept) >= 6:
            break
    return kept


def _detect_dominant_face(frame_bgr, face_detector, haar) -> Optional[Tuple[float, float]]:
    """Return (center_x_fraction, area_fraction) of the dominant face, or None."""
    h, w = frame_bgr.shape[:2]
    frame_area = float(max(1, w * h))
    boxes = _detect_face_boxes(frame_bgr, face_detector, haar)
    if not boxes:
        return None
    fx, fy, fw, fh = max(boxes, key=lambda box: box[2] * box[3])
    return (fx + fw / 2.0) / w, (fw * fh) / frame_area


# --- Clipfac adapter: cached detector + candidate dicts ----------------------

_DETECTOR = None
_DETECTOR_TRIED = False
_HAAR = None
_HAAR_TRIED = False


def get_detector() -> Optional[YuNetFaceDetector]:
    """Process-wide YuNet detector, loaded once; None when unavailable."""
    global _DETECTOR, _DETECTOR_TRIED
    if not _DETECTOR_TRIED:
        _DETECTOR_TRIED = True
        _DETECTOR = open_yunet_detector()
    return _DETECTOR


def get_haar_cascade():
    """Process-wide Haar cascade, loaded once; None when unavailable."""
    global _HAAR, _HAAR_TRIED
    if not _HAAR_TRIED:
        _HAAR_TRIED = True
        _HAAR = open_haar_cascade()
    return _HAAR


def reset_detector() -> None:
    """Forget the cached detectors (tests that point at another model file)."""
    global _DETECTOR, _DETECTOR_TRIED, _HAAR, _HAAR_TRIED
    _DETECTOR = None
    _DETECTOR_TRIED = False
    _HAAR = None
    _HAAR_TRIED = False


def detect_candidates(frame_bgr, allow_haar: bool = True):
    """Face boxes as Clipfac candidate dicts: ``{'box': [x, y, w, h],
    'score': area}`` in the frame's own pixel coordinates.

    YuNet first, Haar fallback when YuNet finds nothing (or is unavailable).
    Never raises for a missing model or a missing OpenCV: returns ``[]``.
    """
    try:
        boxes = _detect_face_boxes(frame_bgr, get_detector(),
                                   get_haar_cascade() if allow_haar else None)
    except Exception as exc:
        logger.debug("face detection failed (%s)", exc)
        return []
    return [{"box": [int(x), int(y), int(w), int(h)], "score": w * h}
            for x, y, w, h in boxes]


# --- clip-level scan + trajectory (ported from reframing.py) -----------------

def filter_face_outliers(
    face_centers: List[Tuple[int, int, int, float]],
) -> List[Tuple[int, int, int, float]]:
    """Remove face detections that are outliers (likely false positives)."""
    if len(face_centers) < 3:
        return face_centers

    try:
        import numpy as np

        # Calculate median position
        x_positions = [x for x, y, area, conf in face_centers]
        y_positions = [y for x, y, area, conf in face_centers]

        median_x = np.median(x_positions)
        median_y = np.median(y_positions)

        # Calculate standard deviation
        std_x = np.std(x_positions)
        std_y = np.std(y_positions)

        # Filter out faces that are more than 2 standard deviations away
        filtered_faces = []
        for face in face_centers:
            x, y, area, conf = face
            if abs(x - median_x) <= 2 * std_x and abs(y - median_y) <= 2 * std_y:
                filtered_faces.append(face)

        logger.info(
            f"Filtered {len(face_centers)} -> {len(filtered_faces)} faces (removed outliers)"
        )
        return (
            filtered_faces if filtered_faces else face_centers
        )  # Return original if all filtered

    except Exception as e:
        logger.warning(f"Error filtering face outliers: {e}")
        return face_centers


def detect_faces_in_clip(
    video_path: Path, start_time: float, end_time: float
) -> List[Tuple[int, int, int, float]]:
    """
    Improved face detection using multiple methods and temporal consistency.
    Returns list of (x, y, area, confidence) tuples.

    Ported from SupoClip's reframing.detect_faces_in_clip, minus its OpenCV-DNN
    branch: those model files ship in neither repo, so the chain here is
    YuNet first, Haar as the last-resort fallback.
    """
    face_centers = []

    try:
        # YuNet (most accurate) with Haar as the last-resort fallback
        yunet, haar_cascade = _open_face_detectors()

        # Sample more frames for better face detection (every 0.5 seconds)
        duration = end_time - start_time
        sample_interval = min(0.5, duration / 10)  # At least 10 samples, max every 0.5s
        sample_times = []

        current_time = start_time
        while current_time < end_time:
            sample_times.append(current_time)
            current_time += sample_interval

        # Ensure we always sample the middle and end
        if duration > 1.0:
            middle_time = start_time + duration / 2
            if middle_time not in sample_times:
                sample_times.append(middle_time)

        sample_times = [t for t in sample_times if t < end_time]
        logger.info(f"Sampling {len(sample_times)} frames for face detection")

        cv2 = _cv2()
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            logger.warning("Unable to open video for face detection: %s", video_path)
            return []

        for sample_time in sample_times:
            try:
                capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, sample_time) * 1000.0)
                ok, frame_bgr = capture.read()
                if not ok or frame_bgr is None:
                    continue
                height, width = frame_bgr.shape[:2]
                detected_faces = []

                # Try YuNet first (most accurate)
                if yunet is not None:
                    try:
                        for fx, fy, fw, fh, confidence in yunet.detect(frame_bgr):
                            x, y, w, h = int(fx), int(fy), int(fw), int(fh)
                            if w > 30 and h > 30:  # Minimum face size
                                detected_faces.append((x, y, w, h, confidence))
                    except Exception as e:
                        logger.warning(
                            f"YuNet detection failed for frame at {sample_time}s: {e}"
                        )

                # If YuNet didn't find faces, use Haar cascade
                if not detected_faces and haar_cascade is not None:
                    try:
                        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

                        faces = haar_cascade.detectMultiScale(
                            gray,
                            scaleFactor=1.05,  # More sensitive
                            minNeighbors=3,  # Less strict
                            minSize=(40, 40),  # Smaller minimum size
                            maxSize=(
                                int(width * 0.7),
                                int(height * 0.7),
                            ),  # Maximum size limit
                        )

                        for x, y, w, h in faces:
                            # Estimate confidence based on face size and position
                            face_area = w * h
                            relative_size = face_area / (width * height)
                            confidence = min(
                                0.9, 0.3 + relative_size * 2
                            )  # Rough confidence estimate
                            detected_faces.append((x, y, w, h, confidence))
                    except Exception as e:
                        logger.warning(
                            f"Haar cascade detection failed for frame at {sample_time}s: {e}"
                        )

                # Process detected faces
                for x, y, w, h, confidence in detected_faces:
                    face_center_x = x + w // 2
                    face_center_y = y + h // 2
                    face_area = w * h

                    # Filter out very small or very large faces
                    frame_area = width * height
                    relative_area = face_area / frame_area

                    if (
                        0.005 < relative_area < 0.3
                    ):  # Face should be 0.5% to 30% of frame
                        face_centers.append(
                            (face_center_x, face_center_y, face_area, confidence)
                        )

            except Exception as e:
                logger.warning(f"Error detecting faces in frame at {sample_time}s: {e}")
                continue

        capture.release()

        # Remove outliers (faces that are very far from the median position)
        if len(face_centers) > 2:
            face_centers = filter_face_outliers(face_centers)

        logger.info(f"Detected {len(face_centers)} reliable face centers")
        return face_centers

    except Exception as e:
        logger.error(f"Error in face detection: {e}")
        return []


def smooth_values(values: List[float], window: int = 15) -> List[float]:
    if not values:
        return []
    smoothed: List[float] = []
    half = window // 2
    for idx in range(len(values)):
        start = max(0, idx - half)
        end = min(len(values), idx + half + 1)
        smoothed.append(sum(values[start:end]) / (end - start))
    return smoothed


def _median_filter(values: List[float], window: int = 3) -> List[float]:
    """Small median filter to remove single-frame detection spikes."""
    if window <= 1 or len(values) < window:
        return list(values)
    half = window // 2
    out: List[float] = []
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        seg = sorted(values[lo:hi])
        out.append(seg[len(seg) // 2])
    return out


def build_crop_trajectory(
    track: List[Tuple[float, Optional[float], float]],
    width: int,
    crop_w: int,
    *,
    deadzone_frac: float = 0.05,
    smooth_time: float = 0.9,
    max_pan_speed_frac: float = 0.4,
) -> List[Tuple[float, int]]:
    """Turn a raw face-centre track into a smooth, eased crop-x trajectory.

    Returns keyframes [(t, x)] for the crop's left edge. The motion is produced
    by a critically-damped spring (Unity-style SmoothDamp) easing toward a
    comfort-zone target, which gives natural ease-in/ease-out with no overshoot
    and no mechanical ramp-then-stop feel. A deadzone keeps the frame still for
    small head movements; a median pre-filter removes detection spikes. Returns
    [] when there isn't enough signal to track.
    """
    if not track:
        return []
    max_x = max(0, width - crop_w)
    if max_x <= 0:
        return []

    centers: List[Optional[float]] = [c for _, c, _ in track]
    times = [t for t, _, _ in track]
    detected = sum(1 for c in centers if c is not None)
    if detected < max(3, len(centers) // 5):
        return []  # too sparse to trust — caller falls back to a static crop

    # Gap-fill missing detections: forward fill, then back fill.
    last: Optional[float] = None
    for i in range(len(centers)):
        if centers[i] is None:
            centers[i] = last
        else:
            last = centers[i]
    last = None
    for i in range(len(centers) - 1, -1, -1):
        if centers[i] is None:
            centers[i] = last
        else:
            last = centers[i]
    if any(c is None for c in centers):
        return []

    desired = [min(max(c - crop_w / 2.0, 0.0), float(max_x)) for c in centers]
    desired = _median_filter(desired, window=3)

    deadzone = max(2.0, crop_w * deadzone_frac)
    max_speed = max(1.0, width * max_pan_speed_frac)
    smooth_time = max(0.05, smooth_time)
    omega = 2.0 / smooth_time

    # Comfort-zone target: a stable goal that only moves once the subject drifts
    # past the deadzone, so the spring isn't chasing sub-deadzone jitter.
    targets: List[float] = []
    anchor = desired[0]
    for d in desired:
        if d - anchor > deadzone:
            anchor = d - deadzone
        elif anchor - d > deadzone:
            anchor = d + deadzone
        targets.append(anchor)

    # Critically-damped spring toward the comfort-zone target.
    eased: List[float] = []
    cur = float(targets[0])
    vel = 0.0
    for i, tgt in enumerate(targets):
        dt = (times[i] - times[i - 1]) if i > 0 else 0.0
        if dt <= 0:
            eased.append(cur)
            continue
        x = omega * dt
        exp_factor = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)
        change = cur - tgt
        max_change = max_speed * smooth_time
        change = max(-max_change, min(change, max_change))
        adj_target = cur - change
        temp = (vel + omega * change) * dt
        vel = (vel - omega * temp) * exp_factor
        out = adj_target + (change + temp) * exp_factor
        # Prevent overshoot past the target.
        if (tgt - cur > 0) == (out > tgt):
            out = tgt
            vel = (out - tgt) / dt
        cur = min(max(out, 0.0), float(max_x))
        eased.append(cur)

    # Final low-pass pass: removes residual velocity steps so the piecewise-
    # linear keyframes read as continuous, fluid motion.
    eased = smooth_values(eased, window=5)

    # Keep keyframes fine enough that linear interpolation tracks the smooth
    # curve without visible faceting.
    def simplify(tol: float) -> List[Tuple[float, int]]:
        keys: List[Tuple[float, int]] = [(0.0, int(round(eased[0])))]
        for i in range(1, len(eased)):
            if abs(eased[i] - keys[-1][1]) >= tol:
                keys.append((times[i], int(round(eased[i]))))
        if keys[-1][0] < times[-1]:
            keys.append((times[-1], int(round(eased[-1]))))
        return keys

    tol = max(1.5, crop_w * 0.006)
    keys = simplify(tol)
    while len(keys) > 90:
        tol *= 1.5
        keys = simplify(tol)

    if keys and keys[0][0] > 0.0:
        keys[0] = (0.0, keys[0][1])
    return keys


def trajectory_has_movement(keys: List[Tuple[float, int]], crop_w: int) -> bool:
    """Whether a trajectory pans enough to be worth a moving crop."""
    if len(keys) < 2:
        return False
    xs = [x for _, x in keys]
    return (max(xs) - min(xs)) >= max(8, crop_w * 0.04)
