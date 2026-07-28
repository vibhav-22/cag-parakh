from __future__ import annotations

import unittest

from backend import checks
from backend.service import ANALYZERS


class CheckCriteriaTests(unittest.TestCase):
    def test_every_analyzer_states_its_criterion_before_anything_runs(self) -> None:
        # The rule has to be printable on the setup screen, so it cannot depend
        # on a result. A check with no rule of its own would silently fall back
        # to the generic heuristic.
        for analyzer in ANALYZERS:
            with self.subTest(analyzer=analyzer):
                self.assertIn(analyzer, checks.EVALUATORS)
                criterion = checks.criterion_for(analyzer)
                self.assertTrue(criterion.strip())
                self.assertTrue(criterion.rstrip().endswith("."))

    def test_an_inconclusive_result_always_says_why_it_could_not_run(self) -> None:
        cases = {
            "same_phone": {"summary": {"overall_verdict": "insufficient_pages"}, "pages": [{"page": 1}]},
            "moire": {"file_verdict": "INCONCLUSIVE", "images": [{"reason": "image too small"}]},
            "scanner_noise": {
                "source_consistency_possible": False,
                "summary": {"overall_risk": "low", "pages_analyzed": 1, "suspicious_regions": 0},
            },
            "tamper_scan": {
                "verdict": "insufficient_signal",
                "pages": [{"page": 1, "indeterminate": True}],
                "indeterminate_pages": 1,
            },
            "readability": {"verdict": "", "tests": []},
        }
        for analyzer, raw in cases.items():
            with self.subTest(analyzer=analyzer):
                verdict = checks.evaluate(analyzer, raw)
                self.assertEqual(verdict.status, checks.INCONCLUSIVE)
                self.assertGreater(len(verdict.reason.split()), 8, verdict.reason)

    def test_a_crashed_detector_is_an_error_not_a_cross(self) -> None:
        verdict = checks.evaluate("moire", {"status": "error", "detail": "Timed out after 360 seconds."})

        self.assertEqual(verdict.status, checks.ERROR)
        self.assertIn("Timed out", verdict.reason)

    def test_presence_checks_pass_on_presence_and_fail_on_absence(self) -> None:
        presence = {
            "signature": ({"count": 1, "regions": [{"page": 1, "confidence": 0.8}]}, {"count": 0, "regions": []}),
            "photo_detection": ({"photo_found": True, "photo_count": 1, "passed": True}, {"photo_found": False}),
            "qr_presence": ({"qr_count": 1, "hits": [{"page": 1, "data": "x"}]}, {"qr_count": 0, "hits": []}),
            "font_analysis": ({"typefaces": [{"typeface": "Arial"}]}, {"typefaces": [], "unique_fonts": []}),
        }
        for analyzer, (found, missing) in presence.items():
            with self.subTest(analyzer=analyzer):
                self.assertEqual(checks.evaluate(analyzer, found).status, checks.PASS)
                self.assertEqual(checks.evaluate(analyzer, missing).status, checks.FAIL)

    def test_metadata_never_returns_a_tick_or_a_cross(self) -> None:
        clean = checks.evaluate("metadata", {"status": "passed", "generation": {"kind": "scanner"}, "issues": []})
        dirty = checks.evaluate("metadata", {
            "status": "failed",
            "generation": {"kind": "image_editor", "producer": "GIMP", "resaved_after_creation": True},
            "issues": [{"signal": "editor_producer"}],
        })

        self.assertEqual(clean.status, checks.INFO)
        self.assertEqual(dirty.status, checks.INFO)
        self.assertIn("GIMP", dirty.reason)


if __name__ == "__main__":
    unittest.main()
