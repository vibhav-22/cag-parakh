from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from backend.models import (
    AnalyzerOutcome,
    AnalyzerRunState,
    AnalyzerRunStatus,
    JobState,
    RiskLevel,
    normalize_analyzer_result,
)


class NormalizeAnalyzerResultTests(unittest.TestCase):
    def test_normalizes_standard_risk_result(self) -> None:
        result = normalize_analyzer_result(
            "metadata",
            {
                "status": "failed",
                "passed": False,
                "risk": "high",
                "suspicious_regions_count": 2,
                "issues": [{"signal": "editor"}, {"signal": "incremental_update"}],
            },
            exit_code=0,
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.REVIEW)
        self.assertEqual(result.risk, RiskLevel.HIGH)
        self.assertEqual(result.summary, "High risk")
        self.assertEqual(result.findings_count, 2)
        self.assertEqual(result.exit_code, 0)

    def test_unwraps_single_document_batch_result(self) -> None:
        result = normalize_analyzer_result(
            "moire",
            [{"file_verdict": "RECAPTURE", "images": []}],
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.REVIEW)
        self.assertEqual(result.summary, "Recapture")
        self.assertEqual(result.raw["file_verdict"], "RECAPTURE")

    def test_normalizes_missing_qr_as_inconclusive(self) -> None:
        result = normalize_analyzer_result(
            "qr_presence",
            {"qr_found": False, "qr_count": 0, "hits": []},
        )

        self.assertEqual(result.outcome, AnalyzerOutcome.INCONCLUSIVE)
        self.assertEqual(result.summary, "No QR code found")

    def test_invalid_payload_becomes_error_result(self) -> None:
        result = normalize_analyzer_result("metadata", ["unexpected"])

        self.assertEqual(result.outcome, AnalyzerOutcome.ERROR)
        self.assertIn("unsupported result shape", result.summary)

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
