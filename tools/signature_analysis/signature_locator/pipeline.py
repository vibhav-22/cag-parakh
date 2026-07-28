from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pdfplumber
from pypdf import PdfReader

from .annotate import annotate_pdf
from .detectors import default_detectors, merge_detections
from .models import Detection, Detector, PageContext
from .ocr import run_ocr
from .render import render_pdf


def collect_pdfs(inputs: Iterable[str | Path], recursive: bool = True) -> list[Path]:
    found: dict[str, Path] = {}
    for raw in inputs:
        path = Path(raw).expanduser().resolve()
        if path.is_file() and path.suffix.lower() == ".pdf":
            found[str(path).lower()] = path
        elif path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            for candidate in iterator:
                if candidate.is_file() and candidate.suffix.lower() == ".pdf":
                    resolved = candidate.resolve()
                    found[str(resolved).lower()] = resolved
        else:
            raise FileNotFoundError(f"PDF or directory not found: {path}")
    return sorted(found.values(), key=lambda item: str(item).lower())


def _safe_stem(path: Path) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_." else "_" for char in path.stem)
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
    return f"{cleaned[:80]}-{digest}"


def process_pdf(
    pdf_path: Path,
    output_root: Path,
    *,
    dpi: int = 160,
    threshold: float = 0.55,
    use_ocr: bool = True,
    ocr_lang: str = "eng",
    tesseract: str | None = None,
    poppler_bin: str | None = None,
    detectors: list[Detector] | None = None,
) -> dict:
    document_id = _safe_stem(pdf_path)
    document_dir = output_root / document_id
    crops_dir = document_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    rendered = render_pdf(pdf_path, dpi=dpi, poppler_bin=poppler_bin)
    reader = PdfReader(str(pdf_path), strict=False)
    if len(rendered) != len(reader.pages):
        raise RuntimeError(
            f"Renderer returned {len(rendered)} pages, PDF reader found {len(reader.pages)}"
        )
    selected_detectors = detectors or default_detectors()
    all_detections: list[Detection] = []
    page_dimensions: list[tuple[float, float]] = []

    with pdfplumber.open(str(pdf_path)) as plumber:
        for page_index, image in enumerate(rendered):
            plumber_page = plumber.pages[page_index]
            width, height = float(plumber_page.width), float(plumber_page.height)
            page_dimensions.append((width, height))
            ocr_words = (
                run_ocr(image, tesseract=tesseract, lang=ocr_lang) if use_ocr else []
            )
            context = PageContext(
                pdf_path=pdf_path,
                page_index=page_index,
                width_pt=width,
                height_pt=height,
                image=image,
                pdf_page=reader.pages[page_index],
                plumber_page=plumber_page,
                ocr_words=ocr_words,
            )
            page_detections: list[Detection] = []
            for detector in selected_detectors:
                try:
                    page_detections.extend(detector.detect(context))
                except Exception:
                    # One heuristic must not prevent the remaining independent
                    # evidence branches from producing an output.
                    continue
            all_detections.extend(merge_detections(page_detections))

    detections = [
        detection for detection in merge_detections(all_detections) if detection.confidence >= threshold
    ]
    for number, detection in enumerate(detections, start=1):
        image = rendered[detection.page_index]
        width, height = page_dimensions[detection.page_index]
        x0, y0, x1, y1 = detection.bbox
        pixel_box = (
            max(0, round(x0 / width * image.width)),
            max(0, round(y0 / height * image.height)),
            min(image.width, round(x1 / width * image.width)),
            min(image.height, round(y1 / height * image.height)),
        )
        crop_path = crops_dir / f"page-{detection.page_index + 1:03d}-signature-{number:03d}.png"
        image.crop(pixel_box).save(crop_path)
        detection.crop_path = str(crop_path.relative_to(document_dir)).replace("\\", "/")

    annotated_path = document_dir / "annotated.pdf"
    annotate_pdf(pdf_path, annotated_path, page_dimensions, detections)
    result = {
        "schema_version": "1.0",
        "input_pdf": str(pdf_path),
        "output_directory": str(document_dir),
        "annotated_pdf": str(annotated_path),
        "page_count": len(rendered),
        "signature_count": len(detections),
        "has_signatures": bool(detections),
        "threshold": threshold,
        "detections": [detection.as_dict() for detection in detections],
        "warnings": [
            "Detections are candidates, not proof of identity or cryptographic validity."
        ],
    }
    report_path = document_dir / "report.json"
    report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["report_json"] = str(report_path)
    return result


def run_batch(
    inputs: Iterable[str | Path],
    output_root: Path,
    **options,
) -> dict:
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    documents = []
    errors = []
    pdfs = collect_pdfs(inputs, recursive=options.pop("recursive", True))
    for pdf_path in pdfs:
        try:
            documents.append(process_pdf(pdf_path, output_root, **options))
        except Exception as error:
            errors.append({"input_pdf": str(pdf_path), "error": str(error)})
    summary = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_root": str(output_root),
        "documents_processed": len(documents),
        "documents_failed": len(errors),
        "total_signatures": sum(item["signature_count"] for item in documents),
        "documents": documents,
        "errors": errors,
    }
    (output_root / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary
