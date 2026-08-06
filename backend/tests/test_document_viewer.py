from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from fastapi import HTTPException

from backend.service import JobStore
from backend.access_control import AuthorizedUser


app_module = importlib.import_module("backend.app")


class _Request:
    def __init__(self, user_id: str = "user-1") -> None:
        self.state = type("State", (), {})()
        self.state.user = AuthorizedUser(user_id, f"{user_id}@example.com", user_id)


class DocumentViewerTests(unittest.TestCase):
    def test_nested_analyzer_artifacts_are_served_without_allowing_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            test_store = JobStore(Path(directory))
            job = test_store.create("sample.pdf", b"%PDF-1.7", ["tamper_scan"], owner_user_id="user-1")
            artifact = (
                test_store.data_dir
                / f"{job['id']}-tamper_scan-report"
                / job["id"]
                / "page_001_annotated.png"
            )
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"png")
            request = _Request()
            with patch.object(app_module, "store", test_store):
                response = app_module.get_artifact(
                    job["id"], "tamper_scan", f"{job['id']}/page_001_annotated.png", request
                )
                with self.assertRaises(HTTPException) as raised:
                    app_module.get_artifact(job["id"], "tamper_scan", "../sample.pdf", request)

        self.assertEqual(Path(response.path), artifact)
        self.assertEqual(raised.exception.status_code, 404)

    def test_document_is_inline_and_pages_render_as_png(self) -> None:
        document = fitz.open()
        page = document.new_page(width=200, height=300)
        page.insert_text((24, 40), "Document preview")
        payload = document.tobytes()
        document.close()

        with tempfile.TemporaryDirectory() as directory:
            test_store = JobStore(Path(directory))
            job = test_store.create("sample.pdf", payload, ["metadata"], owner_user_id="user-1")
            request = _Request()
            with patch.object(app_module, "store", test_store):
                file_response = app_module.get_document(job["id"], request)
                manifest = app_module.get_document_manifest(job["id"], request)
                page_response = app_module.get_document_page(job["id"], 1, request, 144)

        self.assertTrue(file_response.headers["content-disposition"].startswith("inline;"))
        self.assertEqual(manifest.page_count, 1)
        self.assertEqual(manifest.pages[0].width, 200)
        self.assertEqual(page_response.media_type, "image/png")
        self.assertTrue(page_response.body.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
