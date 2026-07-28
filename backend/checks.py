"""Per-detector pass/fail criteria.

Every check in Parakh now carries its own, fixed definition of what a tick and a
cross mean. That used to be a per-run setting the reviewer picked from a
drop-down ("tick when this detector says Clear / Needs review"), which asked the
wrong person the wrong question: whether a missing signature is a pass is a
property of the signature detector, not of the batch.

So the rule lives here, one evaluator per detector, and the same rule is applied
to every document the system has ever screened. Four things come out of an
evaluator:

    status      pass | fail | inconclusive | info | error
    criterion   the fixed rule, stated before the result is known
    reason      why THIS document got THIS status — and, when the status is
                `inconclusive`, why the test could not reach a conclusion
                ("only one page was analysed", "the embedded image was too
                small"). An inconclusive with no reason is a bug.
    facts       the detector's own values, labelled for display: the font names
                it found, the QR payloads it decoded, how the PDF was generated

`info` is deliberate. Metadata answers "how was this file made", and forcing
that into a tick or a cross would make the reviewer decode a symbol instead of
reading the answer, so it reports provenance facts and no verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


PASS = "pass"
FAIL = "fail"
INCONCLUSIVE = "inconclusive"
INFO = "info"
ERROR = "error"


@dataclass
class CheckVerdict:
    status: str
    criterion: str
    reason: str
    facts: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "criterion": self.criterion,
            "reason": self.reason,
            "facts": self.facts,
        }


def _fact(label: str, value: Any) -> dict[str, str] | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, bool):
        value = "Yes" if value else "No"
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    text = str(value).strip()
    if not text:
        return None
    return {"label": label, "value": text[:400]}


def _facts(*pairs: tuple[str, Any]) -> list[dict[str, str]]:
    return [row for label, value in pairs if (row := _fact(label, value))]


def _text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ── metadata ──────────────────────────────────────────────────────────────────

_GENERATION_LABELS = {
    "image_editor": "Image editor",
    "scanner": "Scanner or capture pipeline",
    "browser_or_converter": "Browser print or file converter",
    "office_export": "Office / typesetting export",
    "pdf_library": "PDF library or report writer",
    "unknown": "Unidentified",
}

_SIGNAL_LABELS = {
    "incremental_update": "Re-saved after creation",
    "date_inconsistency": "Modified after creation",
    "editor_producer": "Produced by an image editor",
    "renderer_producer": "Produced by a browser/HTML renderer",
    "editor_image_metadata": "Embedded image carries editor tags",
    "suspicious_structure": "Interactive or overlay structure present",
}


def _check_metadata(raw: dict[str, Any]) -> CheckVerdict:
    criterion = (
        "Reports how the document was generated. No tick or cross: a producer "
        "string is a fact about the file's origin, not a pass or a failure."
    )
    metadata = raw.get("document_metadata") if isinstance(raw.get("document_metadata"), dict) else {}
    generation = raw.get("generation") if isinstance(raw.get("generation"), dict) else {}
    issues = raw.get("issues") if isinstance(raw.get("issues"), list) else []

    kind = str(generation.get("kind") or "unknown")
    origin = _GENERATION_LABELS.get(kind, _GENERATION_LABELS["unknown"])
    producer = generation.get("producer") or metadata.get("producer")
    creator = generation.get("creator") or metadata.get("creator")

    if not generation and not metadata:
        # An older run, or a file the tool could not parse. Say so rather than
        # presenting an empty provenance record as if it were the answer.
        note = _text(raw, "note") or "This run did not record the document's metadata."
        return CheckVerdict(INFO, criterion, note)

    named = producer or creator
    if named:
        reason = f"{origin} — produced by {named}."
    else:
        reason = f"{origin} — the file carries no Producer or Creator string."
    if generation.get("resaved_after_creation"):
        reason += " The file was re-saved after it was first written (incremental update)."
    else:
        reason += " The file has a single-pass structure, with no later re-save."
    if metadata.get("modified_at") and metadata.get("modified_at") != metadata.get("created_at"):
        reason += f" Created {metadata['created_at'] or 'unknown'}, modified {metadata['modified_at']}."
    elif metadata.get("created_at"):
        reason += f" Created {metadata['created_at']}."

    signals = sorted({
        _SIGNAL_LABELS.get(str(item.get("signal")), str(item.get("signal")))
        for item in issues
        if isinstance(item, dict) and item.get("signal")
    })

    return CheckVerdict(
        INFO,
        criterion,
        reason,
        _facts(
            ("Generated by", origin),
            ("Producer", producer),
            ("Creator", creator),
            ("PDF version", generation.get("pdf_version") or metadata.get("format")),
            ("Created", metadata.get("created_at")),
            ("Modified", metadata.get("modified_at")),
            ("Title", metadata.get("title")),
            ("Author", metadata.get("author")),
            ("Pages", generation.get("pages")),
            ("Pages with a text layer", generation.get("pages_with_text_layer")),
            ("Embedded images", generation.get("embedded_images")),
            ("Annotations", generation.get("annotations")),
            ("Re-saved after creation", generation.get("resaved_after_creation")),
            ("Embedded JavaScript", generation.get("has_javascript")),
            ("Interactive form", generation.get("has_acroform")),
            ("Editing signals found", signals or "None"),
        ),
    )


# ── font analysis ─────────────────────────────────────────────────────────────

def _check_font_analysis_legacy(raw: dict[str, Any]) -> CheckVerdict:
    criterion = "Passes when the document embeds at least one font. Fails when it embeds none."
    typefaces = raw.get("typefaces") if isinstance(raw.get("typefaces"), list) else []
    names: list[str] = []
    for item in typefaces:
        if isinstance(item, dict) and item.get("typeface"):
            names.append(str(item["typeface"]))
        elif isinstance(item, str):
            names.append(item)
    if not names:
        unique = raw.get("unique_fonts") if isinstance(raw.get("unique_fonts"), list) else []
        names = [
            str(item.get("font_name"))
            for item in unique
            if isinstance(item, dict) and item.get("font_name")
        ]
    names = sorted(dict.fromkeys(names))
    objects = _count(raw.get("font_objects_count"))

    if not names:
        return CheckVerdict(
            FAIL,
            criterion,
            "No fonts are embedded in this document. Every page is image content with no "
            "text layer — expected for a pure scan or photo, and it means no typeface "
            "evidence is available for this document.",
            _facts(("Fonts found", 0), ("Font objects", objects)),
        )

    return CheckVerdict(
        PASS,
        criterion,
        f"{len(names)} {'font' if len(names) == 1 else 'distinct fonts'} embedded: {', '.join(names)}.",
        _facts(
            ("Fonts found", len(names)),
            ("Font names", names),
            ("Font objects", objects),
        ),
    )


# ── signature ─────────────────────────────────────────────────────────────────

def _font_names(items: Any) -> list[str]:
    names: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("typeface"):
            names.append(str(item["typeface"]))
        elif isinstance(item, dict) and item.get("font_name"):
            names.append(str(item["font_name"]))
        elif isinstance(item, str):
            names.append(item)
    return sorted(dict.fromkeys(names))


def font_inventory(raw: dict[str, Any]) -> tuple[list[str], list[str], int]:
    """Split stored font files from reader-supplied font references."""

    objects = raw.get("unique_fonts") if isinstance(raw.get("unique_fonts"), list) else []
    embedded: list[Any] = []
    referenced: list[Any] = []
    has_embedding_flags = any(
        isinstance(item, dict) and "embedded" in item for item in objects
    )
    if objects:
        for item in objects:
            if not isinstance(item, dict):
                continue
            if has_embedding_flags:
                is_embedded = item.get("embedded") is True
            else:
                # Legacy PyMuPDF output: Base-14 references have xref 0 and
                # extension "n/a"; no font program is stored in the PDF.
                extension = str(item.get("extension") or "").strip().lower()
                is_embedded = _count(item.get("xref")) > 0 and extension not in {"", "n/a", "none"}
            (embedded if is_embedded else referenced).append(item)
        return _font_names(embedded), _font_names(referenced), len(embedded)

    explicit_embedded = raw.get("embedded_typefaces")
    if isinstance(explicit_embedded, list):
        return (
            _font_names(explicit_embedded),
            _font_names(raw.get("referenced_typefaces")),
            _count(raw.get("embedded_font_objects_count")),
        )

    # Compatibility for older third-party analyzer payloads that only supplied
    # a typeface list and no object-level embedding evidence.
    names = _font_names(raw.get("typefaces"))
    return names, [], _count(raw.get("font_objects_count"))


def _check_font_analysis(raw: dict[str, Any]) -> CheckVerdict:
    criterion = (
        "Passes when the PDF stores at least one embedded font. Reader-supplied "
        "font references and text/OCR layers without embedded glyph data do not count."
    )
    names, referenced_names, objects = font_inventory(raw)

    if not names:
        reference_note = ""
        if referenced_names:
            reference_note = (
                f" The PDF references {', '.join(referenced_names)} for its text/OCR layer, "
                "but that font data is not stored in the file."
            )
        return CheckVerdict(
            FAIL,
            criterion,
            "No fonts are embedded in this document." + reference_note,
            _facts(
                ("Embedded fonts", 0),
                ("Referenced fonts", referenced_names),
                ("Embedded font objects", objects),
            ),
        )

    return CheckVerdict(
        PASS,
        criterion,
        f"{len(names)} embedded {'font' if len(names) == 1 else 'fonts'} found: {', '.join(names)}.",
        _facts(
            ("Embedded fonts", len(names)),
            ("Font names", names),
            ("Embedded font objects", objects),
            ("Referenced fonts", referenced_names),
        ),
    )


def _check_signature(raw: dict[str, Any]) -> CheckVerdict:
    minimum = max(1, _count(raw.get("expected_min_signatures")))
    criterion = (
        f"Passes when at least {minimum} handwritten signature "
        f"{'is' if minimum == 1 else 'are'} found. Fails when none is found."
    )
    count = _count(raw.get("count"))
    regions = raw.get("regions") if isinstance(raw.get("regions"), list) else []
    if not count and regions:
        count = len(regions)
    pages = sorted({
        _count(item.get("page")) for item in regions
        if isinstance(item, dict) and item.get("page")
    })
    confidence = max(
        (_number(item.get("confidence")) for item in regions if isinstance(item, dict)),
        default=_number(raw.get("score")),
    )

    if count >= minimum:
        return CheckVerdict(
            PASS,
            criterion,
            f"{count} signature{'' if count == 1 else 's'} found"
            + (f" on page {', '.join(str(page) for page in pages)}." if pages else "."),
            _facts(
                ("Signatures found", count),
                ("Pages", pages),
                ("Highest confidence", f"{confidence:.0%}" if confidence else None),
            ),
        )
    if count:
        return CheckVerdict(
            FAIL,
            criterion,
            f"{count} signature{'' if count == 1 else 's'} found, below the {minimum} "
            "this run required.",
            _facts(("Signatures found", count), ("Required", minimum), ("Pages", pages)),
        )
    return CheckVerdict(
        FAIL,
        criterion,
        "No handwritten signature was found anywhere in this document.",
        _facts(("Signatures found", 0), ("Required", minimum)),
    )


# ── document photo ────────────────────────────────────────────────────────────

def _check_photo_detection(raw: dict[str, Any]) -> CheckVerdict:
    criterion = "Passes when a document photo is found. Fails when no photo is found."
    found = raw.get("photo_found")
    count = _count(raw.get("photo_count"))
    page = raw.get("page")
    quality = _text(raw, "reason").replace("_", " ")

    if found is None and not count:
        return CheckVerdict(
            INCONCLUSIVE,
            criterion,
            "The photo detector returned no presence result for this document, so it "
            "cannot be said whether a photo is present.",
        )
    if not found:
        return CheckVerdict(
            FAIL,
            criterion,
            "No document photo was found. The detector searched every page and located "
            "no passport-style photograph.",
            _facts(("Photos found", 0)),
        )

    count = count or 1
    reason = f"{count} document photo{'' if count == 1 else 's'} found"
    reason += f" on page {page}." if page else "."
    if raw.get("passed") is False and quality:
        reason += f" Image quality needs a look: {quality}."
    return CheckVerdict(
        PASS,
        criterion,
        reason,
        _facts(
            ("Photos found", count),
            ("Page", page),
            ("Quality note", quality if raw.get("passed") is False else None),
            ("Face confidence", f"{_number(raw.get('face_confidence')):.0%}" if raw.get("face_confidence") else None),
            ("Blur score", raw.get("blur")),
            ("Brightness", raw.get("brightness")),
        ),
    )


# ── QR presence ───────────────────────────────────────────────────────────────

def _check_qr_presence(raw: dict[str, Any]) -> CheckVerdict:
    minimum = max(1, _count(raw.get("expected_min_codes")))
    criterion = (
        f"Passes when at least {minimum} QR code "
        f"{'is' if minimum == 1 else 'are'} decoded. Fails when none is decoded."
    )
    count = _count(raw.get("qr_count"))
    hits = raw.get("hits") if isinstance(raw.get("hits"), list) else []
    payloads = [
        str(hit.get("data"))[:120] for hit in hits
        if isinstance(hit, dict) and hit.get("data")
    ]
    pages = sorted({_count(hit.get("page")) for hit in hits if isinstance(hit, dict) and hit.get("page")})

    if count >= minimum:
        return CheckVerdict(
            PASS,
            criterion,
            f"{count} QR code{'' if count == 1 else 's'} decoded"
            + (f" on page {', '.join(str(page) for page in pages)}." if pages else "."),
            _facts(
                ("QR codes found", count),
                ("Pages", pages),
                ("Decoded payloads", payloads[:5]),
            ),
        )
    return CheckVerdict(
        FAIL,
        criterion,
        f"No QR code was decoded. The scan covered every page at the configured "
        f"resolution and rotations and found {count}, below the {minimum} required."
        if minimum > 1 else
        "No QR code was decoded. The scan covered every page at the configured "
        "resolution and rotations and found none.",
        _facts(("QR codes found", count), ("Required", minimum)),
    )


# ── whitener / tamper ─────────────────────────────────────────────────────────

def _check_tamper_scan(raw: dict[str, Any]) -> CheckVerdict:
    threshold = _number(raw.get("review_threshold"), 0.35)
    criterion = (
        f"Passes when the correction-fluid probability stays under {threshold:.0%}. "
        f"Fails at or above it."
    )
    probability = raw.get("document_probability")
    pages = raw.get("pages") if isinstance(raw.get("pages"), list) else []
    indeterminate = _count(raw.get("indeterminate_pages"))
    regions = _count(raw.get("suspicious_regions_count"))

    if _text(raw, "verdict") == "insufficient_signal" or (pages and indeterminate == len(pages)):
        return CheckVerdict(
            INCONCLUSIVE,
            criterion,
            f"All {indeterminate or len(pages)} analysed page(s) returned an indeterminate "
            "reading — the page render carried too little usable signal (very low contrast, "
            "heavy compression, or near-blank pages) for the whitener model to separate "
            "correction fluid from paper.",
            _facts(("Pages analysed", len(pages)), ("Indeterminate pages", indeterminate)),
        )

    if not isinstance(probability, (int, float)):
        return CheckVerdict(
            INCONCLUSIVE,
            criterion,
            "The whitener detector produced no document probability, so no threshold "
            "comparison could be made.",
        )

    value = float(probability)
    facts = _facts(
        ("Whitener probability", f"{value:.0%}"),
        ("Review threshold", f"{threshold:.0%}"),
        ("Pages analysed", len(pages) or raw.get("pages_analyzed")),
        ("Suspicious regions", regions),
    )
    if value >= threshold:
        return CheckVerdict(
            FAIL,
            criterion,
            f"Correction-fluid probability is {value:.0%}, at or above the {threshold:.0%} "
            f"threshold, across {regions} marked region{'' if regions == 1 else 's'}.",
            facts,
        )
    return CheckVerdict(
        PASS,
        criterion,
        f"Correction-fluid probability is {value:.0%}, below the {threshold:.0%} threshold. "
        "No whitener evidence.",
        facts,
    )


# ── moire / recapture ─────────────────────────────────────────────────────────

def _check_moire(raw: dict[str, Any]) -> CheckVerdict:
    criterion = (
        "Passes when the frequency analysis finds no screen/recapture pattern. "
        "Fails when a recapture pattern is found."
    )
    verdict = _text(raw, "file_verdict", "verdict").upper()
    images = raw.get("images") if isinstance(raw.get("images"), list) else []
    reasons = [
        str(image.get("reason")) for image in images
        if isinstance(image, dict) and image.get("reason")
    ]
    facts = _facts(
        ("Images analysed", len(images)),
        ("File verdict", verdict.title() if verdict else None),
        ("Per-image findings", reasons[:4]),
    )

    if verdict == "RECAPTURE":
        return CheckVerdict(
            FAIL,
            criterion,
            "A periodic screen pattern consistent with a photograph of a screen or a "
            "reprinted scan was found"
            + (f": {reasons[0]}." if reasons else "."),
            facts,
        )
    if verdict == "CLEAN":
        return CheckVerdict(
            PASS,
            criterion,
            "No anomalous periodic peaks were found. The image is consistent with a "
            "first-generation capture rather than a photo of a screen or a reprint.",
            facts,
        )
    if not images:
        return CheckVerdict(
            INCONCLUSIVE,
            criterion,
            "No raster image could be extracted from this document, so there was nothing "
            "for the frequency analysis to run on. A vector-only or text-only PDF cannot "
            "be screened for moire.",
            facts,
        )
    detail = reasons[0] if reasons else "the analysis did not settle either way"
    return CheckVerdict(
        INCONCLUSIVE,
        criterion,
        f"The frequency analysis could not reach a conclusion because {detail}. "
        "A recapture pattern can neither be confirmed nor ruled out for this image.",
        facts,
    )


# ── scanner noise ─────────────────────────────────────────────────────────────

def _check_scanner_noise(raw: dict[str, Any]) -> CheckVerdict:
    criterion = (
        "Passes when every page shares one capture fingerprint. Fails when a page or "
        "region carries a different one."
    )
    summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    risk = str(summary.get("overall_risk") or raw.get("risk") or "").lower()
    pages = _count(summary.get("pages_analyzed"))
    suspicious_pages = _count(summary.get("suspicious_pages"))
    suspicious_regions = _count(summary.get("suspicious_regions"))
    reasons = summary.get("reasons") if isinstance(summary.get("reasons"), list) else []
    facts = _facts(
        ("Pages analysed", pages or None),
        ("Suspicious pages", suspicious_pages),
        ("Suspicious regions", suspicious_regions),
        ("Overall risk", risk.title() if risk else None),
    )

    if raw.get("source_consistency_possible") is False:
        # The detector's own report says a single page cannot be tested for
        # cross-page consistency, and downgrades any local finding on one page to
        # "a review hint, not a source mismatch". Grading that as a cross would
        # report a failure the detector explicitly declined to claim.
        hint = (
            f" {suspicious_regions} local region"
            f"{'' if suspicious_regions == 1 else 's'} on the page did look different "
            "from its surroundings, which on a single page is a hint to look, not a "
            "finding."
            if suspicious_regions else ""
        )
        return CheckVerdict(
            INCONCLUSIVE,
            criterion,
            "Only one page was analysed. Cross-page capture consistency needs at least "
            "two pages to compare, so this check could not reach a conclusion." + hint,
            facts,
        )
    if not risk:
        return CheckVerdict(
            INCONCLUSIVE,
            criterion,
            "The scanner-noise report carried no overall risk reading, so no conclusion "
            "could be drawn.",
            facts,
        )
    if risk in {"medium", "high"}:
        detail = str(reasons[0]) if reasons else "capture characteristics differ across the document"
        return CheckVerdict(
            FAIL,
            criterion,
            f"Capture fingerprints do not match across this document: {detail} "
            f"({suspicious_pages} page(s) and {suspicious_regions} region(s) flagged).",
            facts,
        )
    return CheckVerdict(
        PASS,
        criterion,
        f"All {pages or 'analysed'} page(s) share one capture fingerprint, with no page "
        "or region standing out.",
        facts,
    )


# ── same phone ────────────────────────────────────────────────────────────────

def _check_same_phone(raw: dict[str, Any]) -> CheckVerdict:
    criterion = (
        "Passes when every page is compatible with one capture device. Fails when a "
        "page looks captured on a different one. Needs at least two pages."
    )
    summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    verdict = str(summary.get("overall_verdict") or raw.get("verdict") or "").lower()
    score = summary.get("same_phone_score")
    reasons = summary.get("reasons") if isinstance(summary.get("reasons"), list) else []
    pages = raw.get("pages") if isinstance(raw.get("pages"), list) else []
    facts = _facts(
        ("Pages analysed", len(pages) or None),
        ("Same-phone score", f"{_number(score):.0f}/100" if isinstance(score, (int, float)) else None),
        ("Lowest pair score", summary.get("lowest_pair_score")),
        ("Findings", [str(item) for item in reasons][:4]),
    )

    if verdict == "insufficient_pages" or (pages and len(pages) < 2):
        return CheckVerdict(
            INCONCLUSIVE,
            criterion,
            "Only one page was analysed. The same-phone comparison works by comparing "
            "pages against each other, so a single-page document gives it nothing to "
            "compare and the test cannot run.",
            facts,
        )
    if verdict in {"likely_same_phone_or_workflow", "compatible_with_same_phone"}:
        return CheckVerdict(
            PASS,
            criterion,
            f"All {len(pages) or 'analysed'} pages are compatible with a single capture "
            f"device or workflow"
            + (f" (score {_number(score):.0f}/100)." if isinstance(score, (int, float)) else "."),
            facts,
        )
    if verdict == "possibly_different_phone_or_workflow":
        return CheckVerdict(
            FAIL,
            criterion,
            "At least one page looks captured on a different device or through a different "
            "workflow than the rest"
            + (f" (score {_number(score):.0f}/100)." if isinstance(score, (int, float)) else "."),
            facts,
        )
    if verdict == "uncertain_same_phone":
        return CheckVerdict(
            INCONCLUSIVE,
            criterion,
            "The pages score in the uncertain band"
            + (f" ({_number(score):.0f}/100)" if isinstance(score, (int, float)) else "")
            + " — too similar to call different devices, too different to call the same "
            "one. Lighting and focus varying between pages of one capture session "
            "produces this result, so it is not evidence either way.",
            facts,
        )
    return CheckVerdict(
        INCONCLUSIVE,
        criterion,
        "The same-phone comparison returned no usable verdict for this document.",
        facts,
    )


# ── readability ───────────────────────────────────────────────────────────────

def _check_readability(raw: dict[str, Any]) -> CheckVerdict:
    criterion = "Passes when the document is machine-readable. Fails when it is poor or unreadable."
    verdict = _text(raw, "verdict").lower()
    score = raw.get("score")
    passed = _count(raw.get("tests_passed"))
    total = _count(raw.get("tests_total"))
    failed_tests = [
        str(test.get("name")) for test in (raw.get("tests") or [])
        if isinstance(test, dict) and test.get("passed") is False and test.get("name")
    ]
    facts = _facts(
        ("Readability score", f"{score}/100" if isinstance(score, int) else None),
        ("Sub-tests passed", f"{passed}/{total}" if total else None),
        ("Failed sub-tests", failed_tests[:6] or "None"),
    )

    if verdict == "readable":
        return CheckVerdict(
            PASS,
            criterion,
            f"The document is machine-readable, scoring {score}/100 with {passed} of "
            f"{total} sub-tests passed.",
            facts,
        )
    if verdict in {"poor_readability", "unreadable"}:
        label = "unreadable" if verdict == "unreadable" else "poor"
        return CheckVerdict(
            FAIL,
            criterion,
            f"Machine readability is {label}, scoring {score}/100 with {total - passed} of "
            f"{total} sub-tests failing"
            + (f" ({', '.join(failed_tests[:3])})." if failed_tests else "."),
            facts,
        )
    return CheckVerdict(
        INCONCLUSIVE,
        criterion,
        "The readability checker returned no verdict for this file, so its machine "
        "readability could not be established.",
        facts,
    )


EVALUATORS: dict[str, Callable[[dict[str, Any]], CheckVerdict]] = {
    "metadata": _check_metadata,
    "font_analysis": _check_font_analysis,
    "signature": _check_signature,
    "photo_detection": _check_photo_detection,
    "qr_presence": _check_qr_presence,
    "tamper_scan": _check_tamper_scan,
    "moire": _check_moire,
    "scanner_noise": _check_scanner_noise,
    "same_phone": _check_same_phone,
    "readability": _check_readability,
}


def criterion_for(analyzer_id: str) -> str:
    """The fixed rule for a check, statable before any document is screened."""

    evaluator = EVALUATORS.get(analyzer_id)
    if evaluator is None:
        return "Passes when the detector reports no signal. Fails when it reports one."
    return evaluator({}).criterion


def evaluate(analyzer_id: str, raw: dict[str, Any]) -> CheckVerdict:
    """Apply a detector's own pass/fail rule to one raw detector payload."""

    criterion = criterion_for(analyzer_id)
    status = str(raw.get("status") or "").strip().lower()
    if status == "error":
        detail = _text(raw, "detail") or "The detector stopped with an error."
        return CheckVerdict(
            ERROR,
            criterion,
            f"This check could not run: {detail}",
        )

    evaluator = EVALUATORS.get(analyzer_id)
    if evaluator is None:
        # An analyzer added without a rule here still gets an honest reading
        # from the shared fields rather than a silent default tick.
        if raw.get("passed") is False or str(raw.get("risk", "")).lower() in {"medium", "high"}:
            return CheckVerdict(FAIL, criterion, _text(raw, "note", "reason") or "The detector reported a signal.")
        if status in {"insufficient_data", "inconclusive"}:
            return CheckVerdict(
                INCONCLUSIVE, criterion,
                _text(raw, "note", "reason") or "The detector could not reach a conclusion.",
            )
        return CheckVerdict(PASS, criterion, _text(raw, "note", "reason") or "The detector reported no signal.")

    try:
        return evaluator(raw)
    except Exception as exc:  # noqa: BLE001 - a rule bug must not strand the result
        return CheckVerdict(
            INCONCLUSIVE,
            criterion,
            f"The result could not be evaluated against this check's rule: {exc}",
        )
