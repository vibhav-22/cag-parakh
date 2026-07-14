import json
import argparse

from font_utils import extract_text_spans, normalize_for_grouping


def merge_baselines(samples):
    """
    Merge per-PDF span extractions into one baseline profile.

    KEY CHANGE: this now accepts MULTIPLE genuine reference PDFs, not
    just one. That matters because the same official template can
    legitimately produce different font sets depending on which kiosk/
    pipeline generated the PDF - we confirmed this directly: one genuine
    UP e-District certificate used 5 fonts (native server-signed PDF),
    another genuine one used 8 (browser "Print to PDF" capture), and
    neither set was a subset of the other.

    A single-sample baseline would flag the second as tampered purely for
    using a different generation pipeline - a false positive having
    nothing to do with actual tampering. Building the baseline from
    several known-genuine samples means a font is only treated as
    suspicious if it appears in NONE of them.

    - named_fonts: union of every font seen across all samples
    - font_seen_in_n_samples: how many samples each font appeared in, so
      a font seen in only one odd sample can still be surfaced as
      lower-confidence even though it's not flagged as a hard failure
    - spans_by_source: full per-sample span/page-size data is kept (not
      flattened into one set) so compare_pdf_fonts.py can match a
      checked PDF's spans against whichever genuine sample is the
      closest fit, rather than against an averaged/blended one.
    """
    named_font_groups = {}     # normalized -> display name
    font_seen_count = {}       # normalized -> number of samples it appeared in
    internal_fonts = set()
    sample_summaries = []

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

        sample_summaries.append({
            "pdf_file": sample["pdf_file"],
            "named_fonts": sample["named_fonts"],
        })

    return {
        "sample_count": len(samples),
        "samples": sample_summaries,
        "named_fonts": sorted(named_font_groups.values()),
        "font_seen_in_n_samples": {
            named_font_groups[k]: v for k, v in font_seen_count.items()
        },
        "internal_fonts": sorted(internal_fonts),
        "spans_by_source": [
            {
                "pdf_file": s["pdf_file"],
                "page_sizes": s["page_sizes"],
                "spans": s["spans"],
            }
            for s in samples
        ],
    }


def save_baseline(data, output_path):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Create a font/text baseline from one or more genuine reference PDFs. "
            "Pass several genuine PDFs if the same template can legitimately come "
            "from different generation pipelines (confirmed true for UP e-District "
            "certificates) - a single sample will cause false positives on otherwise "
            "genuine documents."
        )
    )
    parser.add_argument("pdf_paths", nargs="+", help="Path(s) to genuine reference PDF(s)")
    parser.add_argument("--output", default="data/baselines/baseline_fonts.json", help="Output baseline JSON")

    args = parser.parse_args()

    samples = [extract_text_spans(p) for p in args.pdf_paths]
    baseline = merge_baselines(samples)
    save_baseline(baseline, args.output)

    print("Baseline created successfully.")
    print(f"Built from {baseline['sample_count']} sample PDF(s).")
    print(f"Union of named fonts found: {len(baseline['named_fonts'])}")
    for font in baseline["named_fonts"]:
        count = baseline["font_seen_in_n_samples"].get(font, 0)
        flag = "" if count == baseline["sample_count"] else f"  (only in {count}/{baseline['sample_count']} samples - lower confidence)"
        print(f"  - {font}{flag}")
    if baseline["internal_fonts"]:
        print(f"Unresolved/internal fonts seen (review manually): {baseline['internal_fonts']}")
    print(f"Saved to: {args.output}")


if __name__ == "__main__":
    main()
