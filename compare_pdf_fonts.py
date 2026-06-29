import json
import argparse

from font_utils import extract_text_spans, normalize_for_grouping

FONT_SIZE_TOLERANCE = 0.30
# Position tolerance is now a FRACTION of page width/height, not raw
# points - see normalized_bbox(). 0.01 = 1% of page dimension.
POSITION_TOLERANCE_FRACTION = 0.01


def create_span_key(span):
    """Uses page + text as matching key. Works when the text itself hasn't changed."""
    return f"{span['page']}::{span['text']}"


def normalized_bbox(bbox, page_size):
    """
    Express bbox as a fraction of page width/height instead of raw point
    coordinates. Needed because the same template can be rendered onto
    differently-sized pages (e.g. a native server-generated PDF vs. a
    browser "Print to PDF" capture) - comparing raw coordinates in that
    case would make every span look "moved" purely from a scale/DPI
    difference that has nothing to do with tampering.
    """
    if not bbox or len(bbox) != 4 or not page_size or not page_size[0] or not page_size[1]:
        return None
    w, h = page_size
    x0, y0, x1, y1 = bbox
    return [x0 / w, y0 / h, x1 / w, y1 / h]


def bbox_distance_normalized(bbox1, size1, bbox2, size2):
    n1 = normalized_bbox(bbox1, size1)
    n2 = normalized_bbox(bbox2, size2)
    if n1 is None or n2 is None:
        return float("inf")
    return max(abs(a - b) for a, b in zip(n1, n2))


def build_baseline_span_index(baseline):
    """
    Baselines can now hold spans from multiple genuine sample PDFs
    (spans_by_source). Flatten them into one lookup keyed by page::text,
    tagging each candidate with the page size it came from, so a checked
    span can be matched against whichever genuine sample is the closest
    fit rather than just whichever one happened to be first.
    """
    index = {}
    for source in baseline.get("spans_by_source", []):
        page_sizes = source.get("page_sizes", {})
        for span in source.get("spans", []):
            key = create_span_key(span)
            entry = dict(span)
            entry["_page_size"] = page_sizes.get(str(span["page"]))
            index.setdefault(key, []).append(entry)
    return index


