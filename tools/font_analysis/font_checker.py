"""
font_checker.py — unified font baseline tool

Two subcommands:

  store   Extract fonts from one or more known-correct PDFs and save a
          baseline JSON for future comparisons.

          python font_checker.py store good1.pdf good2.pdf --baseline fonts.json

  check   Compare a template PDF against the stored baseline and report
          any deviations.

          python font_checker.py check template.pdf --baseline fonts.json

Font extraction is handled entirely by font_utils.extract_text_spans().
"""

import argparse
import json
import sys
from pathlib import Path

from font_utils import extract_text_spans, normalize_for_grouping


# ---------------------------------------------------------------------------
# Baseline building
# ---------------------------------------------------------------------------

def build_baseline(pdf_paths):
    """
    Extract font data from each PDF in pdf_paths and merge into a single
    baseline profile.  Multiple genuine samples are supported so that
    templates which legitimately vary by generation pipeline (e.g.
    server-signed vs. browser-Print-to-PDF) don't trigger false positives.
    """
    samples = []
    for path in pdf_paths:
        print(f"  Extracting: {path}")
        samples.append(extract_text_spans(path))

    named_font_groups = {}
    font_seen_count = {}
    internal_fonts = set()

    for sample in samples:
        seen_this_sample = set()
        for f in sample["named_fonts"]:
            key = normalize_for_grouping(f)
            if key not in named_font_groups:
                named_font_groups[key] = f
            if key not in seen_this_sample:
                font_seen_count[key] = font_seen_count.get(key, 0) + 1
                seen_this_sample.add(key)
        internal_fonts.update(sample["internal_fonts"])

    return {
        "sample_count": len(samples),
        "samples": [{"pdf_file": s["pdf_file"], "named_fonts": s["named_fonts"]} for s in samples],
        "named_fonts": sorted(named_font_groups.values()),
        "font_seen_in_n_samples": {
            named_font_groups[k]: v for k, v in font_seen_count.items()
        },
        "internal_fonts": sorted(internal_fonts),
        "spans_by_source": [
            {"pdf_file": s["pdf_file"], "page_sizes": s["page_sizes"], "spans": s["spans"]}
            for s in samples
        ],
    }


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

FONT_SIZE_TOLERANCE = 0.30
POSITION_TOLERANCE_FRACTION = 0.01


def _span_key(span):
    return f"{span['page']}::{span['text']}"


def _normalized_bbox(bbox, page_size):
    if not bbox or len(bbox) != 4 or not page_size or not page_size[0] or not page_size[1]:
        return None
    w, h = page_size
    x0, y0, x1, y1 = bbox
    return [x0 / w, y0 / h, x1 / w, y1 / h]


def _bbox_distance(bbox1, size1, bbox2, size2):
    n1 = _normalized_bbox(bbox1, size1)
    n2 = _normalized_bbox(bbox2, size2)
    if n1 is None or n2 is None:
        return float("inf")
    return max(abs(a - b) for a, b in zip(n1, n2))


def _build_baseline_index(baseline):
    index = {}
    for source in baseline.get("spans_by_source", []):
        page_sizes = source.get("page_sizes", {})
        for span in source.get("spans", []):
            entry = dict(span)
            entry["_page_size"] = page_sizes.get(str(span["page"]))
            index.setdefault(_span_key(span), []).append(entry)
    return index


