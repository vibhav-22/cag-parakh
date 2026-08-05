from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any

from pydantic import BaseModel, Field, model_validator

from . import checks


def utc_now() -> datetime:
    """Return an aware UTC timestamp for persisted API state."""

    return datetime.now(timezone.utc)


class AnalyzerRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalyzerOutcome(str, Enum):
    CLEAR = "clear"
    REVIEW = "review"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"
    # A check that reports facts and takes no position — metadata provenance is
    # the only one today. It never counts toward clear or flagged totals.
    INFO = "info"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    INFO = "info"
    ERROR = "error"


class CheckFact(BaseModel):
    """One labelled value a detector reported, for display beside its verdict."""

    label: str
    value: str


class EvidenceRegion(BaseModel):
    """A page-relative area that can be drawn over the source document."""

    page: int = Field(ge=1)
    kind: str
    label: str
    message: str
    severity: RiskLevel = RiskLevel.UNKNOWN
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class CheckVerdict(BaseModel):
    """One detector's own pass/fail rule, applied to one document.

    `criterion` is the rule stated in advance and is identical for every
    document. `reason` is why this document landed where it did — and for an
    inconclusive result it must say why the check could not reach a conclusion,
    because "inconclusive" with no explanation tells a reviewer nothing.
    """

    status: CheckStatus
    criterion: str
    reason: str
    facts: list[CheckFact] = Field(default_factory=list)


class NormalizedAnalyzerResult(BaseModel):
    """Stable result envelope shared by every document analyzer."""

    analyzer_id: str
    outcome: AnalyzerOutcome
    risk: RiskLevel = RiskLevel.UNKNOWN
    summary: str
    # The detector's fixed criterion and how this document met it. Optional only
    # so results persisted before per-detector criteria existed still load.
    check: CheckVerdict | None = None
    findings_count: int = Field(default=0, ge=0)
    artifacts: list[str] = Field(default_factory=list)
    regions: list[EvidenceRegion] = Field(default_factory=list)
    exit_code: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class AnalyzerRunState(BaseModel):
    """Lifecycle and result state for one analyzer invocation."""

    analyzer_id: str
    status: AnalyzerRunStatus = AnalyzerRunStatus.QUEUED
    queued_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: NormalizedAnalyzerResult | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "AnalyzerRunState":
        if self.status is AnalyzerRunStatus.QUEUED:
            if self.started_at or self.completed_at or self.result or self.error:
                raise ValueError("A queued analyzer run cannot contain execution state")
        elif self.status is AnalyzerRunStatus.RUNNING:
            if not self.started_at or self.completed_at or self.result or self.error:
                raise ValueError("A running analyzer run requires only started_at")
        elif self.status is AnalyzerRunStatus.COMPLETED:
            if not self.started_at or not self.completed_at or not self.result or self.error:
                raise ValueError("A completed analyzer run requires timestamps and a result")
            if self.result.outcome is AnalyzerOutcome.ERROR:
                raise ValueError("An error result must use failed run status")
        elif self.status is AnalyzerRunStatus.FAILED:
            if not self.started_at or not self.completed_at or not self.result or not self.error:
                raise ValueError("A failed analyzer run requires timestamps, a result, and an error")
            if self.result.outcome is not AnalyzerOutcome.ERROR:
                raise ValueError("A failed analyzer run requires an error result")

        if self.started_at and self.started_at < self.queued_at:
            raise ValueError("started_at cannot precede queued_at")
        if self.completed_at and self.started_at and self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        return self


class JobState(BaseModel):
    """Public polling representation for a document-analysis job."""

    id: str
    filename: str
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    analyzers: list[str]
    settings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    analyzer_runs: dict[str, AnalyzerRunState]
    results: dict[str, NormalizedAnalyzerResult]
    review: dict[str, Any] | None = None
    # SHA-256 of the stored document bytes. This is the join key for "this exact
    # file was screened before" — never a claim about the underlying document,
    # because two photos of one certificate hash differently.
    sha256: str | None = None
    unanalyzable: bool = False
    unanalyzable_reason: str | None = None
    # The named screening test this run was started under: {id, name, goal,
    # guidance}. Null for an ad-hoc run with no profile.
    profile: dict[str, Any] | None = None


