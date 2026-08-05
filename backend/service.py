from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

from .checks import criterion_for
from .models import (
    AnalyzerOutcome,
    AnalyzerRunState,
    AnalyzerRunStatus,
    normalize_analyzer_result,
)


ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 360

# QR scanning is by far the most expensive check because it re-renders each page
# at several DPIs and rotations. The web path constrains that search space for
# responsiveness; override with env vars if you need exhaustive recall.
QR_DPIS = os.getenv("QR_DPIS", "300").strip()
QR_ROTATIONS = os.getenv("QR_ROTATIONS", "0,90").strip()

# Escalation for when the fast pass finds nothing. Measured on a real UP board
# marksheet in batch dc13f4ce…: its QR sits on page 5 and decodes only at
# 350 DPI, rotation 270. The fast pass (300 DPI, rotations 0/90) missed it
# entirely, and so reported "expected at least 1 QR code, found 0" — a check
# failure caused by the search space, not by the document.
#
# 300 DPI does not find that code at *any* rotation, so widening rotations
# alone is not enough; the DPI sweep is what matters. Paying for the wide
# sweep only after a miss keeps documents whose code decodes on the first
# pass fast, while making "no QR code found" mean the document, not the
# settings. Set QR_DEEP_RESCAN=0 to disable.
QR_DEEP_DPIS = os.getenv("QR_DEEP_DPIS", "250,350,450").strip()
QR_DEEP_ROTATIONS = os.getenv("QR_DEEP_ROTATIONS", "0,90,180,270").strip()
QR_DEEP_RESCAN = os.getenv("QR_DEEP_RESCAN", "1").strip() not in ("0", "false", "no")

# The wide sweep needs its own budget. TIMEOUT_SECONDS (360) sizes a single
# fast analyzer pass; the deep sweep re-renders every page at each rung of the
# ladder and runs 16 decode passes per render. Measured on the 5-page
# marksheet with a 5-rung ladder: 474s — which silently blew the 360s budget,
# so the escalation was killed and the fast pass's "found 0" stood.
QR_DEEP_TIMEOUT_SECONDS = max(60, int(os.getenv("QR_DEEP_TIMEOUT_SECONDS", "1200")))

# How hard the QR check looks before it will report a document as having no
# code. The trade is real in both directions: the fast pass alone decoded the
# codes in most documents screened so far, but missed a genuine UP board
# marksheet code that needed a DPI and rotation the fast pass never tried.
#
#   low    - fast pass only. Cheapest. "No QR code" may mean "not found yet".
#   medium - fast pass, then the wide sweep only if it found nothing.
#   high   - always run the wide sweep, and widen the ladder further.
#
# `rescan` is whether a miss escalates; `always` skips the fast pass short
# circuit; `multipliers` are applied to the document's native scan resolution
# (see qr_dpi_ladder).
QR_EFFORT_LEVELS: dict[str, dict[str, Any]] = {
    "low": {"rescan": False, "always": False, "multipliers": ()},
    "medium": {"rescan": True, "always": False, "multipliers": (1.0, 1.2, 1.4, 1.6, 1.8)},
    "high": {
        "rescan": True,
        "always": True,
        "multipliers": (0.8, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.8, 2.0),
    },
}
QR_DEFAULT_EFFORT = "medium"

# Run a job's detectors concurrently. Default to the analyzer count (capped),
# leaving headroom on the box for other jobs.
ANALYZER_WORKERS = max(1, int(os.getenv("ANALYZER_WORKERS", "8")))

# Each detector uses numpy/opencv, which spin up their own thread pools. With
# several detectors running at once that oversubscribes the CPU and slows every
# one of them, so pin each child's math libraries to a single thread.
_CHILD_THREAD_LIMITS = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "OPENCV_NUM_THREADS": "1",
}
ANALYZERS: dict[str, dict[str, Any]] = {
    "metadata": {"script": "tools/metadata_analysis/metadata_check.py", "kind": "json", "flag": "--output", "description": "PDF metadata and structural editing signals"},
    "qr_presence": {"script": "tools/qr_analysis/qr_exists.py", "kind": "json", "flag": "--out", "description": "QR code presence and decoded payloads"},
    "font_analysis": {"script": "tools/font_analysis/pp.py", "kind": "font", "description": "Embedded font, typeface, and font-usage extraction"},
    "moire": {"script": "tools/moire_analysis/moire_scan.py", "kind": "json", "flag": "--json", "description": "Recapture and moire-pattern screening"},
    "same_phone": {"script": "tools/capture_analysis/same_phone/same_phone_pdf_check.py", "kind": "report_dir", "report": "same_phone_pdf_report.json", "description": "Same-phone/capture-workflow compatibility"},
    "tamper_scan": {"script": "tools/tamper_analysis/tamper_detect_local.py", "kind": "whitener", "description": "Correction-fluid (whitener) probability and region detection"},
    "readability": {"script": "tools/readability_analysis/pdf_readability_checker.py", "kind": "json", "flag": "--output", "description": "Machine readability and scan-quality checks"},
    "photo_detection": {"script": "tools/photo_analysis/extract_photo.py", "kind": "json", "flag": "--output", "description": "Passport-style document photo extraction and face-quality checks"},
    "signature": {"script": "tools/signature_analysis/signature_presence_check.py", "kind": "json", "flag": "--output", "description": "Handwritten signature presence and location"},
}
CHECK_CONTRACT_VERSION = 3

