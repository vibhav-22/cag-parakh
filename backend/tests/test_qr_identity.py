from __future__ import annotations

import unittest

from backend.qr_identity import batch_qr_duplicates


def _job(job_id: str, filename: str, payloads: list[str]) -> dict:
    return {
        "id": job_id,
        "filename": filename,
        "results": {
            "qr_presence": {
                "raw": {
                    "hits": [{"page": 1, "payload": payload} for payload in payloads],
                },
            },
        },
    }


class BatchQrDuplicatesTests(unittest.TestCase):
    def test_flags_a_later_document_with_the_same_decoded_payload(self) -> None:
        jobs = [
            _job("job-1", "first.pdf", ["https://verify.example/abc123"]),
            _job("job-2", "second.pdf", ["https://verify.example/abc123"]),
            _job("job-3", "unrelated.pdf", ["https://verify.example/zzz999"]),
        ]

        result = batch_qr_duplicates(jobs)

        self.assertEqual(result["job-1"]["status"], "new")
        self.assertEqual(result["job-2"]["status"], "duplicate")
        self.assertEqual(result["job-2"]["duplicate_of_filename"], "first.pdf")
        self.assertEqual(result["job-2"]["matched_payload"], "https://verify.example/abc123")
        self.assertEqual(result["job-3"]["status"], "new")

    def test_a_job_with_no_decoded_qr_code_is_absent(self) -> None:
        jobs = [_job("job-1", "first.pdf", [])]

        result = batch_qr_duplicates(jobs)

        self.assertEqual(result, {})

    def test_matches_the_legacy_data_field_name(self) -> None:
        jobs = [
            {
                "id": "job-1", "filename": "first.pdf",
                "results": {"qr_presence": {"raw": {"hits": [{"data": "same-value"}]}}},
            },
            {
                "id": "job-2", "filename": "second.pdf",
                "results": {"qr_presence": {"raw": {"hits": [{"data": "same-value"}]}}},
            },
        ]

        result = batch_qr_duplicates(jobs)

        self.assertEqual(result["job-2"]["status"], "duplicate")
        self.assertEqual(result["job-2"]["duplicate_of_filename"], "first.pdf")


if __name__ == "__main__":
    unittest.main()
