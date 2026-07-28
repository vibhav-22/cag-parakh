from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from tools.signature_analysis.signature_locator import detectors


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "signature_analysis" / "signature_presence_check.py"

_spec = importlib.util.spec_from_file_location("signature_presence_check", TOOL)
assert _spec and _spec.loader
sig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sig)


class SignatureAdapterTests(unittest.TestCase):
    def test_build_service_report_normalizes_boxes_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            document_dir = artifact_root / "sample-1234"
            crops_dir = document_dir / "crops"
            crops_dir.mkdir(parents=True)
            annotated = document_dir / "annotated.pdf"
            crop = crops_dir / "page-001-signature-001.png"
            annotated.write_bytes(b"%PDF-1.7")
            crop.write_bytes(b"png")

            report = sig.build_service_report(
                {
                    "input_pdf": "sample.pdf",
                    "output_directory": str(document_dir),
                    "annotated_pdf": str(annotated),
                    "page_count": 1,
                    "threshold": 0.55,
                    "detections": [{
                        "page": 1,
                        "bbox": [120.0, 400.0, 300.0, 480.0],
                        "confidence": 0.86,
                        "method": "text_or_ocr_anchor+embedded_image",
                        "reasons": ["OCR anchor: signature"],
                        "crop": "crops/page-001-signature-001.png",
                    }],
                },
                [(600.0, 800.0)],
                artifact_root,
            )

        self.assertTrue(report["present"])
        self.assertEqual(report["count"], 1)
        self.assertEqual(report["score"], 0.86)
        self.assertEqual(
            report["regions"][0]["bbox_normalized"],
            {"x0": 0.2, "y0": 0.5, "x1": 0.5, "y1": 0.6},
        )
        self.assertEqual(
            report["artifacts"],
            [
                "sample-1234/annotated.pdf",
                "sample-1234/crops/page-001-signature-001.png",
            ],
        )

    def test_empty_result_is_clear_and_keeps_annotated_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact_root = Path(directory)
            document_dir = artifact_root / "unsigned-1234"
            document_dir.mkdir()
            annotated = document_dir / "annotated.pdf"
            annotated.write_bytes(b"%PDF-1.7")

            report = sig.build_service_report(
                {
                    "input_pdf": "unsigned.pdf",
                    "output_directory": str(document_dir),
                    "annotated_pdf": str(annotated),
                    "page_count": 1,
                    "threshold": 0.55,
                    "detections": [],
                },
                [(600.0, 800.0)],
                artifact_root,
            )

        self.assertFalse(report["present"])
        self.assertEqual(report["count"], 0)
        self.assertEqual(report["verdict"], "no_signature_detected")
        self.assertEqual(report["artifacts"], ["unsigned-1234/annotated.pdf"])

    def test_old_conf_flag_remains_a_threshold_alias(self) -> None:
        args = sig.build_parser().parse_args(["sample.pdf", "--conf", "0.72"])
        self.assertEqual(args.threshold, 0.72)

    def test_error_report_preserves_contract(self) -> None:
        report = sig._error(Path("missing.pdf"), "Input not found")
        self.assertEqual(report["status"], "error")
        self.assertIsNone(report["present"])
        self.assertEqual(report["regions"], [])
        self.assertEqual(report["artifacts"], [])


class InkEvidenceTests(unittest.TestCase):
    """Guards on what the ink detector treats as already-explained print."""

    def test_despeckle_drops_scan_grit_and_keeps_strokes(self) -> None:
        mask = np.zeros((40, 40), dtype=bool)
        mask[10:14, 5:30] = True          # a pen stroke
        mask[30, 5] = True                # isolated grit
        mask[33, 20] = True

        cleaned = detectors._despeckle(mask)

        self.assertTrue(cleaned[11, 10])
        self.assertFalse(cleaned[30, 5])
        self.assertFalse(cleaned[33, 20])

    def test_low_confidence_ocr_is_not_treated_as_printed_text(self) -> None:
        """OCR reads handwriting as letters, and that must not mask a signature.

        On the marksheet that prompted this, the signature came back as "U" at
        confidence 18. Counting that as recognized print made the detector
        erase the mark it was looking for before it ever measured it.
        """

        context = _StubContext(
            ocr_words=[
                {"text": "U", "confidence": 18.1, "bbox_px": (10, 10, 20, 20)},
                {"text": "PRAYAGRAJ", "confidence": 92.5, "bbox_px": (30, 30, 90, 44)},
            ]
        )

        boxes = detectors._confident_text_boxes(context)

        self.assertEqual(boxes, [(30.0, 30.0, 90.0, 44.0)])

    def test_upright_marks_score_as_plausible_signatures(self) -> None:
        """A tall stylised initial is a signature; a dense square block is not."""

        upright = Image.new("L", (40, 70), 255)
        ImageDraw.Draw(upright).line([(20, 5), (18, 60), (30, 45)], fill=0, width=3)
        upright_score, _ = detectors._signature_likelihood(upright.convert("RGB"))

        dense_square = Image.new("RGB", (60, 60), (255, 255, 255))
        pixels = np.asarray(dense_square).copy()
        pixels[::2, ::2] = 0
        dense_square = Image.fromarray(pixels)
        square_score, _ = detectors._signature_likelihood(dense_square)

        self.assertGreater(upright_score, square_score)


class _StubContext:
    """The two fields _confident_text_boxes reads, without opening a PDF."""

    def __init__(self, ocr_words: list[dict]) -> None:
        self.ocr_words = ocr_words
        self.plumber_page = self

    def extract_words(self) -> list[dict]:
        return []

    @staticmethod
    def pixels_to_points(bbox: tuple[float, ...]) -> tuple[float, ...]:
        return tuple(float(value) for value in bbox)


if __name__ == "__main__":
    unittest.main()