DEFAULT_ANALYSIS_SETTINGS: dict[str, dict[str, Any]] = {
    "metadata": {"dpi": 200},
    "qr_presence": {
        "dpi": 300,
        "min_codes": 1,
        "max_pages": 0,
        "rotations": [0, 90],
        "stop_after_first": False,
        # How hard to look before concluding a document has no QR code. See
        # QR_EFFORT_LEVELS. "medium" is the default: the fast pass first, and
        # the wide sweep only when the fast pass finds nothing.
        "effort": "medium",
    },
    "same_phone": {"dpi": 180, "max_analysis_side": 1800},
    "tamper_scan": {"dpi": 200, "review_threshold": 0.35, "annotate_all": False},
    "readability": {"dpi": 150, "noise_threshold": 8.0, "sharpness_threshold": 500.0},
    "signature": {"dpi": 200, "confidence": 0.55, "min_signatures": 0},
    # How hard to look for document photos. See PHOTO_EFFORT_LEVELS in
    # tools/photo_analysis/extract_photo.py. "medium" is the default and is the
    # behaviour this detector always had: the embedded-image pass first, and a
    # full page render only when that finds nothing.
    "photo_detection": {"effort": "medium"},
}
PHOTO_EFFORT_LEVELS = ("low", "medium", "high")
PHOTO_DEFAULT_EFFORT = "medium"


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _bounded_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def sanitize_analysis_settings(value: Any) -> dict[str, dict[str, Any]]:
    """Return only supported detector settings, clamped to safe operating ranges."""

    supplied = value if isinstance(value, dict) else {}
    settings = json.loads(json.dumps(DEFAULT_ANALYSIS_SETTINGS))
    metadata = supplied.get("metadata") if isinstance(supplied.get("metadata"), dict) else {}
    qr = supplied.get("qr_presence") if isinstance(supplied.get("qr_presence"), dict) else {}
    phone = supplied.get("same_phone") if isinstance(supplied.get("same_phone"), dict) else {}
    tamper = supplied.get("tamper_scan") if isinstance(supplied.get("tamper_scan"), dict) else {}
    readability = supplied.get("readability") if isinstance(supplied.get("readability"), dict) else {}
    signature = supplied.get("signature") if isinstance(supplied.get("signature"), dict) else {}
    photo = supplied.get("photo_detection") if isinstance(supplied.get("photo_detection"), dict) else {}

    settings["metadata"]["dpi"] = _bounded_int(metadata.get("dpi"), 200, 96, 400)
    effort = str(qr.get("effort", QR_DEFAULT_EFFORT)).strip().lower()
    settings["qr_presence"].update({
        "dpi": _bounded_int(qr.get("dpi"), 300, 96, 600),
        "min_codes": _bounded_int(qr.get("min_codes"), 1, 0, 20),
        "max_pages": _bounded_int(qr.get("max_pages"), 0, 0, 100),
        "stop_after_first": bool(qr.get("stop_after_first", False)),
        "effort": effort if effort in QR_EFFORT_LEVELS else QR_DEFAULT_EFFORT,
    })
    rotations_value = qr.get("rotations", [0, 90])
    if isinstance(rotations_value, str):
        rotations_value = rotations_value.split(",")
    rotations = []
    if isinstance(rotations_value, list):
        for item in rotations_value:
            rotation = _bounded_int(item, -1, -1, 270)
            if rotation in (0, 90, 180, 270) and rotation not in rotations:
                rotations.append(rotation)
    settings["qr_presence"]["rotations"] = rotations or [0, 90]

    settings["same_phone"].update({
        "dpi": _bounded_int(phone.get("dpi"), 180, 96, 400),
        "max_analysis_side": _bounded_int(phone.get("max_analysis_side"), 1800, 800, 4000),
    })
    settings["tamper_scan"].update({
        "dpi": _bounded_int(tamper.get("dpi"), 200, 96, 400),
        "review_threshold": _bounded_float(tamper.get("review_threshold"), 0.35, 0.1, 0.9),
        "annotate_all": bool(tamper.get("annotate_all", False)),
    })
    settings["readability"].update({
        "dpi": _bounded_int(readability.get("dpi"), 150, 96, 300),
        "noise_threshold": _bounded_float(readability.get("noise_threshold"), 8.0, 1.0, 50.0),
        "sharpness_threshold": _bounded_float(readability.get("sharpness_threshold"), 500.0, 50.0, 5000.0),
    })
    settings["signature"].update({
        "dpi": _bounded_int(signature.get("dpi"), 200, 96, 600),
        "confidence": _bounded_float(signature.get("confidence"), 0.55, 0.05, 0.95),
        "min_signatures": _bounded_int(signature.get("min_signatures"), 0, 0, 20),
    })
    photo_effort = str(photo.get("effort", PHOTO_DEFAULT_EFFORT)).strip().lower()
    settings["photo_detection"]["effort"] = (
        photo_effort if photo_effort in PHOTO_EFFORT_LEVELS else PHOTO_DEFAULT_EFFORT
    )
    return settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_batch_name(filenames: list[str]) -> str:
    """Name a batch from what it screens, so an unnamed run is still findable.

    Filenames are what a reviewer actually remembers about a run days later —
    an id or timestamp is not. This never returns an empty string.
    """

    stems = [Path(item).stem.strip() for item in filenames if item and item.strip()]
    if not stems:
        return "Untitled batch"
    if len(stems) == 1:
        return stems[0][:80]
    return f"{stems[0][:60]} +{len(stems) - 1} more"


def content_digest(payload: bytes) -> str:
    """SHA-256 of the exact bytes screened. The duplicate-detection join key."""

    return hashlib.sha256(payload).hexdigest()


