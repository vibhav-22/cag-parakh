from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from backend.models import (
    AnalyzerOutcome,
    AnalyzerRunState,
    AnalyzerRunStatus,
    CheckStatus,
    JobState,
    RiskLevel,
    normalize_analyzer_result,
)


class NormalizeAnalyzerResultTests(unittest.TestCase):
    def test_normalizes_whitener_report_and_regions(self) -> None:
        result = normalize_analyzer_result(
            "tamper_scan",
            {
                "status": "completed",
                "passed": False,
                "risk": "high",
                "verdict": "likely_whitener",
                "document_probability": 0.72,
                "suspicious_regions_count": 1,
                "regions": [{
                    "page": 2,
                    "kind": "whitener_patch",
                    "label": "Possible whitener patch",
                    "reason": "Local correction-fluid confidence 72%.",
                    "severity": "high",
                    "bbox_normalized": {"x0": 0.1, "y0": 0.2, "x1": 0.3, "y1": 0.4},
                }],
                "artifacts": ["abc/page_002_annotated.png"],
            },
            exit_code=0,
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.REVIEW)
        self.assertEqual(result.risk, RiskLevel.HIGH)
        self.assertEqual(result.summary, "Likely whitener detected · 72% probability")
        self.assertEqual(result.findings_count, 1)
        self.assertEqual(result.regions[0].kind, "whitener_patch")
        self.assertEqual(result.artifacts, ["abc/page_002_annotated.png"])

    def test_metadata_reports_provenance_without_grading_it(self) -> None:
        result = normalize_analyzer_result(
            "metadata",
            {
                "status": "failed",
                "passed": False,
                "risk": "high",
                "suspicious_regions_count": 2,
                "issues": [{"signal": "editor_producer"}, {"signal": "incremental_update"}],
                "document_metadata": {"producer": "Adobe Photoshop 25.0", "created_at": "2024-01-31 12:00"},
                "generation": {
                    "kind": "image_editor",
                    "producer": "Adobe Photoshop 25.0",
                    "resaved_after_creation": True,
                    "pages": 1,
                },
            },
            exit_code=0,
        )

        # Metadata answers "how was this made". A tick or a cross would force the
        # reviewer to decode a symbol instead of reading the answer.
        self.assertEqual(result.outcome, AnalyzerOutcome.INFO)
        self.assertEqual(result.check.status, CheckStatus.INFO)
        self.assertEqual(result.summary, "Made with an image editor · Adobe Photoshop 25.0")
        self.assertIn("Adobe Photoshop 25.0", result.check.reason)
        self.assertIn("re-saved after it was first written", result.check.reason)
        self.assertIn(
            {"label": "Producer", "value": "Adobe Photoshop 25.0"},
            [fact.model_dump() for fact in result.check.facts],
        )
        self.assertEqual(result.risk, RiskLevel.HIGH)
        self.assertEqual(result.findings_count, 2)
        self.assertEqual(result.exit_code, 0)

    def test_font_analysis_fails_with_no_fonts_and_lists_the_ones_it_finds(self) -> None:
        empty = normalize_analyzer_result(
            "font_analysis",
            {"typeface_count": 0, "font_objects_count": 0, "typefaces": [], "unique_fonts": []},
        )
        self.assertEqual(empty.outcome, AnalyzerOutcome.REVIEW)
        self.assertEqual(empty.check.status, CheckStatus.FAIL)
        self.assertEqual(empty.summary, "No embedded fonts")
        self.assertIn("No fonts are embedded", empty.check.reason)

        found = normalize_analyzer_result(
            "font_analysis",
            {
                "typeface_count": 2,
                "font_objects_count": 3,
                "typefaces": [
                    {"typeface": "Times New Roman", "pages_used": [1]},
                    {"typeface": "Arial", "pages_used": [1, 2]},
                ],
            },
        )
        self.assertEqual(found.outcome, AnalyzerOutcome.CLEAR)
        self.assertEqual(found.check.status, CheckStatus.PASS)
        self.assertEqual(found.summary, "2 embedded fonts: Arial, Times New Roman")
        self.assertIn("Arial, Times New Roman", found.check.reason)
        self.assertIn(
            {"label": "Font names", "value": "Arial, Times New Roman"},
            [fact.model_dump() for fact in found.check.facts],
        )

    def test_font_reference_without_embedded_program_does_not_pass(self) -> None:
        result = normalize_analyzer_result(
            "font_analysis",
            {
                "font_objects_count": 1,
                "typeface_count": 1,
                "unique_fonts": [{
                    "xref": 0,
                    "font_name": "Times-Roman",
                    "typeface": "Times-Roman",
                    "extension": "n/a",
                    "pages_used": [1],
                }],
                "typefaces": [{"typeface": "Times-Roman", "pages_used": [1]}],
                "font_usage_records": [{"page": 1, "text_sample": "Roll No."}],
            },
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.REVIEW)
        self.assertEqual(result.check.status, CheckStatus.FAIL)
        self.assertEqual(result.summary, "No embedded fonts · references Times-Roman")
        self.assertIn("text/OCR layer", result.check.reason)
        self.assertIn(
            {"label": "Referenced fonts", "value": "Times-Roman"},
            [fact.model_dump() for fact in result.check.facts],
        )

    def test_same_phone_says_why_it_could_not_run_on_one_page(self) -> None:
        result = normalize_analyzer_result(
            "same_phone",
            {
                "summary": {
                    "overall_verdict": "insufficient_pages",
                    "reasons": ["Only one page was analyzed; same-phone comparison needs at least two pages."],
                },
                "pages": [{"page": 1}],
            },
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.INCONCLUSIVE)
        self.assertEqual(result.check.status, CheckStatus.INCONCLUSIVE)
        self.assertIn("Only one page was analysed", result.check.reason)
        self.assertIn("nothing to compare", result.check.reason)

    def test_moire_says_why_the_image_could_not_be_analysed(self) -> None:
        result = normalize_analyzer_result(
            "moire",
            [{
                "file_verdict": "INCONCLUSIVE",
                "images": [{"verdict": "INCONCLUSIVE", "reason": "image too small"}],
            }],
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.INCONCLUSIVE)
        self.assertIn("image too small", result.check.reason)

    def test_whitener_indeterminate_pages_are_inconclusive_not_a_pass(self) -> None:
        result = normalize_analyzer_result(
            "tamper_scan",
            {
                "status": "inconclusive",
                "verdict": "insufficient_signal",
                "review_threshold": 0.35,
                "document_probability": 0.0,
                "pages": [{"page": 1, "indeterminate": True}],
                "indeterminate_pages": 1,
            },
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.INCONCLUSIVE)
        self.assertIn("indeterminate", result.check.reason)

    def test_unwraps_single_document_batch_result(self) -> None:
        result = normalize_analyzer_result(
            "moire",
            [{"file_verdict": "RECAPTURE", "images": []}],
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.REVIEW)
        self.assertEqual(result.summary, "Recapture")
        self.assertEqual(result.raw["file_verdict"], "RECAPTURE")

    def test_missing_qr_fails_the_qr_check(self) -> None:
        result = normalize_analyzer_result(
            "qr_presence",
            {"qr_found": False, "qr_count": 0, "hits": []},
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.REVIEW)
        self.assertEqual(result.check.status, CheckStatus.FAIL)
        self.assertEqual(result.summary, "No QR code found")
        self.assertIn("No QR code was decoded", result.check.reason)

    def test_invalid_payload_becomes_error_result(self) -> None:
        result = normalize_analyzer_result("metadata", ["unexpected"])

        self.assertEqual(result.outcome, AnalyzerOutcome.ERROR)
        self.assertIn("unsupported result shape", result.summary)

    def test_normalizes_readable_report_as_clear(self) -> None:
        result = normalize_analyzer_result(
            "readability",
            {
                "verdict": "readable",
                "note": "READABLE ✅ (image-based PDF, OCR required to extract text)",
                "passed": True,
                "risk": "low",
                "score": 72,
                "tests_passed": 8,
                "tests_total": 11,
                "tests": [
                    {"name": "File Exists", "passed": True, "value": "True", "threshold": "True", "detail": ""},
                ],
            },
            exit_code=0,
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.CLEAR)
        self.assertEqual(result.risk, RiskLevel.LOW)
        self.assertEqual(result.summary, "Readable · 72/100 (8/11 tests passed)")

    def test_normalizes_poor_readability_as_review(self) -> None:
        result = normalize_analyzer_result(
            "readability",
            {
                "verdict": "poor_readability",
                "note": "POOR ⚠️  (needs OCR / manual review)",
                "passed": False,
                "risk": "medium",
                "score": 45,
                "tests_passed": 5,
                "tests_total": 11,
                "tests": [],
            },
            exit_code=1,
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.REVIEW)
        self.assertEqual(result.risk, RiskLevel.MEDIUM)
        self.assertEqual(result.summary, "Poor readability · 45/100 (5/11 tests passed)")

    def test_normalizes_qr_polygon_to_page_region(self) -> None:
        result = normalize_analyzer_result(
            "qr_presence",
            {
                "qr_found": True,
                "qr_count": 1,
                "hits": [{
                    "page": 1,
                    "dpi": 72,
                    "variant": "rot0-raw",
                    "points": [[10, 20], [30, 20], [30, 50], [10, 50]],
                }],
            },
            page_sizes={1: (100, 200)},
        )

        self.assertEqual(len(result.regions), 1)
        self.assertEqual(result.regions[0].kind, "qr_code")
        self.assertAlmostEqual(result.regions[0].x, 0.05)
        self.assertAlmostEqual(result.regions[0].y, 0.075)

    def test_normalizes_scanner_noise_region(self) -> None:
        result = normalize_analyzer_result(
            "scanner_noise",
            {
                "summary": {"overall_risk": "high"},
                "suspicious_regions": [{
                    "page": 2,
                    "severity": "high",
                    "reason": "Noise profile differs.",
                    "bbox_normalized": {"x0": 0.1, "y0": 0.2, "x1": 0.4, "y1": 0.5},
                }],
            },
        )

        self.assertEqual(len(result.regions), 1)
        self.assertEqual(result.regions[0].page, 2)
        self.assertAlmostEqual(result.regions[0].width, 0.3)
        self.assertEqual(result.regions[0].severity, RiskLevel.HIGH)

    def test_a_found_photo_passes_even_when_its_quality_needs_a_look(self) -> None:
        result = normalize_analyzer_result(
            "photo_detection",
            {
                "status": "completed",
                "passed": False,
                "risk": "medium",
                "verdict": "photo_quality_review",
                "photo_found": True,
                "photo_count": 3,
                "reason": "image_too_blurry",
                "findings": [{"kind": "image_too_blurry"}],
                "artifacts": [
                    "detected_photo.jpg",
                    "detected_photo_002.jpg",
                    "detected_photo_003.jpg",
                ],
            },
        )

        # The check asks one question — is there a photo — so a blurry photo is
        # still a photo. The quality finding rides along in the reason.
        self.assertEqual(result.outcome, AnalyzerOutcome.CLEAR)
        self.assertEqual(result.check.status, CheckStatus.PASS)
        self.assertIn("3 document photos found", result.check.reason)
        self.assertIn("image too blurry", result.check.reason)
        self.assertEqual(result.summary, "3 photos detected - quality needs review")
        self.assertEqual(result.findings_count, 1)
        self.assertEqual(result.artifacts, [
            "detected_photo.jpg",
            "detected_photo_002.jpg",
            "detected_photo_003.jpg",
        ])

    def test_a_missing_photo_fails_the_photo_check(self) -> None:
        result = normalize_analyzer_result(
            "photo_detection",
            {
                "status": "completed",
                "passed": False,
                "verdict": "no_photo_detected",
                "photo_found": False,
                "photo_count": 0,
            },
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.REVIEW)
        self.assertEqual(result.check.status, CheckStatus.FAIL)
        self.assertIn("No document photo was found", result.check.reason)

    def test_normalizes_document_photo_bbox_to_page_region(self) -> None:
        result = normalize_analyzer_result(
            "photo_detection",
            {
                "status": "completed",
                "photo_found": True,
                "photo_count": 1,
                "photos": [{
                    "page": 1,
                    "passed": True,
                    "face_confidence": 0.88,
                    "bbox_px": [800, 100, 950, 300],
                    "source_size_px": [1000, 2000],
                }],
            },
            page_sizes={1: (500, 1000)},
        )

        self.assertEqual(len(result.regions), 1)
        region = result.regions[0]
        self.assertEqual(region.kind, "document_photo")
        self.assertEqual(region.label, "Document photo 1")
        self.assertAlmostEqual(region.x, 0.8)
        self.assertAlmostEqual(region.y, 0.05)
        self.assertAlmostEqual(region.width, 0.15)
        self.assertAlmostEqual(region.height, 0.1)
        self.assertIn("88% face confidence", region.message)

    def test_normalizes_signature_presence_and_region(self) -> None:
        result = normalize_analyzer_result(
            "signature",
            {
                "status": "completed",
                "passed": True,
                "risk": "low",
                "verdict": "signature_present",
                "present": True,
                "count": 2,
                "score": 0.91,
                "regions": [{
                    "page": 1,
                    "kind": "signature",
                    "label": "Signature",
                    "reason": "Handwritten signature detected (91% confidence).",
                    "severity": "low",
                    "bbox_normalized": {"x0": 0.5, "y0": 0.8, "x1": 0.7, "y1": 0.9},
                }],
            },
            exit_code=0,
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.CLEAR)
        self.assertEqual(result.summary, "2 signature(s) detected")
        self.assertEqual(len(result.regions), 1)
        self.assertEqual(result.regions[0].kind, "signature")
        self.assertAlmostEqual(result.regions[0].width, 0.2)
        self.assertEqual(result.regions[0].severity, RiskLevel.LOW)

    def test_normalizes_signature_shortfall_for_review(self) -> None:
        result = normalize_analyzer_result(
            "signature",
            {
                "status": "completed",
                "passed": False,
                "risk": "medium",
                "verdict": "signatures_below_minimum",
                "present": False,
                "count": 0,
                "expected_min_signatures": 1,
                "regions": [],
            },
            exit_code=0,
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.REVIEW)
        self.assertEqual(result.summary, "Expected at least 1 signature(s); found 0")

class AnalyzerRunStateTests(unittest.TestCase):
    def test_completed_run_requires_a_result(self) -> None:
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValidationError):
            AnalyzerRunState(
                analyzer_id="metadata",
                status=AnalyzerRunStatus.COMPLETED,
                queued_at=now,
                started_at=now,
                completed_at=now,
            )

    def test_job_state_parses_queued_run_map(self) -> None:
        now = datetime.now(timezone.utc)
        job = JobState(
            id="job-1",
            filename="document.pdf",
            status="queued",
            created_at=now,
            analyzers=["metadata"],
            analyzer_runs={
                "metadata": AnalyzerRunState(analyzer_id="metadata", queued_at=now),
            },
            results={},
        )

        self.assertEqual(job.analyzer_runs["metadata"].status, AnalyzerRunStatus.QUEUED)

    def test_rejects_out_of_order_timestamps(self) -> None:
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValidationError):
            AnalyzerRunState(
                analyzer_id="metadata",
                status=AnalyzerRunStatus.RUNNING,
                queued_at=now,
                started_at=now - timedelta(seconds=1),
            )


if __name__ == "__main__":
    unittest.main()
