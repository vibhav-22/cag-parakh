"""
font_store_check.py

Three commands:

  store    — scan one or more known-correct PDFs, collect every font name,
              save to a JSON store.

      python font_store_check.py store true1.pdf true2.pdf --store fonts.json

  check    — scan a new document, flag any font not present in the store.

      python font_store_check.py check new_doc.pdf --store fonts.json

  inspect  — analyse a PDF and report how it was produced: official govt app,
              HTML/browser renderer, or scanned image.

      python font_store_check.py inspect any.pdf
"""

import argparse
import json
import re
import sys
from pathlib import Path

from font_utils import extract_text_spans, normalize_for_grouping


STORE_DEFAULT = "known_fonts.json"

# Type3 fonts are reported by PyMuPDF as "Type3 (10 0 R)" using their internal
# PDF object reference number, which is document-specific and changes per file.
# Storing or comparing them by xref is meaningless, so we exclude them from the
# named font store and just count them separately.
_TYPE3_XREF_RE = re.compile(r"^Type3\s*\(\d+\s+\d+\s+R\)$", re.IGNORECASE)


def is_type3_xref(font_name):
    return bool(_TYPE3_XREF_RE.match(font_name or ""))


# ── helpers ──────────────────────────────────────────────────────────────────

def load_store(path):
    p = Path(path)
    if not p.exists():
        return {"known_fonts": [], "sources": []}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_store(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def fonts_from_pdf(pdf_path):
    """Return (named_fonts, internal_fonts, type3_count) extracted via font_utils."""
    result = extract_text_spans(pdf_path)
    named = [f for f in result["named_fonts"] if not is_type3_xref(f)]
    type3 = [f for f in result["named_fonts"] if is_type3_xref(f)]
    return named, result["internal_fonts"], len(type3)


# ── store command ─────────────────────────────────────────────────────────────

def cmd_store(args):
    store = load_store(args.store)
    existing_keys = {normalize_for_grouping(f) for f in store["known_fonts"]}
    existing_sources = set(store.get("sources", []))

    added = []

    for pdf in args.pdfs:
        pdf_path = str(Path(pdf).resolve())
        if pdf_path in existing_sources:
            print(f"  [skip] already in store: {pdf}")
            continue

        print(f"  Extracting: {pdf}")
        try:
            named, internal, type3_count = fonts_from_pdf(pdf)
        except Exception as e:
            print(f"  [error] {pdf}: {e}")
            continue

        for font in named:
            key = normalize_for_grouping(font)
            if key not in existing_keys:
                store["known_fonts"].append(font)
                existing_keys.add(key)
                added.append(font)

        store.setdefault("sources", []).append(pdf_path)
        existing_sources.add(pdf_path)

        if type3_count:
            print(f"    Note: {type3_count} Type3 font object(s) found (glyph/stamp/signature shapes) — excluded from store (xref IDs are document-specific).")
        if internal:
            print(f"    Note: {len(internal)} unresolved internal font(s) — not stored.")

    store["known_fonts"] = sorted(store["known_fonts"])
    save_store(store, args.store)

    print(f"\nStore saved to : {args.store}")
    print(f"Total PDFs     : {len(store['sources'])}")
    print(f"Total fonts    : {len(store['known_fonts'])}")
    print(f"Newly added    : {len(added)}")
    if added:
        for f in added:
            print(f"  + {f}")


# ── check command ─────────────────────────────────────────────────────────────

def cmd_check(args):
    store_path = Path(args.store)
    if not store_path.exists():
        sys.exit(f"Font store not found: {store_path}\nRun 'store' first.")

    store = load_store(args.store)
    known_keys = {normalize_for_grouping(f) for f in store["known_fonts"]}

    print(f"Checking: {args.pdf}")
    print(f"Store   : {args.store}  ({len(store['known_fonts'])} known fonts)\n")

    try:
        named, internal, type3_count = fonts_from_pdf(args.pdf)
    except Exception as e:
        sys.exit(f"Error reading PDF: {e}")

    unknown = [f for f in named if normalize_for_grouping(f) not in known_keys]
    known_present = [f for f in named if normalize_for_grouping(f) in known_keys]

    print(f"Fonts in document : {len(named)}")
    print(f"  Known           : {len(known_present)}")
    print(f"  UNKNOWN (new)   : {len(unknown)}")
    if type3_count:
        print(f"  Type3 (shapes)  : {type3_count}  (glyph/stamp objects — not compared by name)")
    if internal:
        print(f"  Unresolved      : {len(internal)}  (check manually)")

    if unknown:
        print("\n[ALERT] Unknown font(s) detected — not present in any stored true PDF:")
        for f in unknown:
            print(f"  ! {f}")
    else:
        print("\n[OK] All fonts are recognised — no new font types detected.")

    if internal:
        print("\nUnresolved fonts (could not identify automatically):")
        for f in internal:
            print(f"  ? {f}")

    # optional JSON report
    if args.output:
        report = {
            "pdf": str(Path(args.pdf).resolve()),
            "store": str(store_path.resolve()),
            "passed": len(unknown) == 0,
            "known_fonts_in_doc": known_present,
            "unknown_fonts": unknown,
            "unresolved_internal_fonts": internal,
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4, ensure_ascii=False)
        print(f"\nReport saved to: {args.output}")

    sys.exit(0 if not unknown else 1)


# ── inspect command ───────────────────────────────────────────────────────────

# Producers/Creators that indicate a browser / HTML-to-PDF renderer
_WEB_PRODUCERS = re.compile(
    r"chromium|chrome|webkit|skia|wkhtmltopdf|weasyprint|puppeteer|playwright|"
    r"prince|fpdf|tcpdf|dompdf|html|phantomjs|electron",
    re.IGNORECASE,
)

# Web / Google fonts that never appear in official system-font-based generators
_WEB_FONTS = re.compile(
    r"roboto|opensans|open.sans|lato|montserrat|raleway|nunito|poppins|"
    r"sourcesans|source.sans|inter|ubuntu|noto|merriweather|playfair|"
    r"mulish|outfit|firasans|fira.sans",
    re.IGNORECASE,
)

# Windows / Office system fonts used by most official govt desktop apps
_SYSTEM_FONTS = re.compile(
    r"arial|times.new.roman|timesnewroman|verdana|calibri|cambria|"
    r"nirmala|mangal|kokila|utsaah|aparajita|latha|gautami|raavi|"
    r"shruti|tunga|vani|vijaya|kartika|chandas|samyak|devanagari",
    re.IGNORECASE,
)


def _classify_font(name):
    if _WEB_FONTS.search(name):
        return "web"
    if _SYSTEM_FONTS.search(name):
        return "system"
    return "unknown"


def cmd_inspect(args):
    import fitz

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        sys.exit(f"PDF not found: {pdf_path}")

    doc = fitz.open(pdf_path)
    meta = doc.metadata

    print("=" * 60)
    print(f"PDF  : {pdf_path.name}")
    print("=" * 60)

    # ── 1. Metadata ────────────────────────────────────────────────
    print("\n[ Metadata ]")
    creator  = meta.get("creator", "").strip()
    producer = meta.get("producer", "").strip()
    print(f"  Creator  : {creator  or '(none)'}")
    print(f"  Producer : {producer or '(none)'}")
    print(f"  Created  : {meta.get('creationDate', '(none)')}")
    print(f"  Modified : {meta.get('modDate', '(none)')}")

    # ── 2. Page content analysis ───────────────────────────────────
    print("\n[ Page content ]")
    total_chars = 0
    total_image_area = 0.0
    total_page_area = 0.0

    for page in doc:
        text = page.get_text().strip()
        total_chars += len(text)
        page_area = page.rect.width * page.rect.height
        total_page_area += page_area
        for img in page.get_images():
            xref = img[0]
            for rect in page.get_image_rects(xref):
                total_image_area += abs(rect.width * rect.height)

    image_coverage = (total_image_area / total_page_area * 100) if total_page_area else 0
    print(f"  Pages          : {len(doc)}")
    print(f"  Extractable chars : {total_chars}")
    print(f"  Image area coverage : {image_coverage:.1f}% of total page area")

    # ── 3. Font analysis ───────────────────────────────────────────
    print("\n[ Fonts ]")
    seen_xrefs = set()
    font_classes = {"web": [], "system": [], "unknown": [], "type3": []}

    for page in doc:
        for font in page.get_fonts(full=True):
            xref, ext, ftype, base, name, enc = font[:6]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            if ftype == "Type3":
                font_classes["type3"].append(name or "(unnamed)")
                continue

            display = base or name or "(unnamed)"
            display = re.sub(r"^[A-Z]{6}\+", "", display)  # strip subset tag
            cls = _classify_font(display)
            font_classes[cls].append(display)

    for label, fonts in font_classes.items():
        if fonts:
            tag = {"web": "WEB", "system": "SYSTEM", "unknown": "OTHER", "type3": "TYPE3 (shapes)"}[label]
            print(f"  [{tag}]")
            for f in fonts:
                print(f"    - {f}")

    doc.close()

    # ── 4. Verdict ─────────────────────────────────────────────────
    print("\n[ Origin verdict ]")

    signals = []
    verdict = None

    # Check both producer and creator for browser/web signals
    is_web_producer = bool(_WEB_PRODUCERS.search(producer) or _WEB_PRODUCERS.search(creator))

    # Flag if the document was modified after creation (possible re-signing or tampering)
    created  = meta.get("creationDate", "")
    modified = meta.get("modDate", "")
    if created and modified and created != modified:
        print(f"  [!] Document was modified after creation")
        print(f"      Created  : {created}")
        print(f"      Modified : {modified}")

    # Scanned: very little text, high image coverage
    if total_chars < 100 and image_coverage > 60:
        verdict = "SCANNED IMAGE PDF"
        signals.append(f"only {total_chars} extractable characters with {image_coverage:.0f}% image area")

    # Hybrid scan+OCR: some text but mostly images
    elif total_chars > 100 and image_coverage > 60:
        verdict = "SCANNED + OCR TEXT LAYER"
        signals.append(f"{image_coverage:.0f}% image area but {total_chars} chars suggests OCR overlay")

    else:
        # Text-based: classify by producer and fonts
        has_web_fonts   = bool(font_classes["web"])
        has_system_fonts = bool(font_classes["system"])

        if is_web_producer and has_system_fonts and not has_web_fonts:
            verdict = "BROWSER-PRINTED — OFFICIAL GOVT WEBSITE"
            signals.append(f"Producer: {producer!r}  (browser/Chrome PDF engine)")
            signals.append("Uses Windows system fonts -> printed from the official portal")
            signals.append(f"System fonts: {font_classes['system']}")

        elif is_web_producer and has_web_fonts and not has_system_fonts:
            verdict = "BROWSER-PRINTED — UNOFFICIAL / RECREATED PAGE"
            signals.append(f"Producer: {producer!r}  (browser/Chrome PDF engine)")
            signals.append("Uses web/Google fonts -> NOT from the official portal (different site or template)")
            signals.append(f"Web fonts: {font_classes['web']}")

        elif is_web_producer and has_web_fonts and has_system_fonts:
            verdict = "BROWSER-PRINTED — MIXED ORIGIN (suspicious)"
            signals.append(f"Producer: {producer!r}")
            signals.append(f"System fonts: {font_classes['system']}")
            signals.append(f"Web fonts: {font_classes['web']}")

        elif is_web_producer and not has_web_fonts and not has_system_fonts:
            verdict = "BROWSER-PRINTED — FONT ORIGIN UNCLEAR"
            signals.append(f"Producer: {producer!r}")

        elif has_web_fonts and not has_system_fonts:
            verdict = "HTML / WEB-BASED GENERATOR (non-browser tool)"
            signals.append(f"Web font(s) with no system fonts: {font_classes['web']}")

        elif has_system_fonts and not has_web_fonts:
            verdict = "NATIVE DESKTOP APPLICATION (govt software / Office)"
            signals.append(f"System font(s): {font_classes['system']}")
            if creator:
                signals.append(f"Creator tool: {creator!r}")

        elif has_system_fonts and has_web_fonts:
            verdict = "MIXED — system + web fonts (possible conversion or merge)"
            signals.append(f"System: {font_classes['system']}")
            signals.append(f"Web: {font_classes['web']}")

        else:
            verdict = "UNKNOWN ORIGIN"
            signals.append("No recognisable fonts and no clear producer metadata")

    print(f"\n  >> {verdict}")
    print("\n  Evidence:")
    for s in signals:
        print(f"    - {s}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="font_store_check",
        description="Store fonts from true PDFs and detect unknown fonts in new documents.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_store = sub.add_parser("store", help="Add fonts from true/correct PDFs to the store.")
    p_store.add_argument("pdfs", nargs="+", metavar="PDF", help="Known-correct PDF file(s)")
    p_store.add_argument("--store", default=STORE_DEFAULT, metavar="FILE",
                         help=f"Font store file (default: {STORE_DEFAULT})")

    p_check = sub.add_parser("check", help="Check a document for unknown font types.")
    p_check.add_argument("pdf", metavar="PDF", help="PDF to check")
    p_check.add_argument("--store", default=STORE_DEFAULT, metavar="FILE",
                         help=f"Font store file (default: {STORE_DEFAULT})")
    p_check.add_argument("--output", default=None, metavar="FILE",
                         help="Optional JSON report output path")

    p_inspect = sub.add_parser("inspect", help="Detect whether a PDF is from a govt app, web renderer, or scanner.")
    p_inspect.add_argument("pdf", metavar="PDF", help="PDF to inspect")

    args = parser.parse_args()
    if args.command == "store":
        cmd_store(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "inspect":
        cmd_inspect(args)


if __name__ == "__main__":
    main()
