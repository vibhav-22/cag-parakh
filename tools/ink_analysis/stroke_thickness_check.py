"""
stroke_thickness_check.py — Feature: stroke_thickness_check

Detects handwritten / digit regions whose stroke thickness is unusually high
compared to nearby writing on the same text line (a common trace of a digit or
letter that was gone over / thickened after the fact).

This module is standalone and does not modify or depend on the existing font
checks. The heavy image work (page rendering, candidate detection, stroke-width
estimation, local comparison) lives in ink_region_utils.py so overwriting_check
can reuse the identical stroke-thickness numbers.

This check only flags regions as "suspicious" / needing manual review. It never
declares fraud.

    python stroke_thickness_check.py document.pdf
    python stroke_thickness_check.py document.pdf --output result.json --dpi 200

JSON schema returned by run():
    {
      "feature": "stroke_thickness_check",
      "status": "passed" | "failed" | "insufficient_data",
      "passed": bool,
      "risk": "low" | "medium" | "high",
      "pages_checked": int,
      "suspicious_regions_count": int,
      "issues": [
        {
          "page": int,
          "bbox": [x1, y1, x2, y2],
          "stroke_width": float,
          "local_average_stroke_width": float,
          "stroke_width_ratio": float,
          "reason": str
        }
      ]
    }
"""

import argparse
import json
import sys

from ink_region_utils import extract_components, REFERENCE_DPI

FEATURE = "stroke_thickness_check"

# A region is suspicious when it is markedly thicker than its neighbours.
STROKE_RATIO_THRESHOLD = 1.8
# Ignore very small components even if their ratio spikes (noise robustness).
MIN_FLAG_AREA_PX = 120
# Need a reasonable number of glyphs before local comparison is meaningful.
MIN_COMPONENTS_FOR_ANALYSIS = 5
# Cap the issue list so the JSON stays clean on very busy pages.
MAX_ISSUES_REPORTED = 50

REASON = "Stroke thickness is unusually high compared to nearby writing"

# A lone thick glyph on these bilingual govt forms is almost always a printed
# Devanagari conjunct/matra or a scan-shading edge, not tampering. A genuinely
# inserted field value or a signature written over the page is a RUN of adjacent
# thick glyphs. So we only keep thick glyphs that cluster together — this is what
# separates real overwriting/insertion from naturally-heavy printed script.
MIN_CLUSTER_SIZE = 3
# Two thick glyphs join a cluster if they sit on the same row and the horizontal
# gap between them is under this multiple of their height.
CLUSTER_GAP_HEIGHT_MULT = 2.0


def _cluster_filter(candidates):
    """Keep only thick glyphs that belong to a cluster of >= MIN_CLUSTER_SIZE."""
    if MIN_CLUSTER_SIZE <= 1 or not candidates:
        return candidates

    n = len(candidates)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        parent[find(a)] = find(b)

    for i in range(n):
        bi = candidates[i]["bbox"]
        hi = bi[3] - bi[1]
        yci = (bi[1] + bi[3]) / 2.0
        for j in range(i + 1, n):
            bj = candidates[j]["bbox"]
            hj = bj[3] - bj[1]
            ycj = (bj[1] + bj[3]) / 2.0
            # Same text row?
            if abs(yci - ycj) > 0.6 * max(hi, hj):
                continue
            # Small horizontal gap (allow slight overlap → negative gap)?
            gap = max(bi[0], bj[0]) - min(bi[2], bj[2])
            if gap <= CLUSTER_GAP_HEIGHT_MULT * max(hi, hj):
                union(i, j)

    sizes = {}
    for i in range(n):
        sizes[find(i)] = sizes.get(find(i), 0) + 1
    return [candidates[i] for i in range(n) if sizes[find(i)] >= MIN_CLUSTER_SIZE]


def _document_risk(issues):
    """low / medium / high from how many regions and how extreme the ratios."""
    if not issues:
        return "low"
    max_ratio = max(i["stroke_width_ratio"] for i in issues)
    count = len(issues)
    if max_ratio >= 2.5 or count >= 5:
        return "high"
    if max_ratio >= 2.0 or count >= 2:
        return "medium"
    return "low"


def run(pdf_path, dpi=REFERENCE_DPI, debug_dir=None):
    """Run the stroke-thickness check and return the result dict (see schema)."""
    try:
        bundle = extract_components(pdf_path, dpi=dpi, debug_dir=debug_dir)
    except FileNotFoundError as e:
        return {
            "feature": FEATURE,
            "status": "insufficient_data",
            "passed": True,
            "risk": "low",
            "pages_checked": 0,
            "suspicious_regions_count": 0,
            "issues": [],
            "note": str(e),
        }

    components = bundle["components"]
    pages_checked = bundle["pages_checked"]

    # Too few handwriting/digit candidates to judge — do NOT fail the document.
    if len(components) < MIN_COMPONENTS_FOR_ANALYSIS:
        return {
            "feature": FEATURE,
            "status": "insufficient_data",
            "passed": True,
            "risk": "low",
            "pages_checked": pages_checked,
            "suspicious_regions_count": 0,
            "issues": [],
            "note": (
                f"Only {len(components)} handwriting/digit candidate(s) found; "
                "not enough for a reliable stroke-thickness comparison."
            ),
        }

    candidates = []
    for c in components:
        # Heading-sized / oversized marks are not field-text candidates.
        if not c.get("is_field_text", True):
            continue
        ratio = c.get("stroke_width_ratio", 0.0)
        if ratio > STROKE_RATIO_THRESHOLD and c["area"] >= MIN_FLAG_AREA_PX:
            candidates.append({
                "page": c["page"],
                "bbox": c["bbox"],
                "stroke_width": c["stroke_width"],
                "local_average_stroke_width": c["local_average_stroke_width"],
                "stroke_width_ratio": ratio,
                "reason": REASON,
            })

    # Suppress isolated thick glyphs (printed Devanagari conjuncts, scan edges);
    # keep only clustered runs that indicate an inserted/overwritten region.
    issues = _cluster_filter(candidates)
    issues.sort(key=lambda i: i["stroke_width_ratio"], reverse=True)
    total_suspicious = len(issues)
    issues = issues[:MAX_ISSUES_REPORTED]

    passed = total_suspicious == 0
    return {
        "feature": FEATURE,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "risk": _document_risk(issues),
        "pages_checked": pages_checked,
        "suspicious_regions_count": total_suspicious,
        "issues": issues,
    }


def main():
    parser = argparse.ArgumentParser(
        prog="stroke_thickness_check",
        description="Flag handwritten/digit regions with unusually thick strokes.",
    )
    parser.add_argument("pdf", metavar="PDF", help="Scanned PDF to check")
    parser.add_argument("--output", default=None, metavar="FILE",
                        help="Optional path to write the JSON result")
    parser.add_argument("--dpi", type=int, default=REFERENCE_DPI, metavar="N",
                        help=f"Render DPI (default: {REFERENCE_DPI})")
    parser.add_argument("--debug-dir", default=None, metavar="DIR",
                        help="Opt-in: write annotated page images to this folder")
    args = parser.parse_args()

    result = run(args.pdf, dpi=args.dpi, debug_dir=args.debug_dir)
    text = json.dumps(result, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Result written to: {args.output}")
    print(text)

    # Exit non-zero only when regions were flagged (useful for pipeline gating).
    sys.exit(1 if not result["passed"] else 0)


if __name__ == "__main__":
    main()
