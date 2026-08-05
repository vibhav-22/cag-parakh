from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

from backend import photo_identity


class _FakeFace:
    def __init__(self, embedding: list[float], det_score: float = 0.9) -> None:
        self.embedding = np.asarray(embedding, dtype="float32")
        self.det_score = det_score


class _FakeInsightFaceApp:
    """Returns a fixed embedding per crop path, keyed by the file's stem."""

    backend_name = "insightface"

    def __init__(self, embeddings_by_stem: dict[str, list[float]]) -> None:
        self._embeddings_by_stem = embeddings_by_stem
        self._next_stem: str | None = None

    def get(self, crop) -> list[_FakeFace]:  # noqa: ANN001 - matches InsightFace's app.get(image)
        embedding = self._embeddings_by_stem.get(self._next_stem)
        return [_FakeFace(embedding)] if embedding else []


def _job(job_id: str, filename: str, artifacts: list[str]) -> dict:
    return {
        "id": job_id,
        "filename": filename,
        "results": {
            "photo_detection": {
                "raw": {
                    "photos": [{"artifact": name} for name in artifacts],
                },
            },
        },
    }


class BatchPhotoDuplicatesTests(unittest.TestCase):
    def test_a_multi_face_crop_uses_the_highest_confidence_face(self) -> None:
        """These forms carry a joint applicant+girl photo, so a crop can hold
        two faces. The strongest detection must win regardless of order."""

        class _MultiFaceApp:
            backend_name = "insightface"

            def get(self, crop):  # noqa: ANN001, ANN202
                return [
                    _FakeFace([0.0, 1.0, 0.0], det_score=0.40),
                    _FakeFace([1.0, 0.0, 0.0], det_score=0.95),
                ]

        embedding = photo_identity._get_embedding(np.zeros((4, 4, 3), "uint8"), _MultiFaceApp())

        np.testing.assert_allclose(embedding, [1.0, 0.0, 0.0], atol=1e-6)

    def test_returns_empty_without_insightface(self) -> None:
        with patch.object(photo_identity.extract_photo, "get_face_app", return_value=object()):
            result = photo_identity.batch_photo_duplicates(
                [_job("job-1", "a.pdf", ["detected_photo.jpg"])], Path("."),
            )
        self.assertEqual(result, {})

    def test_flags_a_later_document_that_shares_a_face_with_an_earlier_one(self) -> None:
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            for job_id in ("job-1", "job-2", "job-3"):
                report_dir = data_dir / f"{job_id}-photo_detection-report"
                report_dir.mkdir()
                (report_dir / "detected_photo.jpg").write_bytes(
                    np.zeros((4, 4, 3), dtype="uint8").tobytes()
                )

            jobs = [
                _job("job-1", "first.pdf", ["detected_photo.jpg"]),
                _job("job-2", "second.pdf", ["detected_photo.jpg"]),
                _job("job-3", "unrelated.pdf", ["detected_photo.jpg"]),
            ]

            app = _FakeInsightFaceApp({
                "job-1": [1.0, 0.0, 0.0],
                "job-2": [0.99, 0.01, 0.0],  # same identity as job-1, near-parallel vector
                "job-3": [0.0, 1.0, 0.0],  # orthogonal: a different person
            })

            def fake_imread(path: str):
                # The crop content doesn't matter; the fake app keys off which
                # job's report_dir the path was read from.
                app._next_stem = "job-1" if "job-1-" in path else (
                    "job-2" if "job-2-" in path else "job-3"
                )
                return np.zeros((4, 4, 3), dtype="uint8")

            with patch.object(photo_identity.extract_photo, "get_face_app", return_value=app), \
                 patch.object(photo_identity.cv2, "imread", side_effect=fake_imread):
                result = photo_identity.batch_photo_duplicates(jobs, data_dir)

        self.assertEqual(result["job-1"]["status"], "first")  # nothing earlier to match against
        self.assertEqual(result["job-2"]["status"], "duplicate")
        self.assertEqual(result["job-2"]["duplicate_of_filename"], "first.pdf")
        self.assertGreater(result["job-2"]["similarity"], photo_identity.SIMILARITY_THRESHOLD)
        # A non-match still reports how close it got, and to which document —
        # here job-3 is orthogonal to both, so the nearest is whichever scored
        # marginally higher, not necessarily the first document in the batch.
        self.assertEqual(result["job-3"]["status"], "no_match")
        self.assertIn(result["job-3"]["duplicate_of_filename"], {"first.pdf", "second.pdf"})
        self.assertLess(result["job-3"]["similarity"], photo_identity.SIMILARITY_THRESHOLD)

    def test_photos_that_hold_no_face_are_reported_not_dropped(self) -> None:
        """A crop the model will not embed used to vanish from the result, which
        rendered identically to "this document has no photo". It has to be
        counted, because it is usually a photo_detection false positive."""

        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            for job_id in ("job-1", "job-2"):
                report_dir = data_dir / f"{job_id}-photo_detection-report"
                report_dir.mkdir()
                for name in ("detected_photo.jpg", "detected_photo_002.jpg"):
                    (report_dir / name).write_bytes(b"")

            jobs = [
                # job-1's second crop holds no face; job-2 has none at all.
                _job("job-1", "half.pdf", ["detected_photo.jpg", "detected_photo_002.jpg"]),
                _job("job-2", "faceless.pdf", ["detected_photo.jpg", "detected_photo_002.jpg"]),
            ]

            class _App:
                backend_name = "insightface"

                def __init__(self) -> None:
                    self.embed_next = False

                def get(self, crop):  # noqa: ANN001, ANN202
                    return [_FakeFace([1.0, 0.0, 0.0])] if self.embed_next else []

            app = _App()

            def fake_imread(path: str):
                app.embed_next = "job-1-" in path and "_002" not in path
                return np.zeros((4, 4, 3), dtype="uint8")

            with patch.object(photo_identity.extract_photo, "get_face_app", return_value=app), \
                 patch.object(photo_identity.cv2, "imread", side_effect=fake_imread):
                result = photo_identity.batch_photo_duplicates(jobs, data_dir)

        self.assertEqual(result["job-1"]["status"], "first")
        self.assertEqual(result["job-1"]["photos_detected"], 2)
        self.assertEqual(result["job-1"]["photos_compared"], 1)
        # Present, but nothing in it could be matched.
        self.assertEqual(result["job-2"]["status"], "no_face")
        self.assertEqual(result["job-2"]["photos_detected"], 2)
        self.assertEqual(result["job-2"]["photos_compared"], 0)
        self.assertIsNone(result["job-2"]["similarity"])

    def test_the_detector_is_relaxed_for_the_identity_pass(self) -> None:
        """The crop is already known to be a face region, so InsightFace's
        scene-level 0.5 default drops real faces here. Measured on the corpus in
        backend/data it cost 5 of 40 crops."""

        class _DetModel:
            det_thresh = 0.5

        class _App:
            backend_name = "insightface"
            det_model = _DetModel()

            def get(self, crop):  # noqa: ANN001, ANN202
                return []

        app = _App()
        with patch.object(photo_identity.extract_photo, "get_face_app", return_value=app):
            photo_identity.batch_photo_duplicates([], Path("."))

        self.assertEqual(app.det_model.det_thresh, photo_identity.DETECTION_THRESHOLD)
        self.assertLess(photo_identity.DETECTION_THRESHOLD, 0.5)


if __name__ == "__main__":
    unittest.main()
