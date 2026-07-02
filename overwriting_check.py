"""
overwriting_check.py — Feature: overwriting_check

Detects handwritten / digit regions that may have been overwritten or retouched.
Rather than trusting any single measurement, it combines four independent
signals per candidate character/digit and scores by how many fire together:

  1. high_ink_density      — much darker/fuller than neighbouring characters
  2. high_stroke_thickness — reuses stroke_thickness_check's stroke_width_ratio
  3. dark_blob             — a concentrated very-dark sub-region inside the glyph
  4. complex_contour       — low solidity / ragged outline (overwrites add edges)

Risk per region:
    low    — 1 signal abnormal
    medium — 2 signals abnormal
    high   — 3 or more signals abnormal

This module is standalone and does not modify the existing font checks. It only
flags regions as "suspicious" / needing manual review — it never declares fraud.

    python overwriting_check.py document.pdf
    python overwriting_check.py document.pdf --output result.json --dpi 200

JSON schema returned by run():
    {
      "feature": "overwriting_check",
      "status": "passed" | "failed" | "insufficient_data",
      "passed": bool,
      "risk": "low" | "medium" | "high",
      "pages_checked": int,
      "suspicious_regions_count": int,
      "issues": [
        {
          "page": int,
          "bbox": [x1, y1, x2, y2],
          "ink_density_ratio": float,
          "stroke_width_ratio": float,
          "dark_blob_score": float,
          "contour_complexity_score": float,
          "signals_triggered": [ ... ],
          "reason": str
        }
      ]
    }
"""

import argparse
import json
import sys

from ink_region_utils import extract_components, REFERENCE_DPI

FEATURE = "overwriting_check"

# Per-signal thresholds.
INK_DENSITY_RATIO_THRESHOLD = 1.9   # fuller/darker than local neighbours
STROKE_RATIO_THRESHOLD = 2.0        # markedly thicker than local neighbours
DARK_BLOB_THRESHOLD = 0.22          # largest self-relative dark sub-blob / ink
SOLIDITY_RATIO_THRESHOLD = 0.6      # this glyph vs its line neighbours (<1 = raggeder)
SOLIDITY_ABS_CEILING = 0.55         # only consider genuinely low-solidity glyphs

# How many independent signals a region needs before we surface it. Overwriting
# is a multi-signal phenomenon; a lone weak signal on printed text is almost
# always natural glyph variation, so the default suppresses single-signal noise.
# The low/medium/high risk tiers (1/2/3+ signals) still apply to what IS
# reported. Lower this to 1 for maximum recall (and more false positives).
MIN_SIGNALS_TO_REPORT = 2

# Ignore very small components (noise robustness).
MIN_FLAG_AREA_PX = 120
# Need a reasonable number of glyphs before local comparison is meaningful.
MIN_COMPONENTS_FOR_ANALYSIS = 5
# Cap the issue list so the JSON stays clean on very busy pages.
MAX_ISSUES_REPORTED = 50

REASON = "Possible overwritten or retouched character/digit region"

_SIGNAL_ORDER = ["high_ink_density", "high_stroke_thickness", "dark_blob", "complex_contour"]


def _risk_from_signal_count(n):
    if n >= 3:
        return "high"
    if n == 2:
        return "medium"
    return "low"


def _document_risk(issues):
    """Document risk = the highest per-region risk present."""
    rank = {"low": 0, "medium": 1, "high": 2}
    if not issues:
        return "low"
    worst = max(issues, key=lambda i: rank[_risk_from_signal_count(len(i["signals_triggered"]))])
    return _risk_from_signal_count(len(worst["signals_triggered"]))


def run(pdf_path, dpi=REFERENCE_DPI, debug_dir=None):
    """Run the overwriting check and return the result dict (see schema)."""
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
                "not enough for a reliable overwriting comparison."
            ),
        }

    issues = []
    for c in components:
        if c["area"] < MIN_FLAG_AREA_PX:
            continue
        # Heading-sized / oversized marks are not field-text candidates.
        if not c.get("is_field_text", True):
            continue

        ink_ratio = c.get("ink_density_ratio", 0.0)
        stroke_ratio = c.get("stroke_width_ratio", 0.0)
        dark_blob = c.get("dark_blob_score", 0.0)
        solidity = c.get("solidity", 1.0)
        solidity_ratio = c.get("solidity_ratio", 1.0)

        signals = []
        if ink_ratio > INK_DENSITY_RATIO_THRESHOLD:
            signals.append("high_ink_density")
        if stroke_ratio > STROKE_RATIO_THRESHOLD:
            signals.append("high_stroke_thickness")
        if dark_blob > DARK_BLOB_THRESHOLD:
            signals.append("dark_blob")
        # Ragged/complex only when this glyph is BOTH low-solidity in absolute
        # terms AND markedly more ragged than its line neighbours — so naturally
        # holey printed letters (E, F, H, %) among their own kind don't trip it.
        if solidity < SOLIDITY_ABS_CEILING and solidity_ratio < SOLIDITY_RATIO_THRESHOLD:
            signals.append("complex_contour")

        if len(signals) < MIN_SIGNALS_TO_REPORT:
            continue

        # Keep signal order stable/readable regardless of check order above.
        signals = [s for s in _SIGNAL_ORDER if s in signals]

        issues.append({
            "page": c["page"],
            "bbox": c["bbox"],
            "ink_density_ratio": ink_ratio,
            "stroke_width_ratio": stroke_ratio,
            "dark_blob_score": dark_blob,
            "contour_complexity_score": c.get("contour_complexity_score", 0.0),
            "signals_triggered": signals,
            "reason": REASON,
        })

    # Most signals first, then by ink-density extremity.
    issues.sort(key=lambda i: (len(i["signals_triggered"]), i["ink_density_ratio"]), reverse=True)
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
        prog="overwriting_check",
        description="Flag handwritten/digit regions that may be overwritten or retouched.",
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

    sys.exit(1 if not result["passed"] else 0)


if __name__ == "__main__":
    main()
