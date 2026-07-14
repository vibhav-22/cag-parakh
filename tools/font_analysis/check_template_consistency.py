import argparse
import json
from collections import defaultdict

from font_utils import extract_text_spans, normalize_for_grouping

FONT_SIZE_TOLERANCE = 0.30


def collect_label_usage(samples):
    """
    Group every span by its exact normalized text across ALL sample PDFs.

    Any text that shows up in 2+ documents is, by definition, repeated
    template content (field labels, boilerplate sentences) rather than
    personal data - a name or address won't naturally repeat across
    different applicants' certificates. This means we don't need to know
    in advance which parts of the page are "static" vs "data" - it falls
    out automatically from which text repeats.
    """
    usage = defaultdict(list)  # text -> list of occurrences across documents

    for sample in samples:
        for span in sample["spans"]:
            usage[span["text"]].append({
                "pdf_file": sample["pdf_file"],
                "page": span["page"],
                "font": span["font"],
                "font_size": span["font_size"],
                "is_bold": span["is_bold"],
                "is_italic": span["is_italic"],
            })

    return usage


def check_consistency(usage):
    issues = []
    consistent_label_count = 0
    checked_label_count = 0
    skipped_unique_count = 0

    for text, occurrences in usage.items():
        if len(occurrences) < 2:
            # Only appears in one document - most likely personal data,
            # not a repeated template label. Nothing to compare against.
            skipped_unique_count += 1
            continue

        checked_label_count += 1

        font_keys = set(normalize_for_grouping(o["font"]) for o in occurrences)
        display_fonts = sorted(set(o["font"] for o in occurrences))
        sizes = [o["font_size"] for o in occurrences]
        bolds = set(o["is_bold"] for o in occurrences)
        italics = set(o["is_italic"] for o in occurrences)

        size_spread = max(sizes) - min(sizes)

        problems = []

        if len(font_keys) > 1:
            problems.append({
                "type": "FONT_MISMATCH",
                "detail": display_fonts,
            })

        if size_spread > FONT_SIZE_TOLERANCE:
            problems.append({
                "type": "SIZE_MISMATCH",
                "detail": sorted(set(sizes)),
                "spread": round(size_spread, 2),
            })

        if len(bolds) > 1:
            problems.append({"type": "BOLD_MISMATCH", "detail": sorted(bolds)})

        if len(italics) > 1:
            problems.append({"type": "ITALIC_MISMATCH", "detail": sorted(italics)})

        if problems:
            issues.append({
                "text": text,
                "seen_in_documents": sorted(set(o["pdf_file"] for o in occurrences)),
                "occurrence_count": len(occurrences),
                "problems": problems,
                "occurrences": occurrences,
            })
        else:
            consistent_label_count += 1

    return {
        "document_count": None,  # filled in by caller
        "checked_label_count": checked_label_count,
        "consistent_label_count": consistent_label_count,
        "inconsistent_label_count": len(issues),
        "skipped_unique_text_count": skipped_unique_count,
        "passed": len(issues) == 0,
        "issues": issues,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Check that repeated/template text (field labels, boilerplate "
            "sentences) uses consistent font, size, and bold/italic style "
            "across a set of PDFs that share the same template."
        )
    )
    parser.add_argument("pdf_paths", nargs="+", help="Paths to PDFs to check together (2 or more)")
    parser.add_argument("--output", default="template_consistency_report.json", help="Output report JSON")

    args = parser.parse_args()

    if len(args.pdf_paths) < 2:
        parser.error("Provide at least 2 PDFs - consistency can't be checked against a single document.")

    samples = [extract_text_spans(p) for p in args.pdf_paths]
    usage = collect_label_usage(samples)
    report = check_consistency(usage)
    report["document_count"] = len(samples)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4, ensure_ascii=False)

    print(f"Checked {report['document_count']} PDF(s).")
    print(f"Repeated labels found (appear in 2+ docs): {report['checked_label_count']}")
    print(f"  Consistent: {report['consistent_label_count']}")
    print(f"  Inconsistent: {report['inconsistent_label_count']}")
    print(f"Unique text skipped (likely personal data, only in 1 doc): {report['skipped_unique_text_count']}")
    print(f"Passed: {report['passed']}")
    print(f"Report saved to: {args.output}")

    if report["issues"]:
        print("\nInconsistencies found:")
        for issue in report["issues"]:
            preview = issue["text"][:50] + ("..." if len(issue["text"]) > 50 else "")
            print(f"- Text: {preview!r}  (seen in {issue['occurrence_count']} spans)")
            for p in issue["problems"]:
                extra = f" (spread={p['spread']})" if "spread" in p else ""
                print(f"    [{p['type']}] {p['detail']}{extra}")


if __name__ == "__main__":
    main()
