from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps

from .models import BBox, Detection, Detector, PageContext


# OCR reads handwriting as low-confidence letters: the signature on a UP Board
# marksheet came back as "U" at confidence 18. Treating that as recognized print
# made the ink detector discard the very mark it was looking for, so only
# confident reads count as text. Real print on the same scan scored 74 to 96.
OCR_TEXT_CONFIDENCE = 50.0


def _clamp(bbox: BBox, width: float, height: float) -> BBox:
    x0, y0, x1, y1 = bbox
    return (
        max(0.0, min(x0, width)),
        max(0.0, min(y0, height)),
        max(0.0, min(x1, width)),
        max(0.0, min(y1, height)),
    )


def _visual_features(image: Image.Image) -> dict[str, float]:
    gray = np.asarray(ImageOps.grayscale(image), dtype=np.uint8)
    if not gray.size:
        return {"dark": 0.0, "white": 0.0, "variation": 0.0, "columns": 0.0}
    dark = gray < 185
    return {
        "dark": float(dark.mean()),
        "white": float((gray > 235).mean()),
        "variation": float(gray.std() / 128.0),
        "columns": float((dark.mean(axis=0) > 0.01).mean()),
    }


def _signature_likelihood(image: Image.Image) -> tuple[float, list[str]]:
    features = _visual_features(image)
    aspect = image.width / max(1, image.height)
    score = 0.0
    reasons: list[str] = []
    if 1.15 <= aspect <= 9:
        score += 0.25
        reasons.append("wide ink region")
    elif 0.30 <= aspect < 1.15:
        # Many Indian official signatures are a tall stylised initial rather
        # than a wide cursive run. Scoring only wide regions made those
        # invisible, so an upright mark earns credit too - less than a wide
        # one, because an upright box is also the shape of a stamp or a photo.
        score += 0.17
        reasons.append("upright handwritten mark")
    if 0.008 <= features["dark"] <= 0.28:
        score += 0.30
        reasons.append("sparse pen-like ink")
    if features["white"] >= 0.55:
        score += 0.18
        reasons.append("mostly light background")
    if 0.10 <= features["columns"] <= 0.90:
        score += 0.15
    if 0.05 <= features["variation"] <= 0.65:
        score += 0.12
    # Strongly suppress square, dense QR-like marks and photographs.
    if 0.8 <= aspect <= 1.2 and features["dark"] > 0.22:
        score -= 0.6
    if features["white"] < 0.35 or features["variation"] > 0.75:
        score -= 0.35
    return max(0.0, min(1.0, score)), reasons


def _despeckle(mask: np.ndarray, min_neighbours: int = 4) -> np.ndarray:
    """Drop isolated dark pixels left by a noisy scan.

    A photocopied certificate carries speckle across the whole page. Left in,
    it lifts every row above any sane ink threshold, which is what stopped this
    detector from segmenting anything. Pen strokes survive because their pixels
    sit next to other stroke pixels; grit does not.
    """

    if not mask.any():
        return mask
    height, width = mask.shape
    padded = np.pad(mask, 1).astype(np.uint8)
    neighbours = sum(
        padded[dy : dy + height, dx : dx + width]
        for dy in range(3)
        for dx in range(3)
    ) - mask.astype(np.uint8)
    return mask & (neighbours >= min_neighbours)


def _confident_text_boxes(context: PageContext) -> list[BBox]:
    """Regions this page has already explained as printed text, in points."""

    boxes: list[BBox] = []
    try:
        for word in context.plumber_page.extract_words() or []:
            boxes.append(
                (
                    float(word["x0"]),
                    float(word["top"]),
                    float(word["x1"]),
                    float(word["bottom"]),
                )
            )
    except Exception:
        pass
    for word in context.ocr_words:
        if float(word.get("confidence", 0.0)) >= OCR_TEXT_CONFIDENCE:
            boxes.append(context.pixels_to_points(word["bbox_px"]))
    return boxes


