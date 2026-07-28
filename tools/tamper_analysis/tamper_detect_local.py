#!/usr/bin/env python3
"""
whitener_detect.py - correction-fluid ("white-out") detector for scanned PDFs.

Deterministic image forensics, no ML. Correction fluid on a physical page has a
distinct optical signature once scanned, and every cue below is measured
*locally* against the region's own surrounding paper so scanner illumination
gradients don't fool it:

  B  brightness   fluid reflects more light than paper -> a few DN brighter
  T  texture      the fluid coat suppresses paper-fiber grain / scanner noise
  C  color        paper scans warm (yellowish); fluid is neutral/bluish white
  E  edge         a fluid patch has a physical step boundary (glare fades)
  X  context      people white out written content, so patches sit in text
  S  shape        daubs are solid blobs, not thin snakes or rings

Each candidate region gets a weighted blend of those cues squashed through a
logistic into a confidence in [0, 1]; a page's probability is the soft-OR of
its regions; the document's probability is the soft-OR of its pages.

Usage:
  python whitener_detect.py scan.pdf
  python whitener_detect.py a.pdf b.pdf --dpi 250 --out results --json
  python whitener_detect.py scan.pdf --fail-over 0.5   # exit 3 if score >= 0.5

Outputs per input file (in <out>/<stem>/):
  report.json               scores, boxes (pixel + PDF-point coords), features
  page_NNN_annotated.png    flagged pages with boxes drawn
  <stem>_annotated.pdf      the whole document rebuilt with boxes drawn
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None

VERSION = "1.1.0"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

# ---------------------------------------------------------------- tunables --
# Candidate gates (region must be brighter than its local background).
Z_DETAIL_GATE = 2.2        # z-score vs. local (median-flattened) background
ABS_DELTA_GATE = 1.6       # and an absolute floor in gray DN
Z_RAW_GATE = 3.0           # fallback gate for patches too big for flattening
ABS_RAW_GATE = 2.5

# Core cue weights (sum to 1). Texture/color are corroboration-only bonuses:
# real daubs are usually written over again (texture restored) and fluid tint
# varies by brand, so their absence must not drag the score down.
W_BRIGHT, W_EDGE, W_TEXT, W_SHAPE = .42, .20, .22, .16
W_SMOOTH_BONUS, W_BLUE_BONUS = .10, .08
SIGMOID_CENTER, SIGMOID_STEEP = 0.52, 8.5

CONF_REPORT = 0.15         # regions below this are dropped from the report
CONF_PAGE = 0.25           # regions below this don't count toward page score
MAX_REGIONS = 12
CLIPPED_PAPER_LUM = 251.0  # above this the brightness cue has no headroom


def odd(n: float) -> int:
    n = int(round(n))
    return n if n % 2 == 1 else n + 1


def clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def robust_sigma(values: np.ndarray, floor: float) -> float:
    med = float(np.median(values))
    mad = float(np.median(np.abs(values - med)))
    return max(1.4826 * mad, floor)


def dil(mask: np.ndarray, k: int, iterations: int = 1) -> np.ndarray:
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    return cv2.dilate(mask.astype(np.uint8), kern, iterations=iterations) > 0


def ero(mask: np.ndarray, k: int) -> np.ndarray:
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    return cv2.erode(mask.astype(np.uint8), kern) > 0


def local_std(gray: np.ndarray, k: int) -> np.ndarray:
    mean = cv2.boxFilter(gray, cv2.CV_32F, (k, k))
    mean2 = cv2.boxFilter(gray * gray, cv2.CV_32F, (k, k))
    return np.sqrt(np.maximum(mean2 - mean * mean, 0.0))


# ------------------------------------------------------------ page analysis --
def analyze_page(rgb: np.ndarray, dpi: int) -> dict:
    """Analyze one rendered page (RGB uint8). Returns a page-result dict."""
    h, w = rgb.shape[:2]
    warnings: list[str] = []
    gray_u8 = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = gray_u8.astype(np.float32)

    # Bitonal / posterized scans carry no brightness or texture signal at all.
    if np.unique(gray_u8[::4, ::4]).size <= 10:
        return {
            "probability": 0.0, "indeterminate": True, "regions": [],
            "warnings": ["bitonal/binarized page - whitener cues are not "
                         "measurable in a 1-bit scan"],
            "paper": {}, "size_px": [w, h],
        }

    # --- paper model (robust: patches are a small minority of pixels) ------
    med = float(np.median(gray))
    if med < 100:
        warnings.append("dark page background - detector assumes white paper")
    paperish = np.abs(gray - med) <= 25
    paper_lum = float(np.median(gray[paperish]))
    sigma = robust_sigma(gray[paperish], 0.6)

    # 6-sigma is right for clean scans, but on noisy/gradient captures sigma
    # reflects illumination structure - uncapped it would miss all but the
    # blackest ink (and re-written strokes on daubs must count as ink).
    ink = gray < (paper_lum - min(max(35.0, 6.0 * sigma), 60.0))
    ink_d3 = dil(ink, 3)

    # "Writing" = dark pixels that could be written content. Page-border bands
    # (scan shadows, bed edges) and big solid dark blobs (photos, stamps) are
    # ink-dark but not writing; text context and fragment bridging must key
    # off real strokes or bright paper next to a scan band scores as "text".
    n_ink, ink_lab, ink_stats, _ = cv2.connectedComponentsWithStats(
        ink.astype(np.uint8) * 255, 8, cv2.CV_32S)
    eb_ink = max(2, int(round(0.01 * min(h, w))))
    max_stroke_area = int((dpi * 0.55) ** 2)  # ~1.4 cm2 of solid dark != strokes
    drop_ink = np.zeros(max(n_ink, 1), bool)
    for ii in range(1, n_ink):
        ix, iy, iw, ih = (int(ink_stats[ii, s]) for s in
                          (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP,
                           cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
        ia = int(ink_stats[ii, cv2.CC_STAT_AREA])
        drop_ink[ii] = (ix <= eb_ink or iy <= eb_ink or ix + iw >= w - eb_ink
                        or iy + ih >= h - eb_ink or ia > max_stroke_area)
    writing = ink & ~drop_ink[ink_lab]

    k_grain = odd(max(5, dpi / 40))
    ink_far = dil(ink, k_grain + 2)
    paper_zone = paperish & ~ink_far
    if not paper_zone.any():
        paper_zone = paperish

    # --- illumination-flattened brightness map ------------------------------
    g_s = cv2.GaussianBlur(gray, (0, 0), 2.0)
    k_bg = odd(min(max(0.7 * dpi, 61), 199))
    bg = cv2.medianBlur(np.clip(g_s, 0, 255).astype(np.uint8), k_bg).astype(np.float32)
    detail = g_s - bg
    d_med = float(np.median(detail[paper_zone]))
    d_sig = robust_sigma(detail[paper_zone], 0.35)

    gs_med = float(np.median(g_s[paper_zone]))
    gs_sig = robust_sigma(g_s[paper_zone], 0.45)

    # --- texture (grain) model ----------------------------------------------
    grain = local_std(gray, k_grain)
    paper_grain = float(np.median(grain[paper_zone]))
    grain_ok = paper_grain >= 0.7
    if not grain_ok:
        warnings.append("paper grain too low (heavy compression or synthetic "
                        "render) - texture cue disabled")

    # --- color model (LAB b* = yellow-blue axis; needs the uint8 path) ------
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    a_f = lab[..., 1].astype(np.float32)
    b_f = lab[..., 2].astype(np.float32)
    chroma = float(np.median(np.abs(a_f[paperish] - 128))
                   + np.median(np.abs(b_f[paperish] - 128)))
    color_ok = chroma >= 1.5
    b_s = cv2.GaussianBlur(b_f, (0, 0), 2.0)
    paper_b = float(np.median(b_s[paper_zone]))

    clipped = paper_lum >= CLIPPED_PAPER_LUM
    if clipped:
        warnings.append("paper is near white-point clipping - brightness cue "
                        "compressed; confidence capped at 0.60")

    # --- candidate mask ------------------------------------------------------
    # Gate thresholds are capped in absolute DN: on photocopies/photos the
    # robust sigma measures illumination structure, and an uncapped z-gate
    # can exceed the 255 ceiling entirely. Real fluid sits tens of DN above
    # paper; candidates just need to reach the scorer, which is the FP gate.
    det_thr = max(ABS_DELTA_GATE, min(Z_DETAIL_GATE * d_sig, 12.0))
    raw_thr = max(ABS_RAW_GATE, min(Z_RAW_GATE * gs_sig, 18.0))
    cand = (detail >= d_med + det_thr) | (g_s >= gs_med + raw_thr)
    # Second physical mode: copier-flattened fluid. A photocopy/rescan of a
    # whitened original renders the daub as a mottled gray blotch, not a
    # bright patch - the signature is grain far above the page baseline in a
    # bright-ish blob (dark high-grain areas are photos/stamps/dense print).
    if grain_ok:
        cand |= ((grain >= max(3.0, 3.0 * paper_grain))
                 & (gray > paper_lum - 45.0) & ~ink_far)
    cand &= ~ink_d3  # also drops JPEG ringing halos hugging strokes

    k_close = odd(2 * max(2, dpi // 100) + 1)
    k_open = odd(2 * max(2, dpi // 130) + 1)
    cand_u8 = (cand.astype(np.uint8)) * 255
    cand_u8 = cv2.morphologyEx(cand_u8, cv2.MORPH_CLOSE,
                               cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_close, k_close)))
    cand_u8 = cv2.morphologyEx(cand_u8, cv2.MORPH_OPEN,
                               cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_open, k_open)))

    # Sobel magnitude for the edge-sharpness cue (computed once per page).
    gx = cv2.Sobel(g_s, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g_s, cv2.CV_32F, 0, 1, ksize=3)
    grad = cv2.magnitude(gx, gy)

    text_zone = dil(writing, odd(dpi * 0.14))  # ~3.5 mm reach around writing

    # Group fragments before scoring: a daub with re-written ink on top breaks
    # into several bright shards; merged, they score as the one patch they are
    # (and the ring then samples true paper instead of sibling fragments).
    # Fragments may only merge ACROSS INK - ink is what split them. Merging
    # across plain paper would chain unrelated illumination speckle into
    # page-scale mega-groups on gradient-heavy photocopies.
    k_grp = odd(dpi * 0.06)
    kern_grp = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_grp, k_grp))
    closed = cv2.morphologyEx(cand_u8, cv2.MORPH_CLOSE, kern_grp) > 0
    cand_bool = cand_u8 > 0
    grouped = ((closed & (cand_bool | writing)).astype(np.uint8)) * 255
    n_lab, labels, stats, _ = cv2.connectedComponentsWithStats(grouped, 8, cv2.CV_32S)

    # A correction-fluid applicator physically can't mark thinner than ~2 mm;
    # anything narrower is watermark/security-pattern glint or JPEG shard.
    min_area = max(120, int(round((dpi / 25.4 * 3.0) ** 2)))  # ~3x3 mm
    min_side = max(6, int(round(dpi / 25.4 * 2.2)))           # ~2.2 mm
    border_band = max(3, int(round(0.015 * min(h, w))))
    r_ring = max(8, int(round(dpi * 0.08)))                   # ~2 mm ring gap->4 mm reach

    order = sorted(range(1, n_lab), key=lambda i: -stats[i, cv2.CC_STAT_AREA])[:40]
    regions = []
    for i in order:
        gx, gy, gw, gh = (int(stats[i, s]) for s in
                          (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP,
                           cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
        sub = (labels[gy:gy + gh, gx:gx + gw] == i) & cand_bool[gy:gy + gh, gx:gx + gw]
        area = int(sub.sum())
        if area < min_area:
            continue
        ys, xs = np.nonzero(sub)
        x, y = gx + int(xs.min()), gy + int(ys.min())
        bw, bh = int(xs.max()) - int(xs.min()) + 1, int(ys.max()) - int(ys.min()) + 1
        area_frac = area / float(h * w)
        if min(bw, bh) < min_side:
            continue
        if bw > 0.85 * w and bh > 0.85 * h:
            continue  # page-frame surround (scan bed / digital canvas), not a patch
        if area_frac > 0.35 or bw * bh > 0.5 * h * w:
            warnings.append("very large bright area skipped (likely exposure, "
                            "not correction fluid)")
            continue

        # Scanner-bed margins: skip blobs that mostly live in the outer frame.
        comp_full = np.zeros((h, w), bool)
        comp_full[gy:gy + gh, gx:gx + gw] = sub
        in_band = comp_full.copy()
        in_band[border_band:h - border_band, border_band:w - border_band] = False
        if in_band.sum() >= 0.25 * area:
            continue

        # Work on a padded crop for all per-region morphology.
        pad = 2 * r_ring + 8
        cx0, cy0 = max(0, x - pad), max(0, y - pad)
        cx1, cy1 = min(w, x + bw + pad), min(h, y + bh + pad)
        m = comp_full[cy0:cy1, cx0:cx1]
        ink_c = ink_d3[cy0:cy1, cx0:cx1]
        gray_c = gray[cy0:cy1, cx0:cx1]
        grain_c = grain[cy0:cy1, cx0:cx1]
        b_c = b_s[cy0:cy1, cx0:cx1]
        grad_c = grad[cy0:cy1, cx0:cx1]

        interior = ero(m, 5) & ~ink_c
        if interior.sum() < 30:
            interior = m & ~ink_c
        if interior.sum() < 30:
            interior = m

        ring = dil(m, 2 * r_ring + 1) & ~dil(m, 9) & ~ink_c
        if ring.sum() < 150:
            ring = dil(m, 4 * r_ring + 1) & ~dil(m, 9) & ~ink_c
        use_global = ring.sum() < 150

        if use_global:
            local_paper = paper_lum
            local_sig = max(sigma, 0.8)
            ring_grain = paper_grain
            ring_b = paper_b
        else:
            # 65th percentile, not median: faint print below the ink threshold
            # pollutes the ring and would drag a median down, manufacturing
            # "brighter than ring" deltas for plain cells in printed tables.
            local_paper = float(np.percentile(gray_c[ring], 65))
            local_sig = robust_sigma(gray_c[ring], 0.8)
            ring_grain = float(np.median(grain_c[ring]))
            ring_b = float(np.median(b_c[ring]))

        # --- cues ---
        # Every cue is the WEAKER of local-ring vs. global-paper evidence.
        # Local-only gets fooled by unprinted paper inside tinted/halftone
        # areas (white form fields beat their gray surroundings but only tie
        # the page's actual paper); real correction fluid beats the paper too.
        def bright_evidence(delta_dn: float, sig: float) -> float:
            z_term = clip01((delta_dn / max(sig, 0.8) - 1.2) / 5.0)
            # Escape hatch: tens of DN above paper is decisive even when the
            # page sigma is inflated by illumination structure (photocopies).
            abs_term = clip01((delta_dn - 8.0) / 22.0)
            return max(z_term, abs_term) * clip01((delta_dn - 1.2) / 2.5)

        med_in = float(np.median(gray_c[interior]))
        delta = med_in - local_paper
        bright_z = delta / local_sig
        f_bright = min(bright_evidence(delta, local_sig),
                       bright_evidence(med_in - paper_lum, sigma))

        grain_in = float(np.median(grain_c[interior]))
        if grain_ok:
            supp = min(1.0 - grain_in / max(ring_grain, 0.3),
                       1.0 - grain_in / max(paper_grain, 0.3))
            f_smooth = clip01(supp / 0.55)
        else:
            supp, f_smooth = None, None

        if color_ok:
            b_in = float(np.median(b_c[interior]))
            db = min(ring_b - b_in, paper_b - b_in)
            f_blue = clip01(db / 4.5)
        else:
            db, f_blue = None, None

        # Text context: either the patch borders surviving ink, or its rows
        # carry ink elsewhere on the page (a daub mid-line covers the ink
        # beneath it, so plain proximity alone misses the classic case).
        tp_zone = float(text_zone[comp_full].mean())
        row_ink = writing[y:y + bh, :].sum(axis=1).astype(np.float32)
        tp_rows = float(np.mean(row_ink > 0.002 * w))
        tp = max(tp_zone, tp_rows)
        f_text = clip01((tp - 0.05) / 0.55)

        f_shape = clip01((area / float(bw * bh) - 0.25) / 0.45)

        boundary = dil(m, 5) & ~ero(m, 5)
        edge_z = float(np.median(grad_c[boundary])) / max(local_sig, 0.8) if boundary.any() else 0.0
        f_edge = clip01((edge_z - 0.7) / 2.0)

        # Mottle (copier-flattened fluid) can stand in for brightness, but only
        # with a paper-level ring (a dark ring means we're inside a photo or
        # print block), writing context, and a region that is still at least
        # as bright as its ring - highlighter, show-through ghosting and
        # handwriting-band texture are all mottled but NET DARKER than their
        # surroundings, while a flattened daub never is.
        # The delta ramp encodes that flattened fluid still reflects well above
        # its ring (+9..+28 on real daubs); handwriting-band texture, ghosting
        # and photocopier toner streak sit within ~0..+8 of ring level and
        # must score ~nothing.
        f_mottle = 0.0
        if grain_ok and local_paper > paper_lum - 50.0 and f_text >= 0.15:
            f_mottle = (clip01((grain_in / max(paper_grain, 0.5) - 2.0) / 3.5)
                        * clip01((delta - 2.0) / 10.0))
        f_primary = max(f_bright, f_mottle)

        raw = (W_BRIGHT * f_primary + W_EDGE * f_edge
               + W_TEXT * f_text + W_SHAPE * f_shape)
        raw = min(1.0, raw + W_SMOOTH_BONUS * (f_smooth or 0.0)
                  + W_BLUE_BONUS * (f_blue or 0.0))
        conf = 1.0 / (1.0 + math.exp(-SIGMOID_STEEP * (raw - SIGMOID_CENTER)))

        # Brightness or mottle is the defining cue: without either the region
        # is not distinguishable from plain paper, whatever the rest says.
        conf *= 0.25 + 0.75 * clip01(f_primary / 0.30)
        # Whitener exists to alter written content: a candidate with no
        # writing near it or anywhere in its rows is background structure.
        if f_text < 0.15:
            conf *= 0.4
        # Thin vertical slivers are page-edge gaps / fold highlights; fluid
        # daubs over (horizontal) text are never tall-and-narrow.
        if bh > 3 * bw and bw < dpi * 0.16:
            conf *= 0.35
        # Bright blob at the page's extreme edge with no ink NEARBY: backing
        # sheet / scanner bed showing past the document, not correction fluid.
        # (Uses ink proximity, not row coverage - a border-hugging frame region
        # spans every text row without being anywhere near the writing.)
        eb = max(2, int(round(0.01 * min(h, w))))
        if (x <= eb or y <= eb or x + bw >= w - eb or y + bh >= h - eb) \
                and tp_zone < 0.3:
            conf *= 0.35
        if area_frac > 0.10:
            conf *= 0.6
        elif area_frac > 0.04:
            conf *= 0.85
        if clipped:
            conf = min(conf, 0.60)
        conf = min(conf, 0.97)

        if conf >= CONF_REPORT:
            regions.append({
                "confidence": round(conf, 3),
                "bbox_px": [x, y, x + bw, y + bh],
                "features": {
                    "mode": "mottle" if f_mottle > f_bright else "bright",
                    "brightness_z": round(bright_z, 2),
                    "delta_dn": round(delta, 2),
                    "delta_vs_paper_dn": round(med_in - paper_lum, 2),
                    "grain_ratio": round(grain_in / max(paper_grain, 0.5), 2)
                    if grain_ok else None,
                    "grain_suppression": round(supp, 3) if supp is not None else None,
                    "blue_shift": round(db, 2) if db is not None else None,
                    "text_context": round(tp, 3),
                    "extent": round(area / float(bw * bh), 3),
                    "edge_z": round(edge_z, 2),
                },
            })

    regions.sort(key=lambda r: -r["confidence"])
    regions = regions[:MAX_REGIONS]

    # Soft-OR with rank decay: the strongest regions drive the page score; a
    # pile of mediocre regions must not compound into near-certainty.
    prob = 1.0
    for rank, r in enumerate(rr for rr in regions if rr["confidence"] >= CONF_PAGE):
        prob *= (1.0 - r["confidence"] * (0.7 ** rank))
    prob = min(1.0 - prob, 0.98)  # a heuristic never gets to claim certainty

    return {
        "probability": round(prob, 3),
        "indeterminate": False,
        "regions": regions,
        "warnings": sorted(set(warnings)),
        "paper": {
            "luminance": round(paper_lum, 1), "sigma": round(sigma, 2),
            "grain": round(paper_grain, 2), "color_scan": bool(color_ok),
        },
        "size_px": [w, h],
    }


# ----------------------------------------------------------------- rendering --
def render_pdf_pages(path: Path, dpi: int):
    """Yield (page_index, rgb_array, effective_dpi, page_rect_pts)."""
    doc = fitz.open(str(path))
    if doc.needs_pass:
        doc.close()
        raise RuntimeError("password-protected PDF")
    try:
        for idx in range(doc.page_count):
            page = doc.load_page(idx)
            eff_dpi = dpi
            est = (page.rect.width / 72 * dpi) * (page.rect.height / 72 * dpi)
            if est > 40e6:  # keep memory sane on posters/plans
                eff_dpi = max(96, int(dpi * math.sqrt(40e6 / est)))
            pix = page.get_pixmap(dpi=eff_dpi, colorspace=fitz.csRGB, alpha=False)
            rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
            yield idx, rgb.copy(), eff_dpi, (page.rect.width, page.rect.height)
    finally:
        doc.close()


def load_image(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)  # unicode-path safe on Windows
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("could not decode image")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


# ---------------------------------------------------------------- annotation --
def annotate(rgb: np.ndarray, regions: list[dict], dpi: int) -> np.ndarray:
    img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    th = max(2, int(round(dpi / 90)))
    fs = max(0.6, dpi / 300)
    for r in regions:
        c = r["confidence"]
        color = (0, 0, 255) if c >= 0.6 else (0, 140, 255) if c >= 0.35 else (0, 200, 255)
        x0, y0, x1, y1 = r["bbox_px"]
        g = 4
        x0, y0 = max(0, x0 - g), max(0, y0 - g)
        x1, y1 = min(img.shape[1] - 1, x1 + g), min(img.shape[0] - 1, y1 + g)
        cv2.rectangle(img, (x0, y0), (x1, y1), color, th)
        label = f"whitener {c:.2f}"
        (tw, tht), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, 2)
        ly = y0 - 10 if y0 - tht - 16 > 0 else min(img.shape[0] - 1, y1 + tht + 12)
        cv2.rectangle(img, (x0, ly - tht - 6), (x0 + tw + 10, ly + base), color, -1)
        cv2.putText(img, label, (x0 + 5, ly), cv2.FONT_HERSHEY_SIMPLEX, fs,
                    (255, 255, 255), 2, cv2.LINE_AA)
    return img


def imwrite(path: Path, bgr: np.ndarray, jpeg_q: int | None = None) -> None:
    ext = ".jpg" if jpeg_q else path.suffix
    params = [cv2.IMWRITE_JPEG_QUALITY, jpeg_q] if jpeg_q else []
    ok, buf = cv2.imencode(ext, bgr, params)
    if not ok:
        raise RuntimeError(f"encode failed for {path}")
    path.write_bytes(buf.tobytes())


# ------------------------------------------------------------------ pipeline --
def process_file(path: Path, out_root: Path, dpi: int, annotate_all: bool) -> dict:
    out_dir = out_root / path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    pages_out = []
    annotated_pngs = []
    annotated_bgr_pages = []   # (bgr, page_rect_pts) for the rebuilt PDF
    any_regions = False

    if path.suffix.lower() in IMAGE_SUFFIXES:
        rgb = load_image(path)
        it = [(0, rgb, dpi, (rgb.shape[1] * 72.0 / dpi, rgb.shape[0] * 72.0 / dpi))]
    else:
        if fitz is None:
            raise RuntimeError("PyMuPDF (pip install pymupdf) is required for PDFs")
        it = render_pdf_pages(path, dpi)

    for idx, rgb, eff_dpi, rect_pts in it:
        res = analyze_page(rgb, eff_dpi)
        res["page"] = idx + 1
        res["dpi"] = eff_dpi
        sx = rect_pts[0] / res["size_px"][0]
        sy = rect_pts[1] / res["size_px"][1]
        for r in res["regions"]:
            x0, y0, x1, y1 = r["bbox_px"]
            r["bbox_pdf_pts"] = [round(x0 * sx, 1), round(y0 * sy, 1),
                                 round(x1 * sx, 1), round(y1 * sy, 1)]
        pages_out.append(res)

        drawn = annotate(rgb, res["regions"], eff_dpi)
        annotated_bgr_pages.append((drawn, rect_pts))
        if res["regions"]:
            any_regions = True
        if res["regions"] or annotate_all:
            png = out_dir / f"page_{idx + 1:03d}_annotated.png"
            imwrite(png, drawn)
            annotated_pngs.append(str(png))
        print(f"  page {idx + 1}: probability {res['probability']:.2f}"
              f"  ({len(res['regions'])} region(s)"
              f"{', indeterminate' if res.get('indeterminate') else ''})",
              flush=True)

    doc_prob = 1.0
    for p in pages_out:
        doc_prob *= (1.0 - p["probability"])
    doc_prob = round(min(1.0 - doc_prob, 0.98), 3)

    annotated_pdf = None
    if (any_regions or annotate_all) and fitz is not None:
        annotated_pdf = out_dir / f"{path.stem}_annotated.pdf"
        newdoc = fitz.open()
        for bgr, (wpt, hpt) in annotated_bgr_pages:
            ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
            page = newdoc.new_page(width=wpt, height=hpt)
            page.insert_image(page.rect, stream=buf.tobytes())
        newdoc.save(str(annotated_pdf))
        newdoc.close()

    report = {
        "tool": "whitener-detect", "version": VERSION,
        "file": str(path), "requested_dpi": dpi,
        "document_probability": doc_prob,
        "max_page_probability": max((p["probability"] for p in pages_out), default=0.0),
        "pages": pages_out,
        "outputs": {
            "annotated_pdf": str(annotated_pdf) if annotated_pdf else None,
            "annotated_pages": annotated_pngs,
        },
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="whitener_detect",
        description="Estimate the probability that a scanned PDF has correction "
                    "fluid (white-out) on it, and box the suspect areas.")
    ap.add_argument("inputs", nargs="+", help="scanned PDF(s) or page image(s)")
    ap.add_argument("--dpi", type=int, default=200,
                    help="render resolution for analysis (default 200)")
    ap.add_argument("--out", default="whitener_out",
                    help="output directory (default ./whitener_out)")
    ap.add_argument("--annotate-all", action="store_true",
                    help="write annotated images/PDF even for clean pages")
    ap.add_argument("--json", action="store_true",
                    help="print the full JSON report(s) to stdout")
    ap.add_argument("--fail-over", type=float, default=None, metavar="P",
                    help="exit with code 3 if any document probability >= P")
    ap.add_argument("--version", action="version", version=VERSION)
    args = ap.parse_args(argv)

    out_root = Path(args.out)
    reports, had_error = [], False
    for raw in args.inputs:
        path = Path(raw)
        print(f"{path.name}:", flush=True)
        if not path.exists():
            print(f"  ERROR: not found", flush=True)
            had_error = True
            continue
        try:
            rep = process_file(path, out_root, args.dpi, args.annotate_all)
        except Exception as exc:
            print(f"  ERROR: {exc}", flush=True)
            had_error = True
            continue
        reports.append(rep)
        verdict = ("LIKELY WHITENER" if rep["document_probability"] >= 0.6 else
                   "possible whitener" if rep["document_probability"] >= 0.35 else
                   "no whitener evidence")
        print(f"  document probability: {rep['document_probability']:.2f}  [{verdict}]")
        if rep["outputs"]["annotated_pdf"]:
            print(f"  annotated pdf: {rep['outputs']['annotated_pdf']}")
        print(f"  report: {Path(out_root) / Path(rep['file']).stem / 'report.json'}")

    if args.json:
        print(json.dumps(reports if len(reports) != 1 else reports[0], indent=2))
    if had_error:
        return 2
    if args.fail_over is not None and any(
            r["document_probability"] >= args.fail_over for r in reports):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
