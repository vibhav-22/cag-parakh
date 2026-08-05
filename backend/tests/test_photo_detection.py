from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import ANY, patch

import cv2
import numpy as np

from tools.photo_analysis import extract_photo


def _portrait_like_crop(side: int = 220) -> np.ndarray:
    """A crop shaped like a photograph: smooth areas plus a few sharp edges.

    This stands in for an ID portrait, so it has to be photographic in the way
    the detector actually measures. An alternating one-pixel checkerboard,
    which this fixture used to be, has no smooth area anywhere - that is the
    signature of a QR code or a halftone block, and the OpenCV fallback now
    rejects exactly that shape of texture.
    """

    rows = np.indices((side, side))[0]
    backdrop = (200 - rows * 0.18).astype(np.uint8)
    crop = np.repeat(backdrop[:, :, None], 3, axis=2).copy()
    centre = side // 2
    cv2.ellipse(crop, (centre, 120), (62, 82), 0, 0, 360, (150, 150, 150), -1)
    cv2.ellipse(crop, (centre, 70), (48, 40), 0, 0, 360, (90, 90, 90), -1)
    cv2.circle(crop, (centre - 22, 115), 9, (40, 40, 40), -1)
    cv2.circle(crop, (centre + 22, 115), 9, (40, 40, 40), -1)
    cv2.line(crop, (centre - 18, 165), (centre + 18, 165), (60, 60, 60), 4)
    cv2.line(crop, (centre, 125), (centre, 148), (105, 105, 105), 3)
    return crop


class _FaceApp:
    backend_name = "opencv"

    def get(self, _image: np.ndarray) -> list[extract_photo.DetectedFace]:
        return [
            extract_photo.DetectedFace(
                bbox=np.asarray([20, 20, 100, 100], dtype=float),
                det_score=0.85,
                eye_pair=True,
            )
        ]


class _Cascade:
    def detectMultiScale3(self, *_args: object, **_kwargs: object) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return (
            np.asarray([[10, 10, 40, 40]], dtype=np.int32),
            np.asarray([0], dtype=np.int32),
            np.asarray([3.0], dtype=float),
        )


class _MarkerFaceApp:
    backend_name = "opencv"

    def get(self, image: np.ndarray) -> list[extract_photo.DetectedFace]:
        ys, xs = np.where(image[:, :, 0] > 0)
        if len(xs) == 0:
            return []
        left, right = int(xs.min()), int(xs.max()) + 1
        top, bottom = int(ys.min()), int(ys.max()) + 1
        if right - left < 70 or bottom - top < 70:
            return []
        return [
            extract_photo.DetectedFace(
                bbox=np.asarray([left, top, right, bottom], dtype=float),
                det_score=0.9,
            )
        ]


class _NoEyeFaceApp(_FaceApp):
    def __init__(self, score: float) -> None:
        self.score = score

    def get(self, _image: np.ndarray) -> list[extract_photo.DetectedFace]:
        return [
            extract_photo.DetectedFace(
                bbox=np.asarray([20, 20, 100, 100], dtype=float),
                det_score=self.score,
                eye_pair=False,
            )
        ]