def _tighten_to_ink(context: PageContext, bbox: BBox, margin_pt: float = 2) -> BBox | None:
    """Trim an anchor search region to its actual dark ink.

    Long rule lines are ignored before measuring the bounds. This gives useful
    localization instead of labeling the entire fixed-size anchor search window.
    """
    pixel_box = context.points_to_pixels(bbox)
    crop = context.image.crop(pixel_box)
    gray = np.asarray(ImageOps.grayscale(crop), dtype=np.uint8)
    if not gray.size:
        return None
    ink = gray < 170
    if ink.sum() < 20:
        return None
    row_counts = ink.sum(axis=1)
    col_counts = ink.sum(axis=0)
    ink[row_counts > crop.width * 0.58, :] = False
    ink[:, col_counts > crop.height * 0.80] = False
    ys, xs = np.nonzero(ink)
    if xs.size < 20:
        return None
    local = context.pixels_to_points(
        (
            pixel_box[0] + int(xs.min()),
            pixel_box[1] + int(ys.min()),
            pixel_box[0] + int(xs.max()) + 1,
            pixel_box[1] + int(ys.max()) + 1,
        )
    )
    return _clamp(
        (
            local[0] - margin_pt,
            local[1] - margin_pt,
            local[2] + margin_pt,
            local[3] + margin_pt,
        ),
        context.width_pt,
        context.height_pt,
    )


class SignatureWidgetDetector:
    name = "pdf_signature_widget"

    @staticmethod
    def _rect_to_top_left(rect: Any, width: float, height: float) -> BBox | None:
        try:
            x0, y0, x1, y1 = map(float, rect)
        except (TypeError, ValueError):
            return None
        # Widget rectangles are in unrotated PDF user space. Most certificates are
        # unrotated; rotated pages are normalized before the annotation is merged.
        return _clamp((min(x0, x1), height - max(y0, y1), max(x0, x1), height - min(y0, y1)), width, height)

    def detect(self, context: PageContext) -> list[Detection]:
        found: list[Detection] = []
        annotations = context.pdf_page.get("/Annots") or []
        for annotation_ref in annotations:
            try:
                annotation = annotation_ref.get_object()
                parent = annotation.get("/Parent")
                parent = parent.get_object() if parent else None
                field_type = annotation.get("/FT") or (parent and parent.get("/FT"))
                value = annotation.get("/V") or (parent and parent.get("/V"))
                subtype = annotation.get("/Subtype")
                if str(field_type) != "/Sig" and not (
                    str(subtype) == "/Widget" and value and value.get_object().get("/Type") == "/Sig"
                ):
                    continue
                bbox = self._rect_to_top_left(
                    annotation.get("/Rect"), context.width_pt, context.height_pt
                )
                if bbox and bbox[2] - bbox[0] > 2 and bbox[3] - bbox[1] > 2:
                    found.append(
                        Detection(
                            context.page_index,
                            bbox,
                            0.99,
                            self.name,
                            ["PDF contains a digital signature field/widget"],
                        )
                    )
            except Exception:
                continue
        return found


