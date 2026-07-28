"""
metadata_check.py — Feature: metadata_check

Detects PDFs that *look* like a plain single-pass scan but carry structural /
metadata traces of having been edited in software (Photoshop / GIMP / img2pdf),
re-saved incrementally, or overlaid with annotations. None of these traces are
visible to the pixel-level checks, so this fills a distinct gap: catching a
"scan" that was actually manipulated as a file.

This check is deterministic (no image analysis) and standalone. It combines
several independent signals and scores by how many — and how strong — they are.
It only flags a document as "suspicious" / needing manual review; it never
declares fraud.

    python metadata_check.py document.pdf
    python metadata_check.py document.pdf --output result.json

Signals (each contributes to risk):
  1. incremental_update      — file re-saved after creation (multiple %%EOF /
                               /Prev in the trailer / multiple xref sections)
  2. date_inconsistency      — ModDate later than CreationDate
  3. editor_producer         — Producer/Creator names an image editor / converter
                               rather than a scanner or govt e-office pipeline
  4. editor_image_metadata   — an embedded image carries editor XMP/EXIF tags
                               (Adobe Photoshop, GIMP, xmp:CreatorTool, …)
  5. suspicious_structure    — embedded JavaScript, AcroForm, or overlay
                               annotations (FreeText / Redact / Stamp) on a doc
                               that should be a flat scan

JSON schema returned by run():
    {
      "feature": "metadata_check",
      "status": "passed" | "failed" | "insufficient_data",
      "passed": bool,
      "risk": "low" | "medium" | "high",
      "pages_checked": int,
      "suspicious_regions_count": int,          # number of signals fired
      "issues": [
        {
          "page": int | null,                   # null = document-level signal
          "bbox": [x1, y1, x2, y2] | null,
          "signal": str,
          "detail": str,
          "reason": str
        }
      ],
      "document_metadata": {...},                 # producer/creator/dates as read
      "generation": {...}                         # how the file was produced
    }

`document_metadata` and `generation` are descriptive, not judgemental: they are
present on a clean document too, because the reviewer reads this check as a
provenance record rather than as a pass/fail test.
"""

import argparse
import io
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF — already a project dependency

FEATURE = "metadata_check"

# Producer/Creator substrings (lower-cased) split into two tiers. Conservative
# on purpose: an unknown producer is treated as NEUTRAL (not flagged).
#
#   EDITOR — raster/image editors. Their presence means pixels were edited; a
#   STRONG signal on a document claimed to be a scan.
EDITOR_PRODUCER_MARKERS = (
    "photoshop", "gimp", "imagemagick", "inkscape", "pixelmator",
    "affinity", "corel", "paint.net", "krita",
)
#   RENDERER — HTML/print-to-PDF and generic conversion pipelines. Common and
#   often LEGITIMATE for born-digital govt e-certificates (e-district portals
#   print from a browser), so this is a WEAK signal — surfaced, not alarming.
#   It is only meaningful when the document is *claimed/expected* to be a scan.
RENDERER_PRODUCER_MARKERS = (
    "skia/pdf", "cairo", "wkhtmltopdf", "img2pdf", "canva",
    "ilovepdf", "smallpdf", "pdf24", "sejda", "pdfsam", "microsoft: print to pdf",
)

# Byte/string markers of editor provenance inside an embedded image's own
# metadata (XMP packet or EXIF Software field).
EDITOR_IMAGE_MARKERS = (
    "adobe photoshop", "photoshop", "gimp", "xmp:creatortool", "pixelmator",
    "affinity photo", "paint.net", "imagemagick", "krita",
)

# Structural markers scanned in the raw PDF bytes.
_JS_RE = re.compile(rb"/JavaScript|/JS\b")
_ACROFORM_RE = re.compile(rb"/AcroForm\b")

