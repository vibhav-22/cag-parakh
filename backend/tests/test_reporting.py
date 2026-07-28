from __future__ import annotations

import csv
import io
import unittest

from backend.reporting import (
    CALIBRATION_NOTICE,
    batch_csv,
    case_report,
    decision_label,
    machine_verdict,
    render_batch_html,
    render_case_html,
)
from backend.version import APP_VERSION, DETECTORS_VERSION


def job(
    *,
    job_id: str = "abc123",
    filename: str = "certificate.pdf",
    outcomes: dict[str, str] | None = None,
    review: dict[str, object] | None = None,
    status: str = "completed",
    unanalyzable: bool = False,
    profile: dict[str, object] | None = None,
) -> dict[str, object]:
    outcomes = outcomes if outcomes is not None else {"metadata": "clear"}
    return {
        "id": job_id,
        "filename": filename,
        "sha256": "f" * 64,
        "profile": profile,
        "status": status,
        "created_at": "2026-07-26T09:00:00+00:00",
        "completed_at": "2026-07-26T09:02:00+00:00",
        "analyzers": list(outcomes),
        "settings": {"tamper_scan": {"review_threshold": 0.35}},
        "analyzer_runs": {
            name: {"status": "failed" if outcome == "error" else "completed", "error": None}
            for name, outcome in outcomes.items()
        },
        "results": {
            name: {
                "outcome": outcome,
                "risk": "high" if outcome == "review" else "low",
                "summary": f"{name} says {outcome}",
                "findings_count": 1 if outcome == "review" else 0,
                "regions": (
                    [{
                        "page": 2, "kind": "whitener_patch", "label": "Likely whitener patch",
                        "message": "Local confidence 71%.", "severity": "high",
                        "x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1,
                    }]
                    if outcome == "review" else []
                ),
            }
            for name, outcome in outcomes.items()
        },
        "review": review,
        "unanalyzable": unanalyzable,
        "unanalyzable_reason": "Pages will not render." if unanalyzable else None,
    }


class MachineVerdictTests(unittest.TestCase):
    def test_a_review_outcome_outranks_every_other_signal(self) -> None:
        state = job(outcomes={"metadata": "clear", "tamper_scan": "review", "moire": "error"})

        self.assertEqual(machine_verdict(state), "review")

    def test_a_check_error_is_not_reported_as_clear(self) -> None:
        state = job(outcomes={"metadata": "clear", "moire": "error"})

        self.assertEqual(machine_verdict(state), "error")

    def test_an_incomplete_job_has_no_verdict_yet(self) -> None:
        self.assertEqual(machine_verdict(job(status="running")), "pending")

    def test_unanalyzable_overrides_the_analyzer_outcomes(self) -> None:
        state = job(outcomes={"tamper_scan": "review"}, unanalyzable=True)

        self.assertEqual(machine_verdict(state), "unanalyzable")


class CaseReportTests(unittest.TestCase):
    def test_the_report_stamps_the_versions_and_settings_it_was_made_against(self) -> None:
        report = case_report(job(), batch_id="batch-1")

        self.assertEqual(report["app_version"], APP_VERSION)
        self.assertEqual(report["detectors_version"], DETECTORS_VERSION)
        self.assertEqual(report["analysis_settings"], {"tamper_scan": {"review_threshold": 0.35}})
        self.assertEqual(report["batch_id"], "batch-1")
        self.assertEqual(report["sha256"], "f" * 64)

    def test_every_report_carries_the_calibration_notice(self) -> None:
        report = case_report(job())

        self.assertEqual(report["calibration_notice"], CALIBRATION_NOTICE)

    def test_located_evidence_is_flattened_with_its_analyzer(self) -> None:
        report = case_report(job(outcomes={"tamper_scan": "review"}))

        self.assertEqual(len(report["evidence_regions"]), 1)
        self.assertEqual(report["evidence_regions"][0]["analyzer_id"], "tamper_scan")
        self.assertEqual(report["evidence_regions"][0]["page"], 2)

    def test_a_missing_decision_is_named_rather_than_left_blank(self) -> None:
        report = case_report(job())

        self.assertEqual(report["review_label"], "Not yet recorded")

    def test_an_escalation_is_labelled_as_a_handoff(self) -> None:
        self.assertEqual(decision_label("escalated"), "Escalated to a second reviewer")


