from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

from .models import (
    AnalyzerOutcome,
    AnalyzerRunState,
    AnalyzerRunStatus,
    normalize_analyzer_result,
)


ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 360
ANALYZERS: dict[str, dict[str, Any]] = {
    "metadata": {"script": "tools/metadata_analysis/metadata_check.py", "kind": "json", "flag": "--output", "description": "PDF metadata and structural editing signals"},
    "qr_presence": {"script": "tools/qr_analysis/qr_exists.py", "kind": "json", "flag": "--out", "description": "QR code presence and decoded payloads"},
    "font_analysis": {"script": "tools/font_analysis/pp.py", "kind": "font", "description": "Embedded font, typeface, and font-usage extraction"},
    "moire": {"script": "tools/moire_analysis/moire_scan.py", "kind": "json", "flag": "--json", "description": "Recapture and moire-pattern screening"},
    "scanner_noise": {"script": "tools/capture_analysis/scanner_noise/scanner_noise_fingerprint_check.py", "kind": "report_dir", "report": "scanner_noise_fingerprint_report.json", "description": "Page and region scanner-noise consistency"},
    "same_phone": {"script": "tools/capture_analysis/same_phone/same_phone_pdf_check.py", "kind": "report_dir", "report": "same_phone_pdf_report.json", "description": "Same-phone/capture-workflow compatibility"},
    "tamper_scan": {"script": "tools/tamper_analysis/tamper_detect_local.py", "kind": "artifacts", "description": "Composite pixel and metadata tamper screening"},
    "readability": {"script": "tools/readability_analysis/pdf_readability_checker.py", "kind": "stdout", "description": "Machine readability and scan-quality checks"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pdf_page_sizes(path: Path) -> dict[int, tuple[float, float]]:
    """Return PDF page dimensions in points, keyed by one-based page number."""

    with fitz.open(path) as document:
        return {
            index + 1: (float(page.rect.width), float(page.rect.height))
            for index, page in enumerate(document)
        }


class JobStore:
    """Thread-safe, in-memory job registry with uploaded files held on disk."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def available_analyzers(self) -> list[dict[str, str]]:
        return [{"id": key, "description": spec["description"]} for key, spec in ANALYZERS.items()]

    def create(self, filename: str, payload: bytes, requested: list[str] | None) -> dict[str, Any]:
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
            "status": "queued",
            "created_at": created_at,
            "analyzers": selected,
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
        return job.copy()

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return json.loads(json.dumps(job)) if job else None

    def update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(changes)

    def path_for(self, job_id: str) -> Path:
        return self.data_dir / f"{job_id}.pdf"


def run_job(store: JobStore, job_id: str) -> None:
    job = store.get(job_id)
    if job is None:
        return
    store.update(job_id, status="running", started_at=_now())
    results: dict[str, Any] = {}
    analyzer_runs = job["analyzer_runs"]
    input_path = store.path_for(job_id)
    try:
        page_sizes = pdf_page_sizes(input_path)
    except (OSError, RuntimeError, ValueError):
        page_sizes = {}
    for analyzer in job["analyzers"]:
        spec = ANALYZERS[analyzer]
        script = ROOT / spec["script"]
        output_path = store.data_dir / f"{job_id}-{analyzer}.json"
        report_dir = store.data_dir / f"{job_id}-{analyzer}-report"
        kind = spec["kind"]
        command = [sys.executable, str(script), str(input_path)]
        started_at = _now()
        analyzer_runs[analyzer] = AnalyzerRunState(
            analyzer_id=analyzer,
            status=AnalyzerRunStatus.RUNNING,
            queued_at=analyzer_runs[analyzer]["queued_at"],
            started_at=started_at,
        ).model_dump(mode="json")
        store.update(job_id, analyzer_runs=analyzer_runs.copy())
        if kind == "json":
            command += [spec["flag"], str(output_path)]
        elif kind == "font":
            command += ["--json", str(output_path), "--csv", str(output_path.with_suffix(".csv"))]
        elif kind == "report_dir":
            command += ["--output-dir", str(report_dir)]
            output_path = report_dir / spec["report"]
        elif kind == "artifacts":
            command += ["--output", str(report_dir)]
        exit_code: int | None = None
        try:
            completed = subprocess.run(
                command, cwd=script.parent, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS, check=False,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            exit_code = completed.returncode
            if output_path.exists():
                raw_result = json.loads(output_path.read_text(encoding="utf-8"))
            elif kind == "artifacts":
                detail = (completed.stderr or completed.stdout)[-2000:]
                artifact_paths = list(report_dir.glob("*")) if report_dir.exists() else []
                artifacts = [item.name for item in artifact_paths if item.suffix.lower() != ".json"]
                crashed = completed.returncode != 0 or "Traceback" in detail or not artifacts
                structured_reports = [item for item in artifact_paths if item.name.endswith("_tamper_analysis.json")]
                if structured_reports and not crashed:
                    raw_result = json.loads(structured_reports[0].read_text(encoding="utf-8"))
                    raw_result.update({"exit_code": completed.returncode, "artifacts": artifacts})
                else:
                    raw_result = {
                        "status": "error" if crashed else "completed",
                        "exit_code": completed.returncode,
                        "artifacts": artifacts,
                        "detail": detail,
                    }
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

        normalized = normalize_analyzer_result(
            analyzer,
            raw_result,
            exit_code=exit_code,
            page_sizes=page_sizes,
        )
        results[analyzer] = normalized.model_dump(mode="json")
        failed = normalized.outcome is AnalyzerOutcome.ERROR
        analyzer_runs[analyzer] = AnalyzerRunState(
            analyzer_id=analyzer,
            status=AnalyzerRunStatus.FAILED if failed else AnalyzerRunStatus.COMPLETED,
            queued_at=analyzer_runs[analyzer]["queued_at"],
            started_at=started_at,
            completed_at=_now(),
            result=normalized,
            error=normalized.summary if failed else None,
        ).model_dump(mode="json")
        store.update(
            job_id,
            results=results.copy(),
            analyzer_runs=analyzer_runs.copy(),
        )
    store.update(
        job_id,
        status="completed",
        completed_at=_now(),
        results=results,
        analyzer_runs=analyzer_runs,
    )