class EmbeddedImageDetector:
    name = "embedded_image"

    def detect(self, context: PageContext) -> list[Detection]:
        found: list[Detection] = []
        page_area = context.width_pt * context.height_pt
        for item in context.plumber_page.images:
            try:
                bbox = _clamp(
                    (
                        float(item["x0"]),
                        float(item["top"]),
                        float(item["x1"]),
                        float(item["bottom"]),
                    ),
                    context.width_pt,
                    context.height_pt,
                )
                width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
                ratio = width * height / page_area
                aspect = width / max(1.0, height)
                # Full-page images are page scans, not isolated signatures.
                if not (0.00025 <= ratio <= 0.12 and 1.1 <= aspect <= 10):
                    continue
                crop = context.image.crop(context.points_to_pixels(bbox))
                likelihood, reasons = _signature_likelihood(crop)
                if likelihood < 0.55:
                    continue
                # A wide logo can look signature-like. Standalone image evidence
                # deliberately remains just below the balanced threshold; overlap
                # with an anchor or ink detector promotes it during evidence merge.
                confidence = min(0.54, 0.37 + likelihood * 0.17)
                found.append(
                    Detection(
                        context.page_index,
                        bbox,
                        confidence,
                        self.name,
                        ["separate raster object in digital PDF", *reasons],
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return found


_ANCHOR_RE = re.compile(
    r"\b(signature|signed|signatory|issuing\s+authority|authorized\s+signatory)\b",
    re.IGNORECASE,
)


class AnchorDetector:
    name = "text_or_ocr_anchor"

    @staticmethod
    def _word_anchors(
        grouped_words: dict[Any, list[dict[str, Any]]], source: str
    ) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        rejected_phrases = (
            "facsimile signature",
            "digitally signed",
            "digital signature",
            "signature certificate",
            "signature is not verified",
        )
        for words in grouped_words.values():
            words.sort(key=lambda word: word["bbox"][0])
            line_text = " ".join(str(word["text"]) for word in words)
            lowered_line = line_text.lower()
            if any(phrase in lowered_line for phrase in rejected_phrases):
                continue
            normalized = [
                re.sub(r"[^a-z]", "", str(word["text"]).lower()) for word in words
            ]
            index = 0
            while index < len(words):
                span = 1
                phrase = None
                if normalized[index].startswith("issuing") and index + 1 < len(words):
                    if normalized[index + 1].startswith("author"):
                        span, phrase = 2, "issuing authority"
                elif normalized[index].startswith("authorized") and index + 1 < len(words):
                    if normalized[index + 1].startswith("signator"):
                        span, phrase = 2, "authorized signatory"
                elif normalized[index].startswith("signature"):
                    phrase = "signature"
                elif normalized[index].startswith("signatory"):
                    phrase = "signatory"
                elif normalized[index] in {"signed", "sign"}:
                    phrase = normalized[index]
                if phrase:
                    selected = words[index : index + span]
                    hits.append(
                        {
                            "text": phrase,
                            "bbox": (
                                min(word["bbox"][0] for word in selected),
                                min(word["bbox"][1] for word in selected),
                                max(word["bbox"][2] for word in selected),
                                max(word["bbox"][3] for word in selected),
                            ),
                            "source": source,
                        }
                    )
                    index += span
                else:
                    index += 1
        return hits

    def _digital_anchors(self, context: PageContext) -> list[dict[str, Any]]:
        try:
            words = context.plumber_page.extract_words() or []
        except Exception:
            return []
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for word in words:
            grouped[round(float(word["top"]) / 4)].append(
                {
                    "text": str(word["text"]),
                    "bbox": (
                        float(word["x0"]),
                        float(word["top"]),
                        float(word["x1"]),
                        float(word["bottom"]),
                    ),
                }
            )
        return self._word_anchors(grouped, "PDF text")

    def _ocr_anchors(self, context: PageContext) -> list[dict[str, Any]]:
        grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for word in context.ocr_words:
            grouped[word["line_key"]].append(
                {
                    "text": word["text"],
                    "bbox": context.pixels_to_points(word["bbox_px"]),
                }
            )
        return self._word_anchors(grouped, "OCR")

    @staticmethod
    def _search_box(anchor: BBox, text: str, width: float, height: float) -> BBox:
        x0, y0, x1, y1 = anchor
        lower = text.lower()
        if "issuing" in lower or "signatory" in lower:
            return _clamp((x0 - 45, y0 - 60, x1 + 10, y0 - 3), width, height)
        return _clamp((x0 - 35, y0 - 60, x1 + 80, y0 - 3), width, height)

    def detect(self, context: PageContext) -> list[Detection]:
        found: list[Detection] = []
        lines = [*self._digital_anchors(context), *self._ocr_anchors(context)]
        seen: list[BBox] = []
        for line in lines:
            match = _ANCHOR_RE.search(line["text"])
            if not match:
                continue
            search_bbox = self._search_box(
                line["bbox"], match.group(0), context.width_pt, context.height_pt
            )
            bbox = _tighten_to_ink(context, search_bbox)
            if not bbox or bbox[2] - bbox[0] < 8 or bbox[3] - bbox[1] < 6:
                continue
            if any(_iou(bbox, old) > 0.75 for old in seen):
                continue
            seen.append(bbox)
            crop = context.image.crop(context.points_to_pixels(bbox))
            likelihood, reasons = _signature_likelihood(crop)
            if likelihood < 0.18:
                continue
            source_bonus = 0.02 if line["source"] == "OCR" else 0.0
            confidence = min(0.91, 0.68 + 0.20 * likelihood + source_bonus)
            found.append(
                Detection(
                    context.page_index,
                    bbox,
                    confidence,
                    self.name,
                    [
                        f"{line['source']} anchor: {line['text']}",
                        "ink found in expected area above anchor",
                        *reasons,
                    ],
                )
            )
        return found


class ClassicalInkDetector:
    """Model-free detector for ink the page has not explained as print.

    This works by elimination rather than by shape. Everything the page can
    account for is removed - confidently recognized text, ruled lines, scan
    speckle - and whatever ink is left standing on its own is measured. That
    order matters: the previous version segmented the page into horizontal
    bands first, which cannot isolate a tall narrow signature sitting directly
    above its own printed name, and collapsed entirely on a speckled photocopy
    where almost every row carries some dark pixels.
    """

    name = "classical_ink"

    # A signature is ink the page did not already account for. Grouping is
    # deliberately tighter vertically than horizontally: strokes of one
    # signature spread sideways, while the printed name sits just below it.
    _GROUP_KERNEL = (13, 5)
    # Certificates are signed at the foot of the page. Only ink below this
    # fraction of the page height may carry a signature on its own evidence.
    _SIGNING_BAND = 0.75
    # Minimum width and height, in analysis pixels, for ink to register a
    # signature without corroboration from another detector.
    _MIN_STANDALONE = (25, 15)

    def detect(self, context: PageContext) -> list[Detection]:
        image = context.image
        scale = min(1.0, 900 / image.width)
        analysis = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        )
        gray = np.asarray(ImageOps.grayscale(analysis), dtype=np.uint8)
        height, width = gray.shape
        ink = _despeckle(gray < 155)
        if not ink.any():
            return []

        recognized_text = _confident_text_boxes(context)
        explained = np.zeros_like(ink)
        for text_bbox in recognized_text:
            x0, y0, x1, y1 = context.points_to_pixels(text_bbox)
            explained[
                max(0, int(y0 * scale) - 1) : int(y1 * scale) + 2,
                max(0, int(x0 * scale) - 1) : int(x1 * scale) + 2,
            ] = True
        remaining = (ink & ~explained).astype(np.uint8)
        if not remaining.any():
            return []

        # Drop ruled lines and table borders, which survive text removal.
        count, labels, stats, _ = cv2.connectedComponentsWithStats(remaining, connectivity=8)
        kept = np.zeros_like(remaining)
        for index in range(1, count):
            box_width = int(stats[index, cv2.CC_STAT_WIDTH])
            box_height = int(stats[index, cv2.CC_STAT_HEIGHT])
            area = int(stats[index, cv2.CC_STAT_AREA])
            if area < 12:
                continue
            if box_width > width * 0.35 and box_height < 6:
                continue
            if box_height > height * 0.30 and box_width < 6:
                continue
            kept[labels == index] = 1
        if not kept.any():
            return []

        grouped = cv2.dilate(
            kept, cv2.getStructuringElement(cv2.MORPH_RECT, self._GROUP_KERNEL)
        )
        clusters, cluster_labels, cluster_stats, _ = cv2.connectedComponentsWithStats(
            grouped, connectivity=8
        )
        ink_mass = np.bincount(
            cluster_labels.ravel(), weights=kept.ravel(), minlength=clusters
        )

        detections: list[Detection] = []
        promotable: list[tuple[float, float, Detection]] = []
        for index in range(1, clusters):
            left = int(cluster_stats[index, cv2.CC_STAT_LEFT])
            top = int(cluster_stats[index, cv2.CC_STAT_TOP])
            box_width = int(cluster_stats[index, cv2.CC_STAT_WIDTH])
            box_height = int(cluster_stats[index, cv2.CC_STAT_HEIGHT])
            if box_width > width * 0.65 or box_height > height * 0.13:
                continue
            aspect = box_width / max(1, box_height)
            wide = box_width >= max(45, box_height * 1.4)
            upright = 18 <= box_width and 18 <= box_height and 0.30 <= aspect < 1.45
            if not (wide or upright):
                continue

            bbox_px = (
                left / scale,
                max(0.0, (top - 3) / scale),
                (left + box_width) / scale,
                min(float(image.height), (top + box_height + 3) / scale),
            )
            bbox = _tighten_to_ink(context, context.pixels_to_points(bbox_px)) or (
                context.pixels_to_points(bbox_px)
            )
            candidate_area = max(1.0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
            text_coverage = sum(
                _intersection_area(bbox, text_bbox) for text_bbox in recognized_text
            ) / candidate_area
            if text_coverage > 0.18:
                continue

            crop = image.crop(context.points_to_pixels(bbox))
            likelihood, reasons = _signature_likelihood(crop)
            if likelihood < 0.62:
                continue

            low_on_page = bbox[1] > context.height_pt * 0.48
            position_bonus = 0.03 if low_on_page else 0.0
            confidence = min(0.54, 0.35 + likelihood * 0.16 + position_bonus)
            detection = Detection(
                context.page_index,
                bbox,
                confidence,
                self.name,
                ["ink the page does not explain as print", *reasons],
            )
            detections.append(detection)
            if (
                likelihood >= 0.80
                and text_coverage <= 0.08
                and bbox[1] > context.height_pt * self._SIGNING_BAND
                # Leftover fragments of a ruled line score well on every visual
                # measure - sparse, dark on light, wide - so size is what rules
                # them out. A signature is a mark, not a 7-pixel sliver.
                and box_height >= self._MIN_STANDALONE[1]
                and box_width >= self._MIN_STANDALONE[0]
            ):
                promotable.append((likelihood, float(ink_mass[index]), detection))

        # Classical evidence normally stays below the accept threshold, so a
        # lone smudge cannot register a signature by itself. A photographed
        # certificate is the case where nothing can corroborate it: no
        # signature widget, no separate raster object, and often no English
        # anchor word. So the single strongest piece of pen-like ink in the
        # signing band may stand alone - and only that one. Promoting every
        # qualifying cluster instead turned pages of Devanagari print, which
        # English OCR cannot confidently read, into six or seven signatures.
        if promotable:
            score, _, best = max(promotable, key=lambda item: (item[0], item[1]))
            best.confidence = min(0.66, 0.56 + score * 0.10)
            best.reasons.append("strongest unexplained ink in the signing band")
        return detections


def _connected_components(mask: np.ndarray, min_pixels: int = 8) -> list[tuple[int, int, int, int, int]]:
    """Return 8-connected component boxes as x0, y0, x1, y1, pixel_count."""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    components: list[tuple[int, int, int, int, int]] = []
    for seed_y, seed_x in zip(*np.nonzero(mask)):
        if seen[seed_y, seed_x]:
            continue
        stack = [(int(seed_y), int(seed_x))]
        seen[seed_y, seed_x] = True
        min_x = max_x = int(seed_x)
        min_y = max_y = int(seed_y)
        pixels = 0
        while stack:
            y, x = stack.pop()
            pixels += 1
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not dx and not dy:
                        continue
                    ny, nx = y + dy, x + dx
                    if (
                        0 <= ny < height
                        and 0 <= nx < width
                        and mask[ny, nx]
                        and not seen[ny, nx]
                    ):
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        if pixels >= min_pixels:
            components.append((min_x, min_y, max_x + 1, max_y + 1, pixels))
    return components


class TableScanDetector:
    """Detect official signing zones in landscape table photographs.

    Phone photographs of registers often appear as one landscape raster object
    centered on an otherwise blank PDF page. Their signatures are commonly
    immediately above blue designation stamps, or in a lower-right approval
    cell. This branch is intentionally limited to that layout so ordinary
    full-page scans, logos, portraits, and vertical phone photos are excluded.
    """

    name = "table_scan"

    @staticmethod
    def _page_box(image_box: BBox, normalized: BBox) -> BBox:
        x0, y0, x1, y1 = image_box
        width, height = x1 - x0, y1 - y0
        return (
            x0 + normalized[0] * width,
            y0 + normalized[1] * height,
            x0 + normalized[2] * width,
            y0 + normalized[3] * height,
        )

    @staticmethod
    def _group_blue_components(
        components: list[tuple[int, int, int, int, int]], width: int
    ) -> list[tuple[int, int, int, int, int]]:
        groups: list[list[int]] = []
        max_gap = max(4, round(width * 0.04))
        for x0, y0, x1, y1, pixels in sorted(components):
            if groups and x0 <= groups[-1][2] + max_gap:
                group = groups[-1]
                group[0] = min(group[0], x0)
                group[1] = min(group[1], y0)
                group[2] = max(group[2], x1)
                group[3] = max(group[3], y1)
                group[4] += pixels
            else:
                groups.append([x0, y0, x1, y1, pixels])
        return [tuple(group) for group in groups]

    def detect(self, context: PageContext) -> list[Detection]:
        page_area = context.width_pt * context.height_pt
        results: list[Detection] = []
        for item in context.plumber_page.images:
            try:
                image_box = (
                    float(item["x0"]),
                    float(item["top"]),
                    float(item["x1"]),
                    float(item["bottom"]),
                )
                width_pt = image_box[2] - image_box[0]
                height_pt = image_box[3] - image_box[1]
                area_ratio = width_pt * height_pt / page_area
                aspect = width_pt / max(1.0, height_pt)
                if not (0.10 <= area_ratio <= 0.35 and 1.15 <= aspect <= 1.75):
                    continue

                crop = context.image.crop(context.points_to_pixels(image_box)).convert("RGB")
                rgb = np.asarray(crop, dtype=np.int16)
                red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
                blue_mask = (blue < 230) & (blue > red + 12) & (blue > green + 4)
                height, width = blue_mask.shape
                components = [
                    component
                    for component in _connected_components(blue_mask, min_pixels=8)
                    if component[4] >= 60
                    and component[0] / width >= 0.35
                    and component[1] / height >= 0.55
                    and component[2] / width <= 0.99
                ]

                # A weak blue mark in the bottom-right approval cell is a better
                # signal than a larger unrelated stamp in the middle of the table.
                has_bottom_right_mark = any(
                    x0 / width >= 0.65 and y0 / height >= 0.78
                    for x0, y0, _x1, _y1, _pixels in components
                )
                if has_bottom_right_mark:
                    normalized_boxes = [(0.69, 0.82, 0.90, 0.94)]
                    confidence = 0.67
                    reason = "lower-right approval/signing cell with stamp evidence"
                else:
                    grouped = self._group_blue_components(
                        [
                            component
                            for component in components
                            if component[1] / height <= 0.78
                        ],
                        width,
                    )
                    strong_groups = [group for group in grouped if group[4] >= 300]
                    normalized_boxes = []
                    for x0, y0, x1, y1, _pixels in strong_groups:
                        nx0, ny0 = x0 / width, y0 / height
                        nx1, ny1 = x1 / width, y1 / height
                        if ny0 < 0.62:
                            normalized_boxes.append(
                                (
                                    min(0.98, nx0 + 0.05),
                                    max(0.0, ny0 - 0.03),
                                    min(0.99, nx1 + 0.08),
                                    min(0.99, ny1),
                                )
                            )
                        else:
                            normalized_boxes.append(
                                (
                                    min(0.98, nx0 + 0.015),
                                    max(0.0, ny0 - 0.06),
                                    min(0.99, nx1 + 0.02),
                                    min(0.99, ny0 + 0.06),
                                )
                            )
                    if normalized_boxes:
                        confidence = 0.76
                        reason = "handwritten ink zone aligned with blue designation stamp"
                    else:
                        features = _visual_features(crop)
                        if features["white"] < 0.40:
                            normalized_boxes = [(0.73, 0.69, 0.92, 0.84)]
                            reason = "lower-right signing cell in a low-contrast table photo"
                        else:
                            normalized_boxes = [(0.69, 0.82, 0.90, 0.94)]
                            reason = "lower-right signing cell in a table photo"
                        confidence = 0.64

                for normalized in normalized_boxes:
                    bbox = _clamp(
                        self._page_box(image_box, normalized),
                        context.width_pt,
                        context.height_pt,
                    )
                    if bbox[2] - bbox[0] >= 12 and bbox[3] - bbox[1] >= 8:
                        results.append(
                            Detection(
                                context.page_index,
                                bbox,
                                confidence,
                                self.name,
                                [
                                    "landscape embedded table/photo layout",
                                    reason,
                                    "review recommended for dense handwritten forms",
                                ],
                            )
                        )
            except (KeyError, TypeError, ValueError):
                continue
        return results


def _iou(a: BBox, b: BBox) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if not intersection:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    return intersection / max(1e-9, area_a + area_b - intersection)


def _intersection_area(a: BBox, b: BBox) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )


def merge_detections(
    detections: list[Detection], iou_threshold: float = 0.35
) -> list[Detection]:
    merged: list[Detection] = []
    for detection in sorted(detections, key=lambda item: item.confidence, reverse=True):
        overlap = next(
            (
                existing
                for existing in merged
                if existing.page_index == detection.page_index
                and _iou(existing.bbox, detection.bbox) >= iou_threshold
            ),
            None,
        )
        if overlap is None:
            merged.append(detection)
        else:
            overlap.reasons.extend(
                reason for reason in detection.reasons if reason not in overlap.reasons
            )
            if detection.method not in overlap.method.split("+"):
                overlap.method += f"+{detection.method}"
            overlap.confidence = min(
                0.995, max(overlap.confidence, detection.confidence) + 0.04
            )
    return sorted(merged, key=lambda item: (item.page_index, -item.confidence))


def default_detectors() -> list[Detector]:
    return [
        SignatureWidgetDetector(),
        EmbeddedImageDetector(),
        AnchorDetector(),
        TableScanDetector(),
        ClassicalInkDetector(),
    ]