def compare_to_baseline(baseline, current):
    issues = []

    baseline_font_keys = {normalize_for_grouping(f) for f in baseline.get("named_fonts", [])}
    current_by_key = {normalize_for_grouping(f): f for f in current.get("named_fonts", [])}

    new_keys = sorted(set(current_by_key) - baseline_font_keys)
    missing_keys = sorted(baseline_font_keys - set(current_by_key))

    if new_keys:
        issues.append({
            "issue_type": "NEW_FONT_DETECTED",
            "severity": "MEDIUM",
            "message": "Font(s) found that don't appear in any baseline sample.",
            "new_fonts": [current_by_key[k] for k in new_keys],
        })

    if missing_keys:
        sample_count = baseline.get("sample_count", 1)
        seen_counts = baseline.get("font_seen_in_n_samples", {})
        missing_display = [f for f in baseline.get("named_fonts", []) if normalize_for_grouping(f) in missing_keys]
        all_low_confidence = all(seen_counts.get(f, sample_count) < sample_count for f in missing_display)
        issues.append({
            "issue_type": "BASELINE_FONT_MISSING",
            "severity": "LOW" if all_low_confidence else "MEDIUM",
            "message": "Some baseline fonts are absent in the checked PDF.",
            "missing_fonts": missing_display,
        })

    internal_baseline = set(baseline.get("internal_fonts", []))
    internal_current = set(current.get("internal_fonts", []))
    newly_unresolved = internal_current - internal_baseline
    if newly_unresolved:
        issues.append({
            "issue_type": "UNRESOLVED_FONT_DETECTED",
            "severity": "MEDIUM",
            "message": "Spans with fonts that could not be resolved — review manually.",
            "details": sorted(newly_unresolved),
        })

    baseline_index = _build_baseline_index(baseline)
    current_map = {}
    for span in current.get("spans", []):
        current_map.setdefault(_span_key(span), []).append(span)
    current_page_sizes = current.get("page_sizes", {})

    for key, baseline_candidates in baseline_index.items():
        if key not in current_map:
            continue
        for cur in current_map[key]:
            cur_size = current_page_sizes.get(str(cur["page"]))
            best = min(baseline_candidates, key=lambda c: _bbox_distance(c["bbox"], c.get("_page_size"), cur["bbox"], cur_size))
            dist = _bbox_distance(best["bbox"], best.get("_page_size"), cur["bbox"], cur_size)

            text, page = cur["text"], cur["page"]

            if normalize_for_grouping(best["font"]) != normalize_for_grouping(cur["font"]):
                issues.append({
                    "issue_type": "SAME_TEXT_DIFFERENT_FONT",
                    "severity": "HIGH",
                    "page": page, "text": text,
                    "baseline_font": best["font"], "current_font": cur["font"],
                    "message": "Same text uses a different font.",
                })

            size_diff = abs(best["font_size"] - cur["font_size"])
            if size_diff > FONT_SIZE_TOLERANCE:
                issues.append({
                    "issue_type": "FONT_SIZE_CHANGED",
                    "severity": "MEDIUM",
                    "page": page, "text": text,
                    "baseline_font_size": best["font_size"], "current_font_size": cur["font_size"],
                    "difference": round(size_diff, 2),
                    "message": "Same text uses a different font size.",
                })

            if best["is_bold"] != cur["is_bold"]:
                issues.append({
                    "issue_type": "BOLD_STYLE_CHANGED",
                    "severity": "HIGH",
                    "page": page, "text": text,
                    "baseline_is_bold": best["is_bold"], "current_is_bold": cur["is_bold"],
                    "message": "Bold/non-bold style changed.",
                })

            if best["is_italic"] != cur["is_italic"]:
                issues.append({
                    "issue_type": "ITALIC_STYLE_CHANGED",
                    "severity": "MEDIUM",
                    "page": page, "text": text,
                    "baseline_is_italic": best["is_italic"], "current_is_italic": cur["is_italic"],
                    "message": "Italic/non-italic style changed.",
                })

            if dist != float("inf") and dist > POSITION_TOLERANCE_FRACTION:
                issues.append({
                    "issue_type": "TEXT_POSITION_CHANGED",
                    "severity": "MEDIUM",
                    "page": page, "text": text,
                    "position_difference_fraction": round(dist, 4),
                    "baseline_bbox": best["bbox"], "current_bbox": cur["bbox"],
                    "message": "Same text moved from its expected position.",
                })

    extra_keys = sorted(set(current_map) - set(baseline_index))
    if extra_keys:
        extra_text = [
            {"page": s["page"], "text": s["text"], "font": s["font"]}
            for k in extra_keys for s in current_map[k]
        ]
        issues.append({
            "issue_type": "EXTRA_TEXT_NOT_IN_BASELINE",
            "severity": "HIGH",
            "message": "Text found in checked PDF that doesn't appear in any baseline sample.",
            "extra_text": extra_text,
        })

    high = [i for i in issues if i["severity"] == "HIGH"]
    medium = [i for i in issues if i["severity"] == "MEDIUM"]
    low = [i for i in issues if i["severity"] == "LOW"]

    return {
        "passed": len(high) == 0,
        "total_issues": len(issues),
        "high_severity_issues": len(high),
        "medium_severity_issues": len(medium),
        "low_severity_issues": len(low),
        "baseline_named_fonts": baseline.get("named_fonts", []),
        "current_named_fonts": current.get("named_fonts", []),
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_store(args):
    print(f"Building baseline from {len(args.pdfs)} PDF(s)...")
    baseline = build_baseline(args.pdfs)

    output = Path(args.baseline)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=4, ensure_ascii=False)

    print(f"\nBaseline saved to: {output}")
    print(f"Samples processed : {baseline['sample_count']}")
    print(f"Named fonts found : {len(baseline['named_fonts'])}")
    for font in baseline["named_fonts"]:
        count = baseline["font_seen_in_n_samples"].get(font, 0)
        note = "" if count == baseline["sample_count"] else f"  (in {count}/{baseline['sample_count']} samples)"
        print(f"  - {font}{note}")
    if baseline["internal_fonts"]:
        print(f"Unresolved internal fonts (review manually): {baseline['internal_fonts']}")


