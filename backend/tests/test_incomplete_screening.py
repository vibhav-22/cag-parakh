from __future__ import annotations

import unittest

from backend import checks


class ReadabilityIncompleteTests(unittest.TestCase):
    """A readability check that could not run must never read as a pass.

    Five of the readability sub-checks shell out to Tesseract or poppler. They
    used to report `passed=True` when those binaries were absent, which both
    inflated the score and could flip a document that genuinely fails into a
    clean verdict — the one output a screening tool must not produce. These
    tests pin the corrected behaviour.
    """

    def test_incomplete_screening_is_inconclusive_not_pass(self) -> None:
        verdict = checks.evaluate(
            "readability",
            {
                "verdict": "incomplete",
                "score": 66,
                "tests_passed": 4,
                "tests_total": 6,
                "tests_declared": 11,
                "complete": False,
                "tests_not_run": [
                    "PDF Stream Integrity",
                    "OCR Confidence",
                    "OCR Word Count",
                    "Image Noise Level",
                    "Image Sharpness",
                ],
                "tests": [],
            },
        )
        self.assertEqual(verdict.status, checks.INCONCLUSIVE)
        self.assertIn("could not run", verdict.reason)

    def test_missing_checks_are_named_so_the_gap_is_actionable(self) -> None:
        verdict = checks.evaluate(
            "readability",
            {
                "verdict": "incomplete",
                "score": 50,
                "tests_passed": 1,
                "tests_total": 2,
                "tests_declared": 3,
                "complete": False,
                "tests_not_run": ["OCR Word Count"],
                "tests": [],
            },
        )
        self.assertIn("OCR Word Count", verdict.reason)

    def test_not_run_checks_override_a_readable_verdict(self) -> None:
        # Defence in depth: even if the upstream tool still said "readable",
        # the presence of un-run checks must win.
        verdict = checks.evaluate(
            "readability",
            {
                "verdict": "readable",
                "score": 81,
                "tests_passed": 9,
                "tests_total": 11,
                "tests_declared": 11,
                "complete": False,
                "tests_not_run": ["OCR Confidence"],
                "tests": [],
            },
        )
        self.assertEqual(verdict.status, checks.INCONCLUSIVE)

    def test_a_complete_readable_screening_still_passes(self) -> None:
        verdict = checks.evaluate(
            "readability",
            {
                "verdict": "readable",
                "score": 95,
                "tests_passed": 11,
                "tests_total": 11,
                "tests_declared": 11,
                "complete": True,
                "tests_not_run": [],
                "tests": [],
            },
        )
        self.assertEqual(verdict.status, checks.PASS)

    def test_a_complete_failing_screening_still_fails(self) -> None:
        verdict = checks.evaluate(
            "readability",
            {
                "verdict": "unreadable",
                "score": 20,
                "tests_passed": 2,
                "tests_total": 11,
                "tests_declared": 11,
                "complete": True,
                "tests_not_run": [],
                "tests": [{"name": "OCR Word Count", "passed": False}],
            },
        )
        self.assertEqual(verdict.status, checks.FAIL)


if __name__ == "__main__":
    unittest.main()
