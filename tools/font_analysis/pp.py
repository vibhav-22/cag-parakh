import fitz  # PyMuPDF
import json
import csv
import re
import argparse
from pathlib import Path


SUBSET_TAG_RE = re.compile(r"^[A-Z]{6}\+(.+)$")


def resolve_font_family(doc, xref):
    """
    For fonts where /BaseFont is missing or empty (this happens for Type3
    fonts, which have no /BaseFont key per the PDF spec), walk into the
    font's /FontDescriptor and pull /FontFamily, falling back to /FontName.

    This is the piece PyMuPDF's high-level get_fonts() doesn't do for you -
    it only reads /BaseFont, so Type3 fonts come back blank.
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

    # /FontFamily is a PDF string: /FontFamily (Arial Unicode MS)
    fam_match = re.search(r"/FontFamily\s*\(([^)]+)\)", fd_obj)
    if fam_match:
        return fam_match.group(1)

    # Fallback: /FontName is a PDF name: /FontName /AAAAAA+ArialUnicodeMS
    name_match = re.search(r"/FontName\s*/(\S+)", fd_obj)
    if name_match:
        return name_match.group(1)

    return None


def strip_subset_tag(name):
    """Strip the 6-uppercase-letter subset prefix, e.g. 'AAAAAA+ArialUnicodeMS' -> 'ArialUnicodeMS'."""
    if not name:
        return name
    m = SUBSET_TAG_RE.match(name)
    return m.group(1) if m else name


def normalize_for_grouping(name):
    """
    Collapse cosmetic naming differences so the SAME typeface doesn't get
    counted twice just because one source wrote it as 'ArialUnicodeMS'
    (from /BaseFont) and another wrote it as 'Arial Unicode MS' (from
    /FontFamily in the FontDescriptor). PDF generators are inconsistent
    about spacing/hyphens/case between these two sources - this is used
    ONLY as a comparison key, never shown to the user.
    """
    if not name:
        return ""
    return re.sub(r"[\s\-_]+", "", name).lower()


def extract_fonts_from_pdf(pdf_path: str):
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(pdf_path)

    font_records = []
    unique_fonts = {}          # keyed by raw font object (xref) - one row per embedded font object
    typeface_groups = {}       # keyed by NORMALIZED name - the "real" distinct font count

    for page_number in range(len(doc)):
        page = doc[page_number]

        page_fonts = page.get_fonts(full=True)

        for font in page_fonts:
            xref = font[0]
            ext = font[1]
            font_type = font[2]
            base_font = font[3]
            name = font[4]
            encoding = font[5]

            resolved_name = base_font
            resolved_via = "BaseFont"

            # This is the key fix: Type3 fonts (and any other font missing
            # /BaseFont) get resolved through FontDescriptor instead of
            # being left blank and silently merged together.
            if not resolved_name:
                fam = resolve_font_family(doc, xref)
                if fam:
                    resolved_name = fam
                    resolved_via = "FontDescriptor"

            typeface = strip_subset_tag(resolved_name) if resolved_name else "Unknown"

            # One row per actual embedded font object (xref) - this preserves
            # the true count of font objects in the file, never silently merges them.
            if xref not in unique_fonts:
                unique_fonts[xref] = {
                    "xref": xref,
                    "font_name": resolved_name or "(unresolved)",
                    "typeface": typeface,
                    "font_type": font_type,
                    "encoding": encoding,
                    "internal_name": name,
                    "extension": ext,
                    "resolved_via": resolved_via,
                    "pages_used": set(),
                }
            unique_fonts[xref]["pages_used"].add(page_number + 1)

            # Group by NORMALIZED typeface to get the count a human actually
            # means by "how many fonts" - same family across subsets/Type0+Type3
            # fallback, even if one source wrote it with spaces and another
            # without, is one font, not two.
            group_key = normalize_for_grouping(typeface)
            if group_key not in typeface_groups:
                typeface_groups[group_key] = {"display_name": typeface, "pages": set()}
            else:
                # Prefer a display name that has spaces (more human-readable,
                # usually the one from /FontFamily) over a squished BaseFont one.
                if " " in typeface and " " not in typeface_groups[group_key]["display_name"]:
                    typeface_groups[group_key]["display_name"] = typeface
            typeface_groups[group_key]["pages"].add(page_number + 1)

        text_dict = page.get_text("dict")

        for block in text_dict.get("blocks", []):
            if "lines" not in block:
                continue

            for line in block["lines"]:
                for span in line.get("spans", []):
                    font_name = span.get("font")
                    font_size = span.get("size")
                    flags = span.get("flags")
                    color = span.get("color")
                    text_sample = span.get("text", "").strip()

                    if not font_name:
                        continue

                    font_records.append({
                        "page": page_number + 1,
                        "font_name": font_name,
                        "font_size": round(font_size, 2) if font_size else None,
                        "flags": flags,
                        "color": color,
                        "text_sample": text_sample[:80],
                    })

    doc.close()

    final_unique_fonts = []
    for font_data in unique_fonts.values():
        font_data["pages_used"] = sorted(list(font_data["pages_used"]))
        final_unique_fonts.append(font_data)

    final_typefaces = [
        {"typeface": g["display_name"], "pages_used": sorted(list(g["pages"]))}
        for g in typeface_groups.values()
    ]

    return {
        "pdf_file": str(pdf_path),
        "font_objects_count": len(final_unique_fonts),   # raw embedded font objects
        "typeface_count": len(final_typefaces),          # actual distinct fonts a human means
        "unique_fonts": final_unique_fonts,
        "typefaces": final_typefaces,
        "font_usage_records": font_records,
    }


def save_json(data, output_path):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def save_csv(font_usage_records, output_path):
    if not font_usage_records:
        return

    fieldnames = ["page", "font_name", "font_size", "flags", "color", "text_sample"]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(font_usage_records)


def main():
    parser = argparse.ArgumentParser(description="Extract all fonts from a digitally born PDF.")
    parser.add_argument("pdf_path", help="Path to input PDF")
    parser.add_argument("--json", default="pdf_fonts.json", help="Output JSON file")
    parser.add_argument("--csv", default="pdf_font_usage.csv", help="Output CSV file")

    args = parser.parse_args()

    data = extract_fonts_from_pdf(args.pdf_path)

    save_json(data, args.json)
    save_csv(data["font_usage_records"], args.csv)

    print("Font extraction completed.")
    print(f"Font objects (raw, includes subset duplicates): {data['font_objects_count']}")
    print(f"Distinct typefaces (merged across subsets/Type3 fallback): {data['typeface_count']}")
    print(f"JSON saved to: {args.json}")
    print(f"CSV saved to: {args.csv}")

    print("\nFont objects (raw):")
    for font in data["unique_fonts"]:
        print(
            f"- xref {font['xref']} | {font['font_name']} | typeface: {font['typeface']} | "
            f"Type: {font['font_type']} | Encoding: {font['encoding']} | "
            f"resolved via: {font['resolved_via']} | Pages: {font['pages_used']}"
        )

    print("\nDistinct typefaces:")
    for tf in data["typefaces"]:
        print(f"- {tf['typeface']} | Pages: {tf['pages_used']}")


if __name__ == "__main__":
    main()