# Overlay annotation subtypes that are suspicious on a flat scan (text added on
# top, redaction boxes, rubber stamps). Link/Popup/Widget are ignored as benign.
SUSPICIOUS_ANNOT_SUBTYPES = {"FreeText", "Redact", "Stamp", "Highlight", "Squiggly", "StrikeOut"}

# Signals considered "strong" for risk tiering.
STRONG_SIGNALS = {"incremental_update", "editor_producer", "editor_image_metadata"}


# ── date parsing ──────────────────────────────────────────────────────────────

def _parse_pdf_date(raw):
    """
    Parse a PDF date string (e.g. 'D:20240131120000+05'30'') into a comparable
    integer YYYYMMDDHHMMSS, ignoring the timezone. Returns None if unparseable.

    Timezone is intentionally dropped: we only use this to compare two dates from
    the SAME document (created vs modified), which share a timezone convention,
    so a coarse comparison is sufficient and robust to messy tz formatting.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if s.startswith("D:"):
        s = s[2:]
    m = re.match(r"(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", s)
    if not m or not m.group(1):
        return None
    parts = [m.group(1)] + [(g or "00") for g in m.groups()[1:]]
    try:
        return int("".join(parts))
    except ValueError:
        return None


# ── individual signal detectors ───────────────────────────────────────────────

def _detect_incremental_update(raw_bytes):
    """
    A single-pass save writes exactly one body + xref + trailer + %%EOF. A file
    that was opened and re-saved (incremental update) appends a second body and
    ends with another %%EOF, and its trailer references the previous xref via
    /Prev. Either is a strong sign the 'scan' was modified after creation.
    """
    eof_count = len(re.findall(rb"%%EOF", raw_bytes))
    prev_count = len(re.findall(rb"/Prev\b", raw_bytes))
    if eof_count > 1 or prev_count > 0:
        detail = f"{eof_count} %%EOF marker(s), {prev_count} /Prev xref link(s)"
        return {
            "page": None,
            "bbox": None,
            "signal": "incremental_update",
            "detail": detail,
            "reason": "PDF was re-saved after creation (incremental update); a "
                      "genuine single-pass scan has exactly one body/xref/%%EOF",
        }
    return None


def _detect_date_inconsistency(meta):
    created = _parse_pdf_date(meta.get("creationDate"))
    modified = _parse_pdf_date(meta.get("modDate"))
    if created is not None and modified is not None and modified > created:
        return {
            "page": None,
            "bbox": None,
            "signal": "date_inconsistency",
            "detail": f"CreationDate={meta.get('creationDate')} < ModDate={meta.get('modDate')}",
            "reason": "Document was modified after it was created; a pristine "
                      "scan is created once and not subsequently modified",
        }
    return None


def _detect_editor_producer(meta):
    blob = " ".join(str(meta.get(k, "")) for k in ("producer", "creator")).lower()
    editor_hits = [m for m in EDITOR_PRODUCER_MARKERS if m in blob]
    if editor_hits:
        return {
            "page": None,
            "bbox": None,
            "signal": "editor_producer",
            "detail": f"Producer/Creator matches image editor {editor_hits}: "
                      f"producer={meta.get('producer')!r} creator={meta.get('creator')!r}",
            "reason": "PDF was produced/edited by an image editor, not a scanner "
                      "driver — pixels were manipulated in software",
        }
    renderer_hits = [m for m in RENDERER_PRODUCER_MARKERS if m in blob]
    if renderer_hits:
        return {
            "page": None,
            "bbox": None,
            "signal": "renderer_producer",
            "detail": f"Producer/Creator matches HTML/print renderer {renderer_hits}: "
                      f"producer={meta.get('producer')!r} creator={meta.get('creator')!r}",
            "reason": "PDF was generated by a browser/HTML print-to-PDF pipeline "
                      "(born-digital), not a scanner — expected for many e-certificates, "
                      "but suspicious if the document is claimed to be a scan",
        }
    return None


def _detect_editor_image_metadata(doc):
    """
    Scan every embedded image's own bytes for editor provenance (XMP packet or
    EXIF Software tag). A raw scanner image carries at most camera/scanner EXIF;
    an image edited in Photoshop/GIMP carries that tool's XMP/CreatorTool.
    """
    issues = []
    seen_xrefs = set()
    for page_index in range(len(doc)):
        for img in doc.get_page_images(page_index, full=True):
            xref = img[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            try:
                info = doc.extract_image(xref)
            except Exception:
                continue
            image_bytes = info.get("image", b"")
            marker = _scan_image_for_editor(image_bytes)
            if marker:
                issues.append({
                    "page": page_index + 1,
                    "bbox": None,
                    "signal": "editor_image_metadata",
                    "detail": f"Embedded image xref {xref} carries editor tag {marker!r}",
                    "reason": "An embedded image was processed by an image editor "
                              "(editor XMP/EXIF present), inconsistent with a raw scan",
                })
    return issues


def _scan_image_for_editor(image_bytes):
    """Return the first editor marker found in an image's XMP/EXIF, else None."""
    if not image_bytes:
        return None
    # Cheap raw-byte scan of any embedded XMP packet first (covers most cases).
    lowered = image_bytes.lower()
    for marker in EDITOR_IMAGE_MARKERS:
        if marker.encode("utf-8") in lowered:
            return marker
    # Fall back to a structured EXIF Software-tag read via Pillow.
    try:
        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as im:
            exif = im.getexif()
            software = str(exif.get(0x0131, "")).lower()  # 0x0131 == Software
            for marker in EDITOR_IMAGE_MARKERS:
                if marker in software:
                    return marker
    except Exception:
        pass
    return None


