from __future__ import annotations

import argparse
import base64
import json
import math
import re
import ssl
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlparse, urlunparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import cv2
import fitz
import numpy as np


IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


@dataclass
class QRHit:
    page: int
    dpi: int
    engine: str
    variant: str
    payload: str
    points: list[list[float]] | None = None
    crop_path: str | None = None


@dataclass
class MatchResult:
    key: str
    qr_value: str
    matched: bool
    score: float
    document_text: str | None
    method: str


@dataclass
class FieldComparison:
    key: str
    document_value: str | None
    web_value: str | None
    matched: bool
    score: float
    method: str


@dataclass
class UrlCheckResult:
    url: str
    ok: bool
    status: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    render_engine: str = "simple"
    error: str | None = None
    browser_error: str | None = None
    tls_verified: bool | None = None
    elapsed_seconds: float = 0.0
    text_excerpt: str | None = None
    web_details: dict[str, str] = field(default_factory=dict)
    document_details: dict[str, str] = field(default_factory=dict)
    structured_matches: list[FieldComparison] = field(default_factory=list)
    qr_field_matches: list[MatchResult] = field(default_factory=list)
    document_line_matches: list[MatchResult] = field(default_factory=list)


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = re.sub(r"\s+", " ", data).strip()
        if cleaned:
            self.parts.append(cleaned)

    def text(self) -> str:
        return "\n".join(self.parts)


def seconds_since(start: float) -> float:
    return round(time.perf_counter() - start, 3)


def json_default(obj: Any) -> Any:
    if hasattr(obj, "__dict__"):
        return vars(obj)
    return str(obj)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=json_default), encoding="utf-8")


def render_pdf_pages(pdf_path: Path, dpis: Iterable[int], max_pages: int | None = None) -> Iterable[tuple[int, int, np.ndarray]]:
    doc = fitz.open(pdf_path)
    try:
        page_count = len(doc) if max_pages is None else min(len(doc), max_pages)
        for page_index in range(page_count):
            page = doc[page_index]
            for dpi in dpis:
                zoom = dpi / 72.0
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                arr = np.frombuffer(pix.samples, dtype=np.uint8)
                arr = arr.reshape(pix.height, pix.width, pix.n)
                if pix.n == 1:
                    rgb = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
                elif pix.n == 3:
                    rgb = arr
                else:
                    rgb = cv2.cvtColor(arr, cv2.COLOR_RGBA2RGB)
                yield page_index + 1, dpi, rgb.copy()
    finally:
        doc.close()


def read_image_inputs(path: Path, dpis: Iterable[int]) -> Iterable[tuple[int, int, np.ndarray]]:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {path}")
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    for dpi in dpis:
        yield 1, dpi, rgb.copy()


def iter_page_images(input_path: Path, dpis: Iterable[int], max_pages: int | None = None) -> Iterable[tuple[int, int, np.ndarray]]:
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        yield from render_pdf_pages(input_path, dpis=dpis, max_pages=max_pages)
    elif suffix in IMAGE_SUFFIXES:
        yield from read_image_inputs(input_path, dpis=dpis)
    else:
        raise ValueError(f"Unsupported input type: {input_path.suffix}. Use PDF or one of {sorted(IMAGE_SUFFIXES)}")


def rotate_rgb(image: np.ndarray, degrees: int) -> np.ndarray:
    if degrees == 0:
        return image
    if degrees == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if degrees == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if degrees == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError(f"Unsupported rotation: {degrees}")


def image_variants(rgb: np.ndarray, rotations: Iterable[int]) -> Iterable[tuple[str, np.ndarray]]:
    for degrees in rotations:
        rotated = rotate_rgb(rgb, degrees)
        gray = cv2.cvtColor(rotated, cv2.COLOR_RGB2GRAY)
        yield f"rot{degrees}-raw", rotated

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        yield f"rot{degrees}-clahe", cv2.cvtColor(clahe, cv2.COLOR_GRAY2RGB)

        blurred = cv2.GaussianBlur(clahe, (0, 0), sigmaX=1.2)
        sharp = cv2.addWeighted(clahe, 1.7, blurred, -0.7, 0)
        yield f"rot{degrees}-sharp", cv2.cvtColor(sharp, cv2.COLOR_GRAY2RGB)

        binary = cv2.adaptiveThreshold(
            sharp,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            41,
            5,
        )
        yield f"rot{degrees}-adaptive", cv2.cvtColor(binary, cv2.COLOR_GRAY2RGB)


