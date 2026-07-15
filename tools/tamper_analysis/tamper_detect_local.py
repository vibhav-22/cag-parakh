"""
╔══════════════════════════════════════════════════════════════╗
║          PDF TAMPER DETECTION TOOL  —  Local Runner          ║
║                                                              ║
║  Detects physical + digital tampering in scanned PDFs:       ║
║    • White correction fluid / pasted paper patches           ║
║    • Overwritten / altered text                              ║
║    • Noise inconsistencies from spliced content              ║
║    • ELA (Error Level Analysis) anomalies                    ║
║    • Ink/contrast irregularities                             ║
║    • PDF metadata anomalies                                  ║
║                                                              ║
║  USAGE:                                                      ║
║    # Analyze specific files:                                 ║
║    python tamper_detect_local.py doc1.pdf doc2.pdf           ║
║                                                              ║
║    # Analyze all PDFs in a folder:                           ║
║    python tamper_detect_local.py --folder /path/to/folder    ║
║                                                              ║
║    # Custom output directory:                                ║
║    python tamper_detect_local.py doc.pdf --output ./reports  ║
║                                                              ║
║  INSTALL DEPENDENCIES:                                       ║
║    pip install pymupdf opencv-python Pillow numpy matplotlib ║
╚══════════════════════════════════════════════════════════════╝
"""

import argparse
import json
import os
import sys
import glob
import tempfile
import traceback
from datetime import datetime

import fitz                          # PyMuPDF  →  pip install pymupdf
import cv2                           # OpenCV   →  pip install opencv-python
import numpy as np
from PIL import Image, ImageChops, ImageEnhance
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec

# ─────────────────────────────────────────────────────────────
# TUNEABLE PARAMETERS  (adjust if you get too many false flags)
# ─────────────────────────────────────────────────────────────
RENDER_DPI     = 300   # Resolution for rendering PDF pages (higher = slower but more detail)
ELA_QUALITY    = 75    # JPEG quality used in ELA re-save (lower = more sensitive)
ELA_AMPLIFY    = 12    # Brightness multiplier for ELA difference image
WHITE_THRESH   = 230   # Pixel brightness (0-255) above which a pixel is "suspiciously white"
MIN_PATCH_AREA = 800   # Minimum pixel area to count a white blob as a "patch"
NOISE_KERNEL   = 5     # Gaussian blur kernel size for noise extraction


# ─────────────────────────────────────────────────────────────
# CORE ANALYSIS FUNCTIONS
# ─────────────────────────────────────────────────────────────

def pdf_to_image(pdf_path: str, dpi: int = RENDER_DPI) -> np.ndarray:
    """Render the first page of a PDF to a BGR numpy array."""
    doc = fitz.open(pdf_path)
    page = doc[0]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    img_bytes = pix.tobytes("png")
    arr = np.frombuffer(img_bytes, dtype=np.uint8)
    img_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    doc.close()
    return img_bgr


def get_pdf_metadata(pdf_path: str) -> dict:
    """Extract PDF metadata dictionary."""
    doc = fitz.open(pdf_path)
    meta = doc.metadata
    doc.close()
    return meta


def ela_analysis(img_bgr: np.ndarray,
                 quality: int = ELA_QUALITY,
                 amplify: float = ELA_AMPLIFY) -> np.ndarray:
    """
    Error Level Analysis (ELA).

    Re-saves the image at a known JPEG quality, then computes the
    per-pixel difference with the original. Regions that were edited
    and re-saved at a *different* quality will show higher error levels
    (appear brighter in the output heatmap).
    """
    pil_img = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp_file:
        tmp = temp_file.name
    try:
        pil_img.save(tmp, "JPEG", quality=quality)
        with Image.open(tmp) as reloaded_file:
            reloaded = reloaded_file.convert("RGB").copy()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    diff = ImageChops.difference(pil_img, reloaded)
    r, g, b = diff.split()
    r = ImageEnhance.Brightness(r).enhance(amplify)
    g = ImageEnhance.Brightness(g).enhance(amplify)
    b = ImageEnhance.Brightness(b).enhance(amplify)
    ela_img = Image.merge("RGB", (r, g, b))
    return np.array(ela_img)


