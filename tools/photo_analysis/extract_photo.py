"""
Extract a passport-style face photo from a PDF or image document.

This is the web-ready form of the repository's document photo extractor. It
keeps the InsightFace path for high-recall detection and falls back to
OpenCV's bundled face cascade when the optional model is not installed.

Examples:
    python extract_photo.py document.pdf --output result.json --save-dir artifacts
    python extract_photo.py document.pdf --save extracted-photo.jpg
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np


BLUR_THRESHOLD = 80.0
BRIGHTNESS_MIN = 35.0
BRIGHTNESS_MAX = 225.0
FACE_CONFIDENCE_THRESHOLD = 0.50
OPENCV_RENDERED_MIN_CROP_SIDE = 160
OPENCV_MULTI_PHOTO_CONFIDENCE = 0.80
OPENCV_NO_EYE_PAIR_CONFIDENCE = 0.90
OPENCV_MAX_NEAR_WHITE_FRACTION = 0.48
# A face always contains smooth area - a cheek, a forehead, a plain backdrop.
# A QR code, a barcode, or a halftone stamp contains none: every block spans
# several modules, so every block is high-variance. Haar cascades fire readily
# on that kind of texture, so the OpenCV fallback requires some smooth area
# before it will call a region a photo. Measured over the corpus, real
# portraits scored 0.215 and above (including badly degraded photocopies)
# while a QR code misread as a face scored 0.059.
OPENCV_MIN_SMOOTH_BLOCK_FRACTION = 0.12
SMOOTH_BLOCK_STD = 20.0
SMOOTH_BLOCK_SIZE = 8
SMOOTH_NORMALIZED_SIDE = 128
MAX_DETECTED_PHOTOS = 50
PHOTO_RENDER_DPI = 300
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".tif", ".tiff", ".bmp"}

# How hard to look for photos before reporting a document as having none, and
# before concluding the ones found are all of them.
#
# The pipeline has always had two stages: pull the raster images embedded in
# the PDF (cheap, no rendering), then render every page and sweep it with a
# sliding window (expensive). Until this setting existed, stage two ran only
# when stage one found *nothing* — so a page whose embedded pass yielded one
# photo was never swept, and a second photo elsewhere on that page was never
# found. That matters most for identity matching, which compares every photo
# in a document against every photo seen earlier.
#
#   low    - embedded images only. Never renders. Cheapest by a wide margin.
#            "No photo" here can mean "the page was never rendered".
#   medium - embedded images, then render and sweep only if nothing was found.
#            The historical behaviour, and still the default.
#   high   - always render and sweep every page, even when the embedded pass
#            already found a photo, at a higher resolution, a denser window
#            stride, and a lower face-confidence floor.
#
# `render_fallback` is whether an empty result escalates to rendering;
# `always_render` sweeps regardless; `stride` is the sliding-window step in
# pixels (smaller = denser = slower); `min_confidence` is the face score a
# region must reach to count as a photo.
PHOTO_EFFORT_LEVELS: dict[str, dict[str, Any]] = {
    "low": {
        "render_fallback": False, "always_render": False,
        "dpi": 200, "stride": 160, "min_confidence": 0.50,
    },
    "medium": {
        "render_fallback": True, "always_render": False,
        "dpi": 300, "stride": 120, "min_confidence": 0.50,
    },
    "high": {
        "render_fallback": True, "always_render": True,
        "dpi": 400, "stride": 90, "min_confidence": 0.40,
    },
}
PHOTO_DEFAULT_EFFORT = "medium"


def effort_settings(effort: str | None) -> dict[str, Any]:
    """Resolve an effort name to its knobs, falling back to the default."""

    return PHOTO_EFFORT_LEVELS.get(
        str(effort or "").strip().lower(), PHOTO_EFFORT_LEVELS[PHOTO_DEFAULT_EFFORT]
    )

_face_app: Any | None = None


@dataclass
class DetectedFace:
    bbox: np.ndarray
    det_score: float
    eye_pair: bool = False


class OpenCvFaceApp:
    """Small adapter exposing the subset of InsightFace used by this tool."""

    backend_name = "opencv"

    def __init__(self) -> None:
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        eye_path = Path(cv2.data.haarcascades) / "haarcascade_eye_tree_eyeglasses.xml"
        self.cascade = cv2.CascadeClassifier(str(cascade_path))
        self.eye_cascade = cv2.CascadeClassifier(str(eye_path))
        if self.cascade.empty() or self.eye_cascade.empty():
            raise RuntimeError("OpenCV's bundled face-validation models are unavailable.")

    def _has_eye_pair(self, gray: np.ndarray, box: tuple[int, int, int, int]) -> bool:
        x, y, width, height = box
        upper_face = gray[y : y + max(1, int(height * 0.68)), x : x + width]
        min_eye = max(5, min(width, height) // 10)
        eyes = self.eye_cascade.detectMultiScale(
            upper_face,
            scaleFactor=1.08,
            minNeighbors=4,
            minSize=(min_eye, min_eye),
            maxSize=(max(min_eye + 1, width // 2), max(min_eye + 1, height // 3)),
        )
        centers = [
            (eye_x + eye_width / 2, eye_y + eye_height / 2)
            for eye_x, eye_y, eye_width, eye_height in eyes
        ]
        for index, first in enumerate(centers):
            for second in centers[index + 1 :]:
                horizontal_gap = abs(first[0] - second[0])
                vertical_gap = abs(first[1] - second[1])
                if width * 0.16 <= horizontal_gap <= width * 0.72 and vertical_gap <= height * 0.22:
                    return True
        return False

    def get(self, image: np.ndarray) -> list[DetectedFace]:
        if image is None or image.size == 0:
            return []
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        min_side = max(20, min(gray.shape[:2]) // 12)
        boxes, _, weights = self.cascade.detectMultiScale3(
            gray,
            scaleFactor=1.08,
            minNeighbors=6,
            minSize=(min_side, min_side),
            outputRejectLevels=True,
        )
        faces = []
        for (x, y, width, height), weight in zip(boxes, weights):
            box = (int(x), int(y), int(width), int(height))
            eye_pair = self._has_eye_pair(gray, box)
            # An eye pair is useful positive evidence, but it cannot be a hard
            # requirement: small, photocopied ID portraits often preserve the
            # face outline while losing individual eye detail.
            confidence = min(
                0.95,
                max(0.60, 0.72 + float(weight) / 40.0 + (0.06 if eye_pair else 0.0)),
            )
            faces.append(DetectedFace(
                bbox=np.asarray([x, y, x + width, y + height], dtype=float),
                det_score=confidence,
                eye_pair=eye_pair,
            ))
        return sorted(
            faces,
            key=lambda face: float((face.bbox[2] - face.bbox[0]) * (face.bbox[3] - face.bbox[1])),
            reverse=True,
        )


def get_face_app() -> Any:
    """Return InsightFace when available, otherwise a dependency-free fallback."""

    global _face_app
    if _face_app is not None:
        return _face_app

    requested = os.getenv("PHOTO_DETECTOR_BACKEND", "auto").strip().lower()
    if requested != "opencv":
        try:
            from insightface.app import FaceAnalysis

            app = FaceAnalysis(
                name=os.getenv("PHOTO_INSIGHTFACE_MODEL", "buffalo_sc"),
                providers=["CPUExecutionProvider"],
            )
            app.prepare(ctx_id=0, det_size=(320, 320))
            app.backend_name = "insightface"
            _face_app = app
            return _face_app
        except Exception as exc:  # noqa: BLE001 - the local fallback is intentional
            if requested == "insightface":
                raise RuntimeError(f"InsightFace could not be loaded: {exc}") from exc

    _face_app = OpenCvFaceApp()
    return _face_app


def quality_check(image: np.ndarray) -> tuple[float, float, float]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return blur, float(gray.mean()), float(gray.std())


def smooth_block_fraction(image: np.ndarray) -> float:
    """Fraction of the crop made of flat, low-variance blocks.

    The crop is normalized to a fixed side so the measure does not depend on
    how large the region happened to be, then split into square blocks. A block
    counts as smooth when its pixels vary little. Faces carry broad smooth
    areas; machine-readable marks and halftone patterns carry almost none.
    """

    if image is None or image.size == 0:
        return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    side = SMOOTH_NORMALIZED_SIDE
    resized = cv2.resize(gray, (side, side)).astype(np.float32)
    block = SMOOTH_BLOCK_SIZE
    count = side // block
    blocks = (
        resized.reshape(count, block, count, block)
        .transpose(0, 2, 1, 3)
        .reshape(-1, block * block)
    )
    return float((blocks.std(axis=1) < SMOOTH_BLOCK_STD).mean())


def _texture_rejects_face(app: Any, crop: np.ndarray) -> bool:
    """True when a region is too uniformly busy to be a photograph.

    Only the OpenCV fallback needs this. InsightFace is a trained face model
    and does not confuse a QR block for a face, so its results are left alone.
    """

    if str(getattr(app, "backend_name", "")).lower() != "opencv":
        return False
    return smooth_block_fraction(crop) < OPENCV_MIN_SMOOTH_BLOCK_FRACTION


def pdf_to_images(pdf_path: Path, dpi: int = PHOTO_RENDER_DPI) -> list[tuple[int, np.ndarray]]:
    images: list[tuple[int, np.ndarray]] = []
    with fitz.open(pdf_path) as document:
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        for page_number, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )
            images.append((page_number, cv2.cvtColor(image, cv2.COLOR_RGB2BGR)))
    return images


def extract_embedded_pdf_images(pdf_path: Path) -> list[tuple[int, np.ndarray]]:
    """Return usable embedded raster images before paying to render every page."""

    images: list[tuple[int, np.ndarray]] = []
    seen_xrefs: set[int] = set()
    with fitz.open(pdf_path) as document:
        for page_number, page in enumerate(document, start=1):
            for image_info in page.get_images(full=True):
                xref = int(image_info[0])
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                try:
                    extracted = document.extract_image(xref)
                    encoded = np.frombuffer(extracted["image"], dtype=np.uint8)
                    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
                except (KeyError, RuntimeError, ValueError):
                    continue
                if image is not None and image.shape[0] > 60 and image.shape[1] > 50:
                    images.append((page_number, image))
    return images


def find_photo_candidates(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Find rectangular page regions whose size and aspect resemble an ID photo."""

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 30, 120)
    edges = cv2.dilate(edges, np.ones((4, 4), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    image_area = image.shape[0] * image.shape[1]
    candidates: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        fraction = (width * height) / max(image_area, 1)
        aspect = width / max(height, 1)
        if 0.003 < fraction < 0.18 and 0.4 < aspect < 1.3 and width > 40 and height > 50:
            candidates.append((x, y, width, height))
    candidates.sort(key=lambda region: region[2] * region[3], reverse=True)
    return candidates[:8]


def _best_face(app: Any, image: np.ndarray) -> Any | None:
    try:
        faces = app.get(image)
    except Exception:
        return None
    if not faces:
        return None
    return max(faces, key=lambda face: float(face.det_score))


def _face_area_fraction(face: Any, image: np.ndarray) -> float:
    left, top, right, bottom = np.asarray(face.bbox, dtype=float)
    return max(0.0, right - left) * max(0.0, bottom - top) / max(
        float(image.shape[0] * image.shape[1]), 1.0
    )


def sliding_window_search(
    image: np.ndarray,
    app: Any,
    tile: int = 350,
    stride: int = 120,
) -> tuple[np.ndarray | None, float, tuple[int, int, int, int] | None]:
    """Search a rendered page when the portrait border cannot be isolated."""

    height, width = image.shape[:2]
    tile = min(tile, max(height, width))
    x_starts = list(range(0, max(1, width - tile + 1), stride))
    y_starts = list(range(0, max(1, height - tile + 1), stride))
    if x_starts[-1] != max(0, width - tile):
        x_starts.append(max(0, width - tile))
    if y_starts[-1] != max(0, height - tile):
        y_starts.append(max(0, height - tile))

    best_score = 0.0
    best_crop: np.ndarray | None = None
    best_bbox: tuple[int, int, int, int] | None = None
    for y in y_starts:
        for x in x_starts:
            patch = image[y : min(height, y + tile), x : min(width, x + tile)]
            face = _best_face(app, patch)
            if face is None or float(face.det_score) <= best_score:
                continue
            face_box = np.asarray(face.bbox, dtype=int)
            pad = max(30, int(max(face_box[2] - face_box[0], face_box[3] - face_box[1]) * 0.38))
            x1 = max(0, x + int(face_box[0]) - pad)
            y1 = max(0, y + int(face_box[1]) - pad)
            x2 = min(width, x + int(face_box[2]) + pad)
            y2 = min(height, y + int(face_box[3]) + pad)
            best_score = float(face.det_score)
            best_crop = image[y1:y2, x1:x2]
            best_bbox = (x1, y1, x2, y2)
    return best_crop, best_score, best_bbox


def _box_iou(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    first_area = max(0, first[2] - first[0]) * max(0, first[3] - first[1])
    second_area = max(0, second[2] - second[0]) * max(0, second[3] - second[1])
    return intersection / max(first_area + second_area - intersection, 1)


def _same_face(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> bool:
    if _box_iou(first, second) >= 0.30:
        return True
    first_center = ((first[0] + first[2]) / 2, (first[1] + first[3]) / 2)
    second_center = ((second[0] + second[2]) / 2, (second[1] + second[3]) / 2)
    distance = float(np.hypot(
        first_center[0] - second_center[0],
        first_center[1] - second_center[1],
    ))
    largest_face_side = max(
        first[2] - first[0],
        first[3] - first[1],
        second[2] - second[0],
        second[3] - second[1],
    )
    return distance <= largest_face_side * 0.35


def sliding_window_search_all(
    image: np.ndarray,
    app: Any,
    *,
    minimum_score: float,
    tile: int = 350,
    stride: int = 120,
) -> list[dict[str, Any]]:
    """Return every distinct face found while scanning a rendered page."""

    height, width = image.shape[:2]
    tile = min(tile, max(height, width))
    x_starts = list(range(0, max(1, width - tile + 1), stride))
    y_starts = list(range(0, max(1, height - tile + 1), stride))
    if x_starts[-1] != max(0, width - tile):
        x_starts.append(max(0, width - tile))
    if y_starts[-1] != max(0, height - tile):
        y_starts.append(max(0, height - tile))

    detections: list[dict[str, Any]] = []
    for y in y_starts:
        for x in x_starts:
            patch = image[y : min(height, y + tile), x : min(width, x + tile)]
            try:
                faces = app.get(patch)
            except Exception:
                continue
            for face in faces:
                score = float(face.det_score)
                if score < minimum_score:
                    continue
                local_box = np.asarray(face.bbox, dtype=int)
                face_bbox = (
                    x + int(local_box[0]),
                    y + int(local_box[1]),
                    x + int(local_box[2]),
                    y + int(local_box[3]),
                )
                face_side = max(
                    face_bbox[2] - face_bbox[0],
                    face_bbox[3] - face_bbox[1],
                )
                pad = max(30, int(face_side * 0.38))
                crop_bbox = (
                    max(0, face_bbox[0] - pad),
                    max(0, face_bbox[1] - pad),
                    min(width, face_bbox[2] + pad),
                    min(height, face_bbox[3] + pad),
                )
                detections.append({
                    "crop": image[crop_bbox[1] : crop_bbox[3], crop_bbox[0] : crop_bbox[2]],
                    "face_score": score,
                    "eye_pair": bool(getattr(face, "eye_pair", False)),
                    "bbox_px": crop_bbox,
                    "face_bbox_px": face_bbox,
                    "source_priority": 1,
                })

    kept: list[dict[str, Any]] = []
    for detection in sorted(
        detections,
        key=lambda item: (float(item["face_score"]), int(item["source_priority"])),
        reverse=True,
    ):
        if any(_same_face(detection["face_bbox_px"], item["face_bbox_px"]) for item in kept):
            continue
        kept.append(detection)
    return kept


def _result_for_crop(
    crop: np.ndarray,
    face_score: float,
    bbox: tuple[int, int, int, int],
) -> dict[str, Any]:
    blur, brightness, contrast = quality_check(crop)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    near_white_fraction = float((gray > 230).mean())
    smooth_fraction = smooth_block_fraction(crop)
    if blur < BLUR_THRESHOLD:
        passed, reason = False, "image_too_blurry"
    elif not BRIGHTNESS_MIN < brightness < BRIGHTNESS_MAX:
        passed, reason = False, "bad_lighting"
    else:
        passed, reason = True, "ok"
    return {
        "pass": passed,
        "reason": reason,
        "blur": blur,
        "brightness": brightness,
        "contrast": contrast,
        "near_white_fraction": near_white_fraction,
        "smooth_block_fraction": smooth_fraction,
        "face_score": face_score,
        "crop": crop,
        "bbox_px": bbox,
    }


def extract_all_and_validate(
    image: np.ndarray,
    app: Any,
    *,
    prefer_full_image: bool = False,
    minimum_crop_side: int = 0,
    minimum_score: float = FACE_CONFIDENCE_THRESHOLD,
    stride: int = 120,
) -> list[dict[str, Any]]:
    """Find and assess every distinct face-bearing photo region on one page."""

    candidates: list[dict[str, Any]] = []
    if prefer_full_image:
        face = _best_face(app, image)
        if (
            face is not None
            and float(face.det_score) >= minimum_score
            and _face_area_fraction(face, image) >= 0.04
            and 0.4 < image.shape[1] / max(image.shape[0], 1) < 1.4
        ):
            face_box = tuple(int(value) for value in np.asarray(face.bbox, dtype=int))
            candidates.append({
                "crop": image,
                "face_score": float(face.det_score),
                "eye_pair": bool(getattr(face, "eye_pair", False)),
                "bbox_px": (0, 0, image.shape[1], image.shape[0]),
                "face_bbox_px": face_box,
                "source_priority": 3,
            })

    for x, y, width, height in find_photo_candidates(image):
        if min(width, height) < minimum_crop_side:
            continue
        crop = image[y : y + height, x : x + width]
        face = _best_face(app, crop)
        if face is None or float(face.det_score) < minimum_score:
            continue
        local_box = np.asarray(face.bbox, dtype=int)
        candidates.append({
            "crop": crop,
            "face_score": float(face.det_score),
            "eye_pair": bool(getattr(face, "eye_pair", False)),
            "bbox_px": (x, y, x + width, y + height),
            "face_bbox_px": (
                x + int(local_box[0]),
                y + int(local_box[1]),
                x + int(local_box[2]),
                y + int(local_box[3]),
            ),
            "source_priority": 2,
        })

    candidates.extend(sliding_window_search_all(
        image,
        app,
        minimum_score=minimum_score,
        stride=stride,
    ))

    kept: list[dict[str, Any]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            int(item["source_priority"]),
            float(item["face_score"]),
            int(item["crop"].shape[0] * item["crop"].shape[1]),
        ),
        reverse=True,
    ):
        crop = candidate["crop"]
        if min(crop.shape[:2]) < minimum_crop_side:
            continue
        if (
            str(getattr(app, "backend_name", "")).lower() == "opencv"
            and not bool(candidate.get("eye_pair"))
            and float(candidate["face_score"]) < OPENCV_NO_EYE_PAIR_CONFIDENCE
        ):
            continue
        if any(_same_face(candidate["face_bbox_px"], item["face_bbox_px"]) for item in kept):
            continue
        kept.append(candidate)

    results = []
    for candidate in kept[:MAX_DETECTED_PHOTOS]:
        result = _result_for_crop(
            candidate["crop"],
            float(candidate["face_score"]),
            candidate["bbox_px"],
        )
        if (
            str(getattr(app, "backend_name", "")).lower() == "opencv"
            and float(result["near_white_fraction"]) > OPENCV_MAX_NEAR_WHITE_FRACTION
        ):
            continue
        if _texture_rejects_face(app, candidate["crop"]):
            continue
        results.append(result)
    return sorted(
        results,
        key=lambda result: (
            int(result["bbox_px"][1]) // 250,
            int(result["bbox_px"][0]),
            int(result["bbox_px"][1]),
        ),
    )


def extract_and_validate(
    image: np.ndarray,
    app: Any,
    prefer_full_image: bool = False,
    minimum_crop_side: int = 0,
) -> dict[str, Any]:
    """Find the strongest face-bearing photo region and assess crop quality."""

    image_blur, image_brightness, image_contrast = quality_check(image)

    if prefer_full_image:
        face = _best_face(app, image)
        if (
            face is not None
            and float(face.det_score) >= FACE_CONFIDENCE_THRESHOLD
            and _face_area_fraction(face, image) >= 0.04
            and 0.4 < image.shape[1] / max(image.shape[0], 1) < 1.4
            and not _texture_rejects_face(app, image)
        ):
            return _result_for_crop(
                image,
                float(face.det_score),
                (0, 0, image.shape[1], image.shape[0]),
            )

    matches: list[tuple[float, np.ndarray, tuple[int, int, int, int]]] = []
    for x, y, width, height in find_photo_candidates(image):
        if min(width, height) < minimum_crop_side:
            continue
        crop = image[y : y + height, x : x + width]
        face = _best_face(app, crop)
        if (
            face is not None
            and float(face.det_score) >= FACE_CONFIDENCE_THRESHOLD
            and not _texture_rejects_face(app, crop)
        ):
            matches.append((float(face.det_score), crop, (x, y, x + width, y + height)))
    if matches:
        score, crop, bbox = max(matches, key=lambda match: match[0])
        return _result_for_crop(crop, score, bbox)

    crop, score, bbox = sliding_window_search(image, app)
    if (
        crop is not None
        and bbox is not None
        and score >= FACE_CONFIDENCE_THRESHOLD
        and min(crop.shape[:2]) >= minimum_crop_side
        and not _texture_rejects_face(app, crop)
    ):
        return _result_for_crop(crop, score, bbox)

    return {
        "pass": False,
        "reason": "no_face_detected",
        "blur": image_blur,
        "brightness": image_brightness,
        "contrast": image_contrast,
        "face_score": 0.0,
        "crop": None,
        "bbox_px": None,
    }


def _save_crop(crop: np.ndarray, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not success:
        raise OSError("The extracted photo could not be encoded.")
    destination.write_bytes(encoded.tobytes())


def process_file(
    path: str | Path,
    *,
    save_path: str | Path | None = None,
    save_dir: str | Path | None = None,
    effort: str | None = None,
) -> dict[str, Any]:
    source = Path(path)
    app = get_face_app()
    backend = str(getattr(app, "backend_name", app.__class__.__name__)).lower()
    level = effort_settings(effort)
    effort_name = str(effort or PHOTO_DEFAULT_EFFORT).strip().lower()
    if effort_name not in PHOTO_EFFORT_LEVELS:
        effort_name = PHOTO_DEFAULT_EFFORT
    render_dpi = int(level["dpi"])
    stride = int(level["stride"])
    minimum_confidence = float(level["min_confidence"])
    if source.suffix.lower() == ".pdf":
        if backend == "opencv" or bool(level["always_render"]):
            # Haar detection needs a predictable scale. Raw embedded page
            # images vary wildly in resolution and previously let small stamp
            # marks outrank real portraits. Render each page at 300 DPI and
            # require a crop large enough to contain a reviewable ID photo.
            #
            # "High" effort renders for a different reason: it takes the
            # rendered page as the single source of truth rather than adding a
            # sweep on top of the embedded pass. Those two passes work in
            # different coordinate spaces — an embedded image is a crop of the
            # page, so its boxes cannot be compared with page boxes to
            # de-duplicate — and running both would report the same portrait
            # twice. The dense sweep finds everything the embedded pass would.
            sources = [(page, image, False) for page, image in pdf_to_images(source, render_dpi)]
            rendered_loaded = True
        else:
            embedded = extract_embedded_pdf_images(source)
            sources = [(page, image, True) for page, image in embedded]
            rendered_loaded = False
    else:
        image = cv2.imread(str(source))
        if image is None:
            raise ValueError(f"Cannot read image: {source}")
        sources = [(1, image, True)]
        rendered_loaded = True

    reference_image = sources[0][1] if sources else None
    detected_results: list[tuple[int, dict[str, Any]]] = []
    for page, image, embedded_image in sources:
        page_results = extract_all_and_validate(
            image,
            app,
            prefer_full_image=embedded_image,
            minimum_crop_side=(
                OPENCV_RENDERED_MIN_CROP_SIDE
                if backend == "opencv" and source.suffix.lower() == ".pdf"
                else 0
            ),
            minimum_score=(
                OPENCV_MULTI_PHOTO_CONFIDENCE
                if backend == "opencv" and source.suffix.lower() == ".pdf"
                else minimum_confidence
            ),
            stride=stride,
        )
        for result in page_results:
            result["source_size_px"] = [int(image.shape[1]), int(image.shape[0])]
        detected_results.extend((page, result) for result in page_results)

    # The embedded pass found nothing, so fall back to rendering every page and
    # sweeping it. At "high" the sweep already ran as the primary pass above;
    # at "low" it never runs, which is exactly what makes "low" cheap — and why
    # a "no photo" reading at low effort means "not looked for", not "absent".
    if (
        source.suffix.lower() == ".pdf"
        and backend != "opencv"
        and bool(level["render_fallback"])
        and not bool(level["always_render"])
        and not detected_results
    ):
        rendered_loaded = True
        for page, image in pdf_to_images(source, render_dpi):
            if reference_image is None:
                reference_image = image
            page_results = extract_all_and_validate(
                image, app, minimum_score=minimum_confidence, stride=stride,
            )
            for result in page_results:
                result["source_size_px"] = [int(image.shape[1]), int(image.shape[0])]
            detected_results.extend((page, result) for result in page_results)

    if not detected_results:
        if reference_image is None and rendered_loaded:
            raise ValueError("The document contains no readable pages.")
        if reference_image is None:
            # Low effort on a PDF that embeds no raster images: nothing was
            # ever decoded, so there is no image to measure quality against.
            # That is still a legitimate "no photo found" — reporting it beats
            # crashing the detector, and `search_effort` in the result tells
            # the check that the pages were never rendered.
            blur, brightness, contrast = 0.0, 0.0, 0.0
        else:
            blur, brightness, contrast = quality_check(reference_image)
        primary_page = 1
        primary_result = {
            "pass": False,
            "reason": "no_face_detected",
            "blur": blur,
            "brightness": brightness,
            "contrast": contrast,
            "face_score": 0.0,
            "crop": None,
            "bbox_px": None,
        }
    else:
        detected_results = detected_results[:MAX_DETECTED_PHOTOS]
        primary_page, primary_result = detected_results[0]

    artifacts: list[str] = []
    photos: list[dict[str, Any]] = []
    for index, (page, result) in enumerate(detected_results, start=1):
        crop = result["crop"]
        artifact: str | None = None
        destination: Path | None = None
        if save_dir:
            filename = "detected_photo.jpg" if index == 1 else f"detected_photo_{index:03d}.jpg"
            destination = Path(save_dir) / filename
        elif save_path:
            requested = Path(save_path)
            destination = requested if index == 1 else requested.with_name(
                f"{requested.stem}_{index:03d}{requested.suffix or '.jpg'}"
            )
        if destination is not None:
            _save_crop(crop, destination)
            artifact = destination.name
            artifacts.append(artifact)
        photos.append({
            "artifact": artifact,
            "page": page,
            "passed": bool(result["pass"]),
            "reason": str(result["reason"]),
            "blur": round(float(result["blur"]), 2),
            "brightness": round(float(result["brightness"]), 2),
            "contrast": round(float(result["contrast"]), 2),
            "near_white_fraction": round(float(result.get("near_white_fraction", 0.0)), 4),
            "smooth_block_fraction": round(float(result.get("smooth_block_fraction", 0.0)), 4),
            "face_confidence": round(float(result["face_score"]), 4),
            "bbox_px": result["bbox_px"],
            "source_size_px": result.get("source_size_px"),
        })

    photo_found = bool(photos)
    passed = photo_found and all(photo["passed"] for photo in photos)
    failed_photos = [photo for photo in photos if not photo["passed"]]
    reason = str(failed_photos[0]["reason"]) if failed_photos else (
        "ok" if photo_found else "no_face_detected"
    )
    finding_messages = {
        "no_face_detected": "No passport-style face photo was detected.",
        "image_too_blurry": "The detected photo is too blurry for reliable visual review.",
        "bad_lighting": "The detected photo has unsuitable lighting.",
    }
    findings = [
        {
            "kind": str(photo["reason"]),
            "message": finding_messages.get(
                str(photo["reason"]),
                "The detected photo needs review.",
            ),
            "page": photo["page"],
            "artifact": photo["artifact"],
        }
        for photo in failed_photos
    ]
    if not photo_found:
        findings = [{
            "kind": "no_face_detected",
            "message": finding_messages["no_face_detected"],
        }]
    return {
        "tool": "document-photo-extractor",
        "version": "1.2.0",
        "status": "completed",
        "passed": passed,
        "risk": "low" if passed else "medium",
        "verdict": "photo_detected" if passed else (
            "photo_quality_review" if photo_found else "no_photo_detected"
        ),
        "photo_found": photo_found,
        "photo_count": len(photos),
        "photos": photos,
        "reason": reason,
        "page": primary_page,
        "blur": round(float(primary_result["blur"]), 2),
        "brightness": round(float(primary_result["brightness"]), 2),
        "contrast": round(float(primary_result["contrast"]), 2),
        "face_confidence": round(float(primary_result["face_score"]), 4),
        "detection_backend": backend,
        # What "no photo found" is worth depends on how hard this run looked,
        # so the effort travels with the result rather than being inferred.
        "search_effort": effort_name,
        "page_rendered": rendered_loaded,
        "render_dpi": render_dpi if source.suffix.lower() == ".pdf" else None,
        "bbox_px": primary_result["bbox_px"],
        "findings": findings,
        "artifacts": artifacts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract passport-style photos from a document")
    parser.add_argument("path", help="PDF or image document")
    parser.add_argument("--output", help="Write the JSON result to this path")
    parser.add_argument("--save", help="Save the first photo here and number any additional photos")
    parser.add_argument("--save-dir", help="Save all detected photos in this artifact directory")
    parser.add_argument(
        "--effort",
        choices=sorted(PHOTO_EFFORT_LEVELS),
        default=PHOTO_DEFAULT_EFFORT,
        help=(
            "How hard to look: low = embedded images only (never renders); "
            "medium = render only if the embedded pass finds nothing; "
            "high = always render every page at higher DPI and sweep densely"
        ),
    )
    args = parser.parse_args()

    source = Path(args.path)
    if not source.is_file() or source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        payload = {
            "status": "error",
            "detail": f"Unsupported or missing document: {source}",
            "artifacts": [],
        }
        exit_code = 2
    else:
        try:
            payload = process_file(
                source, save_path=args.save, save_dir=args.save_dir, effort=args.effort,
            )
            exit_code = 0
        except Exception as exc:  # noqa: BLE001 - the CLI must always return a report
            payload = {"status": "error", "detail": str(exc), "artifacts": []}
            exit_code = 2

    serialized = json.dumps(payload, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