def _detect_suspicious_structure(doc, raw_bytes):
    """Embedded JS, AcroForm, or overlay annotations on a would-be flat scan."""
    issues = []
    if _JS_RE.search(raw_bytes):
        issues.append({
            "page": None,
            "bbox": None,
            "signal": "suspicious_structure",
            "detail": "Embedded JavaScript (/JavaScript or /JS) present",
            "reason": "A flat scan has no reason to embed JavaScript",
        })
    if _ACROFORM_RE.search(raw_bytes):
        issues.append({
            "page": None,
            "bbox": None,
            "signal": "suspicious_structure",
            "detail": "AcroForm (interactive form) present",
            "reason": "Interactive form overlay on a document that should be a flat scan",
        })

    for page_index in range(len(doc)):
        page = doc[page_index]
        try:
            annots = page.annots()
        except Exception:
            annots = None
        if not annots:
            continue
        for annot in annots:
            try:
                subtype = annot.type[1]  # (int, "FreeText")
            except Exception:
                continue
            if subtype in SUSPICIOUS_ANNOT_SUBTYPES:
                rect = annot.rect
                issues.append({
                    "page": page_index + 1,
                    "bbox": [round(rect.x0, 1), round(rect.y0, 1),
                             round(rect.x1, 1), round(rect.y1, 1)],
                    "signal": "suspicious_structure",
                    "detail": f"{subtype} annotation overlaid on page {page_index + 1}",
                    "reason": "Text/redaction/stamp annotation overlaid on a flat scan "
                              "can hide or alter the underlying content",
                })
    return issues


# ── risk tiering ──────────────────────────────────────────────────────────────

def _document_risk(issues):
    """
    high   — 3+ signals, OR an editor fingerprint together with a re-save
    medium — 2+ signals, OR any single strong signal
    low    — a single weak signal
    """
    if not issues:
        return "low"
    names = {i["signal"] for i in issues}
    editor = names & {"editor_producer", "editor_image_metadata"}
    if len(names) >= 3 or (editor and "incremental_update" in names):
        return "high"
    if len(names) >= 2 or (names & STRONG_SIGNALS):
        return "medium"
    return "low"