def normalize_points(points: Any) -> list[list[float]] | None:
    if points is None:
        return None
    arr = np.asarray(points, dtype=float)
    if arr.size == 0:
        return None
    arr = arr.reshape(-1, 2)
    return [[float(x), float(y)] for x, y in arr]


def decode_with_opencv(rgb: np.ndarray, variant: str, page: int, dpi: int) -> list[QRHit]:
    detector = cv2.QRCodeDetector()
    hits: list[QRHit] = []

    ok, decoded_info, points, _ = detector.detectAndDecodeMulti(rgb)
    if ok and decoded_info is not None:
        for idx, payload in enumerate(decoded_info):
            if not payload:
                continue
            point_set = None
            if points is not None and idx < len(points):
                point_set = normalize_points(points[idx])
            hits.append(QRHit(page=page, dpi=dpi, engine="opencv", variant=variant, payload=payload, points=point_set))

    if hits:
        return hits

    payload, points, _ = detector.detectAndDecode(rgb)
    if payload:
        hits.append(QRHit(page=page, dpi=dpi, engine="opencv", variant=variant, payload=payload, points=normalize_points(points)))
    return hits


def decode_with_zxing(rgb: np.ndarray, variant: str, page: int, dpi: int) -> list[QRHit]:
    try:
        import zxingcpp
    except Exception:
        return []

    try:
        results = zxingcpp.read_barcodes(rgb)
    except Exception:
        return []

    hits: list[QRHit] = []
    for result in results:
        payload = getattr(result, "text", None)
        if not payload:
            continue

        fmt = str(getattr(result, "format", "")).lower()
        if "qr" not in fmt and fmt not in {"barcodeformat.qrcode", "qrcode"}:
            continue

        points = None
        position = getattr(result, "position", None)
        if position is not None:
            guessed_points = []
            for name in ("top_left", "top_right", "bottom_right", "bottom_left"):
                point = getattr(position, name, None)
                if point is not None:
                    guessed_points.append([float(point.x), float(point.y)])
            points = guessed_points or None

        hits.append(QRHit(page=page, dpi=dpi, engine="zxing-cpp", variant=variant, payload=payload, points=points))
    return hits


def crop_qr(rgb: np.ndarray, points: list[list[float]] | None, padding: int = 24) -> np.ndarray | None:
    if not points:
        return None
    arr = np.asarray(points, dtype=float)
    if arr.size == 0:
        return None
    x1 = max(0, int(math.floor(arr[:, 0].min())) - padding)
    y1 = max(0, int(math.floor(arr[:, 1].min())) - padding)
    x2 = min(rgb.shape[1], int(math.ceil(arr[:, 0].max())) + padding)
    y2 = min(rgb.shape[0], int(math.ceil(arr[:, 1].max())) + padding)
    if x2 <= x1 or y2 <= y1:
        return None
    return rgb[y1:y2, x1:x2]


