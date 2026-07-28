#!/usr/bin/env python3
"""Locate likely visible signatures and emit the suspicion-system JSON contract.

This is a compatibility adapter around the model-free ``signature_locator``
package. It preserves the analyzer ID and normalized evidence-region schema used
by the backend and frontend while replacing the previous Ultralytics/YOLO
implementation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pdfplumber


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from signature_locator.pipeline import process_pdf  # noqa: E402


VERSION = "2.0.0"
ENGINE = "multi-evidence-local"


def _relative_artifact(path: str | Path, artifact_root: Path) -> str:
    return Path(path).resolve().relative_to(artifact_root.resolve()).as_posix()


def _page_sizes(pdf_path: Path) -> list[tuple[float, float]]:
    with pdfplumber.open(str(pdf_path)) as document:
        return [(float(page.width), float(page.height)) for page in document.pages]


def build_service_report(
    result: dict[str, Any],
    page_sizes: list[tuple[float, float]],
    artifact_root: Path,
) -> dict[str, Any]:
    """Translate signature-locator output into the existing analyzer contract."""

    regions: list[dict[str, Any]] = []
    artifacts = [_relative_artifact(result["annotated_pdf"], artifact_root)]
    methods: set[str] = set()

    for detection in result.get("detections", []):
        page_number = int(detection["page"])
        if page_number < 1 or page_number > len(page_sizes):
            continue
        width, height = page_sizes[page_number - 1]
        x0, y0, x1, y1 = (float(value) for value in detection["bbox"])
        confidence = float(detection.get("confidence") or 0.0)
        method = str(detection.get("method") or "multi_evidence")
        methods.update(part for part in method.split("+") if part)
        reasons = [str(reason) for reason in detection.get("reasons", []) if reason]
        regions.append(
            {
                "page": page_number,
                "kind": "signature",
                "label": "Signature",
                "reason": (
                    f"Likely visible signature ({confidence:.0%} confidence). "
                    + ("; ".join(reasons) if reasons else "Multiple local signals agree.")
                ),
                "severity": "low" if confidence >= 0.65 else "medium",
                "confidence": round(confidence, 4),
                "method": method,
                "bbox_normalized": {
                    "x0": round(max(0.0, min(1.0, x0 / width)), 6),
                    "y0": round(max(0.0, min(1.0, y0 / height)), 6),
                    "x1": round(max(0.0, min(1.0, x1 / width)), 6),
                    "y1": round(max(0.0, min(1.0, y1 / height)), 6),
                },
            }
        )
        crop = detection.get("crop")
        if crop:
            crop_path = Path(result["output_directory"]) / str(crop)
            relative_crop = _relative_artifact(crop_path, artifact_root)
            artifacts.append(relative_crop)
            regions[-1]["artifact"] = relative_crop

    count = len(regions)
    present = count > 0
    score = max((float(region["confidence"]) for region in regions), default=0.0)
    return {
        "tool": "signature-presence",
        "version": VERSION,
        "engine": ENGINE,
        "file": result["input_pdf"],
        "status": "completed",
        "passed": True,
        "risk": "low",
        "verdict": "signature_present" if present else "no_signature_detected",
        "note": (
            f"{count} likely signature(s) detected across {result['page_count']} page(s)."
            if present
            else "No likely visible signature detected."
        ),
        "present": present,
        "count": count,
        "score": round(score, 4),
        "pages": int(result["page_count"]),
        "requested_dpi": result.get("dpi"),
        "confidence_threshold": result.get("threshold"),
        "methods": sorted(methods),
        "regions": regions,
        "artifacts": list(dict.fromkeys(artifacts)),
        "warnings": [
            "Candidates are visual locations, not proof of signer identity or "
            "cryptographic signature validity."
        ],
    }


def _error(input_path: Path, detail: str) -> dict[str, Any]:
    return {
        "tool": "signature-presence",
        "version": VERSION,
        "engine": ENGINE,
        "file": str(input_path),
        "status": "error",
        "passed": None,
        "risk": "unknown",
        "verdict": "error",
        "note": detail,
        "detail": detail,
        "present": None,
        "count": 0,
        "score": 0.0,
        "pages": 0,
        "regions": [],
        "artifacts": [],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Locate likely visible signatures in a PDF."
    )
    parser.add_argument("input", help="PDF document to inspect")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument(
        "--threshold",
        "--conf",
        dest="threshold",
        type=float,
        default=0.55,
        help="Minimum candidate confidence (default: 0.55)",
    )
    parser.add_argument("--output", help="Write the analyzer JSON report here")
    parser.add_argument(
        "--artifact-dir",
        help="Directory served by the backend artifact endpoint",
    )
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--ocr-lang", default="eng")
    parser.add_argument("--tesseract")
    parser.add_argument("--poppler-bin")
    # Accepted so older manual commands do not fail during rollout. They no
    # longer affect inference.
    parser.add_argument("--imgsz", help=argparse.SUPPRESS)
    parser.add_argument("--weights", help=argparse.SUPPRESS)
    parser.add_argument("--edge-margin", help=argparse.SUPPRESS)
    parser.add_argument("--annotate", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = Path(args.input).resolve()
    artifact_root = (
        Path(args.artifact_dir).resolve()
        if args.artifact_dir
        else input_path.parent / f"{input_path.stem}_signature_artifacts"
    )

    if not input_path.is_file():
        report = _error(input_path, f"Input not found: {input_path}")
    elif input_path.suffix.lower() != ".pdf":
        report = _error(input_path, "The signature analyzer currently accepts PDF files.")
    elif not 72 <= args.dpi <= 600:
        report = _error(input_path, "--dpi must be between 72 and 600.")
    elif not 0.0 <= args.threshold <= 1.0:
        report = _error(input_path, "--threshold must be between 0 and 1.")
    else:
        try:
            artifact_root.mkdir(parents=True, exist_ok=True)
            result = process_pdf(
                input_path,
                artifact_root,
                dpi=args.dpi,
                threshold=args.threshold,
                use_ocr=not args.no_ocr,
                ocr_lang=args.ocr_lang,
                tesseract=args.tesseract or os.getenv("TESSERACT_CMD"),
                poppler_bin=args.poppler_bin or os.getenv("POPPLER_BIN"),
            )
            result["dpi"] = args.dpi
            report = build_service_report(result, _page_sizes(input_path), artifact_root)
        except Exception as exc:  # keep analyzer failures inside the JSON contract
            report = _error(input_path, f"Signature detection failed: {exc}")

    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 1 if report["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