# ── public entry point ────────────────────────────────────────────────────────

def run(pdf_path, dpi=200, debug_dir=None):
    """
    Run the metadata/structural tamper check and return the result dict.

    `dpi` and `debug_dir` are accepted for signature-compatibility with the other
    checks (this check does no rendering, so they are unused).
    """
    path = Path(pdf_path)
    if not path.exists():
        return _insufficient(0, f"PDF not found: {path}")

    try:
        raw_bytes = path.read_bytes()
    except Exception as e:
        return _insufficient(0, f"Could not read file: {e}")

    try:
        doc = fitz.open(str(path))
    except Exception as e:
        # Defer true corruption to the dedicated PDF-validity check.
        return _insufficient(0, f"Not a parseable PDF ({e}); defer to PDF validity check")

    try:
        pages_checked = len(doc)
        meta = doc.metadata or {}

        issues = []
        for detector in (_detect_incremental_update,):
            found = detector(raw_bytes)
            if found:
                issues.append(found)
        for detector in (_detect_date_inconsistency, _detect_editor_producer):
            found = detector(meta)
            if found:
                issues.append(found)
        issues.extend(_detect_editor_image_metadata(doc))
        issues.extend(_detect_suspicious_structure(doc, raw_bytes))
        provenance = _describe_provenance(doc, meta, raw_bytes)
    finally:
        doc.close()

    passed = len(issues) == 0
    return {
        "feature": FEATURE,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "risk": _document_risk(issues),
        "pages_checked": pages_checked,
        "suspicious_regions_count": len(issues),
        "issues": issues,
        # Reported whatever the issue count. Metadata is read as a provenance
        # record — "how was this file made" — not as a pass/fail check, so the
        # facts have to be present on a clean document too.
        "document_metadata": _document_metadata(meta),
        "generation": provenance,
    }


# ── provenance description ────────────────────────────────────────────────────

# Producer/Creator substrings that name a scanner driver or a document-capture
# pipeline. Used only to describe provenance, never to flag.
SCANNER_PRODUCER_MARKERS = (
    "scan", "canoscan", "epson", "kodak", "fujitsu", "scansnap", "xerox",
    "ricoh", "sharpdesk", "hp digital sending", "naps2", "vuescan", "paperport",
)
OFFICE_PRODUCER_MARKERS = (
    "microsoft word", "microsoft excel", "microsoft powerpoint", "libreoffice",
    "openoffice", "pages", "google docs", "latex", "pdftex", "xetex", "quartz",
)
PDF_LIBRARY_MARKERS = (
    "itext", "reportlab", "pdfbox", "tcpdf", "fpdf", "jasperreports",
    "crystal reports", "pypdf", "pdflib", "acrobat distiller",
)


def _clean(value):
    text = str(value or "").strip()
    return text or None


def _readable_date(raw):
    """Render a PDF date string as 'YYYY-MM-DD HH:MM'. Returns None if unusable."""
    stamped = _parse_pdf_date(raw)
    if stamped is None:
        return None
    text = str(stamped).zfill(14)
    return f"{text[0:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}"


def _document_metadata(meta):
    """The identifying fields a reviewer reads to place a document's origin."""

    return {
        "producer": _clean(meta.get("producer")),
        "creator": _clean(meta.get("creator")),
        "title": _clean(meta.get("title")),
        "author": _clean(meta.get("author")),
        "subject": _clean(meta.get("subject")),
        "keywords": _clean(meta.get("keywords")),
        "format": _clean(meta.get("format")),
        "encryption": _clean(meta.get("encryption")),
        "created_at": _readable_date(meta.get("creationDate")),
        "modified_at": _readable_date(meta.get("modDate")),
        "creation_date_raw": _clean(meta.get("creationDate")),
        "mod_date_raw": _clean(meta.get("modDate")),
    }