class PhotoDetectionFallbackTests(unittest.TestCase):
    def test_opencv_keeps_small_scan_faces_when_eye_detail_is_lost(self) -> None:
        app = extract_photo.OpenCvFaceApp.__new__(extract_photo.OpenCvFaceApp)
        app.cascade = _Cascade()
        app._has_eye_pair = lambda _gray, _box: False

        faces = app.get(np.zeros((100, 100, 3), dtype=np.uint8))

        self.assertEqual(len(faces), 1)
        self.assertFalse(faces[0].eye_pair)
        self.assertGreaterEqual(faces[0].det_score, extract_photo.FACE_CONFIDENCE_THRESHOLD)

    def test_rendered_page_rejects_tiny_stamp_or_text_face_candidates(self) -> None:
        page = np.full((800, 600, 3), 240, dtype=np.uint8)
        small_crop = page[10:130, 10:130]
        with (
            patch.object(extract_photo, "find_photo_candidates", return_value=[(10, 10, 120, 120)]),
            patch.object(
                extract_photo,
                "sliding_window_search",
                return_value=(small_crop, 0.9, (10, 10, 130, 130)),
            ),
        ):
            result = extract_photo.extract_and_validate(
                page,
                _FaceApp(),
                minimum_crop_side=extract_photo.OPENCV_RENDERED_MIN_CROP_SIDE,
            )

        self.assertEqual(result["reason"], "no_face_detected")
        self.assertIsNone(result["crop"])

    def test_rendered_page_accepts_a_reviewable_photo_crop(self) -> None:
        page = np.full((800, 600, 3), 240, dtype=np.uint8)
        page[20:240, 20:240] = _portrait_like_crop()
        with patch.object(
            extract_photo,
            "find_photo_candidates",
            return_value=[(20, 20, 220, 220)],
        ):
            result = extract_photo.extract_and_validate(
                page,
                _FaceApp(),
                minimum_crop_side=extract_photo.OPENCV_RENDERED_MIN_CROP_SIDE,
            )

        self.assertTrue(result["pass"])
        self.assertEqual(result["bbox_px"], (20, 20, 240, 240))
        self.assertEqual(result["crop"].shape[:2], (220, 220))

    def test_multi_photo_scan_requires_stronger_confidence_without_an_eye_pair(self) -> None:
        page = np.full((800, 600, 3), 180, dtype=np.uint8)
        page[20:240, 20:240] = _portrait_like_crop()
        with (
            patch.object(
                extract_photo,
                "find_photo_candidates",
                return_value=[(20, 20, 220, 220)],
            ),
            patch.object(extract_photo, "sliding_window_search_all", return_value=[]),
        ):
            weak_results = extract_photo.extract_all_and_validate(
                page,
                _NoEyeFaceApp(0.85),
                minimum_crop_side=extract_photo.OPENCV_RENDERED_MIN_CROP_SIDE,
                minimum_score=extract_photo.OPENCV_MULTI_PHOTO_CONFIDENCE,
            )
            strong_results = extract_photo.extract_all_and_validate(
                page,
                _NoEyeFaceApp(0.94),
                minimum_crop_side=extract_photo.OPENCV_RENDERED_MIN_CROP_SIDE,
                minimum_score=extract_photo.OPENCV_MULTI_PHOTO_CONFIDENCE,
            )

        self.assertEqual(weak_results, [])
        self.assertEqual(len(strong_results), 1)

    def test_machine_readable_block_is_not_reported_as_a_photo(self) -> None:
        """A QR code must not count as a document photo.

        The Haar cascade reported the QR block on a UP Board marksheet as a
        face at 87.5% confidence, so the document was recorded as carrying two
        photos when it carried one. Face detection alone cannot tell these
        apart; the absence of any smooth area can.
        """

        page = np.full((800, 600, 3), 250, dtype=np.uint8)
        modules = np.random.default_rng(0).integers(0, 2, size=(22, 22), dtype=np.uint8)
        block = np.kron(modules * 255, np.ones((10, 10), dtype=np.uint8))
        page[20:240, 20:240] = np.repeat(block[:, :, None], 3, axis=2)
        with patch.object(
            extract_photo,
            "find_photo_candidates",
            return_value=[(20, 20, 220, 220)],
        ):
            result = extract_photo.extract_and_validate(
                page,
                _FaceApp(),
                minimum_crop_side=extract_photo.OPENCV_RENDERED_MIN_CROP_SIDE,
            )

        self.assertEqual(result["reason"], "no_face_detected")
        self.assertIsNone(result["crop"])

    def test_insightface_results_skip_the_texture_veto(self) -> None:
        """The veto is a patch for the weak fallback, not for the real model."""

        class _InsightFace(_FaceApp):
            backend_name = "insightface"

        modules = np.random.default_rng(0).integers(0, 2, size=(22, 22), dtype=np.uint8)
        block = np.kron(modules * 255, np.ones((10, 10), dtype=np.uint8))
        crop = np.repeat(block[:, :, None], 3, axis=2)

        self.assertTrue(extract_photo._texture_rejects_face(_FaceApp(), crop))
        self.assertFalse(extract_photo._texture_rejects_face(_InsightFace(), crop))

    def test_multi_photo_scan_rejects_mostly_blank_text_shapes(self) -> None:
        page = np.full((800, 600, 3), 250, dtype=np.uint8)
        page[50:250, 50:65] = 40
        page[230:245, 50:250] = 40
        with (
            patch.object(
                extract_photo,
                "find_photo_candidates",
                return_value=[(40, 40, 220, 220)],
            ),
            patch.object(extract_photo, "sliding_window_search_all", return_value=[]),
        ):
            results = extract_photo.extract_all_and_validate(
                page,
                _FaceApp(),
                minimum_crop_side=extract_photo.OPENCV_RENDERED_MIN_CROP_SIDE,
                minimum_score=extract_photo.OPENCV_MULTI_PHOTO_CONFIDENCE,
            )

        self.assertEqual(results, [])

    def test_sliding_scan_suppresses_duplicate_views_of_the_same_face(self) -> None:
        page = np.zeros((600, 600, 3), dtype=np.uint8)
        page[230:310, 230:310] = 255

        detections = extract_photo.sliding_window_search_all(
            page,
            _MarkerFaceApp(),
            minimum_score=0.8,
            tile=350,
            stride=120,
        )

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0]["face_bbox_px"], (230, 230, 310, 310))

    def test_face_deduplication_keeps_nearby_distinct_portraits(self) -> None:
        self.assertTrue(extract_photo._same_face((100, 100, 200, 200), (105, 105, 205, 205)))
        self.assertFalse(extract_photo._same_face((100, 100, 200, 200), (230, 100, 330, 200)))

    def test_opencv_pdf_path_uses_consistent_render_scale(self) -> None:
        page = np.full((800, 600, 3), 180, dtype=np.uint8)
        detected = {
            "pass": True,
            "reason": "ok",
            "blur": 100.0,
            "brightness": 120.0,
            "contrast": 40.0,
            "face_score": 0.8,
            "crop": page[20:240, 20:240],
            "bbox_px": (20, 20, 240, 240),
        }
        with (
            patch.object(extract_photo, "get_face_app", return_value=_FaceApp()),
            patch.object(extract_photo, "pdf_to_images", return_value=[(1, page)]),
            patch.object(extract_photo, "extract_embedded_pdf_images") as embedded,
            patch.object(extract_photo, "extract_all_and_validate", return_value=[detected]) as validate,
        ):
            result = extract_photo.process_file(Path("scan.pdf"))

        embedded.assert_not_called()
        validate.assert_called_once_with(
            page,
            ANY,
            prefer_full_image=False,
            minimum_crop_side=extract_photo.OPENCV_RENDERED_MIN_CROP_SIDE,
            minimum_score=extract_photo.OPENCV_MULTI_PHOTO_CONFIDENCE,
            stride=extract_photo.PHOTO_EFFORT_LEVELS["medium"]["stride"],
        )
        self.assertTrue(result["photo_found"])
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["photo_count"], 1)

    def test_low_effort_never_renders_a_page(self) -> None:
        """Low is only cheap because it skips rendering entirely. If the embedded
        pass finds nothing, it must still not fall back to a page render."""

        class _InsightApp:
            backend_name = "insightface"

            def get(self, _image):  # noqa: ANN001, ANN202
                return []

        with (
            patch.object(extract_photo, "get_face_app", return_value=_InsightApp()),
            patch.object(extract_photo, "extract_embedded_pdf_images", return_value=[]),
            patch.object(extract_photo, "pdf_to_images") as rendered,
        ):
            result = extract_photo.process_file(Path("scan.pdf"), effort="low")

        rendered.assert_not_called()
        # No embedded images and no render: still a reportable "no photo",
        # not a crashed detector.
        self.assertFalse(result["photo_found"])
        self.assertEqual(result["search_effort"], "low")
        self.assertFalse(result["page_rendered"])

    def test_medium_effort_renders_only_after_an_empty_embedded_pass(self) -> None:
        page = np.full((800, 600, 3), 180, dtype=np.uint8)

        class _InsightApp:
            backend_name = "insightface"

            def get(self, _image):  # noqa: ANN001, ANN202
                return []

        with (
            patch.object(extract_photo, "get_face_app", return_value=_InsightApp()),
            patch.object(extract_photo, "extract_embedded_pdf_images", return_value=[]),
            patch.object(extract_photo, "pdf_to_images", return_value=[(1, page)]) as rendered,
            patch.object(extract_photo, "extract_all_and_validate", return_value=[]),
        ):
            result = extract_photo.process_file(Path("scan.pdf"), effort="medium")

        rendered.assert_called_once()
        self.assertFalse(result["photo_found"])
        self.assertEqual(result["search_effort"], "medium")

    def test_high_effort_renders_even_when_the_embedded_pass_found_a_photo(self) -> None:
        """The gap this setting exists to close: a page whose embedded pass
        already found one portrait was never swept, so a second portrait beside
        it was never found. High must render regardless."""

        page = np.full((800, 600, 3), 180, dtype=np.uint8)
        embedded_image = np.full((300, 250, 3), 200, dtype=np.uint8)
        detected = {
            "pass": True, "reason": "ok", "blur": 100.0, "brightness": 120.0,
            "contrast": 40.0, "face_score": 0.8,
            "crop": page[20:240, 20:240], "bbox_px": (20, 20, 240, 240),
        }

        class _InsightApp:
            backend_name = "insightface"

            def get(self, _image):  # noqa: ANN001, ANN202
                return []

        with (
            patch.object(extract_photo, "get_face_app", return_value=_InsightApp()),
            patch.object(
                extract_photo, "extract_embedded_pdf_images", return_value=[(1, embedded_image)],
            ) as embedded,
            patch.object(extract_photo, "pdf_to_images", return_value=[(1, page)]) as rendered,
            patch.object(extract_photo, "extract_all_and_validate", return_value=[detected]),
        ):
            result = extract_photo.process_file(Path("scan.pdf"), effort="high")

        rendered.assert_called_once()
        # High takes the rendered page as the single source of truth, so the
        # embedded pass is skipped rather than added to — the two work in
        # different coordinate spaces and cannot be de-duplicated against
        # each other.
        embedded.assert_not_called()
        self.assertEqual(result["search_effort"], "high")
        self.assertEqual(result["render_dpi"], extract_photo.PHOTO_EFFORT_LEVELS["high"]["dpi"])
        self.assertTrue(result["page_rendered"])

    def test_an_unknown_effort_falls_back_to_the_default(self) -> None:
        self.assertEqual(
            extract_photo.effort_settings("nonsense"),
            extract_photo.PHOTO_EFFORT_LEVELS[extract_photo.PHOTO_DEFAULT_EFFORT],
        )

    def test_pdf_keeps_and_saves_every_distinct_photo(self) -> None:
        page_one = np.full((800, 600, 3), 180, dtype=np.uint8)
        page_two = np.full((800, 600, 3), 180, dtype=np.uint8)

        def detected(score: float, crop: np.ndarray, bbox: tuple[int, int, int, int]) -> dict[str, object]:
            return {
                "pass": True,
                "reason": "ok",
                "blur": 100.0,
                "brightness": 120.0,
                "contrast": 40.0,
                "face_score": score,
                "crop": crop,
                "bbox_px": bbox,
            }

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(extract_photo, "get_face_app", return_value=_FaceApp()),
                patch.object(
                    extract_photo,
                    "pdf_to_images",
                    return_value=[(1, page_one), (2, page_two)],
                ),
                patch.object(
                    extract_photo,
                    "extract_all_and_validate",
                    side_effect=[
                        [
                            detected(0.90, page_one[20:240, 20:240], (20, 20, 240, 240)),
                            detected(0.85, page_one[300:520, 20:240], (20, 300, 240, 520)),
                        ],
                        [detected(0.88, page_two[20:240, 20:240], (20, 20, 240, 240))],
                    ],
                ),
            ):
                result = extract_photo.process_file(Path("scan.pdf"), save_dir=directory)

        self.assertTrue(result["photo_found"])
        self.assertEqual(result["photo_count"], 3)
        self.assertEqual(result["page"], 1)
        self.assertEqual(
            result["artifacts"],
            ["detected_photo.jpg", "detected_photo_002.jpg", "detected_photo_003.jpg"],
        )
        self.assertEqual([photo["page"] for photo in result["photos"]], [1, 1, 2])


if __name__ == "__main__":
    unittest.main()
