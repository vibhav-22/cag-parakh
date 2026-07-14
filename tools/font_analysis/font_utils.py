"""
Shared utilities for the PDF font baseline / comparison tooling.

Centralizing this logic means create_font_baseline.py and
compare_pdf_fonts.py can't drift out of sync on how a font name or a
text string gets normalized - which matters a lot here, since:

  - Type3 fonts have no /BaseFont key at all (PDF spec), so PyMuPDF's
    basefont field is blank for them. Left unresolved, they either get
    silently merged into one useless "blank" bucket, or lost entirely
    at the span level.
  - The same typeface can be reported as "ArialUnicodeMS" (from
    /BaseFont) in one place and "Arial Unicode MS" (from
    /FontDescriptor's /FontFamily) in another - these must be treated
    as the same font when diffing, not two different ones.
  - Devanagari text can be represented in different Unicode
    normalization forms depending on which renderer produced the PDF,
    so two visually-identical strings can fail to match as dict keys
    unless normalized first.
"""

import re
import unicodedata
from pathlib import Path

SUBSET_TAG_RE = re.compile(r"^[A-Z]{6}\+(.+)$")
UNNAMED_LABEL = "UNNAMED_INTERNAL_FONT"


def strip_subset_tag(name):
    """Strip the 6-uppercase-letter subset prefix, e.g. 'AAAAAA+Verdana' -> 'Verdana'."""
    if not name:
        return name
    m = SUBSET_TAG_RE.match(name)
    return m.group(1) if m else name


def normalize_for_grouping(name):
    """
    Comparison-only key: collapses spacing/case/hyphen differences so
    'ArialUnicodeMS', 'Arial Unicode MS', and 'arial-unicode-ms' are all
    recognized as the same font. Never shown to the user directly.
    """
    if not name:
        return ""
    return re.sub(r"[\s\-_]+", "", name).lower()


def resolve_font_family_from_xref(doc, xref):
    """
    Resolve a typeface name via a font object's /FontDescriptor ->
    /FontFamily (falling back to /FontName). This is the piece PyMuPDF's
    high-level basefont field doesn't give you for Type3 fonts.
    """
    try:
        font_obj = doc.xref_object(xref, compressed=False)
    except Exception:
        return None

    fd_match = re.search(r"/FontDescriptor\s+(\d+)\s+\d+\s+R", font_obj)
    if not fd_match:
        return None

    fd_xref = int(fd_match.group(1))
    try:
        fd_obj = doc.xref_object(fd_xref, compressed=False)
    except Exception:
        return None

    fam_match = re.search(r"/FontFamily\s*\(([^)]+)\)", fd_obj)
    if fam_match:
        return fam_match.group(1)

    name_match = re.search(r"/FontName\s*/(\S+)", fd_obj)
    if name_match:
        return strip_subset_tag(name_match.group(1))

    return None


def build_page_font_catalog(doc, page):
    """
    Build a per-page lookup of every font object, resolving Type3 fonts
    via their FontDescriptor. Used to backfill span['font'] when PyMuPDF
    returns it blank.

    NOTE: PyMuPDF's get_text("dict") spans don't carry the font's xref,
    so we can't always tie a specific blank span back to one exact font
    object with certainty. When a page has exactly one distinct resolved
    Type3 family, we use it confidently. When a page has multiple
    different Type3 families, a blank span is flagged as ambiguous rather
    than guessed at - this is a known limitation, see clean_font_name().
    """
    catalog = {"type3_families": set()}

    try:
        page_fonts = page.get_fonts(full=True)
    except Exception:
        return catalog

    for font in page_fonts:
        xref, ext, font_type, base_font, resource_name, encoding = font[:6]

        if not base_font:
            resolved = resolve_font_family_from_xref(doc, xref)
            if resolved:
                catalog["type3_families"].add(resolved)

    return catalog


