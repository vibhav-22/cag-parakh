from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.service import JobStore, run_job, sanitize_analysis_settings


class JobStoreTests(unittest.TestCase):
    def test_advanced_settings_are_sanitized_and_persisted(self) -> None:
        settings = sanitize_analysis_settings({
            "qr_presence": {"dpi": 900, "min_codes": 3, "rotations": "0,180"},
            "readability": {"noise_threshold": -4, "sharpness_threshold": 1200},
            "unexpected": {"command": "ignored"},
        })

        self.assertEqual(settings["qr_presence"]["dpi"], 600)
        self.assertEqual(settings["qr_presence"]["min_codes"], 3)
        self.assertEqual(settings["qr_presence"]["rotations"], [0, 180])
        self.assertEqual(settings["readability"]["noise_threshold"], 1.0)
        self.assertEqual(settings["readability"]["sharpness_threshold"], 1200.0)
        self.assertNotIn("unexpected", settings)

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create("sample.pdf", b"%PDF-1.7", ["qr_presence"], settings)
            self.assertEqual(job["settings"]["qr_presence"]["min_codes"], 3)

    def test_signature_settings_are_clamped(self) -> None:
        settings = sanitize_analysis_settings({
            "signature": {"dpi": 5000, "confidence": 1.5, "min_signatures": 99},
        })

        self.assertEqual(settings["signature"]["dpi"], 600)
        self.assertEqual(settings["signature"]["confidence"], 0.95)
        self.assertEqual(settings["signature"]["min_signatures"], 20)

    def test_signature_settings_use_locator_defaults(self) -> None:
        settings = sanitize_analysis_settings({})
        self.assertEqual(settings["signature"]["dpi"], 200)
        self.assertEqual(settings["signature"]["confidence"], 0.55)
        self.assertNotIn("imgsz", settings["signature"])

    def test_signature_analyzer_is_registered(self) -> None:
        from backend.service import ANALYZERS

        self.assertIn("signature", ANALYZERS)
        self.assertEqual(ANALYZERS["signature"]["kind"], "json")
        self.assertEqual(ANALYZERS["signature"]["flag"], "--output")

    def test_signature_analyzer_passes_artifact_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create("signed.pdf", b"%PDF-1.7", ["signature"])

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                output_path = Path(command[command.index("--output") + 1])
                artifact_dir = Path(command[command.index("--artifact-dir") + 1])
                nested = artifact_dir / "signed-document"
                nested.mkdir(parents=True)
                (nested / "annotated.pdf").write_bytes(b"%PDF-1.7")
                output_path.write_text(
                    json.dumps({
                        "status": "completed",
                        "passed": True,
                        "risk": "low",
                        "verdict": "signature_present",
                        "present": True,
                        "count": 1,
                        "score": 0.86,
                        "pages": 1,
                        "regions": [{
                            "page": 1,
                            "kind": "signature",
                            "label": "Signature",
                            "reason": "Likely visible signature.",
                            "severity": "low",
                            "confidence": 0.86,
                            "bbox_normalized": {
                                "x0": 0.2, "y0": 0.6, "x1": 0.5, "y1": 0.72,
                            },
                        }],
                        "artifacts": ["signed-document/annotated.pdf"],
                    }),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("backend.service.subprocess.run", side_effect=fake_run):
                run_job(store, job["id"])

            completed = store.get(job["id"])
            assert completed is not None
            result = completed["results"]["signature"]
            self.assertEqual(result["summary"], "1 signature(s) detected")
            self.assertEqual(result["artifacts"], ["signed-document/annotated.pdf"])
            self.assertEqual(result["regions"][0]["kind"], "signature")

    def test_run_job_adapts_whitener_report_and_nested_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create("sample.pdf", b"%PDF-1.7", ["tamper_scan"])

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                report_root = Path(command[command.index("--out") + 1])
                detector_dir = report_root / Path(command[2]).stem
                detector_dir.mkdir(parents=True)
                (detector_dir / "page_001_annotated.png").write_bytes(b"png")
                (detector_dir / "report.json").write_text(json.dumps({
                    "tool": "whitener-detect",
                    "version": "1.1.0",
                    "file": command[2],
                    "requested_dpi": 200,
                    "document_probability": 0.72,
                    "max_page_probability": 0.72,
                    "pages": [{
                        "page": 1,
                        "probability": 0.72,
                        "indeterminate": False,
                        "regions": [{"confidence": 0.72, "bbox_px": [10, 20, 30, 40]}],
                        "size_px": [100, 200],
                    }],
                    "outputs": {},
                }), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="complete", stderr="")

            with patch("backend.service.subprocess.run", side_effect=fake_run):
                run_job(store, job["id"])

            completed = store.get(job["id"])
            assert completed is not None
            result = completed["results"]["tamper_scan"]
            self.assertEqual(result["summary"], "Likely whitener detected · 72% probability")
            self.assertEqual(result["findings_count"], 1)
            self.assertEqual(result["regions"][0]["page"], 1)
            self.assertEqual(result["artifacts"], [f"{job['id']}/page_001_annotated.png"])

    def test_removed_ink_analyzers_are_not_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            available = {item["id"] for item in store.available_analyzers()}

            self.assertNotIn("overwriting", available)
            self.assertNotIn("stroke_thickness", available)
            with self.assertRaisesRegex(ValueError, "Unknown analyzer"):
                store.create("sample.pdf", b"%PDF-1.7", ["overwriting"])

    def test_photo_detector_saves_a_viewable_job_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create("identity.pdf", b"%PDF-1.7", ["photo_detection"])

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                artifact_dir = Path(command[command.index("--save-dir") + 1])
                output_path = Path(command[command.index("--output") + 1])
                artifact_dir.mkdir(parents=True)
                (artifact_dir / "detected_photo.jpg").write_bytes(b"jpeg")
                output_path.write_text(json.dumps({
                    "status": "completed",
                    "passed": True,
                    "risk": "low",
                    "verdict": "photo_detected",
                    "photo_found": True,
                    "photo_count": 1,
                    "page": 1,
                    "artifacts": ["detected_photo.jpg"],
                }), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("backend.service.subprocess.run", side_effect=fake_run):
                run_job(store, job["id"])

            completed = store.get(job["id"])
            assert completed is not None
            result = completed["results"]["photo_detection"]
            self.assertEqual(result["outcome"], "clear")
            self.assertEqual(result["summary"], "1 document photo detected")
            self.assertEqual(result["artifacts"], ["detected_photo.jpg"])
            self.assertTrue(
                (
                    store.data_dir
                    / f"{job['id']}-photo_detection-report"
                    / "detected_photo.jpg"
                ).is_file()
            )

    def test_create_initializes_one_queued_run_per_analyzer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create("sample.pdf", b"%PDF-1.7", ["metadata", "qr_presence"])

            self.assertEqual(job["status"], "queued")
            self.assertEqual(set(job["analyzer_runs"]), {"metadata", "qr_presence"})
            self.assertTrue(all(run["status"] == "queued" for run in job["analyzer_runs"].values()))
            self.assertEqual(job["results"], {})

    def test_run_job_stores_normalized_result_and_completed_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create("sample.pdf", b"%PDF-1.7", ["metadata"])

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                output_path = Path(command[-1])
                output_path.write_text(
                    json.dumps({"status": "passed", "passed": True, "risk": "low", "issues": []}),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("backend.service.subprocess.run", side_effect=fake_run):
                run_job(store, job["id"])

            completed = store.get(job["id"])
            assert completed is not None
            result = completed["results"]["metadata"]
            run = completed["analyzer_runs"]["metadata"]
            self.assertEqual(completed["status"], "completed")
            # Metadata reports provenance and grades nothing, so it lands on the
            # informational outcome rather than a tick.
            self.assertEqual(result["outcome"], "info")
            self.assertEqual(result["check"]["status"], "info")
            self.assertEqual(result["risk"], "low")
            self.assertEqual(result["raw"]["status"], "passed")
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["result"], result)
            self.assertIsNotNone(run["started_at"])
            self.assertIsNotNone(run["completed_at"])

    def test_reload_backfills_check_onto_legacy_results(self) -> None:
        """A job saved before per-detector criteria existed has no `check` key.

        Restarting the service must grade it against today's rule from the raw
        payload already on disk, rather than leaving the reason blank forever.
        """

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create("sample.pdf", b"%PDF-1.7", ["moire"])
            job_id = job["id"]

            legacy_result = {
                "analyzer_id": "moire",
                "outcome": "inconclusive",
                "risk": "unknown",
                "summary": "Inconclusive",
                "findings_count": 0,
                "artifacts": [],
                "regions": [],
                "exit_code": 0,
                "raw": {"file_verdict": "", "images": []},
                # No "check" key: this is the shape saved before checks.py existed.
            }
            legacy_job = store.get(job_id)
            assert legacy_job is not None
            legacy_job["status"] = "completed"
            legacy_job["results"] = {"moire": legacy_result}
            legacy_job["analyzer_runs"]["moire"]["status"] = "completed"
            legacy_job["analyzer_runs"]["moire"]["result"] = legacy_result
            store.update(job_id, **legacy_job)

            reloaded_store = JobStore(Path(directory))
            reloaded = reloaded_store.get(job_id)
            assert reloaded is not None
            result = reloaded["results"]["moire"]
            self.assertIn("check", result)
            self.assertEqual(result["check"]["status"], "inconclusive")
            self.assertTrue(result["check"]["reason"])
            self.assertEqual(reloaded["analyzer_runs"]["moire"]["result"]["check"]["status"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