def compare_against_baseline(baseline, current):
    issues = []

    baseline_font_keys = set(normalize_for_grouping(f) for f in baseline.get("named_fonts", []))
    current_fonts_by_key = {normalize_for_grouping(f): f for f in current.get("named_fonts", [])}

    new_font_keys = sorted(set(current_fonts_by_key) - baseline_font_keys)
    missing_font_keys = sorted(baseline_font_keys - set(current_fonts_by_key))

    if new_font_keys:
        issues.append({
            "issue_type": "NEW_FONT_DETECTED",
            # Downgraded from HIGH to MEDIUM: we confirmed genuine
            # certificates from this template can legitimately use
            # different font sets depending on generation pipeline, so a
            # new font alone isn't proof of tampering - it's a signal to
            # review, especially alongside other issues below.
            "severity": "MEDIUM",
            "message": "Font(s) found that don't appear in any baseline sample.",
            "new_fonts": [current_fonts_by_key[k] for k in new_font_keys],
        })

    if missing_font_keys:
        sample_count = baseline.get("sample_count", 1)
        seen_counts = baseline.get("font_seen_in_n_samples", {})
        missing_display = [f for f in baseline.get("named_fonts", []) if normalize_for_grouping(f) in missing_font_keys]
        # If everything that's "missing" was only ever low-confidence (seen
        # in a minority of baseline samples anyway), treat it as LOW rather
        # than MEDIUM - it was already a weak signal in the baseline.
        all_low_confidence = all(seen_counts.get(f, sample_count) < sample_count for f in missing_display)
        issues.append({
            "issue_type": "BASELINE_FONT_MISSING",
            "severity": "LOW" if all_low_confidence else "MEDIUM",
            "message": "Some baseline fonts are missing in checked PDF.",
            "missing_fonts": missing_display,
        })

    internal_baseline = set(baseline.get("internal_fonts", []))
    internal_current = set(current.get("internal_fonts", []))
    newly_unresolved = internal_current - internal_baseline
    if newly_unresolved:
        issues.append({
            "issue_type": "UNRESOLVED_FONT_DETECTED",
            "severity": "MEDIUM",
            "message": (
                "Some spans use fonts that could not be resolved automatically "
                "(e.g. ambiguous Type3 fallback glyphs with multiple candidate "
                "families on the same page) - review manually."
            ),
            "details": sorted(newly_unresolved),
        })

    baseline_span_index = build_baseline_span_index(baseline)

    current_span_map = {}
    for span in current.get("spans", []):
        current_span_map.setdefault(create_span_key(span), []).append(span)

    current_page_sizes = current.get("page_sizes", {})

    for key, baseline_candidates in baseline_span_index.items():
        if key not in current_span_map:
            continue

        for current_span in current_span_map[key]:
            current_size = current_page_sizes.get(str(current_span["page"]))

            best_match = None
            best_distance = float("inf")
            for cand in baseline_candidates:
                d = bbox_distance_normalized(
                    cand["bbox"], cand.get("_page_size"), current_span["bbox"], current_size
                )
                if d < best_distance:
                    best_distance = d
                    best_match = cand

            if best_match is None:
                continue

            text = current_span["text"]
            page = current_span["page"]

            if normalize_for_grouping(best_match["font"]) != normalize_for_grouping(current_span["font"]):
                issues.append({
                    "issue_type": "SAME_TEXT_DIFFERENT_FONT",
                    "severity": "HIGH",
                    "page": page,
                    "text": text,
                    "baseline_font": best_match["font"],
                    "current_font": current_span["font"],
                    "message": "Same text is using a different font.",
                })

            font_size_diff = abs(best_match["font_size"] - current_span["font_size"])
            if font_size_diff > FONT_SIZE_TOLERANCE:
                issues.append({
                    "issue_type": "FONT_SIZE_CHANGED",
                    "severity": "MEDIUM",
                    "page": page,
                    "text": text,
                    "baseline_font_size": best_match["font_size"],
                    "current_font_size": current_span["font_size"],
                    "difference": round(font_size_diff, 2),
                    "message": "Same text is using a different font size.",
                })

            if best_match["is_bold"] != current_span["is_bold"]:
                issues.append({
                    "issue_type": "BOLD_STYLE_CHANGED",
                    "severity": "HIGH",
                    "page": page,
                    "text": text,
                    "baseline_is_bold": best_match["is_bold"],
                    "current_is_bold": current_span["is_bold"],
                    "message": "Bold/non-bold style changed.",
                })

            if best_match["is_italic"] != current_span["is_italic"]:
                issues.append({
                    "issue_type": "ITALIC_STYLE_CHANGED",
                    "severity": "MEDIUM",
                    "page": page,
                    "text": text,
                    "baseline_is_italic": best_match["is_italic"],
                    "current_is_italic": current_span["is_italic"],
                    "message": "Italic/non-italic style changed.",
                })

            if best_distance != float("inf") and best_distance > POSITION_TOLERANCE_FRACTION:
                issues.append({
                    "issue_type": "TEXT_POSITION_CHANGED",
                    "severity": "MEDIUM",
                    "page": page,
                    "text": text,
                    "position_difference_fraction": round(best_distance, 4),
                    "baseline_bbox": best_match["bbox"],
                    "current_bbox": current_span["bbox"],
                    "message": "Same text moved from its expected relative position on the page.",
                })

    # NEW: detect text in the checked PDF that doesn't appear in ANY
    # baseline sample. The old script only ever iterated baseline keys,
    # so genuinely new/inserted content (exactly what we found overlaid
    # at the bottom of one sample certificate) was never even looked at.
    extra_keys = sorted(set(current_span_map) - set(baseline_span_index))
    if extra_keys:
        extra_text = []
        for key in extra_keys:
            for span in current_span_map[key]:
                extra_text.append({"page": span["page"], "text": span["text"], "font": span["font"]})
        issues.append({
            "issue_type": "EXTRA_TEXT_NOT_IN_BASELINE",
            "severity": "HIGH",
            "message": "Text found in checked PDF that doesn't appear in any baseline sample.",
            "extra_text": extra_text,
        })

    passed = len([i for i in issues if i["severity"] == "HIGH"]) == 0

    return {
        "passed": passed,
        "total_issues": len(issues),
        "high_severity_issues": len([i for i in issues if i["severity"] == "HIGH"]),
        "medium_severity_issues": len([i for i in issues if i["severity"] == "MEDIUM"]),
        "low_severity_issues": len([i for i in issues if i["severity"] == "LOW"]),
        "baseline_named_fonts": baseline.get("named_fonts", []),
        "current_named_fonts": current.get("named_fonts", []),
        "issues": issues,
    }


def main():
    parser = argparse.ArgumentParser(description="Compare a PDF's fonts/text against a baseline.")
    parser.add_argument("baseline_json", help="Path to baseline_fonts.json")
    parser.add_argument("pdf_to_check", help="Path to PDF to check")
    parser.add_argument("--output", default="tampering_font_report.json", help="Output report JSON")

    args = parser.parse_args()

    with open(args.baseline_json, "r", encoding="utf-8") as f:
        baseline = json.load(f)

    current = extract_text_spans(args.pdf_to_check)

    report = compare_against_baseline(baseline, current)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)

    print("Font/text comparison completed.")
    print(f"Passed: {report['passed']}")
    print(f"Total issues: {report['total_issues']}")
    print(f"High severity issues: {report['high_severity_issues']}")
    print(f"Medium severity issues: {report['medium_severity_issues']}")
    print(f"Low severity issues: {report['low_severity_issues']}")
    print(f"Report saved to: {args.output}")

    if report["issues"]:
        print("\nIssues found:")
        for issue in report["issues"]:
            print(f"- [{issue['severity']}] {issue['issue_type']}: {issue['message']}")


if __name__ == "__main__":
    main()