class ProfileReportTests(unittest.TestCase):
    PROFILE = {
        "id": "p1",
        "name": "Birth certificate — full",
        "goal": "Confirm an authentic original before acceptance.",
        "guidance": "Flag if QR missing or whitener over 40%.",
    }

    def test_the_case_report_carries_the_screening_test(self) -> None:
        report = case_report(job(profile=self.PROFILE))

        self.assertEqual(report["profile"]["name"], "Birth certificate — full")

    def test_the_printable_report_shows_the_goal_and_decision_rule(self) -> None:
        html = render_case_html(case_report(job(profile=self.PROFILE)))

        self.assertIn("Screening test", html)
        self.assertIn("Birth certificate — full", html)
        self.assertIn("Decision rule", html)
        self.assertIn("Flag if QR missing or whitener over 40%.", html)

    def test_a_run_with_no_profile_omits_the_test_section(self) -> None:
        html = render_case_html(case_report(job(profile=None)))

        self.assertNotIn("Screening test", html)

    def test_the_batch_csv_records_the_test_per_document(self) -> None:
        batch = {
            "id": "b1", "created_at": "2026-07-26T09:00:00+00:00", "status": "completed",
            "document_count": 1, "jobs": [job(profile=self.PROFILE)],
        }
        rows = list(csv.DictReader(io.StringIO(batch_csv(batch))))

        self.assertEqual(rows[0]["screening_test"], "Birth certificate — full")

    def test_the_batch_report_names_a_shared_test(self) -> None:
        batch = {
            "id": "b1", "created_at": "2026-07-26T09:00:00+00:00", "status": "completed",
            "document_count": 2,
            "jobs": [job(job_id="a", profile=self.PROFILE), job(job_id="b", profile=self.PROFILE)],
        }
        self.assertIn("Birth certificate — full", render_batch_html(batch))


class CaseHtmlTests(unittest.TestCase):
    def test_the_printable_report_states_what_the_screening_does_not_claim(self) -> None:
        html = render_case_html(case_report(job()))

        self.assertIn("What this screening does not claim", html)
        self.assertIn("true-positive rate of this system is unknown", html)

    def test_an_undecided_case_says_the_report_is_incomplete(self) -> None:
        html = render_case_html(case_report(job()))

        self.assertIn("No decision has been recorded", html)

    def test_the_recorded_decision_and_notes_reach_the_report(self) -> None:
        review = {
            "decision": "escalated", "notes": "Portal record does not match.",
            "reviewed_at": "2026-07-26T10:00:00+00:00", "reviewer": "R. Iyer",
            "assigned_to": "S. Mehta",
        }
        html = render_case_html(case_report(job(review=review)))

        self.assertIn("Escalated to a second reviewer", html)
        self.assertIn("Portal record does not match.", html)
        self.assertIn("S. Mehta", html)

    def test_reviewer_supplied_text_is_escaped_not_injected(self) -> None:
        review = {
            "decision": "verified", "notes": "<script>alert(1)</script>",
            "reviewed_at": "2026-07-26T10:00:00+00:00",
        }
        html = render_case_html(case_report(job(review=review)))

        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_a_filename_cannot_smuggle_markup_into_the_report(self) -> None:
        html = render_case_html(case_report(job(filename='<img src=x onerror="boom">.pdf')))

        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img", html)


class BatchExportTests(unittest.TestCase):
    def batch(self) -> dict[str, object]:
        return {
            "id": "batch-77",
            "created_at": "2026-07-26T09:00:00+00:00",
            "status": "completed",
            "document_count": 2,
            "jobs": [
                job(job_id="one", filename="a.pdf", outcomes={"metadata": "clear"}),
                job(
                    job_id="two", filename="b.pdf",
                    outcomes={"tamper_scan": "review", "moire": "error"},
                    review={
                        "decision": "needs_investigation",
                        "notes": "Line one\nline two",
                        "reviewed_at": "2026-07-26T10:00:00+00:00",
                        "reviewer": "R. Iyer",
                        "assigned_to": "",
                    },
                ),
            ],
        }

    def test_the_csv_has_one_row_per_document_with_both_verdicts(self) -> None:
        rows = list(csv.DictReader(io.StringIO(batch_csv(self.batch()))))

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["document"], "a.pdf")
        self.assertEqual(rows[0]["machine_verdict"], "Clear")
        self.assertEqual(rows[0]["reviewer_decision"], "Not yet recorded")
        self.assertEqual(rows[1]["machine_verdict"], "Needs review")
        self.assertEqual(rows[1]["reviewer_decision"], "Needs investigation")
        self.assertEqual(rows[1]["reviewer"], "R. Iyer")

    def test_the_csv_names_the_checks_that_flagged(self) -> None:
        rows = list(csv.DictReader(io.StringIO(batch_csv(self.batch()))))

        self.assertEqual(rows[1]["flagged_checks"], "moire; tamper_scan")

    def test_notes_stay_on_one_csv_row(self) -> None:
        rows = list(csv.DictReader(io.StringIO(batch_csv(self.batch()))))

        self.assertEqual(rows[1]["notes"], "Line one line two")

    def test_the_batch_report_counts_documents_without_a_decision(self) -> None:
        html = render_batch_html(self.batch())

        self.assertIn("1 without a recorded decision", html)
        self.assertIn("What this screening does not claim", html)


if __name__ == "__main__":
    unittest.main()
