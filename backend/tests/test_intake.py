from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import fitz
from fastapi import UploadFile
from PIL import Image

import backend.app as app_module
from backend.app import _preflight_file
from backend.service import JobStore, content_digest, run_job


def pdf_bytes(text: str = "Certificate of completion", pages: int = 1) -> bytes:
    document = fitz.open()
    try:
        for index in range(pages):
            page = document.new_page(width=300, height=400)
            page.insert_text((40, 60), f"{text} {index + 1}", fontsize=11)
        return document.tobytes()
    finally:
        document.close()


def scanned_pdf_bytes() -> bytes:
    """A PDF with no text layer, the way a flatbed scan arrives."""

    document = fitz.open()
    try:
        page = document.new_page(width=300, height=400)
        image = BytesIO()
        Image.new("RGB", (300, 400), "white").save(image, format="PNG")
        page.insert_image(page.rect, stream=image.getvalue())
        return document.tobytes()
    finally:
        document.close()


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (300, 400), "white").save(output, format="PNG")
    return output.getvalue()


class ContentDigestTests(unittest.TestCase):
    def test_identical_bytes_share_a_digest_and_different_bytes_do_not(self) -> None:
        payload = pdf_bytes()

        self.assertEqual(content_digest(payload), content_digest(bytes(payload)))
        self.assertNotEqual(content_digest(payload), content_digest(payload + b" "))

    def test_a_job_records_the_digest_of_what_was_stored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            payload = pdf_bytes()
            job = store.create("certificate.pdf", payload, ["metadata"])

            self.assertEqual(job["sha256"], content_digest(payload))


