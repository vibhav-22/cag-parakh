from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import backend.app as app_module
from fastapi import HTTPException, Response
from backend.access_control import AuthorizedUser
from backend.service import JobStore


class _Request:
    """Stands in for the Starlette request the route reads the account from."""

    def __init__(self, user_id: str) -> None:
        self.state = type("State", (), {})()
        self.state.user = AuthorizedUser(user_id, f"{user_id}@example.com", user_id)


class SQLiteJobStoreTests(unittest.TestCase):
    def test_jobs_batches_and_projects_are_scoped_by_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            project = store.create_project("Alice project", owner_user_id="alice")
            batch = store.create_batch(
                [("alice.pdf", b"%PDF-1.7")],
                ["metadata"],
                project_id=project["id"],
                owner_user_id="alice",
            )
            job_id = batch["job_ids"][0]

            self.assertIsNotNone(store.project_for_user(project["id"], "alice"))
            self.assertIsNotNone(store.batch_state_for_user(batch["id"], "alice"))
            self.assertIsNotNone(store.get_for_user(job_id, "alice"))

            self.assertIsNone(store.project_for_user(project["id"], "bob"))
            self.assertIsNone(store.batch_state_for_user(batch["id"], "bob"))
            self.assertIsNone(store.get_for_user(job_id, "bob"))
            self.assertEqual(store.list_projects_for_user("bob"), [])
            self.assertEqual(store.list_batches_for_user("bob"), [])

    def test_prior_screenings_are_scoped_by_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            payload = b"%PDF-1.7 same"
            alice = store.create("alice.pdf", payload, ["metadata"], owner_user_id="alice")
            store.create("bob.pdf", payload, ["metadata"], owner_user_id="bob")

            alice_priors = store.prior_screenings_for_user(str(alice["sha256"]), "alice")
            bob_priors = store.prior_screenings_for_user(str(alice["sha256"]), "bob")

            self.assertEqual([item["job_id"] for item in alice_priors], [alice["id"]])
            self.assertNotIn(alice["id"], [item["job_id"] for item in bob_priors])

    def test_jobs_use_sqlite_and_hash_sharded_document_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root)
            job = store.create("certificate.pdf", b"%PDF-1.7", ["metadata"])

            document = store.path_for(job["id"])
            relative = document.relative_to(root)
            self.assertTrue((root / JobStore.DATABASE_NAME).is_file())
            self.assertEqual(relative.parts[0], "documents")
            self.assertEqual(len(relative.parts), 4)  # documents/aa/bb/id.pdf
            self.assertEqual(document.read_bytes(), b"%PDF-1.7")
            self.assertEqual(list(root.glob("*.job.json")), [])

            with closing(sqlite3.connect(store.database_path)) as connection:
                indexes = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    )
                }
            self.assertIn("idx_batches_created_id", indexes)
            self.assertIn("idx_batches_project_created_id", indexes)
            self.assertIn("idx_jobs_digest_created_id", indexes)
            self.assertIn("idx_jobs_status_created_id", indexes)

    def test_cursor_and_offset_pagination_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            for index in range(6):
                store.create_batch(
                    [(f"document-{index}.pdf", b"%PDF-1.7")],
                    ["metadata"],
                    name=f"Batch {index}",
                )

            all_batches = store.list_batches()
            first = store.list_batches(limit=2)
            cursor = store.batch_cursor(first[-1])
            second = store.list_batches(limit=2, cursor=cursor)

            self.assertEqual(first + second, all_batches[:4])
            self.assertEqual(store.list_batches(limit=2, offset=2), all_batches[2:4])
            self.assertEqual(len({item["id"] for item in first + second}), 4)
            with self.assertRaisesRegex(ValueError, "either"):
                store.list_batches(limit=2, offset=1, cursor=cursor)

    def test_interrupted_job_keeps_completed_checks_and_requeues_only_pending_ones(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root)
            job = store.create("case.pdf", b"%PDF-1.7", ["metadata", "qr_presence"])
            completed_result = {"outcome": "info", "summary": "Metadata recorded"}
            runs = job["analyzer_runs"]
            runs["metadata"].update({
                "status": "completed",
                "started_at": job["created_at"],
                "completed_at": job["created_at"],
                "result": completed_result,
            })
            runs["qr_presence"].update({
                "status": "running",
                "started_at": job["created_at"],
            })
            store.update(
                job["id"],
                status="running",
                started_at=job["created_at"],
                analyzer_runs=runs,
                results={"metadata": completed_result},
            )

            recovered_store = JobStore(root)
            recovered = recovered_store.get(job["id"])
            assert recovered is not None
            self.assertEqual(recovered["status"], "queued")
            self.assertEqual(recovered["analyzer_runs"]["metadata"]["status"], "completed")
            self.assertEqual(recovered["analyzer_runs"]["qr_presence"]["status"], "queued")
            self.assertEqual(recovered["results"], {"metadata": completed_result})
            self.assertEqual(
                recovered_store.claim_recovery_queue(),
                [(job["id"], ["qr_presence"])],
            )
            self.assertEqual(recovered_store.claim_recovery_queue(), [])

            self.assertIsNotNone(recovered_store.claim_job(job["id"]))
            self.assertIsNone(recovered_store.claim_job(job["id"]))

    def test_legacy_json_and_flat_pdf_are_imported_without_being_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_id = "a" * 32
            batch_id = "b" * 32
            project_id = "c" * 32
            created_at = "2026-01-02T03:04:05+00:00"
            legacy_job = {
                "id": job_id,
                "filename": "legacy.pdf",
                "status": "queued",
                "created_at": created_at,
                "analyzers": ["metadata"],
                "settings": {},
                "analyzer_runs": {
                    "metadata": {
                        "analyzer_id": "metadata",
                        "status": "queued",
                        "queued_at": created_at,
                        "started_at": None,
                        "completed_at": None,
                        "result": None,
                        "error": None,
                    }
                },
                "results": {},
            }
            (root / f"{job_id}.job.json").write_text(json.dumps(legacy_job), encoding="utf-8")
            (root / f"{batch_id}.batch.json").write_text(json.dumps({
                "id": batch_id,
                "name": "Legacy batch",
                "created_at": created_at,
                "project_id": project_id,
                "job_ids": [job_id],
            }), encoding="utf-8")
            (root / f"{project_id}.project.json").write_text(json.dumps({
                "id": project_id,
                "name": "Archive",
                "created_at": created_at,
                "updated_at": created_at,
            }), encoding="utf-8")
            legacy_pdf = root / f"{job_id}.pdf"
            legacy_pdf.write_bytes(b"%PDF-1.7 legacy")

            store = JobStore(root)

            imported = store.get(job_id)
            assert imported is not None
            self.assertTrue(imported["sha256"])
            self.assertEqual(store.batch_state(batch_id)["project_id"], project_id)  # type: ignore[index]
            self.assertEqual(store.path_for(job_id).read_bytes(), legacy_pdf.read_bytes())
            self.assertNotEqual(store.path_for(job_id), legacy_pdf)
            self.assertTrue(legacy_pdf.is_file())
            self.assertTrue((root / f"{job_id}.job.json").is_file())

    def test_history_query_uses_the_compound_project_pagination_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            with closing(sqlite3.connect(store.database_path)) as connection:
                plan = " ".join(
                    str(row[3])
                    for row in connection.execute(
                        "EXPLAIN QUERY PLAN SELECT id FROM batches "
                        "WHERE project_id=? ORDER BY created_at DESC, id DESC LIMIT 50",
                        ("project",),
                    )
                )
            self.assertIn("idx_batches_project_created_id", plan)

    def test_batch_api_exposes_total_and_next_cursor_without_changing_the_array_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            for index in range(3):
                store.create_batch(
                    [(f"document-{index}.pdf", b"%PDF-1.7")], ["metadata"],
                    owner_user_id="alice",
                )
            request = _Request("alice")
            response = Response()
            with patch.object(app_module, "store", store):
                page = app_module.list_batches(request, response, limit=2, offset=0, cursor=None)

                self.assertEqual(len(page), 2)
                self.assertEqual(response.headers["X-Total-Count"], "3")
                cursor = response.headers["X-Next-Cursor"]

                next_response = Response()
                next_page = app_module.list_batches(
                    request, next_response, limit=2, offset=0, cursor=cursor,
                )
                self.assertEqual(len(next_page), 1)
                self.assertNotIn("X-Next-Cursor", next_response.headers)

                with self.assertRaises(HTTPException) as raised:
                    app_module.list_batches(request, Response(), limit=2, offset=0, cursor="not-a-cursor")
                self.assertEqual(raised.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
