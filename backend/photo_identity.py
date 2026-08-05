"""Batch-scoped face identity matching over documents already screened.

Wraps `tools/photo_analysis/extract_photo.py`'s InsightFace backend to answer,
for one batch, "does this document's photo belong to the same person as a
photo already seen earlier in this batch?" The face crops it compares are the
ones `photo_detection` already extracted and saved as artifacts for each
document — this module does no PDF or face-region work of its own, it only
embeds those crops and compares them.

Matching is in-memory and scoped to a single batch_photo_duplicates() call.
There is no persistent index on disk: a batch is small enough (tens to a few
hundred photos) that brute-force cosine similarity is instant, and a
process-wide database would leak matches across unrelated batches.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
_PHOTO_TOOL_DIR = _ROOT / "tools" / "photo_analysis"
if str(_PHOTO_TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(_PHOTO_TOOL_DIR))

import extract_photo  # noqa: E402 - vendored tool lives outside the backend package tree


def _threshold_default() -> float:
    """Cosine similarity at or above which two crops are called the same person.

    The source face_db.py tool used 0.45, tuned for clean passport photos. The
    documents this system actually screens are photocopied and re-scanned
    government forms, where embeddings are far noisier: measured on a real
    two-document batch, the *same* girl photographed twice within one document
    scored 0.378, while two different people scored 0.149 and 0.194. At 0.45
    that genuine match would have been missed, so the default sits below the
    observed same-person score and above the observed different-person scores.

    That is a single measured pair, not a calibration — which is exactly why
    `batch_photo_duplicates` reports the closest score for every document
    whether or not it clears this bar. A reviewer can see a 0.38 near-miss and
    judge it; a threshold that silently swallowed it would be unreviewable.
    Override with PHOTO_SIMILARITY_THRESHOLD once tuned on a real corpus.
    """

    try:
        value = float(os.getenv("PHOTO_SIMILARITY_THRESHOLD", "0.35"))
    except ValueError:
        return 0.35
    return max(0.0, min(1.0, value))


SIMILARITY_THRESHOLD = _threshold_default()


def _detection_threshold_default() -> float:
    """Face-detector confidence floor used when re-detecting inside a saved crop.

    InsightFace defaults to 0.5, which is tuned for finding a face somewhere in
    a full scene. This module asks a different question: the crop it is handed
    is *already known* to be a face region, because `photo_detection` located
    and saved it. Re-detection is only there to get landmarks for alignment, so
    the prior is far stronger than the detector's default assumes.

    At 0.5 the cost is silent data loss. Measured over all 40 photo crops in
    backend/data, 0.5 embedded 32 (80%): five real faces were dropped, among
    them the second copy of the same portrait inside "Copy of 12.pdf" — the
    document's own duplicate photo, invisible to matching. At 0.3 the same
    corpus embeds 37 (93%), and the three that still fail are genuinely not
    faces (a fragment of printed text and a near-black blob that
    `photo_detection` false-positived).

    Lowering it costs nothing in precision here: over all 624 cross-document
    crop pairs the different-person 95th percentile *fell* from 0.275 to 0.252
    and not one pair crossed SIMILARITY_THRESHOLD that did not belong to the
    same person. Padding the crop was tried first and made things worse
    (30/40) — the threshold, not the framing, is what was discarding faces.
    """

    try:
        value = float(os.getenv("PHOTO_IDENTITY_DET_THRESHOLD", "0.3"))
    except ValueError:
        return 0.3
    return max(0.0, min(1.0, value))


DETECTION_THRESHOLD = _detection_threshold_default()


def _get_embedding(crop: np.ndarray, app: Any) -> np.ndarray | None:
    """L2-normalised 512-d embedding for a face crop, or None if unusable.

    Only the InsightFace backend exposes `.embedding` — the OpenCV Haar
    cascade fallback in extract_photo.py has no identity model behind it, so
    callers must not run identity matching against that backend's output.

    A crop can legitimately contain more than one face: these forms carry a
    "joint photo of the applicant and girl" alongside the girl's own photo.
    The highest-confidence detection is taken rather than whichever the
    detector happened to return first, so the same crop always yields the
    same embedding.
    """

    faces = app.get(crop)
    if not faces:
        return None
    face = max(faces, key=lambda item: float(item.det_score))
    embedding = face.embedding.astype("float32")
    norm = float(np.linalg.norm(embedding))
    if not norm:
        return None
    return embedding / norm


def batch_photo_duplicates(
    jobs: list[dict[str, Any]],
    data_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Flag documents whose extracted photo matches a face seen earlier in `jobs`.

    `jobs` must be in the order photos should be considered "first found" —
    batch upload order, which is how `store.batch_state()` already returns
    them. For each job with at least one usable photo_detection crop, this
    embeds every crop and searches it against every embedding collected from
    strictly earlier jobs.

    Returns one entry per job id that had at least one readable photo crop:
        {"status": "duplicate" | "no_match" | "first" | "no_face",
         "duplicate_of_job_id": str | None,
         "duplicate_of_filename": str | None,
         "similarity": float | None,
         "photos_detected": int,
         "photos_compared": int}

    The closest earlier document and its score are reported whether or not the
    score clears SIMILARITY_THRESHOLD — `status` says which side of the line
    it fell on. A near-miss is the single most useful thing to put in front of
    a reviewer, and a blank cell cannot be told apart from "this never ran".

    `photos_detected` counts the crops read off disk and `photos_compared` the
    subset that yielded a face embedding. They differ when `photo_detection`
    boxed something that is not a face, and the gap is reported rather than
    hidden: matching on 1 of a document's 2 photos can produce a "no match"
    that a reviewer would otherwise read as "these are different people".
    `status` is "no_face" when photos existed but none could be embedded.

    Only a batch where InsightFace is not installed (extract_photo.py falls
    back to a plain OpenCV cascade with no identity model) yields an empty
    result — that genuinely is "no comparison happened", and a job with no
    photo crops at all is simply absent.
    """

    try:
        app = extract_photo.get_face_app()
    except Exception:
        return {}
    if str(getattr(app, "backend_name", "")).lower() != "insightface":
        return {}
    # Relax the detector for this pass only; see `_detection_threshold_default`.
    # `extract_photo` runs as a subprocess for actual photo detection, so the
    # app object cached in this process is used by nothing but identity
    # matching and cannot leak the looser setting into a detection result.
    try:
        app.det_model.det_thresh = DETECTION_THRESHOLD
    except AttributeError:
        pass

    seen: list[tuple[np.ndarray, str, str]] = []  # (embedding, job_id, filename)
    outcomes: dict[str, dict[str, Any]] = {}

    for job in jobs:
        job_id = job.get("id")
        if not job_id:
            continue
        result = job.get("results", {}).get("photo_detection") or {}
        raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
        photos = raw.get("photos")
        if not isinstance(photos, list) or not photos:
            continue
        report_dir = data_dir / f"{job_id}-photo_detection-report"

        embeddings_this_job: list[np.ndarray] = []
        photos_read = 0
        closest: tuple[float, str, str] | None = None
        for photo in photos:
            if not isinstance(photo, dict) or not photo.get("artifact"):
                continue
            crop = cv2.imread(str(report_dir / str(photo["artifact"])))
            if crop is None:
                continue
            photos_read += 1
            embedding = _get_embedding(crop, app)
            if embedding is None:
                continue
            embeddings_this_job.append(embedding)
            for other_embedding, other_job_id, other_filename in seen:
                score = float(np.dot(embedding, other_embedding))
                if closest is None or score > closest[0]:
                    closest = (score, other_job_id, other_filename)

        filename = str(job.get("filename") or job_id)
        # How much of what the detector called a photo actually reached the
        # comparison. A crop that yields no face is usually one `photo_detection`
        # got wrong, but it can also be a real face the model would not take —
        # either way the reviewer is told, because a document silently matched
        # on half its photos is a false "no match" waiting to happen.
        counts = {
            "photos_detected": photos_read,
            "photos_compared": len(embeddings_this_job),
        }
        if not embeddings_this_job:
            # Photos were found and saved, but none of them held a face this
            # model would embed. That is a different fact from "this document
            # has no photo", and it must not render as a blank cell.
            outcomes[job_id] = {
                "status": "no_face",
                "duplicate_of_job_id": None,
                "duplicate_of_filename": None,
                "similarity": None,
                **counts,
            }
            continue
        if closest is None:
            # Nothing earlier in the batch carried an embeddable photo.
            outcomes[job_id] = {
                "status": "first",
                "duplicate_of_job_id": None,
                "duplicate_of_filename": None,
                "similarity": None,
                **counts,
            }
        else:
            score, match_job_id, match_filename = closest
            outcomes[job_id] = {
                "status": "duplicate" if score >= SIMILARITY_THRESHOLD else "no_match",
                "duplicate_of_job_id": match_job_id,
                "duplicate_of_filename": match_filename,
                "similarity": round(score, 4),
                **counts,
            }
        # Only add this job's embeddings to the pool after it has been matched
        # against everything earlier, so a document never matches itself.
        for embedding in embeddings_this_job:
            seen.append((embedding, job_id, filename))

    return outcomes