class PriorScreening(BaseModel):
    """A previous screening of byte-identical content."""

    job_id: str
    batch_id: str | None = None
    filename: str
    created_at: datetime
    machine_verdict: str
    review_decision: str | None = None


class PreflightFile(BaseModel):
    """One upload candidate, checked before a batch is created."""

    filename: str
    size_bytes: int = Field(ge=0)
    sha256: str | None = None
    readable: bool
    page_count: int | None = None
    # Set when the file cannot be screened at all, so intake can drop it before
    # the reviewer waits on an analysis that was never going to run.
    blocker: str | None = None
    # Set when the file opens but a text-layer-free scan will limit some checks.
    warning: str | None = None
    prior_screenings: list[PriorScreening] = Field(default_factory=list)


class PreflightReport(BaseModel):
    files: list[PreflightFile]
    duplicate_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)


class BatchState(BaseModel):
    """Public representation of a multi-document screening batch."""

    id: str
    # User-entered at intake, or generated from the batch's documents when left
    # blank — always non-empty so a batch is never left with nothing to search.
    name: str
    created_at: datetime
    status: JobStatus
    document_count: int = Field(ge=1)
    jobs: list[JobState]
    # The project this run was filed into, or None for an unfiled run. Absent
    # on every batch stored before Projects existed — defaulted so those still
    # validate.
    project_id: str | None = None


class BatchSummary(BaseModel):
    """History-list row for a stored batch."""

    id: str
    name: str
    created_at: datetime
    status: JobStatus
    document_count: int = Field(ge=1)
    completed_documents: int = Field(ge=0)
    flagged_documents: int = Field(ge=0)
    project_id: str | None = None


class ProjectState(BaseModel):
    """A folder that groups screening runs. Also the list row — same shape."""

    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    # Live counts, derived at read time from the batches that name this
    # project. Never stored on the project record itself.
    batch_count: int = Field(default=0, ge=0)
    last_activity_at: datetime | None = None


class DocumentPage(BaseModel):
    page: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class DocumentManifest(BaseModel):
    page_count: int = Field(ge=1)
    pages: list[DocumentPage]