def scan_for_qr(
    input_path: Path,
    dpis: Iterable[int],
    max_pages: int | None,
    rotations: Iterable[int],
    save_crops_dir: Path | None = None,
    stop_after_first: bool = False,
) -> list[QRHit]:
    seen: set[tuple[int, str]] = set()
    hits: list[QRHit] = []

    for page, dpi, rgb in iter_page_images(input_path, dpis=dpis, max_pages=max_pages):
        for variant_name, variant_image in image_variants(rgb, rotations=rotations):
            variant_hits = []
            variant_hits.extend(decode_with_zxing(variant_image, variant_name, page, dpi))
            variant_hits.extend(decode_with_opencv(variant_image, variant_name, page, dpi))

            for hit in variant_hits:
                key = (page, hit.payload)
                if key in seen:
                    continue
                seen.add(key)

                if save_crops_dir is not None:
                    crop = crop_qr(variant_image, hit.points)
                    if crop is not None:
                        save_crops_dir.mkdir(parents=True, exist_ok=True)
                        crop_name = f"page{page}_qr{len(hits) + 1}_{hit.engine}_{hit.variant}.png"
                        crop_path = save_crops_dir / sanitize_filename(crop_name)
                        cv2.imwrite(str(crop_path), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
                        hit.crop_path = str(crop_path)

                hits.append(hit)
                if stop_after_first:
                    return hits
    return hits


def sanitize_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def flatten_json(value: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(value, dict):
        for key, inner in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten_json(inner, child_prefix))
    elif isinstance(value, list):
        for idx, inner in enumerate(value):
            child_prefix = f"{prefix}.{idx}" if prefix else str(idx)
            out.update(flatten_json(inner, child_prefix))
    elif value is not None:
        out[prefix or "value"] = str(value)
    return out


def try_base64_decode(text: str) -> str | None:
    candidate = text.strip()
    if len(candidate) < 12 or not re.fullmatch(r"[A-Za-z0-9_+/=-]+", candidate):
        return None
    padded = candidate + "=" * (-len(candidate) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        decoded = raw.decode("utf-8", errors="strict").strip()
    except Exception:
        return None
    if not decoded or decoded == text:
        return None
    return decoded


def parse_xml_like(text: str) -> dict[str, str]:
    try:
        root = ElementTree.fromstring(text)
    except Exception:
        return {}
    out: dict[str, str] = {}

    def walk(node: ElementTree.Element, prefix: str = "") -> None:
        name = f"{prefix}.{node.tag}" if prefix else node.tag
        for key, value in node.attrib.items():
            out[f"{name}.{key}"] = str(value)
        if node.text and node.text.strip():
            out[name] = node.text.strip()
        for child in list(node):
            walk(child, name)

    walk(root)
    return out


def parse_key_value_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in re.split(r"[\r\n;|]+", text):
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith(("http://", "https://")):
            continue
        match = re.match(r"^([A-Za-z][A-Za-z0-9 _./()-]{1,50})\s*[:=-]\s*(.{2,})$", line)
        if match:
            key = normalize_key(match.group(1))
            out[key] = match.group(2).strip()
    return out


def normalize_key(key: str) -> str:
    key = unicodedata.normalize("NFKD", key)
    key = re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_").lower()
    return key or "value"


def parse_qr_payload(payload: str) -> dict[str, str]:
    details: dict[str, str] = {"raw_payload": payload}
    text = unquote(payload.strip())

    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        details["url"] = text
        details["url_domain"] = parsed.netloc
        if parsed.path:
            details["url_path"] = parsed.path
            tail = parsed.path.rstrip("/").split("/")[-1]
            if tail:
                details["url_path_tail"] = tail
        for key, values in parse_qs(parsed.query, keep_blank_values=False).items():
            if not values:
                continue
            joined = " ".join(v.strip() for v in values if v.strip())
            if joined:
                details[normalize_key(key)] = joined

    for candidate in [text, try_base64_decode(text)]:
        if not candidate:
            continue
        try:
            decoded = json.loads(candidate)
            details.update(flatten_json(decoded))
        except Exception:
            pass
        details.update(parse_xml_like(candidate))
        details.update(parse_key_value_text(candidate))

        base64_inner = try_base64_decode(candidate)
        if base64_inner:
            try:
                details.update(flatten_json(json.loads(base64_inner)))
            except Exception:
                details.update(parse_key_value_text(base64_inner))
                details.update(parse_xml_like(base64_inner))

    return {normalize_key(k): str(v).strip() for k, v in details.items() if str(v).strip()}


def payload_url(payload: str) -> str | None:
    text = payload.strip()
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    return None


def extract_urls_from_payload_report(payload: str, details: dict[str, str]) -> list[str]:
    urls: list[str] = []
    normalized_seen: set[str] = set()
    raw_url = payload_url(payload)
    if raw_url:
        normalized_seen.add(unquote(raw_url))
        urls.append(raw_url)
    for value in details.values():
        found = payload_url(value)
        if found and unquote(found) not in normalized_seen:
            normalized_seen.add(unquote(found))
            urls.append(found)
    return urls


def decode_response_body(raw: bytes, content_type: str | None) -> str:
    charset_match = re.search(r"charset=([A-Za-z0-9_.-]+)", content_type or "", flags=re.I)
    encodings = [charset_match.group(1)] if charset_match else []
    encodings.extend(["utf-8", "utf-16", "latin-1"])
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def response_to_text(body: str, content_type: str | None) -> str:
    low_type = (content_type or "").lower()
    stripped = body.strip()
    if "html" in low_type or stripped.lower().startswith(("<!doctype html", "<html")):
        extractor = TextExtractor()
        extractor.feed(body)
        return extractor.text()
    if "json" in low_type:
        try:
            parsed = json.loads(body)
            flat = flatten_json(parsed)
            return "\n".join(f"{key}: {value}" for key, value in flat.items())
        except Exception:
            return body
    return body


def is_sparse_web_text(text: str) -> bool:
    compact = compact_text(text)
    if len(compact) < 80:
        return True
    normalized = normalize_text(text)
    if normalized in {"civil registration system"}:
        return True
    return False


FIELD_ALIASES: dict[str, set[str]] = {
    "registration_number": {"registration number", "registration no", "reg no", "reg number"},
    "name": {"name"},
    "gender": {"gender", "sex"},
    "date_of_birth": {"dob", "date of birth"},
    "mother_name": {"name of mother", "mother name"},
    "father_name": {"name of father", "father name"},
    "place_of_birth": {"place of birth"},
    "registration_date": {"registration date", "date of registration"},
    "registration_unit_name": {"registration unit name", "registration unit", "issuing authority"},
}


def canonical_field_label(label: str) -> str | None:
    normalized = normalize_text(label)
    if not normalized:
        return None
    for key, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if normalized == alias:
                return key
    return None


def split_label_value(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line:
        return None
    if "\t" in line:
        label, value = line.split("\t", 1)
        return label.strip(), value.strip()
    line = re.sub(r"\s+", " ", line).strip()
    match = re.match(r"^([A-Za-z][A-Za-z0-9 '/().-]{1,60})\s*:\s*(.+)$", line)
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def extract_date(value: str) -> str | None:
    match = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", value)
    if not match:
        match = re.search(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", value)
        if not match:
            return None
        yyyy, mm, dd = match.groups()
        return f"{int(dd):02d}-{int(mm):02d}-{yyyy}"
    dd, mm, yyyy = match.groups()
    return f"{int(dd):02d}-{int(mm):02d}-{yyyy}"


def clean_field_value(key: str, value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" \t:-,")
    if key == "gender" and "/" in value:
        value = value.split("/", 1)[0].strip(" \t:-,")
    if key == "place_of_birth":
        value = re.split(r"\s+/\s+", value, maxsplit=1)[0].strip(" \t:-,")
    if key in {"date_of_birth", "registration_date"}:
        found = extract_date(value)
        if found:
            return found
    if key == "gender":
        normalized = normalize_text(value)
        if "female" in normalized:
            return "FEMALE"
        if "male" in normalized:
            return "MALE"
    if key == "registration_number":
        value = re.sub(r"\s*:\s*", ": ", value)
        value = re.sub(r"\s+", " ", value)
    if key in {"name", "mother_name", "father_name", "registration_unit_name", "place_of_birth"}:
        value = value.upper()
    return value.strip()


def has_meaningful_field_value(key: str, value: str) -> bool:
    cleaned = clean_field_value(key, value)
    if len(compact_text(cleaned)) < 3:
        return False
    if key == "gender":
        return cleaned in {"MALE", "FEMALE"}
    if key in {"date_of_birth", "registration_date"}:
        return extract_date(cleaned) is not None
    if cleaned.endswith(":"):
        return False
    return True


def set_detail(details: dict[str, str], key: str, value: str, replace: bool = False) -> None:
    if not value or not has_meaningful_field_value(key, value):
        return
    cleaned = clean_field_value(key, value)
    if replace or key not in details:
        details[key] = cleaned


def extract_structured_details_from_web_text(text: str) -> dict[str, str]:
    details: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        pair = split_label_value(line)
        if not pair:
            continue
        label, value = pair
        key = canonical_field_label(label)
        if key:
            set_detail(details, key, value, replace=True)
    return details


def line_has_label(line: str, key: str) -> bool:
    normalized = normalize_text(line)
    return any(alias in normalized for alias in FIELD_ALIASES[key])


def find_line_index(lines: list[str], key: str) -> int | None:
    for idx, line in enumerate(lines):
        if line_has_label(line, key):
            return idx
    return None


def find_date_near(lines: list[str], start: int, end: int) -> str | None:
    for line in lines[start:end]:
        found = extract_date(line)
        if found:
            return found
    return None


def looks_like_person_name(line: str) -> bool:
    value = line.strip()
    normalized = normalize_text(value)
    if not normalized or len(normalized.split()) > 4:
        return False
    if any(word in normalized for word in {"name", "aadhaar", "birth", "date", "address"}):
        return False
    if re.search(r"\d", value):
        return False
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z .'-]{2,}", value))


def looks_like_place_line(line: str) -> bool:
    value = line.strip().strip(",._-")
    normalized = normalize_text(value)
    if not normalized or len(compact_text(value)) < 4:
        return False
    if re.search(r"[#&$~]", value):
        return False
    if any(word in normalized for word in {"name", "mother", "father", "aadhaar", "date", "birth"}):
        return False
    if any(
        word in normalized
        for word in {
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
            "thousand",
        }
    ):
        return False
    return bool(re.search(r"[A-Z]{3,}", value))


def looks_like_registration_unit(line: str) -> bool:
    normalized = normalize_text(line)
    prefixes = (
        "grama panchayat",
        "gram panchayat",
        "nagar nigam",
        "nagar palika",
        "municipal",
        "cantonment",
    )
    return any(normalized.startswith(prefix) for prefix in prefixes)


def next_parent_stop_index(lines: list[str], start: int, limit: int) -> int:
    for idx in range(start, min(len(lines), limit)):
        normalized = normalize_text(lines[idx])
        if any(word in normalized for word in {"aadhaar", "address", "registration number", "date of registration"}):
            return idx
    return min(len(lines), limit)


def extract_structured_details_from_document_lines(lines: list[str]) -> dict[str, str]:
    details: dict[str, str] = {}

    for line in lines:
        pair = split_label_value(line)
        if not pair:
            continue
        label, value = pair
        key = canonical_field_label(label)
        if key:
            set_detail(details, key, value)

    joined = "\n".join(lines)
    reg_match = re.search(r"\b[A-Z]-\d{4}\s*:\s*\d+-\d+-\d+\b", joined)
    if reg_match:
        set_detail(details, "registration_number", reg_match.group(0), replace=True)

    name_idx = find_line_index(lines, "name")
    if name_idx is not None:
        pair = split_label_value(lines[name_idx])
        if pair and canonical_field_label(pair[0]) == "name":
            set_detail(details, "name", pair[1], replace=True)

    gender_idx = find_line_index(lines, "gender")
    if gender_idx is not None:
        for line in lines[gender_idx : min(len(lines), gender_idx + 5)]:
            if "female" in normalize_text(line) or re.search(r"\bfemale\b", normalize_text(line)):
                set_detail(details, "gender", "FEMALE", replace=True)
                break
            if re.search(r"\bmale\b", normalize_text(line)):
                set_detail(details, "gender", "MALE", replace=True)
                break

    dob_idx = find_line_index(lines, "date_of_birth")
    if dob_idx is not None:
        found = find_date_near(lines, dob_idx, min(len(lines), dob_idx + 10))
        if found:
            set_detail(details, "date_of_birth", found, replace=True)

    place_idx = find_line_index(lines, "place_of_birth")
    mother_idx = find_line_index(lines, "mother_name")
    if place_idx is not None:
        end = mother_idx if mother_idx is not None and mother_idx > place_idx else min(len(lines), place_idx + 10)
        start = place_idx + 1
        if dob_idx is not None:
            for idx in range(place_idx + 1, end):
                if extract_date(lines[idx]):
                    start = idx + 1
                    break
        place_parts = [line.strip(" ,._-") for line in lines[start:end] if looks_like_place_line(line)]
        if place_parts:
            set_detail(details, "place_of_birth", ", ".join(place_parts), replace=True)

    father_idx = find_line_index(lines, "father_name")
    if father_idx is not None:
        stop = next_parent_stop_index(lines, father_idx + 1, father_idx + 10)
        candidates = [
            line.strip()
            for line in lines[father_idx + 1 : stop]
            if looks_like_person_name(line)
        ]
        if candidates:
            set_detail(details, "mother_name", candidates[0], replace=True)
        if len(candidates) > 1:
            set_detail(details, "father_name", candidates[1], replace=True)

    reg_date_idx = find_line_index(lines, "registration_date")
    if reg_date_idx is not None:
        found = find_date_near(lines, reg_date_idx, min(len(lines), reg_date_idx + 8))
        if found:
            set_detail(details, "registration_date", found, replace=True)

    for line in lines:
        if looks_like_registration_unit(line):
            set_detail(details, "registration_unit_name", line, replace=True)
            break

    return details


def compare_structured_details(
    document_details: dict[str, str],
    web_details: dict[str, str],
    threshold: float,
) -> list[FieldComparison]:
    keys = [
        "registration_number",
        "name",
        "gender",
        "date_of_birth",
        "mother_name",
        "father_name",
        "place_of_birth",
        "registration_date",
        "registration_unit_name",
    ]
    results: list[FieldComparison] = []
    for key in keys:
        document_value = document_details.get(key)
        web_value = web_details.get(key)
        if not document_value and not web_value:
            continue
        if not document_value or not web_value:
            results.append(
                FieldComparison(
                    key=key,
                    document_value=document_value,
                    web_value=web_value,
                    matched=False,
                    score=0.0,
                    method="missing-field",
                )
            )
            continue

        doc_clean = clean_field_value(key, document_value)
        web_clean = clean_field_value(key, web_value)
        if key in {"date_of_birth", "registration_date"} and extract_date(doc_clean) == extract_date(web_clean):
            results.append(FieldComparison(key, doc_clean, web_clean, True, 100.0, "date-exact"))
            continue
        if compact_text(doc_clean) == compact_text(web_clean):
            results.append(FieldComparison(key, doc_clean, web_clean, True, 100.0, "exact"))
            continue

        score = fuzzy_score(normalize_text(doc_clean), normalize_text(web_clean))
        results.append(FieldComparison(key, doc_clean, web_clean, score >= threshold, round(score, 1), "fuzzy"))
    return results


def verified_ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def url_fetch_candidates(url: str) -> list[str]:
    candidates = [url]
    parsed = urlparse(url)
    if parsed.scheme == "http":
        upgraded = urlunparse(parsed._replace(scheme="https"))
        if upgraded not in candidates:
            candidates.append(upgraded)
    return candidates


def make_request(url: str) -> Request:
    return Request(
        url,
        headers={
            "User-Agent": "LocalQRVerifier/1.0",
            "Accept": "text/html,application/json,text/plain,*/*;q=0.8",
        },
    )


def fetch_url_simple(
    request: Request,
    timeout: float,
    max_bytes: int,
    allow_insecure: bool,
) -> tuple[bytes, int | None, str, str | None, bool]:
    tls_verified = True
    try:
        with urlopen(request, timeout=timeout, context=verified_ssl_context()) as response:
            return (
                response.read(max_bytes),
                getattr(response, "status", None),
                response.geturl(),
                response.headers.get("content-type"),
                tls_verified,
            )
    except Exception as exc:
        if not allow_insecure or "CERTIFICATE_VERIFY_FAILED" not in str(exc):
            raise
        tls_verified = False
        with urlopen(request, timeout=timeout, context=ssl._create_unverified_context()) as response:
            return (
                response.read(max_bytes),
                getattr(response, "status", None),
                response.geturl(),
                response.headers.get("content-type"),
                tls_verified,
            )


def fetch_url_with_browser(url: str, timeout: float, browser_wait_ms: int) -> tuple[str, int | None, str, str | None]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install playwright && python -m playwright install chromium"
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            response = page.goto(url, wait_until="networkidle", timeout=int(timeout * 1000))
            if browser_wait_ms > 0:
                page.wait_for_timeout(browser_wait_ms)
            text = page.locator("body").inner_text(timeout=5000)
            status = response.status if response else None
            content_type = response.headers.get("content-type") if response else None
            return text, status, page.url, content_type
        finally:
            browser.close()


def useful_document_lines_for_web_check(document_lines: list[str], limit: int = 80) -> dict[str, str]:
    selected: dict[str, str] = {}
    for idx, line in enumerate(document_lines):
        normalized = normalize_text(line)
        compact = compact_text(line)
        if len(compact) < 4 or len(normalized.split()) > 12:
            continue
        key = f"ocr_line_{idx + 1}"
        selected[key] = line
        if len(selected) >= limit:
            break
    return selected


def visit_url_and_cross_check(
    url: str,
    qr_details: dict[str, str],
    document_lines: list[str],
    threshold: float,
    timeout: float,
    allow_insecure: bool = False,
    render_mode: str = "auto",
    browser_wait_ms: int = 2500,
    max_bytes: int = 2_000_000,
) -> UrlCheckResult:
    start = time.perf_counter()
    render_engine = "simple"
    tls_verified: bool | None = True
    browser_error = None
    errors: list[str] = []
    document_details = extract_structured_details_from_document_lines(document_lines)
    try:
        for candidate_url in url_fetch_candidates(url):
            try:
                if render_mode == "browser":
                    page_text, status, final_url, content_type = fetch_url_with_browser(candidate_url, timeout, browser_wait_ms)
                    render_engine = "browser"
                    tls_verified = None
                else:
                    raw, status, final_url, content_type, tls_verified = fetch_url_simple(
                        request=make_request(candidate_url),
                        timeout=timeout,
                        max_bytes=max_bytes,
                        allow_insecure=allow_insecure,
                    )
                    body = decode_response_body(raw, content_type)
                    page_text = response_to_text(body, content_type)
                    if render_mode == "auto" and is_sparse_web_text(page_text):
                        try:
                            page_text, status, final_url, content_type = fetch_url_with_browser(candidate_url, timeout, browser_wait_ms)
                            render_engine = "browser"
                        except Exception as exc:
                            browser_error = str(exc)
                break
            except Exception as exc:
                errors.append(f"{candidate_url}: {exc}")
        else:
            raise RuntimeError("; ".join(errors))
    except Exception as exc:
        return UrlCheckResult(
            url=url,
            ok=False,
            error=str(exc),
            render_engine=render_engine,
            tls_verified=tls_verified,
            elapsed_seconds=seconds_since(start),
            document_details=document_details,
        )

    page_lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    qr_field_matches = compare_qr_details_to_document(qr_details, page_lines, threshold=threshold)
    web_details = extract_structured_details_from_web_text(page_text)
    structured_matches = compare_structured_details(document_details, web_details, threshold=threshold)

    return UrlCheckResult(
        url=url,
        ok=True,
        status=status,
        final_url=final_url,
        content_type=content_type,
        render_engine=render_engine,
        browser_error=browser_error,
        tls_verified=tls_verified,
        elapsed_seconds=seconds_since(start),
        text_excerpt=page_text[:1000],
        web_details=web_details,
        document_details=document_details,
        structured_matches=structured_matches,
        qr_field_matches=qr_field_matches,
        document_line_matches=[],
    )


def is_noise_value(key: str, value: str) -> bool:
    value = value.strip()
    low_key = key.lower()
    low_value = value.lower()
    if low_key in {"raw_payload", "url", "url_domain", "url_path", "url_path_tail"}:
        return True
    if low_value.startswith(("http://", "https://")):
        return True
    if len(value) < 3:
        return True
    if len(value) > 180:
        return True
    if low_key in {"signature", "sig", "hash", "digest", "signeddata", "signed_data"}:
        return True
    if is_machine_token(value):
        return True
    return False


def is_machine_token(value: str) -> bool:
    value = value.strip()
    compact = re.sub(r"[^A-Za-z0-9+/=_-]+", "", value)
    if len(compact) < 18:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact)) and " " not in value


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(text))


def date_variants(value: str) -> set[str]:
    variants = {value}
    nums = re.findall(r"\d+", value)
    if len(nums) >= 3:
        a, b, c = nums[:3]
        if len(a) == 4:
            yyyy, mm, dd = a, b.zfill(2), c.zfill(2)
        elif len(c) == 4:
            dd, mm, yyyy = a.zfill(2), b.zfill(2), c
        else:
            return variants
        variants.update(
            {
                f"{dd}/{mm}/{yyyy}",
                f"{dd}-{mm}-{yyyy}",
                f"{yyyy}-{mm}-{dd}",
                f"{int(dd)}/{int(mm)}/{yyyy}",
                f"{int(dd)}-{int(mm)}-{yyyy}",
            }
        )
    return variants


def fuzzy_score(a: str, b: str) -> float:
    try:
        from rapidfuzz import fuzz

        return float(fuzz.partial_ratio(a, b))
    except Exception:
        from difflib import SequenceMatcher

        return SequenceMatcher(None, a, b).ratio() * 100.0


def find_best_match(value: str, document_lines: list[str], full_document_text: str, threshold: float) -> tuple[bool, float, str | None, str]:
    normalized_doc = normalize_text(full_document_text)
    compact_doc = compact_text(full_document_text)

    for variant in date_variants(value):
        if compact_text(variant) and compact_text(variant) in compact_doc:
            return True, 100.0, variant, "exact-date-or-id"

    normalized_value = normalize_text(value)
    compact_value = compact_text(value)
    if not normalized_value or not compact_value:
        return False, 0.0, None, "empty"

    if normalized_value in normalized_doc or compact_value in compact_doc:
        return True, 100.0, value, "exact"

    if is_machine_token(value):
        return False, 0.0, None, "token-exact-only"

    best_score = 0.0
    best_line: str | None = None
    for line in document_lines:
        normalized_line = normalize_text(line)
        if not normalized_line:
            continue
        if len(compact_text(line)) < max(4, min(10, len(compact_value) // 2)):
            continue
        score = fuzzy_score(normalized_value, normalized_line)
        if score > best_score:
            best_score = score
            best_line = line

    return best_score >= threshold, best_score, best_line, "fuzzy"


def compare_qr_details_to_document(
    details: dict[str, str],
    document_lines: list[str],
    threshold: float = 82.0,
) -> list[MatchResult]:
    full_text = "\n".join(document_lines)
    results: list[MatchResult] = []
    for key, value in details.items():
        if is_noise_value(key, value):
            continue
        matched, score, line, method = find_best_match(value, document_lines, full_text, threshold)
        results.append(MatchResult(key=key, qr_value=value, matched=matched, score=round(score, 1), document_text=line, method=method))
    return results


def ocr_pdf_or_image(
    input_path: Path,
    languages: list[str],
    gpu: bool | str,
    dpi: int = 300,
    max_pages: int | None = None,
) -> list[dict[str, Any]]:
    try:
        import easyocr
    except Exception as exc:
        raise RuntimeError(
            "EasyOCR is not installed. Install requirements.txt first. "
            "For GPU use, install a CUDA-enabled PyTorch build before installing EasyOCR."
        ) from exc

    use_gpu = resolve_gpu(gpu)
    reader = easyocr.Reader(languages, gpu=use_gpu)
    page_results: list[dict[str, Any]] = []

    for page, _, rgb in iter_page_images(input_path, dpis=[dpi], max_pages=max_pages):
        results = reader.readtext(rgb, detail=1, paragraph=False)
        lines = []
        detailed = []
        for item in results:
            if len(item) < 2:
                continue
            box, text = item[0], str(item[1]).strip()
            confidence = float(item[2]) if len(item) >= 3 else None
            if text:
                lines.append(text)
                detailed.append({"text": text, "confidence": confidence, "box": box})
        page_results.append({"page": page, "lines": lines, "items": detailed})
    return page_results


def resolve_gpu(gpu: bool | str) -> bool:
    if isinstance(gpu, bool):
        return gpu
    gpu = gpu.lower()
    if gpu in {"yes", "true", "1", "cuda"}:
        return True
    if gpu in {"no", "false", "0", "cpu"}:
        return False
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def parse_int_list(values: list[str] | None, default: list[int]) -> list[int]:
    if not values:
        return default
    out: list[int] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                out.append(int(part))
    return out


def common_qr_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input", type=Path, help="PDF or image file to scan.")
    parser.add_argument("--dpi", nargs="*", default=None, help="Render DPI values. Default: 250 350 450")
    parser.add_argument("--max-pages", type=int, default=None, help="Only scan the first N pages.")
    parser.add_argument("--rotations", nargs="*", default=None, help="Rotations to try. Default: 0 90 180 270")
    parser.add_argument("--save-crops", type=Path, default=None, help="Folder where detected QR crops should be saved.")
    parser.add_argument("--out", type=Path, default=None, help="Write a JSON report to this path.")
    parser.add_argument("--stop-after-first", action="store_true", help="Stop scanning after the first QR code is found.")


def hits_to_dicts(hits: list[QRHit]) -> list[dict[str, Any]]:
    return [asdict(hit) for hit in hits]


def fail(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)
