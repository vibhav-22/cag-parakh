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

    def test_qr_miss_escalates_to_a_wider_dpi_sweep(self) -> None:
        """A real UP board marksheet's QR decodes only at 350 DPI; the fast
        pass (300 DPI) reported the document as having none."""

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create("marksheet.pdf", b"%PDF-1.7", ["qr_presence"])
            seen_dpis: list[str] = []

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                dpi = command[command.index("--dpi") + 1]
                seen_dpis.append(dpi)
                output_path = Path(command[command.index("--out") + 1])
                # Only the wide sweep decodes anything, mirroring the measured
                # behaviour: 300 DPI finds nothing at any rotation.
                found = "350" in dpi
                output_path.write_text(json.dumps({
                    "qr_found": found,
                    "qr_count": 1 if found else 0,
                    "hits": [{"page": 5, "dpi": 350, "payload": "HS/1221057946"}] if found else [],
                }), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("backend.service.subprocess.run", side_effect=fake_run):
                run_job(store, job["id"])

            completed = store.get(job["id"])
            assert completed is not None
            raw = completed["results"]["qr_presence"]["raw"]

            self.assertEqual(seen_dpis, ["300", "250,350,450"])  # fast, then deep
            self.assertEqual(raw["qr_count"], 1)
            self.assertTrue(raw["deep_rescan"])
            self.assertEqual(completed["results"]["qr_presence"]["outcome"], "clear")

    def test_qr_dpi_ladder_is_derived_from_the_scans_own_resolution(self) -> None:
        """The usable decode window sits above the scan's native resolution,
        so the ladder has to move with each document rather than be fixed."""

        import fitz

        from backend.service import native_scan_dpi, qr_dpi_ladder

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.pdf"
            document = fitz.open()
            page = document.new_page(width=595, height=842)  # A4
            # 2058x2711 over 8.26x11.69in is the real page-5 geometry that
            # decoded only well above its ~250 DPI native resolution.
            pixmap = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 2058, 2711))
            pixmap.clear_with(255)
            page.insert_image(page.rect, pixmap=pixmap)
            document.save(path)
            document.close()

            native = native_scan_dpi(path)
            assert native is not None
            # 2058/8.26in = 249 across, 2711/11.69in = 232 down. The lower
            # axis is the one that limits how many pixels a QR module gets,
            # so that is the resolution the ladder must be built from.
            self.assertAlmostEqual(native, 232, delta=8)

            ladder = qr_dpi_ladder(path)
            assert ladder is not None
            values = [int(item) for item in ladder.split(",")]
            self.assertEqual(values[0], min(values))
            # Must reach into the measured 350-400 window, which a ladder
            # pinned at the native resolution alone would never touch.
            self.assertTrue(any(340 <= value <= 420 for value in values), values)

    def test_qr_dpi_ladder_is_none_for_a_pdf_with_no_raster_page(self) -> None:
        import fitz

        from backend.service import qr_dpi_ladder

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text.pdf"
            document = fitz.open()
            document.new_page(width=595, height=842).insert_text((72, 72), "born digital")
            document.save(path)
            document.close()

            self.assertIsNone(qr_dpi_ladder(path))

    def test_qr_effort_low_never_escalates(self) -> None:
        """Low effort is the time-efficient mode: one pass, no wide sweep."""

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create(
                "marksheet.pdf", b"%PDF-1.7", ["qr_presence"],
                {"qr_presence": {"effort": "low"}},
            )
            calls: list[str] = []

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                calls.append(command[command.index("--dpi") + 1])
                Path(command[command.index("--out") + 1]).write_text(
                    json.dumps({"qr_found": False, "qr_count": 0, "hits": []}), encoding="utf-8"
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("backend.service.subprocess.run", side_effect=fake_run):
                run_job(store, job["id"])

            completed = store.get(job["id"])
            assert completed is not None
            self.assertEqual(calls, ["300"])  # fast pass only
            raw = completed["results"]["qr_presence"]["raw"]
            self.assertEqual(raw["search_effort"], "low")
            # The reason must admit the search was shallow rather than imply
            # the document has no code.
            reason = completed["results"]["qr_presence"]["check"]["reason"]
            self.assertIn("raise QR search effort", reason)

    def test_qr_effort_high_sweeps_even_after_the_fast_pass_finds_one(self) -> None:
        """High effort looks for codes the fast pass could not reach, so it
        must not stop just because the cheap pass already found something."""

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create(
                "two-codes.pdf", b"%PDF-1.7", ["qr_presence"],
                {"qr_presence": {"effort": "high"}},
            )
            calls: list[str] = []

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                dpi = command[command.index("--dpi") + 1]
                calls.append(dpi)
                deep = "," in dpi
                hits = [{"page": 1, "payload": "first"}]
                if deep:
                    hits.append({"page": 5, "payload": "second"})
                Path(command[command.index("--out") + 1]).write_text(
                    json.dumps({"qr_found": True, "qr_count": len(hits), "hits": hits}),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("backend.service.subprocess.run", side_effect=fake_run):
                run_job(store, job["id"])

            completed = store.get(job["id"])
            assert completed is not None
            self.assertEqual(len(calls), 2)  # escalated despite a hit
            self.assertEqual(completed["results"]["qr_presence"]["raw"]["qr_count"], 2)

    def test_qr_deep_rescan_never_loses_codes_the_fast_pass_found(self) -> None:
        """The wide sweep replaces the fast result only if it found at least
        as much — a deep pass that decodes fewer codes must not win."""

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create(
                "regress.pdf", b"%PDF-1.7", ["qr_presence"],
                {"qr_presence": {"effort": "high"}},
            )

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                deep = "," in command[command.index("--dpi") + 1]
                hits = [] if deep else [{"page": 1, "payload": "only-fast-finds-me"}]
                Path(command[command.index("--out") + 1]).write_text(
                    json.dumps({"qr_found": bool(hits), "qr_count": len(hits), "hits": hits}),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("backend.service.subprocess.run", side_effect=fake_run):
                run_job(store, job["id"])

            completed = store.get(job["id"])
            assert completed is not None
            self.assertEqual(completed["results"]["qr_presence"]["raw"]["qr_count"], 1)

    def test_qr_deep_rescan_timeout_is_inconclusive_not_a_clean_miss(self) -> None:
        """A sweep killed part-way proves nothing about the document. Measured
        at 474s against a 360s budget, this silently produced a "no QR code"
        verdict whose reason claimed the wide search had completed."""

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create("slow.pdf", b"%PDF-1.7", ["qr_presence"])

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                if "," in command[command.index("--dpi") + 1]:
                    raise subprocess.TimeoutExpired(command, float(kwargs.get("timeout") or 0))
                Path(command[command.index("--out") + 1]).write_text(
                    json.dumps({"qr_found": False, "qr_count": 0, "hits": []}), encoding="utf-8"
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("backend.service.subprocess.run", side_effect=fake_run):
                run_job(store, job["id"])

            completed = store.get(job["id"])
            assert completed is not None
            result = completed["results"]["qr_presence"]

            self.assertEqual(result["raw"]["deep_rescan_incomplete"], "timeout")
            self.assertEqual(result["check"]["status"], "inconclusive")
            self.assertEqual(result["outcome"], "inconclusive")
            self.assertIn("ran out of time", result["check"]["reason"])
            # It must not assert the document has no QR code.
            self.assertNotIn("still decoded nothing", result["check"]["reason"])

    def test_qr_deep_rescan_gets_a_longer_budget_than_the_fast_pass(self) -> None:
        from backend.service import QR_DEEP_TIMEOUT_SECONDS, TIMEOUT_SECONDS

        self.assertGreater(QR_DEEP_TIMEOUT_SECONDS, TIMEOUT_SECONDS)

    def test_qr_deep_rescan_keeps_the_fast_result_when_it_also_finds_nothing(self) -> None:
        """A genuinely QR-free document must not be reported as errored or
        changed by the escalation — it stays a plain 'found none'."""

        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create("no-qr.pdf", b"%PDF-1.7", ["qr_presence"])

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                output_path = Path(command[command.index("--out") + 1])
                output_path.write_text(
                    json.dumps({"qr_found": False, "qr_count": 0, "hits": []}), encoding="utf-8"
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("backend.service.subprocess.run", side_effect=fake_run):
                run_job(store, job["id"])

            completed = store.get(job["id"])
            assert completed is not None
            raw = completed["results"]["qr_presence"]["raw"]

            self.assertEqual(raw["qr_count"], 0)
            self.assertNotIn("deep_rescan", raw)

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
            legacy_job["check_contract_version"] = 0
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


class ProjectStoreTests(unittest.TestCase):
    def test_project_is_created_listed_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            project = store.create_project("Q3 intake")

            self.assertEqual(project["name"], "Q3 intake")
            self.assertTrue(project["id"])
            self.assertEqual(project["created_at"], project["updated_at"])
            self.assertTrue(store.database_path.exists())
            self.assertEqual(list(Path(directory).glob("*.project.json")), [])
            self.assertEqual([p["id"] for p in store.list_projects()], [project["id"]])

            # A restart must not lose the committed project row.
            reloaded_store = JobStore(Path(directory))
            reloaded = reloaded_store.project(project["id"])
            self.assertIsNotNone(reloaded)
            assert reloaded is not None
            self.assertEqual(reloaded["name"], "Q3 intake")

    def test_project_name_is_trimmed_and_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            project = store.create_project("  Q3 intake  ")
            self.assertEqual(project["name"], "Q3 intake")

            with self.assertRaises(ValueError):
                store.create_project("")
            with self.assertRaises(ValueError):
                store.create_project("   ")

    def test_batch_records_its_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            project = store.create_project("Q3 intake")
            batch = store.create_batch(
                [("sample.pdf", b"%PDF-1.7")], ["qr_presence"],
                project_id=project["id"],
            )

            self.assertEqual(batch["project_id"], project["id"])
            state = store.batch_state(batch["id"])
            assert state is not None
            self.assertEqual(state["project_id"], project["id"])
            filed = store.list_project_batches(project["id"])
            self.assertEqual([b["id"] for b in filed], [batch["id"]])

    def test_batch_with_unknown_project_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))

            with self.assertRaises(ValueError):
                store.create_batch(
                    [("sample.pdf", b"%PDF-1.7")], ["qr_presence"],
                    project_id="not-a-real-project",
                )

            # Validation must happen before any document is written, so a
            # deleted-mid-upload project never orphans stored content.
            self.assertEqual(list(Path(directory).glob("*.job.json")), [])
            self.assertEqual(list((Path(directory) / "documents").rglob("*.pdf")), [])
            self.assertEqual(store.list_batches(), [])

    def test_deleting_a_project_unfiles_its_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            project = store.create_project("Q3 intake")
            batch = store.create_batch(
                [("sample.pdf", b"%PDF-1.7")], ["qr_presence"],
                project_id=project["id"],
            )

            self.assertTrue(store.delete_project(project["id"]))
            self.assertIsNone(store.project(project["id"]))

            state = store.batch_state(batch["id"])
            assert state is not None
            self.assertIsNone(state["project_id"])
            self.assertIn(batch["id"], [b["id"] for b in store.list_batches()])

            self.assertIsNone(store.batch_state(batch["id"])["project_id"])

    def test_project_foreign_key_unfiles_batches_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            project = store.create_project("Temporary")
            batch = store.create_batch(
                [("sample.pdf", b"%PDF-1.7")], ["qr_presence"],
                project_id=project["id"],
            )
            store.delete_project(project["id"])

            reloaded_store = JobStore(Path(directory))
            state = reloaded_store.batch_state(batch["id"])
            assert state is not None
            self.assertIsNone(state["project_id"])

    def test_empty_project_survives_a_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            project = store.create_project("Empty so far")

            reloaded_store = JobStore(Path(directory))
            self.assertIsNotNone(reloaded_store.project(project["id"]))
            self.assertEqual(reloaded_store.list_project_batches(project["id"]), [])

    def test_renaming_a_project_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            project = store.create_project("Old name")
            renamed = store.rename_project(project["id"], "New name")
            assert renamed is not None
            self.assertEqual(renamed["name"], "New name")
            self.assertGreaterEqual(renamed["updated_at"], project["updated_at"])

            reloaded_store = JobStore(Path(directory))
            reloaded = reloaded_store.project(project["id"])
            assert reloaded is not None
            self.assertEqual(reloaded["name"], "New name")

            self.assertIsNone(store.rename_project("missing-project", "x"))

    def test_project_batch_count_matches_the_listed_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            project = store.create_project("Q3 intake")
            store.create_batch(
                [("a.pdf", b"%PDF-1.7")], ["qr_presence"], project_id=project["id"],
            )
            store.create_batch(
                [("b.pdf", b"%PDF-1.7")], ["qr_presence"], project_id=project["id"],
            )
            store.create_batch([("c.pdf", b"%PDF-1.7")], ["qr_presence"])  # unfiled

            listed = store.list_projects()
            row = next(p for p in listed if p["id"] == project["id"])
            self.assertEqual(row["batch_count"], len(store.list_project_batches(project["id"])))
            self.assertEqual(row["batch_count"], 2)


if __name__ == "__main__":
    unittest.main()