def _file_digest(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except OSError:
        return None


def _finalize_incomplete(job: dict[str, Any], message: str) -> None:
    """Close out a job whose remaining analyzer runs can no longer finish."""

    now = _now()
    runs = job.setdefault("analyzer_runs", {})
    results = job.setdefault("results", {})
    for analyzer, run in runs.items():
        if run.get("status") in ("completed", "failed"):
            continue
        normalized = normalize_analyzer_result(analyzer, {"status": "error", "detail": message})
        results[analyzer] = normalized.model_dump(mode="json")
        queued_at = run.get("queued_at") or now
        runs[analyzer] = AnalyzerRunState(
            analyzer_id=analyzer,
            status=AnalyzerRunStatus.FAILED,
            queued_at=queued_at,
            started_at=run.get("started_at") or queued_at,
            completed_at=now,
            result=normalized,
            error=normalized.summary,
        ).model_dump(mode="json")
    job["status"] = "completed"
    job.setdefault("started_at", job.get("created_at") or now)
    job["completed_at"] = now


def pdf_page_sizes(path: Path) -> dict[int, tuple[float, float]]:
    """Return PDF page dimensions in points, keyed by one-based page number."""

    with fitz.open(path) as document:
        return {
            index + 1: (float(page.rect.width), float(page.rect.height))
            for index, page in enumerate(document)
        }


def native_scan_dpi(path: Path) -> int | None:
    """The highest resolution any embedded page scan actually holds, in DPI.

    A scanned page carries one big raster image; how many pixels it has per
    page inch is the only resolution that exists in the file. Rendering above
    it interpolates and below it discards modules. Returns None for a PDF with
    no usable raster page image (a born-digital text PDF), where the caller
    should keep its fixed ladder.

    The image is measured against the page box in whichever orientation fits:
    these scans are frequently landscape rasters placed on a portrait page via
    a rotation transform, and measuring width-against-width would report a
    wildly anisotropic DPI that means nothing.
    """

    best: float = 0.0
    try:
        with fitz.open(path) as document:
            for page in document:
                width_in = float(page.rect.width) / 72.0
                height_in = float(page.rect.height) / 72.0
                if width_in <= 0 or height_in <= 0:
                    continue
                for info in page.get_images(full=True):
                    try:
                        image = document.extract_image(int(info[0]))
                    except (KeyError, RuntimeError, ValueError):
                        continue
                    pixel_width = float(image.get("width") or 0)
                    pixel_height = float(image.get("height") or 0)
                    if pixel_width <= 0 or pixel_height <= 0:
                        continue
                    upright = min(pixel_width / width_in, pixel_height / height_in)
                    rotated = min(pixel_width / height_in, pixel_height / width_in)
                    best = max(best, upright, rotated)
    except (OSError, RuntimeError, ValueError):
        return None
    if best < 50 or best > 1200:
        return None
    return int(round(best))


def qr_dpi_ladder(path: Path, effort: str = QR_DEFAULT_EFFORT) -> str | None:
    """DPI values to try for QR decoding, derived from the document's own scan.

    Measured on the UP board marksheet in batch dc13f4ce… (page 5, native
    ~250 DPI), decoding succeeded only at 350 and 400 DPI — 250, 300, 450,
    500 and 600 all failed, and no adaptive-threshold block size rescued the
    ones that failed. The usable window was roughly 1.4x-1.6x the resolution
    the scan actually holds: below it there are too few pixels per QR module
    for the decoder to sample, and above it the render is interpolating pixels
    the scan never contained.

    A fixed absolute ladder cannot hit that window across documents, because
    it moves with each scan's own resolution. This returns the native
    resolution plus multiples spanning that window.
    """

    multipliers = QR_EFFORT_LEVELS.get(effort, QR_EFFORT_LEVELS[QR_DEFAULT_EFFORT])["multipliers"]
    if not multipliers:
        return None
    native = native_scan_dpi(path)
    if native is None:
        return None
    ladder: list[int] = []
    for multiplier in multipliers:
        value = int(round(native * multiplier / 10.0) * 10)
        value = max(150, min(900, value))
        if value not in ladder:
            ladder.append(value)
    return ",".join(str(value) for value in ladder)


def _load_whitener_result(
    report_dir: Path,
    input_path: Path,
    completed: subprocess.CompletedProcess[str],
) -> dict[str, Any]:
    """Adapt the whitener detector report to the website's analyzer contract."""

    detector_dir = report_dir / input_path.stem
    report_path = detector_dir / "report.json"
    detail = (completed.stderr or completed.stdout)[-2000:]
    if completed.returncode != 0 or not report_path.is_file():
        return {
            "status": "error",
            "exit_code": completed.returncode,
            "detail": detail or "Whitener detector did not produce a report.",
        }

    report = json.loads(report_path.read_text(encoding="utf-8"))
    probability = float(report.get("document_probability") or 0.0)
    pages = report.get("pages") if isinstance(report.get("pages"), list) else []
    indeterminate_pages = sum(
        bool(page.get("indeterminate")) for page in pages if isinstance(page, dict)
    )
    all_indeterminate = bool(pages) and indeterminate_pages == len(pages)
    if all_indeterminate:
        risk, verdict = "unknown", "insufficient_signal"
    elif probability >= 0.6:
        risk, verdict = "high", "likely_whitener"
    elif probability >= 0.35:
        risk, verdict = "medium", "possible_whitener"
    else:
        risk, verdict = "low", "no_whitener_evidence"

    regions: list[dict[str, Any]] = []
    for page_result in pages:
        if not isinstance(page_result, dict):
            continue
        page = int(page_result.get("page") or 1)
        size = page_result.get("size_px")
        if not isinstance(size, list) or len(size) < 2 or not size[0] or not size[1]:
            continue
        for item in page_result.get("regions", []):
            bbox = item.get("bbox_px") if isinstance(item, dict) else None
            if not isinstance(bbox, list) or len(bbox) < 4:
                continue
            confidence = float(item.get("confidence") or 0.0)
            severity = "high" if confidence >= 0.6 else "medium" if confidence >= 0.35 else "low"
            label = (
                "Likely whitener patch" if confidence >= 0.6
                else "Possible whitener patch" if confidence >= 0.35
                else "Low-confidence whitener candidate"
            )
            regions.append({
                "page": page,
                "kind": "whitener_patch",
                "label": label,
                "reason": f"Local correction-fluid confidence {confidence:.0%}.",
                "severity": severity,
                "confidence": confidence,
                "bbox_normalized": {
                    "x0": float(bbox[0]) / float(size[0]),
                    "y0": float(bbox[1]) / float(size[1]),
                    "x1": float(bbox[2]) / float(size[0]),
                    "y1": float(bbox[3]) / float(size[1]),
                },
            })

    artifacts = [
        path.relative_to(report_dir).as_posix()
        for path in detector_dir.rglob("*")
        if path.is_file() and path != report_path
    ]
    report.update({
        "status": "inconclusive" if all_indeterminate else "completed",
        "passed": None if all_indeterminate else probability < 0.35,
        "risk": risk,
        "verdict": verdict,
        "exit_code": completed.returncode,
        "pages_analyzed": len(pages),
        "pages_with_evidence": sum(bool(page.get("regions")) for page in pages if isinstance(page, dict)),
        "indeterminate_pages": indeterminate_pages,
        "suspicious_regions_count": len(regions),
        "regions": regions,
        "artifacts": artifacts,
    })
    # The detector's original values are absolute local paths; expose only the
    # safe artifact paths served by this job.
    report["outputs"] = {"artifacts": artifacts}
    return report


class JobStore:
    """Thread-safe job registry persisted as JSON files beside the uploads."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._batches: dict[str, dict[str, Any]] = {}
        self._projects: dict[str, dict[str, Any]] = {}
        # sha256 -> job ids, oldest first. Powers the intake pre-flight warning
        # that this exact file has been screened before.
        self._by_digest: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self._load()

    def _job_path(self, job_id: str) -> Path:
        return self.data_dir / f"{job_id}.job.json"

    def _batch_path(self, batch_id: str) -> Path:
        return self.data_dir / f"{batch_id}.batch.json"

    def _project_path(self, project_id: str) -> Path:
        return self.data_dir / f"{project_id}.project.json"

    @staticmethod
    def _write_state(path: Path, payload: dict[str, Any]) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)

    def _load(self) -> None:
        """Restore persisted jobs and batches from previous service runs."""

        for path in sorted(self.data_dir.glob("*.job.json")):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(job, dict) or not job.get("id"):
                continue
            dirty = False
            if job.get("status") != "completed":
                _finalize_incomplete(
                    job, "The service restarted before this check finished."
                )
                dirty = True
            if not job.get("sha256"):
                # Jobs stored before digests existed still need to participate in
                # duplicate detection, so hash the document we already kept. The
                # original upload is gone, so for a job that came from an image
                # this is the digest of the converted PDF and will not match a
                # fresh upload of that image. Only legacy records are affected.
                digest = _file_digest(self.path_for(job["id"]))
                if digest:
                    job["sha256"] = digest
                    dirty = True
            if self._migrate_legacy_checks(job):
                dirty = True
            if dirty:
                self._write_state(self._job_path(job["id"]), job)
            self._jobs[job["id"]] = job
            if job.get("sha256"):
                self._by_digest.setdefault(str(job["sha256"]), []).append(job["id"])
        for path in sorted(self.data_dir.glob("*.project.json")):
            try:
                project = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(project, dict) or not project.get("id"):
                continue
            self._projects[project["id"]] = project
        for path in sorted(self.data_dir.glob("*.batch.json")):
            try:
                batch = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(batch, dict) or not batch.get("id"):
                continue
            batch["job_ids"] = [
                job_id for job_id in batch.get("job_ids", []) if job_id in self._jobs
            ]
            if batch["job_ids"]:
                # A batch whose project file is gone is unfiled, not lost. Self-
                # heals a project deleted out of band; delete_project already
                # does this in-process.
                if batch.get("project_id") and batch["project_id"] not in self._projects:
                    batch["project_id"] = None
                    self._write_state(self._batch_path(batch["id"]), batch)
                self._batches[batch["id"]] = batch

    def _migrate_legacy_checks(self, job: dict[str, Any]) -> bool:
        """Backfill the per-detector `check` verdict onto results saved before it existed.

        checks.py's evaluators are pure functions of a detector's own `raw`
        output, so a job screened before that criterion/reason/facts rule
        existed (or before a rule changed) can be re-graded from what is
        already on disk, with no re-run. Without this, results like the
        reason behind an `inconclusive` reading stay blank forever for every
        job screened before the rule shipped.
        """

        results = job.get("results")
        if not isinstance(results, dict):
            return False
        saved_version = int(job.get("check_contract_version") or 0)
        pending = [
            analyzer_id for analyzer_id, result in results.items()
            if isinstance(result, dict)
            and ("check" not in result or saved_version < CHECK_CONTRACT_VERSION)
        ]
        if not pending:
            return False
        try:
            page_sizes = pdf_page_sizes(self.path_for(job["id"]))
        except (OSError, RuntimeError, ValueError):
            page_sizes = {}
        analyzer_runs = job.get("analyzer_runs")
        if not isinstance(analyzer_runs, dict):
            analyzer_runs = {}
        for analyzer_id in pending:
            result = results[analyzer_id]
            normalized = normalize_analyzer_result(
                analyzer_id,
                result.get("raw", {}) if isinstance(result.get("raw"), dict) else {},
                exit_code=result.get("exit_code"),
                page_sizes=page_sizes,
            ).model_dump(mode="json")
            results[analyzer_id] = normalized
            run = analyzer_runs.get(analyzer_id)
            if isinstance(run, dict) and isinstance(run.get("result"), dict):
                run["result"] = normalized
        job["check_contract_version"] = CHECK_CONTRACT_VERSION
        return True

    def available_analyzers(self) -> list[dict[str, Any]]:
        return [
            {
                "id": key,
                "description": spec["description"],
                # What earns this check a tick, stated before anything is run.
                "criterion": criterion_for(key),
                "available": True,
                "availability_message": None,
            }
            for key, spec in ANALYZERS.items()
        ]

    def create(
        self,
        filename: str,
        payload: bytes,
        requested: list[str] | None,
        settings: dict[str, dict[str, Any]] | None = None,
        digest: str | None = None,
        profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register a job for `payload`.

        `digest` is the hash of the file as the user supplied it, which is not
        always the hash of `payload`: an uploaded image is normalized to PDF
        first, and that conversion is not byte-stable. Callers holding the
        original bytes must pass it, or duplicate detection silently stops
        working for every non-PDF upload.

        `profile` is the named screening test this run was started under, stamped
        onto the job so the case remembers the goal and decision rule it was
        judged against.
        """

        selected = requested or list(ANALYZERS)
        unknown = sorted(set(selected) - set(ANALYZERS))
        if unknown:
            raise ValueError(f"Unknown analyzer(s): {', '.join(unknown)}")
        job_id = uuid.uuid4().hex
        upload_path = self.data_dir / f"{job_id}.pdf"
        upload_path.write_bytes(payload)
        created_at = _now()
        job = {
            "id": job_id,
            "filename": Path(filename).name,
            "sha256": digest or content_digest(payload),
            "profile": profile,
            "status": "queued",
            "created_at": created_at,
            "check_contract_version": CHECK_CONTRACT_VERSION,
            "analyzers": selected,
            "settings": sanitize_analysis_settings(settings),
            "analyzer_runs": {
                analyzer: AnalyzerRunState(
                    analyzer_id=analyzer,
                    queued_at=created_at,
                ).model_dump(mode="json")
                for analyzer in selected
            },
            "results": {},
        }
        with self._lock:
            self._jobs[job_id] = job
            self._by_digest.setdefault(job["sha256"], []).append(job_id)
            self._write_state(self._job_path(job_id), job)
        return job.copy()

    def create_batch(
        self,
        uploads: list[tuple[str, bytes]] | list[tuple[str, bytes, str]],
        requested: list[str] | None,
        settings: dict[str, dict[str, Any]] | None = None,
        profile: dict[str, Any] | None = None,
        name: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Create one job per upload. A third tuple element is the file digest."""

        # Validate before creating a single job: a project deleted mid-upload
        # must not leave orphaned job files behind it.
        if project_id:
            with self._lock:
                if project_id not in self._projects:
                    raise ValueError("That project no longer exists.")
        jobs = [
            self.create(
                item[0], item[1], requested, settings,
                item[2] if len(item) > 2 else None, profile,
            )
            for item in uploads
        ]
        cleaned_name = (name or "").strip()
        batch = {
            "id": uuid.uuid4().hex,
            "name": cleaned_name or _default_batch_name([job["filename"] for job in jobs]),
            "created_at": _now(),
            "project_id": project_id,
            "job_ids": [job["id"] for job in jobs],
        }
        with self._lock:
            self._batches[batch["id"]] = batch
            self._write_state(self._batch_path(batch["id"]), batch)
        return batch.copy()

    @staticmethod
    def _batch_status(jobs: list[dict[str, Any]]) -> str:
        if jobs and all(job["status"] == "completed" for job in jobs):
            return "completed"
        if any(job["status"] in ("running", "completed") for job in jobs):
            return "running"
        return "queued"

    def batch_state(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            batch = self._batches.get(batch_id)
            batch = dict(batch) if batch else None
        if batch is None:
            return None
        jobs = [job for job_id in batch["job_ids"] if (job := self.get(job_id))]
        return {
            "id": batch["id"],
            "name": batch.get("name") or _default_batch_name([job["filename"] for job in jobs]),
            "created_at": batch["created_at"],
            "project_id": batch.get("project_id"),
            "status": self._batch_status(jobs),
            "document_count": len(jobs),
            "jobs": jobs,
        }

    @classmethod
    def _batch_summary(cls, batch: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
        """The BatchSummary projection. One definition, shared by the library
        list and the per-project list, so the two can never disagree."""

        return {
            "id": batch["id"],
            "name": batch.get("name") or _default_batch_name([job["filename"] for job in jobs]),
            "created_at": batch["created_at"],
            "project_id": batch.get("project_id"),
            "status": cls._batch_status(jobs),
            "document_count": len(jobs),
            "completed_documents": sum(job["status"] == "completed" for job in jobs),
            # A document marked unanalyzable has left the review queue, so it
            # must stop counting as outstanding flagged work.
            "flagged_documents": sum(
                not job.get("unanalyzable")
                and any(
                    result.get("outcome") in ("review", "error")
                    for result in job.get("results", {}).values()
                )
                for job in jobs
            ),
        }

    def list_batches(self) -> list[dict[str, Any]]:
        with self._lock:
            batches = [dict(batch) for batch in self._batches.values()]
        summaries = []
        for batch in sorted(batches, key=lambda item: str(item["created_at"]), reverse=True):
            jobs = [job for job_id in batch["job_ids"] if (job := self.get(job_id))]
            if not jobs:
                continue
            summaries.append(self._batch_summary(batch, jobs))
        return summaries

    def list_project_batches(self, project_id: str) -> list[dict[str, Any]]:
        """Batches filed under one project, newest first. Same shape as
        list_batches() — built from the same _batch_summary()."""

        with self._lock:
            batches = [
                dict(batch) for batch in self._batches.values()
                if batch.get("project_id") == project_id
            ]
        summaries = []
        for batch in sorted(batches, key=lambda item: str(item["created_at"]), reverse=True):
            jobs = [job for job_id in batch["job_ids"] if (job := self.get(job_id))]
            if not jobs:
                continue
            summaries.append(self._batch_summary(batch, jobs))
        return summaries

    def create_project(self, name: str) -> dict[str, Any]:
        """Create an empty project. Raises ValueError on a blank name."""

        cleaned = (name or "").strip()[:80]
        if not cleaned:
            raise ValueError("Give the project a name.")
        now = _now()
        project = {"id": uuid.uuid4().hex, "name": cleaned, "created_at": now, "updated_at": now}
        with self._lock:
            self._projects[project["id"]] = project
            self._write_state(self._project_path(project["id"]), project)
        return project.copy()

    def project(self, project_id: str) -> dict[str, Any] | None:
        with self._lock:
            project = self._projects.get(project_id)
            return dict(project) if project else None

    def rename_project(self, project_id: str, name: str) -> dict[str, Any] | None:
        """Rename a project. None when unknown; ValueError on a blank name."""

        cleaned = (name or "").strip()[:80]
        if not cleaned:
            raise ValueError("Give the project a name.")
        with self._lock:
            project = self._projects.get(project_id)
            if project is None:
                return None
            project["name"] = cleaned
            project["updated_at"] = _now()
            self._write_state(self._project_path(project_id), project)
            return project.copy()

    def delete_project(self, project_id: str) -> bool:
        """Delete a project and unfile — never delete — the batches inside it."""

        with self._lock:
            if project_id not in self._projects:
                return False
            del self._projects[project_id]
            for batch in self._batches.values():
                if batch.get("project_id") == project_id:
                    batch["project_id"] = None
                    self._write_state(self._batch_path(batch["id"]), batch)
            self._project_path(project_id).unlink(missing_ok=True)
            return True

    def list_projects(self) -> list[dict[str, Any]]:
        """Projects newest first, each with a live batch count and last
        activity, derived from list_batches() so the two views can never
        disagree about how many batches a project holds."""

        with self._lock:
            projects = [dict(project) for project in self._projects.values()]
        counts: dict[str, int] = {}
        latest: dict[str, str] = {}
        for summary in self.list_batches():  # lock already released
            project_id = summary.get("project_id")
            if not project_id:
                continue
            counts[project_id] = counts.get(project_id, 0) + 1
            if project_id not in latest:  # list_batches() is newest-first
                latest[project_id] = summary["created_at"]
        return [
            {
                **project,
                "batch_count": counts.get(project["id"], 0),
                "last_activity_at": latest.get(project["id"]),
            }
            for project in sorted(projects, key=lambda item: str(item["created_at"]), reverse=True)
        ]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return json.loads(json.dumps(job)) if job else None

    def batch_id_for_job(self, job_id: str) -> str | None:
        with self._lock:
            for batch in self._batches.values():
                if job_id in batch.get("job_ids", []):
                    return str(batch["id"])
        return None

    def prior_screenings(self, digest: str, exclude_job_id: str | None = None) -> list[dict[str, Any]]:
        """Every earlier screening of byte-identical content, newest first.

        A hit means "this exact file has been through here before" and nothing
        more. Two captures of the same certificate are two different files, so
        this can never be phrased as "another reviewer screened this document".
        """

        from .reporting import machine_verdict  # local: reporting imports nothing from here

        with self._lock:
            job_ids = list(self._by_digest.get(digest, []))
        matches = []
        for job_id in job_ids:
            if job_id == exclude_job_id:
                continue
            job = self.get(job_id)
            if job is None:
                continue
            review = job.get("review") if isinstance(job.get("review"), dict) else None
            matches.append({
                "job_id": job_id,
                "batch_id": self.batch_id_for_job(job_id),
                "filename": job.get("filename"),
                "created_at": job.get("created_at"),
                "machine_verdict": machine_verdict(job),
                "review_decision": (review or {}).get("decision"),
            })
        matches.sort(key=lambda item: str(item["created_at"]), reverse=True)
        return matches

    def reset_analyzers(self, job_id: str, analyzers: list[str]) -> list[str]:
        """Return the named analyzers to the queue so the job can run again.

        Only checks that actually finished may be reset; a run still in flight
        would otherwise have its own completion overwrite the reset state.
        """

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return []
            runs = job.setdefault("analyzer_runs", {})
            results = job.setdefault("results", {})
            reset: list[str] = []
            for analyzer in analyzers:
                run = runs.get(analyzer)
                if not run or run.get("status") not in ("completed", "failed"):
                    continue
                runs[analyzer] = AnalyzerRunState(
                    analyzer_id=analyzer,
                    queued_at=_now(),
                ).model_dump(mode="json")
                results.pop(analyzer, None)
                reset.append(analyzer)
            if not reset:
                return []
            job["status"] = "queued"
            job["completed_at"] = None
            job["unanalyzable"] = False
            job["unanalyzable_reason"] = None
            self._write_state(self._job_path(job_id), job)
            return reset

    def update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(changes)
            self._write_state(self._job_path(job_id), job)

    def path_for(self, job_id: str) -> Path:
        return self.data_dir / f"{job_id}.pdf"


def _analyzer_command(
    analyzer: str,
    spec: dict[str, Any],
    input_path: Path,
    output_path: Path,
    report_dir: Path,
    settings: dict[str, Any],
) -> tuple[list[str], Path]:
    """Build the subprocess command and the path its result lands in."""

    command = [sys.executable, str(ROOT / spec["script"]), str(input_path)]
    if analyzer == "metadata":
        command += ["--dpi", str(settings["dpi"])]
    elif analyzer == "qr_presence":
        command += ["--dpi", str(settings["dpi"]), "--rotations", *map(str, settings["rotations"])]
        command += ["--save-crops", str(report_dir)]
        if settings["max_pages"]:
            command += ["--max-pages", str(settings["max_pages"])]
        if settings["stop_after_first"]:
            command.append("--stop-after-first")
    elif analyzer == "same_phone":
        command += ["--dpi", str(settings["dpi"]), "--max-analysis-side", str(settings["max_analysis_side"])]
    elif analyzer == "tamper_scan":
        command += ["--dpi", str(settings["dpi"]), "--fail-over", str(settings["review_threshold"])]
        if settings["annotate_all"]:
            command.append("--annotate-all")
    elif analyzer == "readability":
        command += [
            "--dpi", str(settings["dpi"]),
            "--noise-threshold", str(settings["noise_threshold"]),
            "--sharpness-threshold", str(settings["sharpness_threshold"]),
        ]
    elif analyzer == "photo_detection":
        command += [
            "--save-dir", str(report_dir),
            "--effort", str(settings.get("effort") or PHOTO_DEFAULT_EFFORT),
        ]
    elif analyzer == "signature":
        command += [
            "--dpi", str(settings["dpi"]),
            "--threshold", str(settings["confidence"]),
            "--artifact-dir", str(report_dir),
        ]
    kind = spec["kind"]
    if kind == "json":
        command += [spec["flag"], str(output_path)]
    elif kind == "font":
        command += ["--json", str(output_path), "--csv", str(output_path.with_suffix(".csv"))]
    elif kind == "report_dir":
        command += ["--output-dir", str(report_dir)]
        output_path = report_dir / spec["report"]
    elif kind == "whitener":
        command += ["--out", str(report_dir)]
    return command, output_path


def _deep_qr_rescan(
    script: Path,
    input_path: Path,
    output_path: Path,
    report_dir: Path,
    settings: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """Re-scan for QR codes over a wider DPI/rotation sweep.

    Returns `(result, outcome)`. `result` is the deep payload only when it
    actually decoded something, so a genuinely QR-free document keeps the fast
    pass's result and a document whose code needed a higher DPI stops being
    reported as having none. `outcome` is one of "ok", "empty", "timeout" or
    "error", and the caller records it: a sweep that was killed part-way is
    not evidence of absence, and the report must not describe it as one.

    A failure never turns a completed check into an error — the fast pass's
    result stands — but it must not be silently indistinguishable from a
    completed search either.
    """

    # Prefer a ladder derived from this document's own scan resolution; the
    # fixed one is the fallback for born-digital PDFs with no raster page.
    effort = str(settings.get("effort") or QR_DEFAULT_EFFORT)
    dpis = qr_dpi_ladder(input_path, effort) or QR_DEEP_DPIS
    command = [
        sys.executable, str(script), str(input_path),
        "--dpi", dpis,
        "--rotations", *QR_DEEP_ROTATIONS.split(","),
        "--save-crops", str(report_dir),
        "--out", str(output_path),
    ]
    if settings.get("max_pages"):
        command += ["--max-pages", str(settings["max_pages"])]
    try:
        subprocess.run(
            command, cwd=script.parent, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=QR_DEEP_TIMEOUT_SECONDS, check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", **_CHILD_THREAD_LIMITS},
        )
        if not output_path.exists():
            return None, "error"
        result = json.loads(output_path.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except (OSError, json.JSONDecodeError):
        return None, "error"
    finally:
        output_path.unlink(missing_ok=True)
    if not isinstance(result, dict):
        return None, "error"
    if not int(result.get("qr_count") or 0):
        return None, "empty"
    result["deep_rescan"] = True
    result["deep_rescan_dpis"] = dpis
    result["deep_rescan_rotations"] = QR_DEEP_ROTATIONS
    return result, "ok"


def _run_analyzer(
    store: JobStore,
    job_id: str,
    analyzer: str,
    input_path: Path,
    page_sizes: dict[int, tuple[float, float]],
    settings: dict[str, Any],
    analyzer_results: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Any, int | None]:
    """Run one detector subprocess and return (normalized_result, run_state, queued_at)."""

    spec = ANALYZERS[analyzer]
    script = ROOT / spec["script"]
    output_path = store.data_dir / f"{job_id}-{analyzer}.json"
    report_dir = store.data_dir / f"{job_id}-{analyzer}-report"
    kind = spec["kind"]
    command, output_path = _analyzer_command(analyzer, spec, input_path, output_path, report_dir, settings)
    exit_code: int | None = None
    try:
        completed = subprocess.run(
            command, cwd=script.parent, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS, check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", **_CHILD_THREAD_LIMITS},
        )
        exit_code = completed.returncode
        if output_path.exists():
            raw_result = json.loads(output_path.read_text(encoding="utf-8"))
        elif kind == "whitener":
            raw_result = _load_whitener_result(report_dir, input_path, completed)
        elif kind == "stdout":
            crashed = completed.returncode not in (0, 1) or "Traceback" in completed.stderr
            raw_result = {
                "status": "error" if crashed else "completed",
                "exit_code": completed.returncode,
                "report": completed.stdout[-6000:],
                "detail": completed.stderr[-2000:],
            }
        else:
            raw_result = {
                "status": "error", "exit_code": completed.returncode,
                "detail": (completed.stderr or completed.stdout)[-2000:],
            }
    except subprocess.TimeoutExpired:
        raw_result = {"status": "error", "detail": f"Timed out after {TIMEOUT_SECONDS} seconds."}
    except (OSError, json.JSONDecodeError) as exc:
        raw_result = {"status": "error", "detail": str(exc)}
    finally:
        output_path.unlink(missing_ok=True)
        if kind == "font":
            output_path.with_suffix(".csv").unlink(missing_ok=True)

    if analyzer == "qr_presence" and QR_DEEP_RESCAN and isinstance(raw_result, dict):
        level = QR_EFFORT_LEVELS.get(
            str(settings.get("effort") or QR_DEFAULT_EFFORT), QR_EFFORT_LEVELS[QR_DEFAULT_EFFORT]
        )
        found = int(raw_result.get("qr_count") or 0)
        # "high" sweeps even when the fast pass already found something, since
        # a document can carry a second code the fast pass could not reach.
        should_rescan = (
            level["rescan"]
            and raw_result.get("status") != "error"
            and (not found or level["always"])
        )
        if should_rescan:
            deep, deep_outcome = _deep_qr_rescan(
                script, input_path, output_path, report_dir, settings
            )
            if deep is not None and int(deep.get("qr_count") or 0) >= found:
                raw_result = deep
            elif deep_outcome in ("timeout", "error"):
                # The wide sweep did not finish. "No QR code" from this run is
                # a statement about the search being cut short, not about the
                # document, and the verdict has to be able to say so.
                raw_result["deep_rescan_incomplete"] = deep_outcome

    if analyzer == "qr_presence" and isinstance(raw_result, dict):
        artifacts: list[str] = []
        for hit in raw_result.get("hits", []):
            if not isinstance(hit, dict) or not hit.get("crop_path"):
                continue
            try:
                crop_path = Path(str(hit["crop_path"])).resolve()
                relative_crop = crop_path.relative_to(report_dir.resolve()).as_posix()
            except (OSError, ValueError):
                hit.pop("crop_path", None)
                continue
            hit["crop_path"] = relative_crop
            artifacts.append(relative_crop)
        raw_result["artifacts"] = list(dict.fromkeys(artifacts))
        # A "no QR code" reading means something different at each effort
        # level, so the report has to carry which one produced it.
        raw_result["search_effort"] = str(settings.get("effort") or QR_DEFAULT_EFFORT)
        minimum = int(settings["min_codes"])
        count = int(raw_result.get("qr_count") or 0)
        raw_result["expected_min_codes"] = minimum
        if count < minimum:
            raw_result.update({
                "passed": False,
                "risk": "medium",
                "verdict": "qr_count_below_minimum",
                "note": f"Expected at least {minimum} QR code(s); found {count}.",
            })
    if analyzer == "signature" and isinstance(raw_result, dict) and raw_result.get("status") == "completed":
        minimum = int(settings["min_signatures"])
        count = int(raw_result.get("count") or 0)
        raw_result["expected_min_signatures"] = minimum
        if minimum > 0 and count < minimum:
            raw_result.update({
                "passed": False,
                "risk": "medium",
                "verdict": "signatures_below_minimum",
                "note": f"Expected at least {minimum} signature(s); found {count}.",
            })
    if analyzer == "tamper_scan" and isinstance(raw_result, dict):
        threshold = float(settings["review_threshold"])
        probability = raw_result.get("document_probability")
        raw_result["review_threshold"] = threshold
        if isinstance(probability, (int, float)):
            if float(probability) >= threshold:
                raw_result.update({"passed": False, "risk": "high", "verdict": "likely_whitener"})
            else:
                raw_result.update({"passed": True, "risk": "low", "verdict": "no_whitener_evidence"})

    normalized = normalize_analyzer_result(analyzer, raw_result, exit_code=exit_code, page_sizes=page_sizes)
    return normalized.model_dump(mode="json"), normalized, exit_code


def run_job(store: JobStore, job_id: str, only: list[str] | None = None) -> None:
    """Run a job's analyzers. `only` re-runs a subset and keeps the rest.

    A retry after a check error must not discard the checks that did complete,
    so the existing results are the starting point rather than an empty dict.
    """

    job = store.get(job_id)
    if job is None:
        return
    store.update(job_id, status="running", started_at=_now())
    results: dict[str, Any] = dict(job.get("results", {})) if only else {}
    analyzer_runs = job["analyzer_runs"]
    input_path = store.path_for(job_id)
    try:
        page_sizes = pdf_page_sizes(input_path)
    except (OSError, RuntimeError, ValueError):
        page_sizes = {}

    analyzers = [item for item in job["analyzers"] if item in only] if only else job["analyzers"]
    if not analyzers:
        store.update(job_id, status="completed", completed_at=_now())
        return
    settings = sanitize_analysis_settings(job.get("settings"))
    started_at = _now()
    for analyzer in analyzers:
        analyzer_runs[analyzer] = AnalyzerRunState(
            analyzer_id=analyzer,
            status=AnalyzerRunStatus.RUNNING,
            queued_at=analyzer_runs[analyzer]["queued_at"],
            started_at=started_at,
        ).model_dump(mode="json")
    store.update(job_id, analyzer_runs=analyzer_runs.copy())

    # The detectors are independent subprocesses, so run them concurrently and
    # merge each result into the shared job state as it lands.
    lock = threading.Lock()

    def worker(analyzer: str) -> None:
        with lock:
            context_results = json.loads(json.dumps(results))
        result_json, normalized, _ = _run_analyzer(
            store,
            job_id,
            analyzer,
            input_path,
            page_sizes,
            settings.get(analyzer, {}),
            context_results,
        )
        failed = normalized.outcome is AnalyzerOutcome.ERROR
        run_state = AnalyzerRunState(
            analyzer_id=analyzer,
            status=AnalyzerRunStatus.FAILED if failed else AnalyzerRunStatus.COMPLETED,
            queued_at=analyzer_runs[analyzer]["queued_at"],
            started_at=started_at,
            completed_at=_now(),
            result=normalized,
            error=normalized.summary if failed else None,
        ).model_dump(mode="json")
        with lock:
            results[analyzer] = result_json
            analyzer_runs[analyzer] = run_state
            store.update(job_id, results=results.copy(), analyzer_runs=analyzer_runs.copy())

    workers = max(1, min(len(analyzers), ANALYZER_WORKERS))
    if workers == 1:
        for analyzer in analyzers:
            worker(analyzer)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="analyzer") as pool:
            list(pool.map(worker, analyzers))

    store.update(
        job_id,
        status="completed",
        completed_at=_now(),
        results=results,
        analyzer_runs=analyzer_runs,
    )


# Bounded worker pool so a large batch queues instead of launching every
# detector subprocess at once.
ANALYSIS_WORKERS = max(1, int(os.getenv("ANALYSIS_WORKERS", "2")))
_executor = ThreadPoolExecutor(max_workers=ANALYSIS_WORKERS, thread_name_prefix="analysis-worker")


def _execute_job(store: JobStore, job_id: str, only: list[str] | None = None) -> None:
    try:
        run_job(store, job_id, only)
    except Exception as exc:  # noqa: BLE001 - a worker crash must not strand the job
        job = store.get(job_id)
        if job is None:
            return
        _finalize_incomplete(job, f"Analysis stopped unexpectedly: {exc}")
        store.update(job_id, **job)


def submit_job(store: JobStore, job_id: str, only: list[str] | None = None) -> None:
    """Queue a job for analysis on the shared worker pool."""

    _executor.submit(_execute_job, store, job_id, only)