class PriorScreeningTests(unittest.TestCase):
    def test_a_resubmitted_file_finds_its_earlier_screening(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            payload = pdf_bytes()
            first = store.create("original.pdf", payload, ["metadata"])
            store.update(first["id"], status="completed", results={"metadata": {"outcome": "clear"}})
            second = store.create("resubmitted.pdf", payload, ["metadata"])

            priors = store.prior_screenings(str(second["sha256"]), exclude_job_id=second["id"])

            self.assertEqual([item["job_id"] for item in priors], [first["id"]])
            self.assertEqual(priors[0]["filename"], "original.pdf")
            self.assertEqual(priors[0]["machine_verdict"], "clear")

    def test_a_different_capture_of_the_same_document_is_not_a_match(self) -> None:
        # The join key is the bytes. Two photos of one certificate are two files,
        # and the UI copy depends on this staying true.
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            first = store.create("capture-a.pdf", pdf_bytes("Certificate A"), ["metadata"])
            second = store.create("capture-b.pdf", pdf_bytes("Certificate B"), ["metadata"])

            self.assertEqual(store.prior_screenings(str(second["sha256"]), second["id"]), [])
            self.assertEqual(store.prior_screenings(str(first["sha256"]), first["id"]), [])

    def test_digests_survive_a_service_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = pdf_bytes()
            first = JobStore(Path(directory)).create("original.pdf", payload, ["metadata"])

            reloaded = JobStore(Path(directory))
            priors = reloaded.prior_screenings(content_digest(payload))

            self.assertEqual([item["job_id"] for item in priors], [first["id"]])

    def test_a_job_stored_before_digests_existed_is_backfilled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = pdf_bytes()
            source_store = JobStore(Path(directory) / "source")
            job = source_store.create("legacy.pdf", payload, ["metadata"])
            # Simulate the pre-digest JSON record before the SQLite migration.
            path = Path(directory) / f"{job['id']}.job.json"
            stored = dict(job)
            stored.pop("sha256")
            path.write_text(json.dumps(stored), encoding="utf-8")
            (Path(directory) / f"{job['id']}.pdf").write_bytes(payload)

            reloaded = JobStore(Path(directory))

            self.assertEqual(reloaded.get(job["id"])["sha256"], content_digest(payload))
            self.assertEqual(len(reloaded.prior_screenings(content_digest(payload))), 1)


class PreflightTests(unittest.TestCase):
    def check(self, filename: str, payload: bytes):
        upload = UploadFile(filename=filename, file=BytesIO(payload))
        return asyncio.run(_preflight_file(upload))

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = JobStore(Path(self.directory.name))
        patcher = patch.object(app_module, "store", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.directory.cleanup)

    def test_a_readable_pdf_reports_its_pages_and_no_blocker(self) -> None:
        row = self.check("certificate.pdf", pdf_bytes(pages=3))

        self.assertTrue(row.readable)
        self.assertEqual(row.page_count, 3)
        self.assertIsNone(row.blocker)
        self.assertIsNone(row.warning)

    def test_a_corrupt_pdf_is_reported_rather_than_raised(self) -> None:
        row = self.check("broken.pdf", b"this is not a pdf at all")

        self.assertFalse(row.readable)
        self.assertIn("valid PDF", str(row.blocker))

    def test_an_unsupported_file_type_is_reported_rather_than_raised(self) -> None:
        row = self.check("notes.docx", b"PK\x03\x04payload")

        self.assertFalse(row.readable)
        self.assertIn("not a supported", str(row.blocker).lower())

    def test_a_scan_with_no_text_layer_warns_without_blocking(self) -> None:
        row = self.check("flatbed.pdf", scanned_pdf_bytes())

        self.assertTrue(row.readable)
        self.assertIn("No text layer", str(row.warning))

    def test_an_image_is_digested_as_uploaded_not_as_converted(self) -> None:
        # Intake normalizes images to PDF, and that conversion is not byte-stable:
        # MuPDF stamps fresh ids each time. Digesting the converted output would
        # give the same photo a new digest on every upload, so duplicate detection
        # would never fire for images. The digest is of the file as supplied.
        payload = png_bytes()
        row = self.check("scan.png", payload)
        name, converted, digest = asyncio.run(
            app_module._read_screening_upload(UploadFile(filename="scan.png", file=BytesIO(payload)))
        )

        self.assertTrue(row.readable)
        self.assertEqual(name, "scan.png")
        self.assertEqual(row.sha256, content_digest(payload))
        self.assertEqual(digest, content_digest(payload))
        self.assertNotEqual(content_digest(converted), digest)

    def test_the_same_image_uploaded_twice_matches_itself(self) -> None:
        payload = png_bytes()
        name, converted, digest = asyncio.run(
            app_module._read_screening_upload(UploadFile(filename="scan.png", file=BytesIO(payload)))
        )
        earlier = self.store.create(name, converted, ["metadata"], None, digest)
        self.store.update(earlier["id"], status="completed", results={"metadata": {"outcome": "clear"}})

        row = self.check("scan-again.png", payload)

        self.assertEqual([item.job_id for item in row.prior_screenings], [earlier["id"]])

    def test_pre_flight_surfaces_an_earlier_screening_of_the_same_file(self) -> None:
        payload = pdf_bytes()
        earlier = self.store.create("original.pdf", payload, ["metadata"])
        self.store.update(earlier["id"], status="completed", results={"metadata": {"outcome": "review"}})

        row = self.check("resubmitted.pdf", payload)

        self.assertEqual(len(row.prior_screenings), 1)
        self.assertEqual(row.prior_screenings[0].filename, "original.pdf")
        self.assertEqual(row.prior_screenings[0].machine_verdict, "review")

    def test_an_oversized_file_is_blocked_at_intake(self) -> None:
        row = self.check("huge.pdf", b"%PDF-" + b"0" * (app_module.MAX_UPLOAD_BYTES + 1))

        self.assertFalse(row.readable)
        self.assertIn("25 MB", str(row.blocker))


class ProfileStampTests(unittest.TestCase):
    def test_a_batch_stamps_its_profile_onto_every_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            profile = {"id": "p1", "name": "Certificate check", "goal": "g", "guidance": "rule"}
            batch = store.create_batch(
                [("a.pdf", pdf_bytes(), content_digest(pdf_bytes())),
                 ("b.pdf", pdf_bytes("B"), content_digest(pdf_bytes("B")))],
                ["metadata"], None, profile,
            )
            for job_id in batch["job_ids"]:
                self.assertEqual(store.get(job_id)["profile"]["name"], "Certificate check")

    def test_an_ad_hoc_run_has_no_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create("a.pdf", pdf_bytes(), ["metadata"])
            self.assertIsNone(job["profile"])

    def test_the_profile_sanitizer_keeps_identity_and_drops_execution_config(self) -> None:
        # A test names an intent. What earns a tick belongs to the detector, so a
        # profile can no longer carry a result mapping at all.
        cleaned = app_module._requested_profile(json.dumps({
            "id": "p1", "name": "  Test  ", "goal": "why", "guidance": "how",
            "analyzers": ["metadata"], "settings": {"metadata": {"dpi": 999}},
            "result_rules": {"metadata": "review"},
        }))
        self.assertEqual(set(cleaned), {"id", "name", "goal", "guidance"})
        self.assertEqual(cleaned["name"], "Test")

    def test_a_nameless_profile_is_dropped(self) -> None:
        self.assertIsNone(app_module._requested_profile(json.dumps({"goal": "x"})))
        self.assertIsNone(app_module._requested_profile(None))

    def test_profile_text_is_length_capped(self) -> None:
        cleaned = app_module._requested_profile(json.dumps({
            "name": "n" * 500, "goal": "g" * 900, "guidance": "r" * 5000,
        }))
        self.assertEqual(len(cleaned["name"]), 80)
        self.assertEqual(len(cleaned["goal"]), 400)
        self.assertEqual(len(cleaned["guidance"]), 2000)


class RerunTests(unittest.TestCase):
    def completed_job_with_one_failure(self, store: JobStore) -> dict[str, object]:
        job = store.create("sample.pdf", pdf_bytes(), ["metadata", "moire"])

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if "moire_scan.py" in " ".join(command):
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="detector crashed")
            output = Path(command[command.index("--output") + 1])
            output.write_text(json.dumps({"status": "completed", "risk": "low"}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with patch("backend.service.subprocess.run", side_effect=fake_run):
            run_job(store, job["id"])
        return store.get(job["id"])

    def test_only_the_failed_check_is_reset_and_its_result_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = self.completed_job_with_one_failure(store)
            self.assertEqual(job["analyzer_runs"]["moire"]["status"], "failed")

            reset = store.reset_analyzers(job["id"], ["moire"])

            after = store.get(job["id"])
            self.assertEqual(reset, ["moire"])
            self.assertEqual(after["status"], "queued")
            self.assertNotIn("moire", after["results"])
            self.assertIn("metadata", after["results"])
            self.assertEqual(after["analyzer_runs"]["moire"]["status"], "queued")

    def test_re_running_a_subset_keeps_the_results_that_completed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = self.completed_job_with_one_failure(store)
            store.reset_analyzers(job["id"], ["moire"])

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                output = Path(command[command.index("--json") + 1])
                output.write_text(
                    json.dumps([{
                        "file_verdict": "CLEAN",
                        "images": [{"verdict": "CLEAN", "reason": "no anomalous periodic peaks"}],
                    }]),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("backend.service.subprocess.run", side_effect=fake_run):
                run_job(store, job["id"], ["moire"])

            after = store.get(job["id"])
            self.assertEqual(after["status"], "completed")
            self.assertEqual(set(after["results"]), {"metadata", "moire"})
            self.assertEqual(after["results"]["moire"]["outcome"], "clear")

    def test_a_check_still_in_flight_is_never_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create("sample.pdf", pdf_bytes(), ["metadata"])

            self.assertEqual(store.reset_analyzers(job["id"], ["metadata"]), [])
            self.assertEqual(store.get(job["id"])["status"], "queued")


if __name__ == "__main__":
    unittest.main()