def _value(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _risk(payload: dict[str, Any]) -> RiskLevel:
    candidate = _value(payload, "risk") or _value(payload, "summary", "overall_risk")
    normalized = str(candidate or "").strip().lower()
    if normalized in {"low", "none", "minimal", "clean"}:
        return RiskLevel.LOW
    if normalized in {"medium", "moderate", "warning"}:
        return RiskLevel.MEDIUM
    if normalized in {"high", "critical", "severe"}:
        return RiskLevel.HIGH
    return RiskLevel.UNKNOWN


def _findings_count(payload: dict[str, Any]) -> int:
    explicit = payload.get("suspicious_regions_count")
    if isinstance(explicit, int) and explicit >= 0:
        return explicit

    summary = payload.get("summary")
    if isinstance(summary, dict):
        counts = [summary.get("suspicious_pages"), summary.get("suspicious_regions")]
        numeric = [item for item in counts if isinstance(item, int) and item >= 0]
        if numeric:
            return sum(numeric)

    for key in ("issues", "findings", "suspicious_pages", "suspicious_regions", "regions"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return 0


def _verdict(payload: dict[str, Any]) -> str:
    value = (
        payload.get("file_verdict")
        or payload.get("verdict")
        or _value(payload, "summary", "overall_verdict")
        or ""
    )
    return str(value).strip().lower()


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalized_region(
    *,
    page: int,
    kind: str,
    label: str,
    message: str,
    severity: RiskLevel,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> EvidenceRegion | None:
    left, right = sorted((_clip(x0), _clip(x1)))
    top, bottom = sorted((_clip(y0), _clip(y1)))
    if right - left < 0.001 or bottom - top < 0.001:
        return None
    return EvidenceRegion(
        page=page,
        kind=kind,
        label=label,
        message=message,
        severity=severity,
        x=round(left, 6),
        y=round(top, 6),
        width=round(right - left, 6),
        height=round(bottom - top, 6),
    )


def _page_pixels(
    page: int,
    dpi: float,
    page_sizes: dict[int, tuple[float, float]] | None,
) -> tuple[float, float] | None:
    size = page_sizes.get(page) if page_sizes else None
    if not size or dpi <= 0:
        return None
    return size[0] * dpi / 72.0, size[1] * dpi / 72.0


def _qr_point_to_original(
    x: float,
    y: float,
    rotation: int,
    page_width: float,
    page_height: float,
) -> tuple[float, float]:
    if rotation == 90:
        return y, page_height - x
    if rotation == 180:
        return page_width - x, page_height - y
    if rotation == 270:
        return page_width - y, x
    return x, y


def _evidence_regions(
    analyzer_id: str,
    payload: dict[str, Any],
    risk: RiskLevel,
    page_sizes: dict[int, tuple[float, float]] | None,
) -> list[EvidenceRegion]:
    regions: list[EvidenceRegion] = []

    if analyzer_id == "qr_presence":
        for hit in payload.get("hits", []):
            if not isinstance(hit, dict) or not isinstance(hit.get("points"), list):
                continue
            page = hit.get("page")
            dpi = hit.get("dpi")
            if not isinstance(page, int) or not isinstance(dpi, (int, float)):
                continue
            pixels = _page_pixels(page, float(dpi), page_sizes)
            if not pixels:
                continue
            rotation_match = re.search(r"rot(0|90|180|270)", str(hit.get("variant", "")))
            rotation = int(rotation_match.group(1)) if rotation_match else 0
            points: list[tuple[float, float]] = []
            for point in hit["points"]:
                if isinstance(point, list) and len(point) >= 2:
                    points.append(
                        _qr_point_to_original(float(point[0]), float(point[1]), rotation, *pixels)
                    )
            if not points:
                continue
            xs, ys = zip(*points)
            margin_x, margin_y = 5 / pixels[0], 5 / pixels[1]
            region = _normalized_region(
                page=page,
                kind="qr_code",
                label="QR code",
                message="QR code detected in this area.",
                severity=RiskLevel.LOW,
                x0=min(xs) / pixels[0] - margin_x,
                y0=min(ys) / pixels[1] - margin_y,
                x1=max(xs) / pixels[0] + margin_x,
                y1=max(ys) / pixels[1] + margin_y,
            )
            if region:
                regions.append(region)

    if analyzer_id == "photo_detection":
        for index, photo in enumerate(payload.get("photos", []), start=1):
            if not isinstance(photo, dict) or not isinstance(photo.get("bbox_px"), (list, tuple)):
                continue
            bbox = photo["bbox_px"]
            if len(bbox) < 4:
                continue
            page = int(photo.get("page") or payload.get("page") or 1)
            source_size = photo.get("source_size_px")
            if isinstance(source_size, (list, tuple)) and len(source_size) >= 2:
                pixels = float(source_size[0]), float(source_size[1])
            else:
                # The photo detector has historically rendered PDF pages at
                # 300 DPI. This fallback gives saved results boxes without
                # forcing a detector rerun; new output carries source_size_px.
                dpi = float(payload.get("render_dpi") or 300)
                pixels = _page_pixels(page, dpi, page_sizes)
            if not pixels or pixels[0] <= 0 or pixels[1] <= 0:
                continue
            confidence = _float(photo.get("face_confidence"))
            passed = photo.get("passed") is True
            message = f"Document photo {index} detected"
            if confidence:
                message += f" ({confidence:.0%} face confidence)"
            message += "."
            region = _normalized_region(
                page=page,
                kind="document_photo",
                label=f"Document photo {index}",
                message=message,
                severity=RiskLevel.LOW if passed else RiskLevel.MEDIUM,
                x0=float(bbox[0]) / pixels[0],
                y0=float(bbox[1]) / pixels[1],
                x1=float(bbox[2]) / pixels[0],
                y1=float(bbox[3]) / pixels[1],
            )
            if region:
                regions.append(region)

    if analyzer_id == "tamper_scan":
        for item in payload.get("regions", []):
            if not isinstance(item, dict) or not isinstance(item.get("bbox_normalized"), dict):
                continue
            bbox = item["bbox_normalized"]
            severity = _risk({"risk": item.get("severity")})
            region = _normalized_region(
                page=int(item.get("page", 1)),
                kind=str(item.get("kind") or "tamper_signal"),
                label=str(item.get("label") or "Tamper signal"),
                message=str(item.get("reason") or "A local tamper signal was detected."),
                severity=severity,
                x0=float(bbox.get("x0", 0)),
                y0=float(bbox.get("y0", 0)),
                x1=float(bbox.get("x1", 0)),
                y1=float(bbox.get("y1", 0)),
            )
            if region:
                regions.append(region)

    if analyzer_id == "signature":
        for item in payload.get("regions", []):
            if not isinstance(item, dict) or not isinstance(item.get("bbox_normalized"), dict):
                continue
            bbox = item["bbox_normalized"]
            severity = _risk({"risk": item.get("severity")})
            region = _normalized_region(
                page=int(item.get("page", 1)),
                kind=str(item.get("kind") or "signature"),
                label=str(item.get("label") or "Signature"),
                message=str(item.get("reason") or "A handwritten signature was detected."),
                severity=severity,
                x0=float(bbox.get("x0", 0)),
                y0=float(bbox.get("y0", 0)),
                x1=float(bbox.get("x1", 0)),
                y1=float(bbox.get("y1", 0)),
            )
            if region:
                regions.append(region)

    return regions[:100]


# Every check's own rule decides its outcome. The mapping is one-to-one so the
# tick a reviewer sees and the outcome the batch counts can never disagree.
_CHECK_OUTCOMES = {
    checks.PASS: AnalyzerOutcome.CLEAR,
    checks.FAIL: AnalyzerOutcome.REVIEW,
    checks.INCONCLUSIVE: AnalyzerOutcome.INCONCLUSIVE,
    checks.INFO: AnalyzerOutcome.INFO,
    checks.ERROR: AnalyzerOutcome.ERROR,
}


def _legacy_outcome(analyzer_id: str, payload: dict[str, Any], risk: RiskLevel) -> AnalyzerOutcome:
    """The pre-criteria heuristic. Retained for analyzers with no rule of their own."""

    status = str(payload.get("status") or "").strip().lower()
    if status == "error":
        return AnalyzerOutcome.ERROR
    if status in {"insufficient_data", "inconclusive"}:
        return AnalyzerOutcome.INCONCLUSIVE
    if payload.get("passed") is False or risk in {RiskLevel.MEDIUM, RiskLevel.HIGH}:
        return AnalyzerOutcome.REVIEW

    verdict = _verdict(payload)
    if any(token in verdict for token in ("recapture", "suspicion", "different_phone", "tamper")):
        return AnalyzerOutcome.REVIEW
    if any(token in verdict for token in ("inconclusive", "insufficient", "uncertain")):
        return AnalyzerOutcome.INCONCLUSIVE
    if verdict in {"clean", "passed", "clear", "readable", "likely_same_phone_or_workflow", "compatible_with_same_phone"}:
        return AnalyzerOutcome.CLEAR

    if analyzer_id == "qr_presence" and payload.get("qr_found") is False:
        return AnalyzerOutcome.INCONCLUSIVE
    if analyzer_id == "readability" and payload.get("exit_code") == 1:
        return AnalyzerOutcome.REVIEW
    if status == "completed" and risk is RiskLevel.UNKNOWN:
        return AnalyzerOutcome.INCONCLUSIVE
    return AnalyzerOutcome.CLEAR


_GENERATION_SUMMARIES = {
    "image_editor": "Made with an image editor",
    "scanner": "Made by a scanner or capture pipeline",
    "browser_or_converter": "Made by a browser print or converter",
    "office_export": "Exported from an office application",
    "pdf_library": "Generated by a PDF library",
    "unknown": "Generation pipeline unidentified",
}


def _summary(analyzer_id: str, payload: dict[str, Any], outcome: AnalyzerOutcome, risk: RiskLevel) -> str:
    if outcome is AnalyzerOutcome.ERROR:
        return str(payload.get("detail") or "Analyzer failed")

    note = payload.get("note")
    if outcome is AnalyzerOutcome.INCONCLUSIVE and isinstance(note, str) and note.strip():
        return note.strip()

    verdict = _verdict(payload)
    probability = payload.get("document_probability")
    if analyzer_id == "metadata":
        # This check reports provenance, so its one-liner names the pipeline that
        # made the file rather than a risk grade the check does not assign.
        generation = payload.get("generation")
        if isinstance(generation, dict):
            origin = _GENERATION_SUMMARIES.get(str(generation.get("kind")), "Generation pipeline unidentified")
            source = generation.get("producer") or generation.get("creator")
            return f"{origin}{f' · {source}' if source else ''}"
    if analyzer_id == "font_analysis":
        names, referenced_names, _ = checks.font_inventory(payload)
        count = len(names)
        if count == 0:
            if referenced_names:
                return f"No embedded fonts · references {', '.join(referenced_names)}"
            return "No embedded fonts"
        return f"{count} embedded font{'' if count == 1 else 's'}: {', '.join(names)}"
    if analyzer_id == "photo_detection":
        if payload.get("photo_found") is True:
            count = payload.get("photo_count")
            count = int(count) if isinstance(count, int) and count > 0 else 1
            noun = "photo" if count == 1 else "photos"
            if payload.get("passed") is True:
                return f"{count} document {noun} detected"
            return f"{count} {noun} detected - quality needs review"
        return "No document photo detected"
    if analyzer_id == "tamper_scan" and isinstance(probability, (int, float)):
        label = {
            "likely_whitener": "Likely whitener detected",
            "possible_whitener": "Possible whitener detected",
            "no_whitener_evidence": "No whitener evidence",
        }.get(verdict, "Whitener analysis complete")
        return f"{label} · {float(probability):.0%} probability"
    if analyzer_id == "readability" and verdict and isinstance(payload.get("score"), int):
        label = verdict.replace("_", " ").strip().capitalize()
        return (
            f"{label} · {payload['score']}/100 "
            f"({payload.get('tests_passed', 0)}/{payload.get('tests_total', 0)} tests passed)"
        )
    if analyzer_id == "qr_presence" and isinstance(payload.get("expected_min_codes"), int):
        expected = payload["expected_min_codes"]
        count = int(payload.get("qr_count") or 0)
        if count < expected:
            return f"Expected at least {expected} QR code(s); found {count}"
    if analyzer_id == "signature" and isinstance(payload.get("present"), bool):
        count = int(payload.get("count") or 0)
        expected = payload.get("expected_min_signatures")
        if isinstance(expected, int) and expected > 0 and count < expected:
            return f"Expected at least {expected} signature(s); found {count}"
        if payload["present"]:
            return f"{count} signature(s) detected"
        return "No signature detected"
    if verdict:
        return verdict.replace("_", " ").strip().capitalize()

    if analyzer_id == "qr_presence" and isinstance(payload.get("qr_found"), bool):
        count = payload.get("qr_count", 0)
        return f"QR found ({count})" if payload["qr_found"] else "No QR code found"
    if analyzer_id == "font_analysis" and isinstance(payload.get("typeface_count"), int):
        return f"{payload['typeface_count']} typefaces found"
    if risk is not RiskLevel.UNKNOWN:
        return f"{risk.value.capitalize()} risk"
    if outcome is AnalyzerOutcome.INCONCLUSIVE:
        return "Analysis inconclusive"
    return "Analysis completed"


def normalize_analyzer_result(
    analyzer_id: str,
    payload: Any,
    *,
    exit_code: int | None = None,
    page_sizes: dict[int, tuple[float, float]] | None = None,
) -> NormalizedAnalyzerResult:
    """Translate a detector-specific payload into the shared API contract."""

    if isinstance(payload, dict):
        raw = dict(payload)
    elif isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
        # Some command-line tools accept batches and therefore emit a list even
        # though the API always invokes them for one document.
        raw = dict(payload[0])
    else:
        raw = {
            "status": "error",
            "detail": "Analyzer returned an unsupported result shape.",
            "value": payload,
        }
    risk = _risk(raw)
    verdict = checks.evaluate(analyzer_id, raw)
    if analyzer_id in checks.EVALUATORS:
        outcome = _CHECK_OUTCOMES[verdict.status]
    else:
        outcome = _legacy_outcome(analyzer_id, raw, risk)
    artifacts = raw.get("artifacts")
    return NormalizedAnalyzerResult(
        analyzer_id=analyzer_id,
        outcome=outcome,
        risk=risk,
        check=CheckVerdict.model_validate(verdict.as_dict()),
        summary=_summary(analyzer_id, raw, outcome, risk),
        findings_count=_findings_count(raw),
        artifacts=[str(item) for item in artifacts] if isinstance(artifacts, list) else [],
        regions=_evidence_regions(analyzer_id, raw, risk, page_sizes),
        exit_code=exit_code if exit_code is not None else (
            raw.get("exit_code") if isinstance(raw.get("exit_code"), int) else None
        ),
        raw=raw,
    )
