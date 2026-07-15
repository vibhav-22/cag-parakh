from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from backend.service import JobStore


app_module = importlib.import_module("backend.app")


class DocumentViewerTests(unittest.TestCase):
    def test_document_is_inline_and_pages_render_as_png(self) -> None:
        document = fitz.open()
        page = document.new_page(width=200, height=300)
        page.insert_text((24, 40), "Document preview")
        payload = document.tobytes()
        document.close()

        with tempfile.TemporaryDirectory() as directory:
            test_store = JobStore(Path(directory))
            job = test_store.create("sample.pdf", payload, ["metadata"])
            with patch.object(app_module, "store", test_store):
                file_response = app_module.get_document(job["id"])
                manifest = app_module.get_document_manifest(job["id"])
                page_response = app_module.get_document_page(job["id"], 1, 144)

        self.assertTrue(file_response.headers["content-disposition"].startswith("inline;"))
        self.assertEqual(manifest.page_count, 1)
        self.assertEqual(manifest.pages[0].width, 200)
        self.assertEqual(page_response.media_type, "image/png")
        self.assertTrue(page_response.body.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
