#!/usr/bin/env python
"""
same_phone_pdf_check

Screen a PDF made from document photos and estimate whether the pages are
compatible with the same phone/capture workflow.

This is intentionally different from scanner_noise_fingerprint_check:
- lighting, crop, page angle, handwriting, and local shadows are treated as
  capture-condition variation, not strong different-phone evidence.
- the output is "same-phone compatible" rather than "tamper/fraud".

It is still a screening signal, not proof of device identity.
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
from typing import Any

import numpy as np
from PIL import Image, ImageFilter
from pypdf import PdfReader


TOOL_VERSION = "0.1.0"
EPS = 1e-9


@dataclass
class EmbeddedImageInfo:
    name: str
    width: int
    height: int
    bits_per_component: int
    color_space: str
    filter: str


@dataclass
class PageFeatures:
    page: int
    rendered_image: str
    rendered_width_px: int
    rendered_height_px: int
    embedded_images: list[EmbeddedImageInfo]
    primary_image_width: int | None
    primary_image_height: int | None
    primary_image_aspect: float | None
    primary_image_megapixels: float | None
    primary_image_filter: str | None
    primary_image_color_space: str | None
    noise_std: float
    spectral_fingerprint: list[float]
    sharpness: float
    illumination_std: float
    illumination_range: float
    blockiness: float
    condition_note: str


@dataclass
class PairAssessment:
    page_a: int
    page_b: int
    same_phone_score: float
    verdict: str
    metadata_score: float
    geometry_score: float
    sensor_texture_score: float
    condition_similarity_score: float
    condition_variation_score: float
    reasons: list[str]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check whether PDF document photos are compatible with the same phone."
    )
    parser.add_argument("pdf", help="Input PDF path.")
    parser.add_argument(
        "--output-dir",
        help="Directory for reports. Defaults to a folder next to the PDF.",
    )
    parser.add_argument("--dpi", type=int, default=180, help="Render DPI. Default: 180.")
    parser.add_argument(
        "--max-analysis-side",
        type=int,
        default=1800,
        help="Maximum rendered page side used for analysis. Default: 1800.",
    )
    parser.add_argument(
        "--keep-images",
        action="store_true",
        help="Keep rendered page images in the output directory.",
    )
    return parser.parse_args(argv)


def locate_pdftoppm() -> str:
    configured = os.environ.get("PDFTOPPM")
    candidates: list[str] = []
    if configured:
        candidates.append(configured)

    native = (
        Path.home()
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
    candidates.append(str(native))

    found = shutil.which("pdftoppm")
    if found:
        candidates.append(found)

    bundled = (
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "bin"
    )
    candidates.extend(
        [
            str(bundled / "pdftoppm.exe"),
            str(bundled / "pdftoppm.cmd"),
            str(bundled / "pdftoppm"),
        ]
    )

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("Could not find pdftoppm. Install Poppler or set PDFTOPPM.")


def run_command(command: list[str]) -> None:
    executable = Path(command[0])
    if executable.suffix.lower() in {".cmd", ".bat"}:
        command = ["cmd.exe", "/c", *command]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
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
    prefix = render_dir / "page"
    run_command([locate_pdftoppm(), "-r", str(dpi), "-png", str(pdf_path), str(prefix)])
    pages = sorted(render_dir.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
    if not pages:
        raise RuntimeError("PDF rendering produced no pages.")
    return pages


def pdf_metadata_and_images(pdf_path: Path) -> tuple[dict[str, str], dict[int, list[EmbeddedImageInfo]]]:
    reader = PdfReader(str(pdf_path))
    metadata = {str(k).lstrip("/"): str(v) for k, v in (reader.metadata or {}).items()}
    page_images: dict[int, list[EmbeddedImageInfo]] = {}
    for page_number, page in enumerate(reader.pages, start=1):
        images: list[EmbeddedImageInfo] = []
        resources = page.get("/Resources") or {}
        xobjects = resources.get("/XObject") if resources else None
        if xobjects:
            for name, obj in xobjects.get_object().items():
                image = obj.get_object()
                if image.get("/Subtype") != "/Image":
                    continue
                images.append(
                    EmbeddedImageInfo(
                        name=str(name),
                        width=int(image.get("/Width", 0)),
                        height=int(image.get("/Height", 0)),
                        bits_per_component=int(image.get("/BitsPerComponent", 0)),
                        color_space=str(image.get("/ColorSpace")),
                        filter=str(image.get("/Filter")),
                    )
                )
        page_images[page_number] = images
    return metadata, page_images


def load_gray(path: Path, max_side: int) -> tuple[np.ndarray, tuple[int, int]]:
    with Image.open(path) as image:
        image = image.convert("L")
        original_size = image.size
        width, height = image.size
        scale = min(1.0, max_side / max(width, height))
        if scale < 1.0:
            size = (max(1, int(width * scale)), max(1, int(height * scale)))
            image = image.resize(size, Image.Resampling.BILINEAR)
        return np.asarray(image, dtype=np.float32) / 255.0, original_size


def flatten_illumination(gray: np.ndarray) -> np.ndarray:
    radius = max(10, int(max(gray.shape) / 45))
    image = Image.fromarray(np.clip(gray * 255.0, 0, 255).astype(np.uint8))
    low = np.asarray(image.filter(ImageFilter.GaussianBlur(radius=radius)), dtype=np.float32) / 255.0
    flattened = gray - low + float(np.mean(low))
    return np.clip(flattened, 0.0, 1.0)


def residual(gray: np.ndarray) -> np.ndarray:
    flat = flatten_illumination(gray)
    image = Image.fromarray(np.clip(flat * 255.0, 0, 255).astype(np.uint8))
    blur = np.asarray(image.filter(ImageFilter.GaussianBlur(radius=2.0)), dtype=np.float32) / 255.0
    return flat - blur


def gradient(gray: np.ndarray) -> np.ndarray:
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:] = np.abs(gray[:, 1:] - gray[:, :-1])
    gy[1:, :] = np.abs(gray[1:, :] - gray[:-1, :])
    return gx + gy


def background_mask(gray: np.ndarray) -> np.ndarray:
    grad = gradient(gray)
    bright = gray >= max(0.22, float(np.percentile(gray, 18)))
    low_edge = grad <= float(np.percentile(grad, 72))
    mask = bright & low_edge
    border_y = max(2, int(gray.shape[0] * 0.02))
    border_x = max(2, int(gray.shape[1] * 0.02))
    mask[:border_y, :] = False
    mask[-border_y:, :] = False
    mask[:, :border_x] = False
    mask[:, -border_x:] = False
    if mask.mean() < 0.12:
        return np.ones_like(mask, dtype=bool)
    return mask


def resize_float(values: np.ndarray, max_side: int) -> np.ndarray:
    height, width = values.shape
    scale = min(1.0, max_side / max(height, width))
    if scale >= 1.0:
        return values.astype(np.float32)
    size = (max(1, int(width * scale)), max(1, int(height * scale)))
    image = Image.fromarray(values.astype(np.float32), mode="F")
    return np.asarray(image.resize(size, Image.Resampling.BILINEAR), dtype=np.float32)


def spectral_fingerprint(resid: np.ndarray, mask: np.ndarray, bins: int = 24) -> np.ndarray:
    work = resid.copy()
    work[~mask] = 0.0
    work = resize_float(work, 512).copy()
    work -= float(np.mean(work))
    height, width = work.shape
    window = np.hanning(height)[:, None] * np.hanning(width)[None, :]
    spectrum = np.log1p(np.abs(np.fft.rfft2(work * window)) ** 2)
    fy = np.fft.fftfreq(height)[:, None]
    fx = np.fft.rfftfreq(width)[None, :]
    radius = np.sqrt(fx * fx + fy * fy)
    edges = np.linspace(0.02, 0.5, bins + 1)
    out = np.zeros(bins, dtype=np.float32)
    for i in range(bins):
        bucket = (radius >= edges[i]) & (radius < edges[i + 1])
        if np.any(bucket):
            out[i] = float(np.mean(spectrum[bucket]))
    out -= float(np.mean(out))
    norm = float(np.linalg.norm(out))
    if norm > EPS:
        out /= norm
    return out


def sharpness(gray: np.ndarray) -> float:
    lap = (
        -4.0 * gray[1:-1, 1:-1]
        + gray[:-2, 1:-1]
        + gray[2:, 1:-1]
        + gray[1:-1, :-2]
        + gray[1:-1, 2:]
    )
    return float(np.var(lap))


def illumination(gray: np.ndarray) -> tuple[float, float]:
    thumb = Image.fromarray(np.clip(gray * 255.0, 0, 255).astype(np.uint8)).resize(
        (32, 32), Image.Resampling.BILINEAR
    )
    arr = np.asarray(thumb, dtype=np.float32) / 255.0
    return float(np.std(arr)), float(np.percentile(arr, 95) - np.percentile(arr, 5))


def blockiness(gray: np.ndarray) -> float:
    if min(gray.shape) < 24:
        return 1.0
    vd = np.abs(gray[:, 1:] - gray[:, :-1])
    hd = np.abs(gray[1:, :] - gray[:-1, :])
    vb = np.arange(7, vd.shape[1], 8)
    hb = np.arange(7, hd.shape[0], 8)
    if vb.size == 0 or hb.size == 0:
        return 1.0
    vm = np.ones(vd.shape[1], dtype=bool)
    hm = np.ones(hd.shape[0], dtype=bool)
    vm[vb] = False
    hm[hb] = False
    boundary = float(np.mean(vd[:, vb]) + np.mean(hd[hb, :]))
    interior = float(np.mean(vd[:, vm]) + np.mean(hd[hm, :]))
    return float(boundary / (interior + EPS))


def primary_image(images: list[EmbeddedImageInfo]) -> EmbeddedImageInfo | None:
    if not images:
        return None
    return max(images, key=lambda item: item.width * item.height)


def condition_note(illum_std: float, illum_range: float, sharp: float) -> str:
    notes: list[str] = []
    if illum_range > 0.32 or illum_std > 0.085:
        notes.append("uneven lighting/shadow")
    if sharp < 0.003:
        notes.append("soft/blurred capture")
    elif sharp > 0.025:
        notes.append("very sharp/high-contrast capture")
    return "; ".join(notes) if notes else "normal phone-photo variation"


def extract_page_features(
    page_number: int,
    image_path: Path,
    embedded_images: list[EmbeddedImageInfo],
    max_analysis_side: int,
) -> PageFeatures:
    gray, original_size = load_gray(image_path, max_analysis_side)
    flat = flatten_illumination(gray)
    resid = residual(gray)
    mask = background_mask(flat)
    values = resid[mask] if mask.any() else resid.reshape(-1)
    illum_std, illum_range = illumination(gray)
    primary = primary_image(embedded_images)
    page_sharpness = sharpness(flat)
    return PageFeatures(
        page=page_number,
        rendered_image=image_path.name,
        rendered_width_px=original_size[0],
        rendered_height_px=original_size[1],
        embedded_images=embedded_images,
        primary_image_width=None if primary is None else primary.width,
        primary_image_height=None if primary is None else primary.height,
        primary_image_aspect=None if primary is None or primary.height == 0 else primary.width / primary.height,
        primary_image_megapixels=None if primary is None else (primary.width * primary.height) / 1_000_000,
        primary_image_filter=None if primary is None else primary.filter,
        primary_image_color_space=None if primary is None else primary.color_space,
        noise_std=float(np.std(values)),
        spectral_fingerprint=[round(float(x), 6) for x in spectral_fingerprint(resid, mask)],
        sharpness=page_sharpness,
        illumination_std=illum_std,
        illumination_range=illum_range,
        blockiness=blockiness(flat),
        condition_note=condition_note(illum_std, illum_range, page_sharpness),
    )


def relative_similarity(a: float | None, b: float | None, scale: float = 1.0) -> float:
    if a is None or b is None:
        return 0.5
    diff = abs(a - b) / (abs(a) + abs(b) + EPS)
    return float(np.clip(1.0 - diff * 2.0 * scale, 0.0, 1.0))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom <= EPS:
        return 0.5
    return float(np.clip((np.dot(va, vb) / denom + 1.0) / 2.0, 0.0, 1.0))


def metadata_score(a: PageFeatures, b: PageFeatures) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    if a.primary_image_filter and a.primary_image_filter == b.primary_image_filter:
        score += 45
        reasons.append("same embedded image compression family")
    elif a.primary_image_filter and b.primary_image_filter:
        reasons.append("different embedded image compression family")
    else:
        score += 20
        reasons.append("embedded image compression unavailable on one page")

    if a.primary_image_color_space and a.primary_image_color_space == b.primary_image_color_space:
        score += 25
        reasons.append("same color space")
    elif a.primary_image_color_space and b.primary_image_color_space:
        reasons.append("different color space")
    else:
        score += 10

    if a.primary_image_width and b.primary_image_width:
        score += 15
        reasons.append("both pages contain direct photo images")
    if len(a.embedded_images) == len(b.embedded_images):
        score += 15
        reasons.append("same number of embedded images")

    return float(np.clip(score, 0.0, 100.0)), reasons


def geometry_score(a: PageFeatures, b: PageFeatures) -> tuple[float, list[str]]:
    aspect = relative_similarity(a.primary_image_aspect, b.primary_image_aspect, scale=0.8)
    mp = relative_similarity(a.primary_image_megapixels, b.primary_image_megapixels, scale=0.5)
    score = 100.0 * (0.55 * aspect + 0.45 * mp)
    reasons: list[str] = []
    if score >= 75:
        reasons.append("photo dimensions/aspect are compatible")
    elif score >= 55:
        reasons.append("photo dimensions differ, but still compatible with crop/app processing")
    else:
        reasons.append("photo dimensions/aspect differ strongly")
    return float(score), reasons


def sensor_texture_score(a: PageFeatures, b: PageFeatures) -> tuple[float, list[str]]:
    spectral = cosine_similarity(a.spectral_fingerprint, b.spectral_fingerprint)
    noise = relative_similarity(a.noise_std, b.noise_std, scale=0.6)
    block = relative_similarity(a.blockiness, b.blockiness, scale=0.5)
    score = 100.0 * (0.65 * spectral + 0.25 * noise + 0.10 * block)
    reasons: list[str] = []
    if score >= 75:
        reasons.append("background noise texture is compatible")
    elif score >= 55:
        reasons.append("noise texture is partly compatible but capture conditions differ")
    else:
        reasons.append("noise texture differs strongly")
    return float(score), reasons


def condition_scores(a: PageFeatures, b: PageFeatures) -> tuple[float, float, list[str]]:
    illum = relative_similarity(a.illumination_range, b.illumination_range, scale=0.45)
    sharp = relative_similarity(a.sharpness, b.sharpness, scale=0.25)
    condition_similarity = 100.0 * (0.55 * illum + 0.45 * sharp)
    condition_variation = 100.0 - condition_similarity
    reasons: list[str] = []
    if condition_similarity < 55:
        reasons.append("lighting/focus conditions vary")
    elif condition_similarity < 75:
        reasons.append("minor lighting/focus variation")
    else:
        reasons.append("similar lighting/focus conditions")
    return float(condition_similarity), float(condition_variation), reasons


def pair_assessment(a: PageFeatures, b: PageFeatures) -> PairAssessment:
    meta, meta_reasons = metadata_score(a, b)
    geom, geom_reasons = geometry_score(a, b)
    texture, texture_reasons = sensor_texture_score(a, b)
    condition_sim, condition_var, condition_reasons = condition_scores(a, b)

    # Phone identity compatibility intentionally downweights lighting/focus.
    same_phone = 0.30 * meta + 0.25 * geom + 0.35 * texture + 0.10 * condition_sim
    if same_phone >= 78:
        verdict = "likely_same_phone_or_workflow"
    elif same_phone >= 62:
        verdict = "compatible_with_same_phone"
    elif same_phone >= 48:
        verdict = "uncertain_same_phone"
    else:
        verdict = "possibly_different_phone_or_workflow"

    return PairAssessment(
        page_a=a.page,
        page_b=b.page,
        same_phone_score=round(float(same_phone), 2),
        verdict=verdict,
        metadata_score=round(float(meta), 2),
        geometry_score=round(float(geom), 2),
        sensor_texture_score=round(float(texture), 2),
        condition_similarity_score=round(float(condition_sim), 2),
        condition_variation_score=round(float(condition_var), 2),
        reasons=meta_reasons + geom_reasons + texture_reasons + condition_reasons,
    )


def overall_summary(pages: list[PageFeatures], pairs: list[PairAssessment], metadata: dict[str, str]) -> dict[str, Any]:
    if len(pages) < 2:
        return {
            "overall_verdict": "insufficient_pages",
            "confidence": "limited",
            "same_phone_score": None,
            "reasons": ["Only one page was analyzed; same-phone comparison needs at least two pages."],
        }

    scores = np.asarray([p.same_phone_score for p in pairs], dtype=np.float32)
    median_score = float(np.median(scores))
    min_score = float(np.min(scores))
    variation = float(np.max([p.condition_variation_score for p in pairs]))

    if median_score >= 78 and min_score >= 60:
        verdict = "likely_same_phone_or_workflow"
    elif median_score >= 62 and min_score >= 48:
        verdict = "compatible_with_same_phone"
    elif median_score >= 48:
        verdict = "uncertain_same_phone"
    else:
        verdict = "possibly_different_phone_or_workflow"

    reasons: list[str] = []
    creator = metadata.get("Creator") or metadata.get("Producer")
    if creator:
        reasons.append(f"PDF creator/producer: {creator}.")
    reasons.append(f"Median same-phone compatibility score is {median_score:.1f}.")
    reasons.append(f"Lowest pair compatibility score is {min_score:.1f}.")
    if variation >= 45:
        reasons.append("Capture conditions vary strongly, but this is not treated as strong different-phone proof.")
    elif variation >= 25:
        reasons.append("Capture conditions vary moderately.")
    else:
        reasons.append("Capture conditions are broadly similar.")

    return {
        "overall_verdict": verdict,
        "confidence": "screening",
        "same_phone_score": round(median_score, 2),
        "lowest_pair_score": round(min_score, 2),
        "max_condition_variation_score": round(variation, 2),
        "reasons": reasons,
    }


def report_page_dict(page: PageFeatures) -> dict[str, Any]:
    data = asdict(page)
    data.pop("spectral_fingerprint", None)
    return data


def write_text_report(report: dict[str, Any], path: Path) -> None:
    lines: list[str] = []
    lines.append("same_phone_pdf_check report")
    lines.append("=" * 35)
    lines.append(f"Input PDF: {report['input_pdf']}")
    lines.append(f"Created: {report['created_at']}")
    lines.append(f"Overall verdict: {report['summary']['overall_verdict']}")
    lines.append(f"Same-phone score: {report['summary']['same_phone_score']}")
    lines.append(f"Confidence: {report['summary']['confidence']}")
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
            "- Page {page}: image={primary_image_width}x{primary_image_height}, "
            "filter={primary_image_filter}, noise={noise_std:.5f}, "
            "illum_range={illumination_range:.3f}, note={condition_note}".format(**page)
        )
    lines.append("")
    lines.append("Page-pair same-phone compatibility:")
    for pair in report["pair_assessments"]:
        lines.append(
            "- Pages {page_a}-{page_b}: score={same_phone_score:.1f}, "
            "verdict={verdict}, condition_variation={condition_variation_score:.1f}".format(**pair)
        )
        lines.append("  Reasons: " + "; ".join(pair["reasons"]))
    lines.append("")
    lines.append("How to read this:")
    lines.append("- likely_same_phone_or_workflow: strong compatibility, not proof.")
    lines.append("- compatible_with_same_phone: same phone is plausible despite capture differences.")
    lines.append("- uncertain_same_phone: not enough stable evidence either way.")
    lines.append("- possibly_different_phone_or_workflow: review carefully with other checks.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"Input PDF does not exist: {pdf_path}")
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else pdf_path.with_name(pdf_path.stem + "_same_phone_report")
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata, embedded_by_page = pdf_metadata_and_images(pdf_path)
    render_parent: tempfile.TemporaryDirectory[str] | None = None
    if args.keep_images:
        render_dir = output_dir / "rendered_pages"
        render_dir.mkdir(parents=True, exist_ok=True)
    else:
        render_parent = tempfile.TemporaryDirectory(prefix="same_phone_pages_")
        render_dir = Path(render_parent.name)

    try:
        rendered = render_pdf(pdf_path, render_dir, args.dpi)
        pages = [
            extract_page_features(
                page_number=i,
                image_path=image_path,
                embedded_images=embedded_by_page.get(i, []),
                max_analysis_side=args.max_analysis_side,
            )
            for i, image_path in enumerate(rendered, start=1)
        ]
        pairs: list[PairAssessment] = []
        for i in range(len(pages)):
            for j in range(i + 1, len(pages)):
                pairs.append(pair_assessment(pages[i], pages[j]))

        report = {
            "tool": "same_phone_pdf_check",
            "tool_version": TOOL_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "input_pdf": str(pdf_path),
            "output_dir": str(output_dir),
            "settings": {
                "dpi": args.dpi,
                "max_analysis_side": args.max_analysis_side,
            },
            "important_note": (
                "This is a screening signal for same-phone/workflow compatibility. "
                "PDF conversion apps often strip EXIF/device identity, so this cannot "
                "prove the exact phone model or device."
            ),
            "pdf_metadata": metadata,
            "summary": overall_summary(pages, pairs, metadata),
            "pages": [report_page_dict(p) for p in pages],
            "pair_assessments": [asdict(p) for p in pairs],
        }
        json_path = output_dir / "same_phone_pdf_report.json"
        text_path = output_dir / "same_phone_pdf_report.txt"
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
        report = analyze(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print("same_phone_pdf_check complete")
    print(f"Overall verdict: {report['summary']['overall_verdict']}")
    print(f"Same-phone score: {report['summary']['same_phone_score']}")
    print(f"JSON report: {report['report_files']['json']}")
    print(f"Text report: {report['report_files']['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