def clean_font_name(raw_font, catalog=None):
    """
    Resolve a span's raw font string to a clean display name.

    1. Non-empty raw_font -> strip subset tag, use directly.
    2. Empty raw_font (typically Type3 fallback glyphs) -> use the page's
       single resolved Type3 family if there's exactly one candidate.
       If there are zero or multiple candidates, return an explicit
       UNNAMED_INTERNAL_FONT label (optionally listing candidates) rather
       than silently guessing or merging - this should be reviewed
       manually via page.get_fonts(full=True) on the source PDF.
    """
    if raw_font:
        return strip_subset_tag(raw_font)

    if catalog:
        families = catalog.get("type3_families", set())
        if len(families) == 1:
            return next(iter(families))
        if len(families) > 1:
            return f"{UNNAMED_LABEL} (ambiguous Type3 - candidates: {', '.join(sorted(families))})"

    return UNNAMED_LABEL


def detect_font_style(font_name, flags):
    name = (font_name or "").lower()

    is_bold = (
        "bold" in name
        or "black" in name
        or "semibold" in name
        or bool(flags & 16)
    )

    is_italic = (
        "italic" in name
        or "oblique" in name
        or bool(flags & 2)
    )

    return {"is_bold": is_bold, "is_italic": is_italic}


def normalize_text(text):
    """
    Cleans text for comparison. Applies Unicode NFC normalization first -
    important for Devanagari, where the same visible character can be
    represented as different underlying codepoint sequences (composed vs
    decomposed matras) depending on the renderer, which would otherwise
    make identical-looking text fail to match as a dict key.
    """
    if not text:
        return ""

    text = unicodedata.normalize("NFC", text)
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text


def extract_text_spans(pdf_path):
    """
    Extract every text span from a PDF, with:
      - Type3 fonts resolved via FontDescriptor where possible
      - font names grouped by a normalized key so cosmetic naming
        differences don't get counted as different fonts
      - page sizes recorded, so bbox comparisons elsewhere can be done in
        page-relative (not raw point) coordinates
      - text run through NFC normalization
    """
    import fitz  # imported here so font_utils.py can be unit-tested
                 # without requiring PyMuPDF for callers that only need
                 # the pure string-handling helpers above.

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(pdf_path)

    spans = []
    font_groups = {}      # normalized_key -> best display name seen
    internal_fonts = set()
    page_sizes = {}

    for page_index in range(len(doc)):
        page = doc[page_index]
        page_sizes[str(page_index + 1)] = [
            round(page.rect.width, 2),
            round(page.rect.height, 2),
        ]

        catalog = build_page_font_catalog(doc, page)
        text_dict = page.get_text("dict")

        for block in text_dict.get("blocks", []):
            if "lines" not in block:
                continue

            for line in block["lines"]:
                for span in line.get("spans", []):
                    text = normalize_text(span.get("text", ""))
                    if not text:
                        continue

                    raw_font = span.get("font", "")
                    clean_font = clean_font_name(raw_font, catalog)
                    font_size = round(float(span.get("size", 0)), 2)
                    flags = int(span.get("flags", 0))
                    bbox = [round(x, 2) for x in span.get("bbox", [])]

                    style = detect_font_style(clean_font, flags)

                    if clean_font.startswith(UNNAMED_LABEL):
                        internal_fonts.add(clean_font)
                    else:
                        key = normalize_for_grouping(clean_font)
                        if key not in font_groups:
                            font_groups[key] = clean_font
                        elif " " in clean_font and " " not in font_groups[key]:
                            # Prefer the more human-readable spaced form
                            # (usually the FontDescriptor /FontFamily one)
                            font_groups[key] = clean_font

                    spans.append({
                        "page": page_index + 1,
                        "text": text,
                        "font": clean_font,
                        "raw_font": raw_font,
                        "font_size": font_size,
                        "flags": flags,
                        "is_bold": style["is_bold"],
                        "is_italic": style["is_italic"],
                        "bbox": bbox,
                    })

    doc.close()

    named_fonts = sorted(font_groups.values())

    return {
        "pdf_file": str(pdf_path),
        "page_sizes": page_sizes,
        "named_font_count": len(named_fonts),
        "named_fonts": named_fonts,
        "internal_fonts": sorted(internal_fonts),
        "spans": spans,
    }
