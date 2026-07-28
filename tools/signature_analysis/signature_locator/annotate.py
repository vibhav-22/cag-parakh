from __future__ import annotations

import shutil
from pathlib import Path

import fitz

from .models import Detection


RED = (0.92, 0.12, 0.12)
WHITE = (1.0, 1.0, 1.0)


def annotate_pdf(
    input_path: Path,
    output_path: Path,
    page_dimensions: list[tuple[float, float]],
    detections: list[Detection],
) -> None:
    """Write labeled boxes using the backend's existing PyMuPDF runtime."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not detections:
        shutil.copyfile(input_path, output_path)
        return

    by_page: dict[int, list[Detection]] = {}
    for detection in detections:
        by_page.setdefault(detection.page_index, []).append(detection)

    document = fitz.open(str(input_path))
    try:
        for page_index, page in enumerate(document):
            page_detections = by_page.get(page_index, [])
            if not page_detections:
                continue
            source_width, source_height = page_dimensions[page_index]
            scale_x = page.rect.width / max(source_width, 1.0)
            scale_y = page.rect.height / max(source_height, 1.0)

            for number, detection in enumerate(page_detections, start=1):
                x0, y0, x1, y1 = detection.bbox
                box = fitz.Rect(
                    x0 * scale_x,
                    y0 * scale_y,
                    x1 * scale_x,
                    y1 * scale_y,
                )
                page.draw_rect(box, color=RED, width=1.8, overlay=True)

                label = f"Signature {number} ({detection.confidence:.0%})"
                label_width = fitz.get_text_length(
                    label, fontname="hebo", fontsize=8
                ) + 6
                label_top = max(0.0, box.y0 - 12.0)
                label_box = fitz.Rect(
                    box.x0,
                    label_top,
                    min(page.rect.width, box.x0 + label_width),
                    label_top + 11.0,
                )
                page.draw_rect(
                    label_box,
                    color=RED,
                    fill=RED,
                    width=0,
                    overlay=True,
                )
                page.insert_text(
                    (label_box.x0 + 3, label_box.y0 + 8),
                    label,
                    fontname="hebo",
                    fontsize=8,
                    color=WHITE,
                    overlay=True,
                )

        document.save(str(output_path), garbage=3, deflate=True)
    finally:
        document.close()