def cmd_check(args):
    baseline_path = Path(args.baseline)
    if not baseline_path.exists():
        sys.exit(f"Baseline not found: {baseline_path}\nRun 'store' first to create it.")

    with open(baseline_path, "r", encoding="utf-8") as f:
        baseline = json.load(f)

    print(f"Extracting fonts from: {args.pdf}")
    current = extract_text_spans(args.pdf)

    print("Comparing against baseline...")
    report = compare_to_baseline(baseline, current)

    output = Path(args.output)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)

    print(f"\nResult   : {'PASSED' if report['passed'] else 'FAILED'}")
    print(f"Issues   : {report['total_issues']}  "
          f"(HIGH={report['high_severity_issues']}, "
          f"MEDIUM={report['medium_severity_issues']}, "
          f"LOW={report['low_severity_issues']})")
    print(f"Report   : {output}")

    if report["issues"]:
        print("\nIssues:")
        for issue in report["issues"]:
            print(f"  [{issue['severity']}] {issue['issue_type']}: {issue['message']}")

    sys.exit(0 if report["passed"] else 1)


def main():
    parser = argparse.ArgumentParser(
        prog="font_checker",
        description="Store PDF font baselines and check future templates against them.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # store
    p_store = sub.add_parser("store", help="Extract fonts from correct PDF(s) and save baseline.")
    p_store.add_argument("pdfs", nargs="+", metavar="PDF", help="Path(s) to known-correct PDF(s)")
    p_store.add_argument("--baseline", default="baseline_fonts.json", metavar="FILE",
                         help="Where to save the baseline (default: baseline_fonts.json)")

    # check
    p_check = sub.add_parser("check", help="Check a PDF template against the stored baseline.")
    p_check.add_argument("pdf", metavar="PDF", help="Path to the PDF to check")
    p_check.add_argument("--baseline", default="baseline_fonts.json", metavar="FILE",
                         help="Baseline JSON to compare against (default: baseline_fonts.json)")
    p_check.add_argument("--output", default="font_check_report.json", metavar="FILE",
                         help="Where to save the report (default: font_check_report.json)")

    args = parser.parse_args()

    if args.command == "store":
        cmd_store(args)
    elif args.command == "check":
        cmd_check(args)


if __name__ == "__main__":
    main()
