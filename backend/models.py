from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any

from pydantic import BaseModel, Field, model_validator


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


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"


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


class NormalizedAnalyzerResult(BaseModel):
    """Stable result envelope shared by every document analyzer."""

    analyzer_id: str
    outcome: AnalyzerOutcome
    risk: RiskLevel = RiskLevel.UNKNOWN
    summary: str
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
    analyzer_runs: dict[str, AnalyzerRunState]
    results: dict[str, NormalizedAnalyzerResult]
    review: dict[str, Any] | None = None


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

    if analyzer_id == "scanner_noise":
        for item in payload.get("suspicious_regions", []):
            if not isinstance(item, dict) or not isinstance(item.get("bbox_normalized"), dict):
                continue
            bbox = item["bbox_normalized"]
            page = item.get("page")
            if not isinstance(page, int):
                continue
            severity = _risk({"risk": item.get("severity")})
            region = _normalized_region(
                page=page,
                kind="capture_inconsistency",
                label="Capture inconsistency",
                message=str(item.get("reason") or "Local capture characteristics differ from the page."),
                severity=severity,
                x0=float(bbox.get("x0", 0)),
                y0=float(bbox.get("y0", 0)),
                x1=float(bbox.get("x1", 0)),
                y1=float(bbox.get("y1", 0)),
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

    return regions[:100]


def _outcome(analyzer_id: str, payload: dict[str, Any], risk: RiskLevel) -> AnalyzerOutcome:
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
    if verdict in {"clean", "passed", "clear", "likely_same_phone_or_workflow", "compatible_with_same_phone"}:
        return AnalyzerOutcome.CLEAR

    if analyzer_id == "qr_presence" and payload.get("qr_found") is False:
        return AnalyzerOutcome.INCONCLUSIVE
    if analyzer_id == "readability" and payload.get("exit_code") == 1:
        return AnalyzerOutcome.REVIEW
    if status == "completed" and risk is RiskLevel.UNKNOWN:
        return AnalyzerOutcome.INCONCLUSIVE
    return AnalyzerOutcome.CLEAR


def _summary(analyzer_id: str, payload: dict[str, Any], outcome: AnalyzerOutcome, risk: RiskLevel) -> str:
    if outcome is AnalyzerOutcome.ERROR:
        return str(payload.get("detail") or "Analyzer failed")

    note = payload.get("note")
    if outcome is AnalyzerOutcome.INCONCLUSIVE and isinstance(note, str) and note.strip():
        return note.strip()

    verdict = _verdict(payload)
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
    outcome = _outcome(analyzer_id, raw, risk)
    artifacts = raw.get("artifacts")
    return NormalizedAnalyzerResult(
        analyzer_id=analyzer_id,
        outcome=outcome,
        risk=risk,
        summary=_summary(analyzer_id, raw, outcome, risk),
        findings_count=_findings_count(raw),
        artifacts=[str(item) for item in artifacts] if isinstance(artifacts, list) else [],
        regions=_evidence_regions(analyzer_id, raw, risk, page_sizes),
        exit_code=exit_code if exit_code is not None else (
            raw.get("exit_code") if isinstance(raw.get("exit_code"), int) else None
        ),
        raw=raw,
    )