def _generator_kind(blob):
    """Classify the producing pipeline from the Producer/Creator strings.

    Descriptive only. 'image_editor' is the one classification that is also a
    flagged signal, and the flag is raised by `_detect_editor_producer`, not
    here — this function's job is to say what made the file.
    """

    if not blob.strip():
        return "unknown", "Producer and Creator are both empty"
    for marker in EDITOR_PRODUCER_MARKERS:
        if marker in blob:
            return "image_editor", f"Made with an image editor ({marker})"
    for marker in SCANNER_PRODUCER_MARKERS:
        if marker in blob:
            return "scanner", f"Made by a scanner or capture pipeline ({marker})"
    for marker in RENDERER_PRODUCER_MARKERS:
        if marker in blob:
            return "browser_or_converter", f"Made by a browser print / converter pipeline ({marker})"
    for marker in OFFICE_PRODUCER_MARKERS:
        if marker in blob:
            return "office_export", f"Exported from an office or typesetting application ({marker})"
    for marker in PDF_LIBRARY_MARKERS:
        if marker in blob:
            return "pdf_library", f"Generated by a PDF library or report writer ({marker})"
    return "unknown", "Producer/Creator did not match any known pipeline"


def _describe_provenance(doc, meta, raw_bytes):
    """Structural facts about how this file was produced, independent of risk."""

    blob = " ".join(str(meta.get(key, "")) for key in ("producer", "creator")).lower()
    kind, explanation = _generator_kind(blob)

    eof_count = len(re.findall(rb"%%EOF", raw_bytes))
    prev_count = len(re.findall(rb"/Prev\b", raw_bytes))

    image_xrefs = set()
    text_pages = 0
    annotation_count = 0
    for page_index in range(len(doc)):
        page = doc[page_index]
        for img in doc.get_page_images(page_index, full=True):
            image_xrefs.add(img[0])
        try:
            if page.get_text("text").strip():
                text_pages += 1
        except Exception:
            pass
        try:
            annotation_count += sum(1 for _ in (page.annots() or []))
        except Exception:
            pass

    return {
        "kind": kind,
        "explanation": explanation,
        "producer": _clean(meta.get("producer")),
        "creator": _clean(meta.get("creator")),
        "pdf_version": _clean(meta.get("format")),
        "pages": len(doc),
        "embedded_images": len(image_xrefs),
        "pages_with_text_layer": text_pages,
        "annotations": annotation_count,
        "eof_markers": eof_count,
        "prev_xref_links": prev_count,
        "resaved_after_creation": eof_count > 1 or prev_count > 0,
        "has_javascript": bool(_JS_RE.search(raw_bytes)),
        "has_acroform": bool(_ACROFORM_RE.search(raw_bytes)),
    }


def _insufficient(pages_checked, note):
    return {
        "feature": FEATURE,
        "status": "insufficient_data",
        "passed": True,
        "risk": "low",
        "pages_checked": pages_checked,
        "suspicious_regions_count": 0,
        "issues": [],
        "note": note,
    }


def main():
    parser = argparse.ArgumentParser(
        prog="metadata_check",
        description="Flag PDFs with structural/metadata traces of software editing "
                    "or re-saving that a pixel-level scan check cannot see.",
    )
    parser.add_argument("pdf", metavar="PDF", help="PDF to check")
    parser.add_argument("--output", default=None, metavar="FILE",
                        help="Optional path to write the JSON result")
    parser.add_argument("--dpi", type=int, default=200, metavar="N",
                        help="Unused (accepted for CLI parity with other checks)")
    parser.add_argument("--debug-dir", default=None, metavar="DIR",
                        help="Unused (accepted for CLI parity with other checks)")
    args = parser.parse_args()

    result = run(args.pdf, dpi=args.dpi, debug_dir=args.debug_dir)
    text = json.dumps(result, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Result written to: {args.output}")
    print(text)

    sys.exit(1 if not result["passed"] else 0)


if __name__ == "__main__":
    main()
