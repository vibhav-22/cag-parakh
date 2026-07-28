#!/usr/bin/env python3
"""Benchmark the current signature locator over a labeled PDF folder."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from signature_locator.pipeline import process_pdf


def discover_pdfs(root: Path) -> list[Path]:
    if root.is_file() and root.suffix.lower() == ".pdf":
        return [root.resolve()]
    if root.is_dir():
        return sorted(
            (path.resolve() for path in root.rglob("*.pdf")),
            key=lambda path: str(path).casefold(),
        )
    return []


def load_labels(path: Path | None) -> dict[str, bool]:
    if path is None:
        return {}
    labels: dict[str, bool] = {}
    truthy = {"1", "true", "yes", "y", "positive", "present"}
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.reader(stream):
            if len(row) < 2 or row[0].strip().casefold() in {
                "filename",
                "file",
                "name",
            }:
                continue
            labels[Path(row[0].strip()).name] = row[1].strip().casefold() in truthy
    return labels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark signature presence over a PDF folder."
    )
    parser.add_argument("input", help="PDF or directory searched recursively")
    parser.add_argument("--labels", help="CSV with filename,has_signature")
    parser.add_argument("--out", default="signature_benchmark")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--tesseract")
    parser.add_argument("--poppler-bin")
    args = parser.parse_args(argv)

    documents = discover_pdfs(Path(args.input))
    if not documents:
        parser.error("No PDFs found.")
    labels = load_labels(Path(args.labels)) if args.labels else {}
    output_root = Path(args.out).resolve()
    artifacts = output_root / "artifacts"
    output_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for index, pdf_path in enumerate(documents, start=1):
        print(f"[{index}/{len(documents)}] {pdf_path.name}", flush=True)
        try:
            result = process_pdf(
                pdf_path,
                artifacts,
                dpi=args.dpi,
                threshold=args.threshold,
                use_ocr=not args.no_ocr,
                tesseract=args.tesseract,
                poppler_bin=args.poppler_bin,
            )
            present: bool | None = bool(result["has_signatures"])
            count = int(result["signature_count"])
            best = max(
                (
                    float(item.get("confidence") or 0.0)
                    for item in result.get("detections", [])
                ),
                default=0.0,
            )
            error = ""
        except Exception as exc:
            present, count, best, error = None, 0, 0.0, str(exc)

        truth = labels.get(pdf_path.name)
        if truth is not None and present is not None:
            key = (
                "tp"
                if truth and present
                else "fn"
                if truth
                else "fp"
                if present
                else "tn"
            )
            counts[key] += 1
        rows.append(
            {
                "document": pdf_path.name,
                "present": present,
                "count": count,
                "max_confidence": round(best, 4),
                "truth": "" if truth is None else int(truth),
                "error": error,
            }
        )

    with (output_root / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    payload = {
        "settings": {"dpi": args.dpi, "threshold": args.threshold},
        "documents": rows,
        "confusion": counts if labels else None,
    }
    (output_root / "summary.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    if labels:
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        print(
            f"precision={precision:.3f} recall={recall:.3f} "
            f"tp={tp} fp={fp} tn={counts['tn']} fn={fn}"
        )
    print(f"Wrote {output_root / 'summary.json'}")
    return 1 if any(row["error"] for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
