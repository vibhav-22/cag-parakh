#!/usr/bin/env python
"""
scanner_noise_fingerprint_check

Prototype PDF capture-source consistency checker for scanned or photographed
documents. It renders PDF pages, extracts content-suppressed noise residuals,
compares pages, and flags local regions whose noise/compression/blur profile
does not fit the rest of the page.

This is a risk signal, not a forensic conclusion.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageFilter

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - pypdf is optional at runtime
    PdfReader = None  # type: ignore


TOOL_VERSION = "0.1.3"
EPS = 1e-9


@dataclass
class PageFeatures:
    page: int
    image_file: str
    width_px: int
    height_px: int
    capture_class: str
    mask_coverage: float
    noise_std: float
    residual_mad: float
    row_band_strength: float
    column_band_strength: float
    illumination_std: float
    illumination_range: float
    sharpness: float
    blockiness: float
    spectral_bins: list[float]
    row_profile: list[float]
    column_profile: list[float]


@dataclass
class PairwiseComparison:
    page_a: int
    page_b: int
    mismatch_score: float
    spectral_dissimilarity: float
    row_profile_correlation: float | None
    column_profile_correlation: float | None
    statistic_dissimilarity: float
    reason: str


@dataclass
class SuspiciousPage:
    page: int
    score: float
    severity: str
    reason: str


@dataclass
class SuspiciousRegion:
    page: int
    score: float
    severity: str
    bbox_px: dict[str, int]
    bbox_normalized: dict[str, float]
    reason: str


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check whether pages or regions in a PDF show inconsistent "
            "scanner/camera/printer capture noise."
        )
    )
    parser.add_argument("pdf", help="Input PDF path.")
    parser.add_argument(
        "--output-dir",
        help="Directory for report output. Defaults to a folder next to the PDF.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=220,
        help="Render DPI. Higher DPI is slower but may expose more noise. Default: 220.",
    )
    parser.add_argument(
        "--grid",
        type=int,
        default=4,
        help="Grid size for local region checks. Default: 4 means 4x4 tiles.",
    )
    parser.add_argument(
        "--keep-images",
        action="store_true",
        help="Keep rendered page PNGs in the output directory.",
    )
    parser.add_argument(
        "--max-analysis-side",
        type=int,
        default=1800,
        help="Maximum page side in pixels used for analysis. Default: 1800.",
    )
    parser.add_argument(
        "--page-only",
        action="store_true",
        help="Only compare pages with each other; skip local suspicious-region checks.",
    )
    return parser.parse_args(argv)


def locate_pdftoppm() -> str:
    configured = os.environ.get("PDFTOPPM")
    candidates: list[str] = []
    if configured:
        candidates.append(configured)

    home = Path.home()
    native_poppler = (
        home
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "native"
        / "poppler"
        / "Library"
        / "bin"
        / "pdftoppm.exe"
    )
    candidates.append(str(native_poppler))

    found = shutil.which("pdftoppm")
    if found:
        candidates.append(found)

    bundled_bin = (
        home
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "bin"
    )
    candidates.extend(
        [
            str(bundled_bin / "pdftoppm"),
            str(bundled_bin / "pdftoppm.exe"),
            str(bundled_bin / "pdftoppm.cmd"),
        ]
    )

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate

    raise RuntimeError(
        "Could not find pdftoppm. Install Poppler or set PDFTOPPM to the "
        "pdftoppm executable path."
    )


def run_command(command: list[str]) -> None:
    executable = Path(command[0])
    if executable.suffix.lower() in {".cmd", ".bat"}:
        command = ["cmd.exe", "/c", *command]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            + " ".join(command)
            + "\nSTDOUT:\n"
            + result.stdout
            + "\nSTDERR:\n"
            + result.stderr
        )


def render_pdf(pdf_path: Path, render_dir: Path, dpi: int) -> list[Path]:
    render_dir.mkdir(parents=True, exist_ok=True)
    output_prefix = render_dir / "page"
    pdftoppm = locate_pdftoppm()
    run_command([pdftoppm, "-r", str(dpi), "-png", str(pdf_path), str(output_prefix)])
    pages = sorted(render_dir.glob("page-*.png"), key=page_number_from_render_name)
    if not pages:
        raise RuntimeError("PDF rendering produced no PNG pages.")
    return pages


def page_number_from_render_name(path: Path) -> int:
    stem = path.stem
    try:
        return int(stem.split("-")[-1])
    except ValueError:
        return 0


def read_pdf_metadata(pdf_path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {"page_count": None, "metadata": {}, "page_images": []}
    if PdfReader is None:
        return metadata
    try:
        reader = PdfReader(str(pdf_path))
        metadata["page_count"] = len(reader.pages)
        if reader.metadata:
            metadata["metadata"] = {
                str(k).lstrip("/"): str(v) for k, v in reader.metadata.items()
            }
        page_images: list[dict[str, Any]] = []
        for page_number, page in enumerate(reader.pages, start=1):
            resources = page.get("/Resources") or {}
            xobjects = resources.get("/XObject") if resources else None
            images: list[dict[str, Any]] = []
            if xobjects:
                for name, obj in xobjects.get_object().items():
                    image = obj.get_object()
                    if image.get("/Subtype") == "/Image":
                        images.append(
                            {
                                "name": str(name),
                                "width": int(image.get("/Width", 0)),
                                "height": int(image.get("/Height", 0)),
                                "bits_per_component": int(
                                    image.get("/BitsPerComponent", 0)
                                ),
                                "color_space": str(image.get("/ColorSpace")),
                                "filter": str(image.get("/Filter")),
                            }
                        )
            page_images.append({"page": page_number, "images": images})
        metadata["page_images"] = page_images
    except Exception as exc:
        metadata["metadata_error"] = str(exc)
    return metadata


def load_grayscale(path: Path, max_side: int) -> tuple[np.ndarray, tuple[int, int]]:
    with Image.open(path) as image:
        image = image.convert("L")
        original_size = image.size
        width, height = image.size
        scale = min(1.0, max_side / max(width, height))
        if scale < 1.0:
            resized = (max(1, int(width * scale)), max(1, int(height * scale)))
            image = image.resize(resized, Image.Resampling.BILINEAR)
        gray = np.asarray(image, dtype=np.float32) / 255.0
    return gray, original_size


def gaussian_residual(gray: np.ndarray, radius: float = 2.0) -> np.ndarray:
    source = Image.fromarray(np.clip(gray * 255.0, 0, 255).astype(np.uint8))
    blurred = np.asarray(
        source.filter(ImageFilter.GaussianBlur(radius=radius)), dtype=np.float32
    )
    return gray - (blurred / 255.0)


def gradient_magnitude(gray: np.ndarray) -> np.ndarray:
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:] = np.abs(gray[:, 1:] - gray[:, :-1])
    gy[1:, :] = np.abs(gray[1:, :] - gray[:-1, :])
    return gx + gy


def content_suppression_mask(gray: np.ndarray) -> np.ndarray:
    gradient = gradient_magnitude(gray)
    brightness_floor = max(0.25, float(np.percentile(gray, 25)))
    edge_threshold = float(np.percentile(gradient, 70))
    mask = (gray >= brightness_floor) & (gradient <= edge_threshold)

    if mask.mean() < 0.12:
        brightness_floor = max(0.18, float(np.percentile(gray, 15)))
        edge_threshold = float(np.percentile(gradient, 85))
        mask = (gray >= brightness_floor) & (gradient <= edge_threshold)

    border_y = max(2, int(gray.shape[0] * 0.015))
    border_x = max(2, int(gray.shape[1] * 0.015))
    mask[:border_y, :] = False
    mask[-border_y:, :] = False
    mask[:, :border_x] = False
    mask[:, -border_x:] = False
    return mask


def safe_masked_values(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    masked = values[mask]
    if masked.size:
        return masked
    return values.reshape(-1)


def masked_axis_profile(
    values: np.ndarray, mask: np.ndarray, axis: int, normalize: bool = True
) -> np.ndarray:
    counts = mask.sum(axis=axis).astype(np.float32)
    sums = (values * mask).sum(axis=axis)
    profile = np.full_like(sums, np.nan, dtype=np.float32)
    valid = counts > max(4, values.shape[axis] * 0.03)
    profile[valid] = sums[valid] / np.maximum(counts[valid], 1.0)
    profile = fill_nan_by_interpolation(profile)
    profile = profile - np.median(profile)
    if not normalize:
        return profile.astype(np.float32)
    scale = np.std(profile)
    if scale > EPS:
        profile = profile / scale
    return profile.astype(np.float32)


def fill_nan_by_interpolation(values: np.ndarray) -> np.ndarray:
    values = values.astype(np.float32)
    finite = np.isfinite(values)
    if finite.all():
        return values
    if not finite.any():
        return np.zeros_like(values, dtype=np.float32)
    indices = np.arange(values.size)
    values[~finite] = np.interp(indices[~finite], indices[finite], values[finite])
    return values


def resample_vector(values: np.ndarray, length: int) -> np.ndarray:
    if values.size == length:
        return values.astype(np.float32)
    if values.size <= 1:
        return np.zeros(length, dtype=np.float32)
    old_x = np.linspace(0.0, 1.0, values.size)
    new_x = np.linspace(0.0, 1.0, length)
    return np.interp(new_x, old_x, values).astype(np.float32)


def resize_float_array(values: np.ndarray, max_side: int) -> np.ndarray:
    height, width = values.shape
    scale = min(1.0, max_side / max(height, width))
    if scale >= 1.0:
        return values.astype(np.float32)
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    image = Image.fromarray(values.astype(np.float32), mode="F")
    return np.asarray(image.resize(new_size, Image.Resampling.BILINEAR), dtype=np.float32)


def spectral_bins(residual: np.ndarray, mask: np.ndarray, bins: int = 18) -> np.ndarray:
    working = residual.copy()
    if mask.any():
        working[~mask] = 0.0
    working = resize_float_array(working, 512)
    working = working - float(np.mean(working))
    height, width = working.shape
    if height < 8 or width < 8:
        return np.zeros(bins, dtype=np.float32)

    window = np.hanning(height)[:, None] * np.hanning(width)[None, :]
    spectrum = np.abs(np.fft.rfft2(working * window)) ** 2
    power = np.log1p(spectrum)

    fy = np.fft.fftfreq(height)[:, None]
    fx = np.fft.rfftfreq(width)[None, :]
    radius = np.sqrt(fx * fx + fy * fy)
    edges = np.linspace(0.015, 0.5, bins + 1)
    output = np.zeros(bins, dtype=np.float32)
    for idx in range(bins):
        bucket = (radius >= edges[idx]) & (radius < edges[idx + 1])
        if np.any(bucket):
            output[idx] = float(np.mean(power[bucket]))

    output = output - float(np.mean(output))
    norm = float(np.linalg.norm(output))
    if norm > EPS:
        output = output / norm
    return output


def illumination_features(gray: np.ndarray) -> tuple[float, float]:
    thumb = Image.fromarray(np.clip(gray * 255.0, 0, 255).astype(np.uint8)).resize(
        (32, 32), Image.Resampling.BILINEAR
    )
    arr = np.asarray(thumb, dtype=np.float32) / 255.0
    return float(np.std(arr)), float(np.percentile(arr, 95) - np.percentile(arr, 5))


def laplacian_sharpness(gray: np.ndarray) -> float:
    center = gray[1:-1, 1:-1] * -4.0
    lap = (
        center
        + gray[:-2, 1:-1]
        + gray[2:, 1:-1]
        + gray[1:-1, :-2]
        + gray[1:-1, 2:]
    )
    return float(np.var(lap))


def blockiness_score(gray: np.ndarray) -> float:
    height, width = gray.shape
    if height < 24 or width < 24:
        return 0.0

    vertical_diff = np.abs(gray[:, 1:] - gray[:, :-1])
    horizontal_diff = np.abs(gray[1:, :] - gray[:-1, :])

    v_boundary = np.arange(7, vertical_diff.shape[1], 8)
    h_boundary = np.arange(7, horizontal_diff.shape[0], 8)
    if v_boundary.size == 0 or h_boundary.size == 0:
        return 0.0

    v_mask = np.ones(vertical_diff.shape[1], dtype=bool)
    h_mask = np.ones(horizontal_diff.shape[0], dtype=bool)
    v_mask[v_boundary] = False
    h_mask[h_boundary] = False

    boundary = float(
        np.mean(vertical_diff[:, v_boundary]) + np.mean(horizontal_diff[h_boundary, :])
    )
    interior = float(np.mean(vertical_diff[:, v_mask]) + np.mean(horizontal_diff[h_mask, :]))
    return float(boundary / (interior + EPS))


def capture_class(
    illumination_std: float,
    illumination_range: float,
    row_band_strength: float,
    column_band_strength: float,
    blockiness: float,
) -> str:
    if illumination_std > 0.115 or illumination_range > 0.46:
        return "camera-photo-like"
    if max(row_band_strength, column_band_strength) > 0.11 and illumination_std < 0.085:
        return "scanner-like"
    if blockiness > 1.18 and illumination_std > 0.08:
        return "camera-photo-like"
    return "mixed-or-uncertain"


def extract_page_features(
    image_file: Path,
    page: int,
    max_analysis_side: int,
    keep_profiles: bool = True,
) -> tuple[PageFeatures, np.ndarray, np.ndarray, np.ndarray]:
    gray, original_size = load_grayscale(image_file, max_analysis_side)
    residual = gaussian_residual(gray)
    mask = content_suppression_mask(gray)
    values = safe_masked_values(residual, mask)

    raw_row_profile = masked_axis_profile(residual, mask, axis=1, normalize=False)
    raw_column_profile = masked_axis_profile(residual, mask, axis=0, normalize=False)
    row_band_strength = float(np.std(raw_row_profile))
    column_band_strength = float(np.std(raw_column_profile))
    row_profile = resample_vector(
        masked_axis_profile(residual, mask, axis=1, normalize=True), 512
    )
    column_profile = resample_vector(
        masked_axis_profile(residual, mask, axis=0, normalize=True), 512
    )
    illumination_std, illumination_range = illumination_features(gray)
    sharpness = laplacian_sharpness(gray)
    blockiness = blockiness_score(gray)
    bins = spectral_bins(residual, mask)
    residual_mad = float(np.median(np.abs(values - np.median(values))))

    page_features = PageFeatures(
        page=page,
        image_file=image_file.name,
        width_px=original_size[0],
        height_px=original_size[1],
        capture_class=capture_class(
            illumination_std,
            illumination_range,
            row_band_strength,
            column_band_strength,
            blockiness,
        ),
        mask_coverage=float(mask.mean()),
        noise_std=float(np.std(values)),
        residual_mad=residual_mad,
        row_band_strength=row_band_strength,
        column_band_strength=column_band_strength,
        illumination_std=illumination_std,
        illumination_range=illumination_range,
        sharpness=sharpness,
        blockiness=blockiness,
        spectral_bins=round_list(bins),
        row_profile=round_list(row_profile) if keep_profiles else [],
        column_profile=round_list(column_profile) if keep_profiles else [],
    )
    return page_features, gray, residual, mask


def round_list(values: Iterable[float], digits: int = 6) -> list[float]:
    return [round(float(v), digits) for v in values]


def pearson(a: np.ndarray, b: np.ndarray) -> float | None:
    if a.size != b.size or a.size < 2:
        return None
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    a = a - float(np.mean(a))
    b = b - float(np.mean(b))
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= EPS:
        return None
    return float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= EPS:
        return 0.0
    cosine = float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))
    return float((1.0 - cosine) / 2.0)


def statistic_vector(page: PageFeatures) -> np.ndarray:
    return np.array(
        [
            page.noise_std,
            page.residual_mad,
            page.row_band_strength,
            page.column_band_strength,
            page.illumination_std,
            page.illumination_range,
            page.sharpness,
            page.blockiness,
        ],
        dtype=np.float32,
    )


def statistic_dissimilarity(a: PageFeatures, b: PageFeatures) -> float:
    va = statistic_vector(a)
    vb = statistic_vector(b)
    relative = np.abs(va - vb) / (np.abs(va) + np.abs(vb) + EPS)
    return float(np.clip(np.mean(relative) * 2.0, 0.0, 1.0))


def compare_pages(pages: list[PageFeatures]) -> list[PairwiseComparison]:
    comparisons: list[PairwiseComparison] = []
    for left_idx in range(len(pages)):
        for right_idx in range(left_idx + 1, len(pages)):
            left = pages[left_idx]
            right = pages[right_idx]
            spectral_d = cosine_distance(
                np.asarray(left.spectral_bins, dtype=np.float32),
                np.asarray(right.spectral_bins, dtype=np.float32),
            )
            row_corr = pearson(
                np.asarray(left.row_profile, dtype=np.float32),
                np.asarray(right.row_profile, dtype=np.float32),
            )
            col_corr = pearson(
                np.asarray(left.column_profile, dtype=np.float32),
                np.asarray(right.column_profile, dtype=np.float32),
            )
            stat_d = statistic_dissimilarity(left, right)

            row_weight = min(0.12, (left.row_band_strength + right.row_band_strength) * 0.04)
            col_weight = min(
                0.12, (left.column_band_strength + right.column_band_strength) * 0.04
            )
            spectral_weight = 0.46
            statistic_weight = 0.38
            row_d = 0.0 if row_corr is None else (1.0 - row_corr) / 2.0
            col_d = 0.0 if col_corr is None else (1.0 - col_corr) / 2.0
            total_weight = spectral_weight + statistic_weight + row_weight + col_weight
            mismatch = (
                spectral_d * spectral_weight
                + stat_d * statistic_weight
                + row_d * row_weight
                + col_d * col_weight
            ) / total_weight
            mismatch_score = float(
                np.clip(100.0 * (1.0 - math.exp(-8.0 * mismatch)), 0.0, 100.0)
            )

            reason_parts = []
            if left.capture_class != right.capture_class:
                reason_parts.append(
                    f"capture class differs ({left.capture_class} vs {right.capture_class})"
                )
            if spectral_d > 0.38:
                reason_parts.append("noise spectrum differs")
            if stat_d > 0.18:
                reason_parts.append("noise/blur/compression statistics differ")
            if row_corr is not None and row_corr < 0.25:
                reason_parts.append("row streak profile has weak correlation")
            if col_corr is not None and col_corr < 0.25:
                reason_parts.append("column streak profile has weak correlation")
            if not reason_parts:
                reason_parts.append("page fingerprints are broadly consistent")

            comparisons.append(
                PairwiseComparison(
                    page_a=left.page,
                    page_b=right.page,
                    mismatch_score=round(mismatch_score, 2),
                    spectral_dissimilarity=round(float(spectral_d), 4),
                    row_profile_correlation=None
                    if row_corr is None
                    else round(float(row_corr), 4),
                    column_profile_correlation=None
                    if col_corr is None
                    else round(float(col_corr), 4),
                    statistic_dissimilarity=round(float(stat_d), 4),
                    reason="; ".join(reason_parts),
                )
            )
    return comparisons


def suspicious_pages(
    pages: list[PageFeatures], comparisons: list[PairwiseComparison]
) -> list[SuspiciousPage]:
    if len(pages) <= 1:
        return []

    by_page: dict[int, list[float]] = {page.page: [] for page in pages}
    for comparison in comparisons:
        by_page[comparison.page_a].append(comparison.mismatch_score)
        by_page[comparison.page_b].append(comparison.mismatch_score)

    page_medians = {
        page_num: float(np.median(scores)) if scores else 0.0
        for page_num, scores in by_page.items()
    }

    if len(pages) == 2:
        only_score = comparisons[0].mismatch_score if comparisons else 0.0
        if only_score < 45:
            return []
        severity = "high" if only_score >= 76 else "medium"
        return [
            SuspiciousPage(
                page=pages[0].page,
                score=round(only_score, 2),
                severity=severity,
                reason=(
                    "Only two pages are available, so the tool can flag a pair "
                    "mismatch but cannot decide which page is the outlier."
                ),
            ),
            SuspiciousPage(
                page=pages[1].page,
                score=round(only_score, 2),
                severity=severity,
                reason=(
                    "Only two pages are available, so the tool can flag a pair "
                    "mismatch but cannot decide which page is the outlier."
                ),
            ),
        ]

    all_medians = np.array(list(page_medians.values()), dtype=np.float32)
    baseline = float(np.median(all_medians))
    mad = float(np.median(np.abs(all_medians - baseline))) + EPS
    findings: list[SuspiciousPage] = []
    for page in pages:
        score = page_medians[page.page]
        robust_z = (score - baseline) / (1.4826 * mad)
        if score >= 70 or (score >= 42 and robust_z >= 2.5):
            severity = "high" if score >= 74 else "medium"
            findings.append(
                SuspiciousPage(
                    page=page.page,
                    score=round(score, 2),
                    severity=severity,
                    reason=(
                        f"Page median mismatch score {score:.1f} is above the "
                        f"document baseline {baseline:.1f}."
                    ),
                )
            )
    findings = sorted(findings, key=lambda item: item.score, reverse=True)
    if len(pages) >= 3 and len(findings) > len(pages) / 2:
        return []
    return findings


def tile_metrics(
    gray: np.ndarray, residual: np.ndarray, mask: np.ndarray, grid: int
) -> list[dict[str, Any]]:
    height, width = gray.shape
    tiles: list[dict[str, Any]] = []
    for row in range(grid):
        for col in range(grid):
            y0 = int(round(row * height / grid))
            y1 = int(round((row + 1) * height / grid))
            x0 = int(round(col * width / grid))
            x1 = int(round((col + 1) * width / grid))
            tile_gray = gray[y0:y1, x0:x1]
            tile_residual = residual[y0:y1, x0:x1]
            tile_mask = mask[y0:y1, x0:x1]
            coverage = float(tile_mask.mean()) if tile_mask.size else 0.0
            if coverage < 0.08:
                continue
            values = safe_masked_values(tile_residual, tile_mask)
            tiles.append(
                {
                    "row": row,
                    "col": col,
                    "bbox": (x0, y0, x1, y1),
                    "coverage": coverage,
                    "noise_std": float(np.std(values)),
                    "residual_mad": float(np.median(np.abs(values - np.median(values)))),
                    "sharpness": laplacian_sharpness(tile_gray)
                    if min(tile_gray.shape) > 4
                    else 0.0,
                    "blockiness": blockiness_score(tile_gray),
                }
            )
    return tiles


def robust_z_scores(values: np.ndarray) -> np.ndarray:
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad <= EPS:
        std = float(np.std(values))
        if std <= EPS:
            return np.zeros_like(values, dtype=np.float32)
        return ((values - median) / std).astype(np.float32)
    return ((values - median) / (1.4826 * mad)).astype(np.float32)


def suspicious_regions_for_page(
    page: int,
    gray: np.ndarray,
    residual: np.ndarray,
    mask: np.ndarray,
    grid: int,
) -> list[SuspiciousRegion]:
    if grid < 2:
        return []
    tiles = tile_metrics(gray, residual, mask, grid)
    if len(tiles) < max(6, grid):
        return []

    metric_names = [
        "noise_std",
        "residual_mad",
        "sharpness",
        "blockiness",
    ]
    matrix = np.array(
        [[float(tile[name]) for name in metric_names] for tile in tiles], dtype=np.float32
    )
    z = np.vstack([robust_z_scores(matrix[:, idx]) for idx in range(matrix.shape[1])]).T
    height, width = gray.shape
    findings: list[SuspiciousRegion] = []
    for tile, tile_z in zip(tiles, z):
        abs_z = np.abs(tile_z)
        max_z = float(np.max(abs_z))
        combined = float(np.sqrt(np.mean(np.square(abs_z))))
        score = float(np.clip(max(max_z * 22.0, combined * 28.0), 0.0, 100.0))
        if score < 72:
            continue
        strongest = int(np.argmax(abs_z))
        metric = metric_names[strongest]
        x0, y0, x1, y1 = tile["bbox"]
        severity = "high" if score >= 82 else "medium"
        findings.append(
            SuspiciousRegion(
                page=page,
                score=round(score, 2),
                severity=severity,
                bbox_px={"x0": x0, "y0": y0, "x1": x1, "y1": y1},
                bbox_normalized={
                    "x0": round(x0 / width, 4),
                    "y0": round(y0 / height, 4),
                    "x1": round(x1 / width, 4),
                    "y1": round(y1 / height, 4),
                },
                reason=(
                    f"Tile {tile['row'] + 1},{tile['col'] + 1} has an unusual "
                    f"{metric.replace('_', ' ')} value compared with other tiles "
                    "on the same page."
                ),
            )
        )

    return sorted(findings, key=lambda item: item.score, reverse=True)[:8]


def overall_risk(
    page_findings: list[SuspiciousPage],
    region_findings: list[SuspiciousRegion],
    comparisons: list[PairwiseComparison],
    pages_analyzed: int,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    max_page = max((item.score for item in page_findings), default=0.0)
    max_region = max((item.score for item in region_findings), default=0.0)
    max_pair = max((item.mismatch_score for item in comparisons), default=0.0)

    if pages_analyzed < 2 and not page_findings:
        if max_region >= 72:
            return "medium", [
                "Only one page was analyzed, so page-to-page scanner/camera source consistency could not be tested.",
                "Local tile differences were found, but on a single page they are review hints rather than a high-confidence source mismatch.",
            ]
        return "low", [
            "Only one page was analyzed, so page-to-page scanner/camera source consistency could not be tested.",
            "No strong local capture-noise anomaly was detected.",
        ]

    if max_page >= 74:
        reasons.append(f"High page-level mismatch score detected ({max_page:.1f}).")
    elif max_page >= 42:
        reasons.append(f"Medium page-level mismatch score detected ({max_page:.1f}).")

    if max_region >= 82:
        reasons.append(f"High local-region anomaly score detected ({max_region:.1f}).")
    elif max_region >= 72:
        reasons.append(f"Medium local-region anomaly score detected ({max_region:.1f}).")

    if max_pair >= 76:
        reasons.append(f"High pairwise page mismatch score detected ({max_pair:.1f}).")
    elif max_pair >= 45:
        reasons.append(f"Medium pairwise page mismatch score detected ({max_pair:.1f}).")

    if max_page >= 74 or max_region >= 82 or max_pair >= 76:
        if max_page >= 74:
            return "high", reasons
        if len(comparisons) == 1 and max_pair >= 76:
            return "high", reasons
        reasons.append(
            "The high pair/local scores do not isolate a clear outlier page, so this is treated as capture-condition variability."
        )
        return "medium", reasons
    if max_page >= 42 or max_region >= 72 or max_pair >= 45:
        return "medium", reasons
    return "low", ["No strong scanner/camera noise inconsistency was detected."]


def compact_page_features(page: PageFeatures) -> dict[str, Any]:
    data = asdict(page)
    data.pop("row_profile", None)
    data.pop("column_profile", None)
    data.pop("spectral_bins", None)
    return data


def build_report(
    pdf_path: Path,
    output_dir: Path,
    dpi: int,
    grid: int,
    page_only: bool,
    page_features: list[PageFeatures],
    comparisons: list[PairwiseComparison],
    page_findings: list[SuspiciousPage],
    region_findings: list[SuspiciousRegion],
) -> dict[str, Any]:
    if len(page_features) < 2:
        region_findings = downgrade_single_page_region_findings(region_findings)

    risk, reasons = overall_risk(
        page_findings, region_findings, comparisons, len(page_features)
    )
    return {
        "tool": "scanner_noise_fingerprint_check",
        "tool_version": TOOL_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "input_pdf": str(pdf_path),
        "output_dir": str(output_dir),
        "settings": {
            "dpi": dpi,
            "grid": grid,
            "page_only": page_only,
        },
        "source_consistency_possible": len(page_features) >= 2,
        "important_note": (
            "This report is a screening signal. It can support manual review, "
            "but it should not be treated as proof of fraud by itself."
        ),
        "pdf": read_pdf_metadata(pdf_path),
        "summary": {
            "overall_risk": risk,
            "confidence": "limited" if len(page_features) < 2 else "screening",
            "reasons": reasons,
            "pages_analyzed": len(page_features),
            "suspicious_pages": len(page_findings),
            "suspicious_regions": len(region_findings),
        },
        "pages": [compact_page_features(page) for page in page_features],
        "pairwise_page_comparisons": [asdict(item) for item in comparisons],
        "suspicious_pages": [asdict(item) for item in page_findings],
        "suspicious_regions": [asdict(item) for item in region_findings],
    }


def downgrade_single_page_region_findings(
    region_findings: list[SuspiciousRegion],
) -> list[SuspiciousRegion]:
    downgraded: list[SuspiciousRegion] = []
    for item in region_findings:
        severity = "medium" if item.severity == "high" else item.severity
        reason = item.reason
        note = (
            " Single-page local finding; this is not enough by itself for a "
            "high-confidence source mismatch."
        )
        if note.strip() not in reason:
            reason += note
        downgraded.append(
            SuspiciousRegion(
                page=item.page,
                score=item.score,
                severity=severity,
                bbox_px=item.bbox_px,
                bbox_normalized=item.bbox_normalized,
                reason=reason,
            )
        )
    return downgraded


def write_text_report(report: dict[str, Any], text_path: Path) -> None:
    lines: list[str] = []
    lines.append("scanner_noise_fingerprint_check report")
    lines.append("=" * 45)
    lines.append(f"Input PDF: {report['input_pdf']}")
    lines.append(f"Created: {report['created_at']}")
    lines.append(f"Overall risk: {report['summary']['overall_risk'].upper()}")
    lines.append(f"Confidence: {report['summary']['confidence']}")
    lines.append(
        "Source consistency possible: "
        + ("yes" if report["source_consistency_possible"] else "no")
    )
    lines.append("")
    lines.append("Important note:")
    lines.append(report["important_note"])
    lines.append("")
    lines.append("Why:")
    for reason in report["summary"]["reasons"]:
        lines.append(f"- {reason}")
    lines.append("")
    lines.append("Pages:")
    for page in report["pages"]:
        lines.append(
            "- Page {page}: {capture_class}, noise_std={noise_std:.5f}, "
            "illumination_std={illumination_std:.4f}, blockiness={blockiness:.3f}".format(
                **page
            )
        )
    lines.append("")
    lines.append("Page-to-page comparisons:")
    comparisons = report["pairwise_page_comparisons"]
    if comparisons:
        for item in comparisons:
            lines.append(
                "- Pages {page_a}-{page_b}: mismatch={mismatch_score:.1f}; {reason}".format(
                    **item
                )
            )
    else:
        lines.append("- Not enough pages for page-to-page comparison.")
    lines.append("")
    lines.append("Suspicious pages:")
    if report["suspicious_pages"]:
        for item in report["suspicious_pages"]:
            lines.append(
                "- Page {page}: {severity}, score={score:.1f}; {reason}".format(**item)
            )
    else:
        lines.append("- None flagged.")
    lines.append("")
    lines.append("Suspicious regions:")
    if report["suspicious_regions"]:
        for item in report["suspicious_regions"]:
            bbox = item["bbox_normalized"]
            lines.append(
                "- Page {page}: {severity}, score={score:.1f}, "
                "box=({x0:.2f},{y0:.2f})-({x1:.2f},{y1:.2f}); {reason}".format(
                    page=item["page"],
                    severity=item["severity"],
                    score=item["score"],
                    x0=bbox["x0"],
                    y0=bbox["y0"],
                    x1=bbox["x1"],
                    y1=bbox["y1"],
                    reason=item["reason"],
                )
            )
    else:
        lines.append("- None flagged.")
    lines.append("")
    lines.append("Recommended next step:")
    if report["summary"]["overall_risk"] == "low":
        lines.append(
            "Use this result alongside OCR, QR, metadata, and visual checks. "
            "No strong capture-source inconsistency was found."
        )
    else:
        lines.append(
            "Manually inspect the flagged pages/regions and compare with OCR, QR, "
            "metadata, and visual layout checks before drawing a conclusion."
        )

    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def analyze_pdf(args: argparse.Namespace) -> dict[str, Any]:
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"Input PDF does not exist: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("Input must be a PDF file.")

    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
    else:
        output_dir = pdf_path.with_name(pdf_path.stem + "_scanner_noise_report")
    output_dir.mkdir(parents=True, exist_ok=True)

    render_parent: tempfile.TemporaryDirectory[str] | None = None
    if args.keep_images:
        render_dir = output_dir / "rendered_pages"
        if render_dir.exists():
            shutil.rmtree(render_dir)
        render_dir.mkdir(parents=True, exist_ok=True)
    else:
        render_parent = tempfile.TemporaryDirectory(prefix="scanner_noise_pages_")
        render_dir = Path(render_parent.name)

    try:
        rendered_pages = render_pdf(pdf_path, render_dir, args.dpi)
        page_features: list[PageFeatures] = []
        region_findings: list[SuspiciousRegion] = []
        for index, image_file in enumerate(rendered_pages, start=1):
            features, gray, residual, mask = extract_page_features(
                image_file=image_file,
                page=index,
                max_analysis_side=args.max_analysis_side,
            )
            page_features.append(features)
            if not args.page_only:
                region_findings.extend(
                    suspicious_regions_for_page(index, gray, residual, mask, args.grid)
                )

        comparisons = compare_pages(page_features)
        page_findings = suspicious_pages(page_features, comparisons)
        report = build_report(
            pdf_path=pdf_path,
            output_dir=output_dir,
            dpi=args.dpi,
            grid=args.grid,
            page_only=args.page_only,
            page_features=page_features,
            comparisons=comparisons,
            page_findings=page_findings,
            region_findings=region_findings,
        )
        json_path = output_dir / "scanner_noise_fingerprint_report.json"
        text_path = output_dir / "scanner_noise_fingerprint_report.txt"
        json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        write_text_report(report, text_path)
        report["report_files"] = {"json": str(json_path), "text": str(text_path)}
        return report
    finally:
        if render_parent is not None:
            render_parent.cleanup()


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        report = analyze_pdf(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    summary = report["summary"]
    files = report.get("report_files", {})
    print(f"scanner_noise_fingerprint_check complete")
    print(f"Overall risk: {summary['overall_risk'].upper()}")
    print(f"Pages analyzed: {summary['pages_analyzed']}")
    if files:
        print(f"JSON report: {files['json']}")
        print(f"Text report: {files['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
