from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.service import JobStore, run_job


class JobStoreTests(unittest.TestCase):
    def test_removed_ink_analyzers_are_not_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            available = {item["id"] for item in store.available_analyzers()}

            self.assertNotIn("overwriting", available)
            self.assertNotIn("stroke_thickness", available)
            with self.assertRaisesRegex(ValueError, "Unknown analyzer"):
                store.create("sample.pdf", b"%PDF-1.7", ["overwriting"])

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
            self.assertEqual(result["outcome"], "clear")
            self.assertEqual(result["risk"], "low")
            self.assertEqual(result["raw"]["status"], "passed")
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["result"], result)
            self.assertIsNotNone(run["started_at"])
            self.assertIsNotNone(run["completed_at"])


if __name__ == "__main__":
    unittest.main()