def detect_white_patches(img_bgr: np.ndarray,
                         thresh: int = WHITE_THRESH,
                         min_area: int = MIN_PATCH_AREA) -> tuple:
    """
    Detect abnormally bright/white rectangular blobs.

    Correction fluid and pasted paper create suspiciously uniform
    pure-white areas that stand out against the natural grey-toned
    background of a real scanned document.

    Returns:
        white_mask  — binary mask of bright pixels
        patches     — list of dicts with bbox, area, aspect_ratio
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, white_mask = cv2.threshold(gray, thresh, 255, cv2.THRESH_BINARY)

    # Morphological closing merges nearby bright spots into single blobs
    kernel = np.ones((15, 15), np.uint8)
    closed = cv2.morphologyEx(white_mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    patches = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area > min_area:
            x, y, w, h = cv2.boundingRect(cnt)
            aspect = w / max(h, 1)
            if 0.3 < aspect < 15:   # filter out very thin lines / blobs
                patches.append({
                    "bbox": (x, y, w, h),
                    "area": int(area),
                    "aspect_ratio": round(aspect, 2)
                })
    return white_mask, patches


def noise_analysis(img_bgr: np.ndarray, kernel: int = NOISE_KERNEL) -> np.ndarray:
    """
    Extract the noise layer by subtracting a blurred version.

    A genuine scan has consistent low-level noise everywhere.
    Pasted paper / correction fluid = suspiciously ZERO noise.
    Content spliced from a different scan = unusually HIGH noise.
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    blurred = cv2.GaussianBlur(gray, (kernel, kernel), 0)
    noise = np.abs(gray - blurred)
    noise_norm = cv2.normalize(noise, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    return noise_norm


def detect_noise_anomalies(noise_map: np.ndarray) -> tuple:
    """
    Divide the noise map into a 10×10 grid and flag cells whose mean
    noise deviates significantly from the document average.

    Returns:
        anomalies    — list of dicts describing each flagged cell
        overall_mean — baseline noise mean across the document
        overall_std  — baseline noise std across the document
    """
    h, w = noise_map.shape
    cell_h, cell_w = h // 10, w // 10
    cell_stats = []

    for row in range(10):
        for col in range(10):
            y1, y2 = row * cell_h, (row + 1) * cell_h
            x1, x2 = col * cell_w, (col + 1) * cell_w
            cell = noise_map[y1:y2, x1:x2]
            cell_stats.append(float(np.mean(cell)))

    overall_mean = float(np.mean(cell_stats))
    overall_std  = float(np.std(cell_stats))

    anomalies = []
    for i, stat in enumerate(cell_stats):
        row, col = i // 10, i % 10
        if stat < overall_mean - 1.5 * overall_std:
            anomalies.append({
                "type": "LOW_NOISE",
                "reason": "Suspiciously clean — possible correction fluid or pasted paper",
                "grid_pos": (row, col),
                "noise_level": round(stat, 2),
                "expected": round(overall_mean, 2)
            })
        elif stat > overall_mean + 2.0 * overall_std:
            anomalies.append({
                "type": "HIGH_NOISE",
                "reason": "Unusually noisy — content possibly from a different scan/source",
                "grid_pos": (row, col),
                "noise_level": round(stat, 2),
                "expected": round(overall_mean, 2)
            })
    return anomalies, overall_mean, overall_std


def detect_ink_inconsistency(img_bgr: np.ndarray) -> np.ndarray:
    """
    Map local contrast variance across the image.

    Overwritten text or content written with a different pen shows up
    as patches of anomalously high or low local contrast compared to
    the surrounding genuine content.
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    kernel_size = 31
    gray_f = gray.astype(np.float32)
    mean    = cv2.blur(gray_f, (kernel_size, kernel_size))
    mean_sq = cv2.blur(gray_f ** 2, (kernel_size, kernel_size))
    local_std = np.sqrt(np.maximum(mean_sq - mean ** 2, 0))
    local_std_norm = cv2.normalize(local_std, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    return local_std_norm


def compute_ela_score(ela_img: np.ndarray) -> dict:
    """Summarise ELA output into scalar metrics."""
    return {
        "mean":               round(float(np.mean(ela_img)), 2),
        "max":                round(float(np.max(ela_img)), 2),
        "std":                round(float(np.std(ela_img)), 2),
        "high_ela_pixel_pct": round(float(np.sum(ela_img > 80) / ela_img.size * 100), 2)
    }


# ─────────────────────────────────────────────────────────────
# SCORING ENGINE
# ─────────────────────────────────────────────────────────────

def compute_tamper_score(white_patches: list,
                         noise_anomalies: list,
                         ela_scores: dict,
                         metadata: dict) -> tuple:
    """
    Combine all signals into a single tamper likelihood score (0–100).

    Scoring weights:
        White patches (physical)  → up to 35 pts
        Low-noise regions         → up to 25 pts
        High-noise regions        → up to 15 pts
        ELA anomaly               → up to 20 pts
        Metadata editor hint      →  5 pts
    """
    score = 0
    reasons = []

    # ── White patches ──────────────────────────────────────────
    n_patches = len(white_patches)
    if n_patches >= 3:
        score += 35
        reasons.append(f"[HIGH] {n_patches} suspicious white patch(es) — correction fluid / pasted paper")
    elif n_patches >= 1:
        score += 20
        reasons.append(f"[MED]  {n_patches} white patch(es) — possible correction fluid")

    # ── Noise anomalies ────────────────────────────────────────
    low_noise  = [a for a in noise_anomalies if a["type"] == "LOW_NOISE"]
    high_noise = [a for a in noise_anomalies if a["type"] == "HIGH_NOISE"]

    if len(low_noise) >= 3:
        score += 25
        reasons.append(f"[HIGH] {len(low_noise)} suspiciously clean region(s) — pasted paper or white-out")
    elif len(low_noise) >= 1:
        score += 12
        reasons.append(f"[MED]  {len(low_noise)} low-noise region(s) — possible physical alteration")

    if len(high_noise) >= 2:
        score += 15
        reasons.append(f"[MED]  {len(high_noise)} high-noise region(s) — content from a different source")
    elif len(high_noise) == 1:
        score += 7
        reasons.append(f"[MED]  1 high-noise region — minor noise inconsistency")

    # ── ELA ────────────────────────────────────────────────────
    ela_pct = ela_scores["high_ela_pixel_pct"]
    if ela_pct > 15:
        score += 20
        reasons.append(f"[HIGH] ELA: {ela_pct}% of pixels show compression editing artifacts")
    elif ela_pct > 8:
        score += 10
        reasons.append(f"[MED]  ELA: {ela_pct}% of pixels flagged — moderate anomaly")

    # ── Metadata ───────────────────────────────────────────────
    creator  = (metadata.get("creator")  or "").lower()
    producer = (metadata.get("producer") or "").lower()
    editors  = ["photoshop", "gimp", "paint", "illustrator", "acrobat", "inkscape"]
    matched  = [e for e in editors if e in creator + producer]
    if matched:
        score += 5
        reasons.append(f"[MED]  Metadata: image editor detected ({', '.join(matched)})")

    score = min(score, 100)

    if score >= 60:
        verdict = "HIGH LIKELIHOOD OF TAMPERING"
    elif score >= 30:
        verdict = "MODERATE SUSPICION — Further investigation recommended"
    else:
        verdict = "LOW SUSPICION — No strong tampering signals detected"

    return score, verdict, reasons


# ─────────────────────────────────────────────────────────────
# REPORT GENERATOR  (per-document)
# ─────────────────────────────────────────────────────────────

def generate_report(pdf_path: str, output_dir: str) -> tuple:
    """Run all analyses on one PDF and save a visual report PNG."""
    name = os.path.splitext(os.path.basename(pdf_path))[0]
    sep  = "=" * 60
    print(f"\n{sep}\n  Analyzing: {name}\n{sep}")

    # ── Load & render ──────────────────────────────────────────
    img_bgr  = pdf_to_image(pdf_path)
    img_rgb  = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    metadata = get_pdf_metadata(pdf_path)

    # ── Run analyses ───────────────────────────────────────────
    ela_img                       = ela_analysis(img_bgr)
    white_mask, white_patches     = detect_white_patches(img_bgr)
    noise_map                     = noise_analysis(img_bgr)
    noise_anom, noise_mean, _     = detect_noise_anomalies(noise_map)
    ink_map                       = detect_ink_inconsistency(img_bgr)
    ela_scores                    = compute_ela_score(ela_img)

    score, verdict, reasons = compute_tamper_score(
        white_patches, noise_anom, ela_scores, metadata
    )

    print(f"  Score  : {score}/100")
    print(f"  Verdict: {verdict}")
    for r in reasons:
        print(f"    • {r}")

    # ── Annotated original (red boxes around patches) ──────────
    annotated = img_rgb.copy()
    for p in white_patches:
        x, y, w, h = p["bbox"]
        cv2.rectangle(annotated, (x, y), (x + w, y + h), (220, 30, 30), 6)
        cv2.putText(annotated, "PATCH", (x, max(y - 12, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (220, 30, 30), 3)

    # ── Score colour ───────────────────────────────────────────
    score_color = "#e03030" if score >= 60 else "#e09030" if score >= 30 else "#30b860"

    # ── Build 2×3 visual report ────────────────────────────────
    fig = plt.figure(figsize=(22, 16), facecolor="#0f0f0f")
    fig.suptitle(
        f"TAMPER ANALYSIS REPORT — {name.upper()}\n"
        f"Score: {score}/100   |   {verdict}",
        color=score_color, fontsize=15, fontweight="bold", y=0.98
    )

    gs = GridSpec(2, 3, figure=fig, hspace=0.35, wspace=0.25)

    panels = [
        (gs[0, 0], img_rgb,   "Original Document",          None),
        (gs[0, 1], annotated, "White Patch Detection",      None),
        (gs[0, 2], ela_img,   "ELA — Error Level Analysis", "hot"),
        (gs[1, 0], noise_map, "Noise Map",                  "viridis"),
        (gs[1, 1], ink_map,   "Ink Inconsistency Map",      "plasma"),
        (gs[1, 2], None,      "Findings Summary",            None),
    ]

    for spec, data, title, cmap in panels:
        ax = fig.add_subplot(spec)
        ax.set_facecolor("#1a1a1a")
        ax.set_title(title, color="#00d4ff", fontsize=11, pad=6, fontweight="bold")
        ax.axis("off")
        if data is not None:
            ax.imshow(data, cmap=cmap, aspect="auto")

    # ── Findings text panel ────────────────────────────────────
    ax_t = fig.add_subplot(gs[1, 2])
    ax_t.set_facecolor("#111111")
    ax_t.axis("off")
    ax_t.set_title("Findings Summary", color="#00d4ff", fontsize=11, pad=6, fontweight="bold")

    lines = [
        f"FILE    : {name}",
        f"SCORE   : {score}/100",
        f"VERDICT : {verdict}",
        "",
        "─── FINDINGS ───────────────────────",
        *[f"  {r}" for r in reasons],
        "",
        "─── DETECTION STATS ────────────────",
        f"  White patches     : {len(white_patches)}",
        f"  Noise anomalies   : {len(noise_anom)}",
        f"    Low-noise cells : {sum(1 for a in noise_anom if a['type']=='LOW_NOISE')}",
        f"    High-noise cells: {sum(1 for a in noise_anom if a['type']=='HIGH_NOISE')}",
        f"  ELA flagged pixels: {ela_scores['high_ela_pixel_pct']}%",
        f"  ELA mean level    : {ela_scores['mean']}",
        "",
        "─── PDF METADATA ───────────────────",
        f"  Creator : {metadata.get('creator')  or 'N/A'}",
        f"  Producer: {metadata.get('producer') or 'N/A'}",
        f"  Created : {metadata.get('creationDate') or 'N/A'}",
        f"  Modified: {metadata.get('modDate')      or 'N/A'}",
    ]

    ax_t.text(0.04, 0.97, "\n".join(lines),
              transform=ax_t.transAxes, va="top", ha="left",
              fontsize=8.2, color="white", fontfamily="monospace",
              linespacing=1.5)

    out_path = os.path.join(output_dir, f"{name}_tamper_report.png")
    plt.savefig(out_path, dpi=120, bbox_inches="tight",
                facecolor="#0f0f0f", edgecolor="none")
    plt.close()
    print(f"  Report → {out_path}")

    height, width = img_bgr.shape[:2]
    risk = "high" if score >= 60 else "medium" if score >= 30 else "low"
    regions = []
    for patch in white_patches:
        x, y, w, h = patch["bbox"]
        regions.append({
            "page": 1,
            "kind": "white_patch",
            "label": "Possible pasted or corrected area",
            "severity": risk,
            "reason": "An unusually uniform bright patch was detected in this area.",
            "bbox_normalized": {
                "x0": x / width,
                "y0": y / height,
                "x1": (x + w) / width,
                "y1": (y + h) / height,
            },
        })
    for anomaly in noise_anom:
        row, col = anomaly["grid_pos"]
        regions.append({
            "page": 1,
            "kind": "noise_anomaly",
            "label": "Noise inconsistency",
            "severity": risk,
            "reason": anomaly["reason"],
            "bbox_normalized": {
                "x0": col / 10,
                "y0": row / 10,
                "x1": (col + 1) / 10,
                "y1": (row + 1) / 10,
            },
        })

    structured_path = os.path.join(output_dir, f"{name}_tamper_analysis.json")
    with open(structured_path, "w", encoding="utf-8") as structured_file:
        json.dump({
            "status": "completed",
            "score": score,
            "verdict": verdict,
            "risk": risk,
            "reasons": reasons,
            "regions": regions,
        }, structured_file, indent=2, ensure_ascii=False)

    return out_path, score, verdict, reasons


# ─────────────────────────────────────────────────────────────
# SUMMARY CHART  (all documents)
# ─────────────────────────────────────────────────────────────

def generate_summary(results: list, output_dir: str) -> str:
    """Save a horizontal bar chart summarising scores for all PDFs."""
    fig, ax = plt.subplots(figsize=(14, max(5, len(results) * 1.4)),
                           facecolor="#0f0f0f")
    ax.set_facecolor("#0f0f0f")
    ax.set_title("TAMPER DETECTION SUMMARY — ALL DOCUMENTS",
                 color="white", fontsize=14, fontweight="bold", pad=15)

    names  = [r["name"]  for r in results]
    scores = [r["score"] for r in results]
    colors = ["#e03030" if s >= 60 else "#e09030" if s >= 30 else "#30b860"
              for s in scores]

    bars = ax.barh(names, scores, color=colors, height=0.5, edgecolor="#444")
    ax.set_xlim(0, 115)
    ax.set_xlabel("Tamper Score (0–100)", color="white", fontsize=11)
    ax.tick_params(colors="white", labelsize=11)
    for spine in ax.spines.values():
        spine.set_color("#333333")

    ax.axvline(60, color="#e03030", linestyle="--", linewidth=1.2, alpha=0.7)
    ax.axvline(30, color="#e09030", linestyle="--", linewidth=1.2, alpha=0.7)

    for bar, score, r in zip(bars, scores, results):
        short_verdict = r["verdict"].split("—")[0].strip()
        ax.text(score + 1.5, bar.get_y() + bar.get_height() / 2,
                f"{score}/100 — {short_verdict}",
                va="center", color="white", fontsize=9)

    legend_patches = [
        mpatches.Patch(color="#e03030", label="HIGH likelihood of tampering  (score >= 60)"),
        mpatches.Patch(color="#e09030", label="MODERATE suspicion            (score 30–59)"),
        mpatches.Patch(color="#30b860", label="LOW suspicion                 (score < 30)"),
    ]
    ax.legend(handles=legend_patches, loc="lower right",
              facecolor="#1a1a1a", labelcolor="white", fontsize=9, framealpha=0.8)

    plt.tight_layout()
    out_path = os.path.join(output_dir, "SUMMARY_tamper_report.png")
    plt.savefig(out_path, dpi=120, bbox_inches="tight",
                facecolor="#0f0f0f", edgecolor="none")
    plt.close()
    print(f"\n  Summary chart → {out_path}")
    return out_path


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Detect tampering in scanned PDF documents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tamper_detect_local.py doc1.pdf doc2.pdf
  python tamper_detect_local.py --folder ./scans
  python tamper_detect_local.py *.pdf --output ./reports
        """
    )
    parser.add_argument(
        "pdfs", nargs="*",
        help="One or more PDF file paths to analyze"
    )
    parser.add_argument(
        "--folder", "-f",
        help="Folder to scan for all *.pdf files"
    )
    parser.add_argument(
        "--output", "-o",
        default="./tamper_reports",
        help="Output directory for report images (default: ./tamper_reports)"
    )
    parser.add_argument(
        "--dpi", type=int, default=RENDER_DPI,
        help=f"Render DPI (default: {RENDER_DPI})"
    )
    return parser.parse_args()


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    args = parse_args()

    # Collect PDF paths
    pdf_paths = list(args.pdfs)
    if args.folder:
        found = sorted(glob.glob(os.path.join(args.folder, "*.pdf")))
        if not found:
            print(f"[WARN] No PDF files found in folder: {args.folder}")
        pdf_paths.extend(found)

    if not pdf_paths:
        print("No PDF files specified. Use positional args or --folder.")
        print("Run with --help for usage information.")
        sys.exit(1)

    # Override DPI if provided
    global RENDER_DPI
    RENDER_DPI = args.dpi

    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)

    # Header
    banner = "█" * 62
    print(f"\n{banner}")
    print("  PDF TAMPER DETECTION TOOL")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Files   : {len(pdf_paths)}")
    print(f"  Output  : {os.path.abspath(output_dir)}")
    print(f"{banner}")

    results      = []
    report_files = []

    for pdf in pdf_paths:
        if not os.path.isfile(pdf):
            print(f"\n  [SKIP] File not found: {pdf}")
            continue
        try:
            out_img, score, verdict, reasons = generate_report(pdf, output_dir)
            report_files.append(out_img)
            results.append({
                "name":    os.path.splitext(os.path.basename(pdf))[0],
                "score":   score,
                "verdict": verdict,
                "reasons": reasons,
            })
        except Exception as e:
            print(f"\n  [ERROR] {pdf}: {e}")
            traceback.print_exc()

    # Summary chart
    if len(results) > 1:
        summary_path = generate_summary(results, output_dir)
        report_files.insert(0, summary_path)

    # Final table
    print("\n" + "─" * 62)
    print("  FINAL RESULTS")
    print("─" * 62)
    for r in results:
        flag = "!!!" if r["score"] >= 60 else " ! " if r["score"] >= 30 else "   "
        print(f"  [{flag}]  {r['name']:<20s}  {r['score']:3d}/100   {r['verdict']}")
    print("─" * 62)
    print(f"\n  Reports saved to: {os.path.abspath(output_dir)}/")
    print()


if __name__ == "__main__":
    main()
