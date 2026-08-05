"""Batch-scoped QR-code duplicate detection by hashing decoded payloads.

Unlike photo identity matching (backend/photo_identity.py), this needs no
model and no crop artifacts: the `qr_presence` analyzer already decodes each
QR code to text, so "the same code" is exact string equality on that text,
not a similarity search. Hashing gives a stable join key without repeating
the raw payload as the comparison key everywhere it's handled.

Two documents in one batch carrying the identical QR payload — the same
verification URL, the same encoded reference number — is a much stronger
signal than either document raising it alone (a shared template reused
across forged documents, for instance), which is what this surfaces.
"""

from __future__ import annotations

import hashlib
from typing import Any


def _payload_hash(payload: str) -> str:
    return hashlib.sha256(payload.strip().encode("utf-8")).hexdigest()


def batch_qr_duplicates(jobs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Flag documents whose decoded QR payload was already seen earlier in `jobs`.

    `jobs` must be in "first found" order — batch upload order, matching what
    `store.batch_state()` already returns. Returns one entry per job id that
    decoded at least one QR code:
        {"status": "new" | "duplicate",
         "duplicate_of_job_id": str | None,
         "duplicate_of_filename": str | None,
         "matched_payload": str | None}
    A job that decoded no QR code is absent from the result — callers render
    that row's cells blank rather than treating "no data" as "no match".
    """

    seen: dict[str, tuple[str, str]] = {}  # payload hash -> (job_id, filename)
    outcomes: dict[str, dict[str, Any]] = {}

    for job in jobs:
        job_id = job.get("id")
        if not job_id:
            continue
        result = job.get("results", {}).get("qr_presence") or {}
        raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
        hits = raw.get("hits")
        if not isinstance(hits, list) or not hits:
            continue

        # The QR scanner's own field is "payload"; "data" is accepted too for
        # results built by hand (tests, older stored jobs) that used that name.
        payloads_this_job: list[tuple[str, str]] = []
        best_match: tuple[str, str, str] | None = None  # (matched_payload, job_id, filename)
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            payload = hit.get("payload") or hit.get("data")
            if not payload or not str(payload).strip():
                continue
            payload = str(payload).strip()
            digest = _payload_hash(payload)
            payloads_this_job.append((digest, payload))
            if best_match is None and digest in seen:
                match_job_id, match_filename = seen[digest]
                best_match = (payload, match_job_id, match_filename)

        if not payloads_this_job:
            continue
        filename = str(job.get("filename") or job_id)
        if best_match is not None:
            matched_payload, match_job_id, match_filename = best_match
            outcomes[job_id] = {
                "status": "duplicate",
                "duplicate_of_job_id": match_job_id,
                "duplicate_of_filename": match_filename,
                "matched_payload": matched_payload[:200],
            }
        else:
            outcomes[job_id] = {
                "status": "new",
                "duplicate_of_job_id": None,
                "duplicate_of_filename": None,
                "matched_payload": None,
            }
        # Only remember this job's payloads after matching against everything
        # earlier, and keep the earliest job for a given hash, so "duplicate
        # of" always points back to the first document that carried it.
        for digest, payload in payloads_this_job:
            seen.setdefault(digest, (job_id, filename))

    return outcomes
