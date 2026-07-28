from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from PIL import Image


BBox = tuple[float, float, float, float]


@dataclass(slots=True)
class PageContext:
    pdf_path: Path
    page_index: int
    width_pt: float
    height_pt: float
    image: Image.Image
    pdf_page: Any
    plumber_page: Any
    ocr_words: list[dict[str, Any]] = field(default_factory=list)

    def pixels_to_points(self, bbox: BBox) -> BBox:
        sx = self.width_pt / self.image.width
        sy = self.height_pt / self.image.height
        x0, y0, x1, y1 = bbox
        return x0 * sx, y0 * sy, x1 * sx, y1 * sy

    def points_to_pixels(self, bbox: BBox) -> tuple[int, int, int, int]:
        sx = self.image.width / self.width_pt
        sy = self.image.height / self.height_pt
        x0, y0, x1, y1 = bbox
        return (
            max(0, round(x0 * sx)),
            max(0, round(y0 * sy)),
            min(self.image.width, round(x1 * sx)),
            min(self.image.height, round(y1 * sy)),
        )


@dataclass(slots=True)
class Detection:
    page_index: int
    bbox: BBox
    confidence: float
    method: str
    reasons: list[str] = field(default_factory=list)
    crop_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "page": self.page_index + 1,
            "bbox": [round(v, 2) for v in self.bbox],
            "bbox_coordinate_system": "PDF points, origin at displayed page top-left",
            "confidence": round(self.confidence, 3),
            "method": self.method,
            "reasons": self.reasons,
            "crop": self.crop_path,
        }


class Detector(Protocol):
    name: str

    def detect(self, context: PageContext) -> list[Detection]:
        ...
