from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from backend.service import JobStore
from backend.vlm import analyze_document, answer_document_question
from backend.vlm_documents import VLMDocumentStore


def make_pdf(path: Path, page_texts: list[str]) -> None:
    document = fitz.open()
    for text in page_texts:
        page = document.new_page(width=300, height=400)
        page.insert_text((24, 48), text)
    document.save(path)
    document.close()


class VLMAnalysisTests(unittest.TestCase):
    def test_vlm_is_not_a_screening_analyzer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            job = store.create("sample.pdf", b"%PDF-1.7", None)

        self.assertNotIn("vlm_review", {item["id"] for item in store.available_analyzers()})
        self.assertNotIn("vlm_review", job["analyzers"])

    def test_standalone_document_store_persists_a_separate_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = VLMDocumentStore(Path(directory))
            created = store.create("sample.pdf", b"%PDF-1.7")
            loaded = store.get(created["id"])

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["filename"], "sample.pdf")

    def test_large_document_review_reports_partial_coverage_and_regions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.pdf"
            make_pdf(path, ["Identity page", "Address page", "Signature page"])
            response = json.dumps({
                "document_type": "application",
                "summary": "One date needs review.",
                "risk": "medium",
                "findings": [{
                    "page": 1,
                    "category": "date_inconsistency",
                    "observation": "The printed dates differ.",
                    "severity": "medium",
                    "bbox": [0.1, 0.2, 0.5, 0.3],
                }],
                "limitations": [],
            })
            with patch("backend.vlm._chat_completion", return_value=response):
                result = analyze_document(path, {}, {"max_pages": 2, "dpi": 96})

        self.assertEqual(result["pages_analyzed"], 2)
        self.assertEqual(result["total_pages"], 3)
        self.assertEqual(result["risk"], "medium")
        self.assertFalse(result["passed"])
        self.assertEqual(result["findings"][0]["bbox"], [0.1, 0.2, 0.5, 0.3])
        self.assertTrue(any("2 representative pages" in item for item in result["limitations"]))

    def test_question_retrieves_relevant_page_and_filters_bad_citations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.pdf"
            make_pdf(path, ["Name: Asha", "Registration number: ZX-2048", "Address: Pune"])
            response = json.dumps({
                "answer": "The registration number is ZX-2048.",
                "confidence": "high",
                "citations": [
                    {"page": 2, "evidence": "Registration number: ZX-2048"},
                    {"page": 99, "evidence": "invalid"},
                ],
                "limitations": [],
            })
            with patch("backend.vlm._chat_completion", return_value=response):
                result = answer_document_question(
                    path, "What is the registration number?", {}, max_pages=1, dpi=96
                )

        self.assertEqual(result["retrieved_pages"], [2])
        self.assertEqual([item["page"] for item in result["citations"]], [2])

if __name__ == "__main__":
    unittest.main()
