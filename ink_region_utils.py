"""
ink_region_utils.py — shared image-analysis helpers for the handwriting/digit
suspicion checks (stroke_thickness_check.py and overwriting_check.py).

This module does NOT make any pass/fail decision. It only:

  1. Renders each PDF page to a grayscale image (via PyMuPDF, the same
     dependency the font checks already use).
  2. Isolates dark ink (adaptive threshold + morphological opening for noise),
     then strips out long table/rule lines and LARGE dense graphic blocks
     (QR codes, barcodes, seals, photos, halftone-shaded scan areas).
  3. Finds connected components that plausibly represent handwritten characters
     or digit groups, discarding things the caller was explicitly asked NOT to
     flag: tiny dots/noise, stamps/seals/large graphics, table/rule lines and
     full-width printed headings.
  4. Computes, per component, every signal both feature modules need — most
     importantly a stroke-width estimate (distance transform on the skeleton),
     so overwriting_check can reuse the exact stroke-thickness output rather
     than recomputing it.
  5. Compares each glyph against the DOCUMENT'S printed-body norm rather than
     just its immediate line neighbours. Boldness is measured size-invariantly
     as stroke_width / glyph_height, and the reference is the median boldness of
     all body-sized glyphs on the page. This is deliberate: tampered field
     values (e.g. an inserted name/date in a heavier font) are usually a whole
     run of uniformly-thick characters, so a line-local comparison would cancel
     them out (every neighbour is equally thick → ratio ≈ 1.0). Comparing to the
     printed-body norm instead makes the inserted run stand out. Heading-sized
     glyphs are excluded from flagging so genuinely-bold headings don't trip it.

All thresholds are expressed at a 200-DPI reference and scaled by the actual
render DPI, so results are stable regardless of the DPI the caller picks.
"""

from pathlib import Path

import cv2
import numpy as np

import fitz  # PyMuPDF — already a project dependency


# ── Tunable geometry constants (defined at REFERENCE_DPI, scaled at runtime) ───

REFERENCE_DPI = 200

# Component size gates (reject noise on the small side, graphics on the big side)
MIN_HEIGHT_PX = 10          # smaller → dot / speckle / noise
MIN_WIDTH_PX = 3
MIN_AREA_PX = 40            # ink-pixel floor for a real character
MAX_HEIGHT_PX = 100         # taller → stamp / logo / heading block
MAX_WIDTH_PX = 170          # a single glyph/digit-group this wide is a graphic/word
MAX_AREA_FRAC = 0.10        # bbox bigger than this fraction of the page → graphic
MAX_WIDTH_FRAC = 0.55       # wider than this fraction of the page → rule/heading
LINE_ASPECT_RATIO = 12.0    # w/h or h/w beyond this → a table/underline rule
# A blob large in BOTH dimensions is an emblem/seal/photo, not a character.
BIG_BLOB_W_PX = 120
BIG_BLOB_H_PX = 70

# Line grouping tolerance: components whose y-centres fall within this multiple
# of the local median glyph height are treated as the same text line.
LINE_Y_TOLERANCE = 0.6


def _scale(px_value, dpi):
    """Scale a REFERENCE_DPI pixel constant to the actual render DPI."""
    return px_value * (dpi / REFERENCE_DPI)


# ── Page rendering ────────────────────────────────────────────────────────────

def render_page_gray(page, dpi):
    """Render a PyMuPDF page to a grayscale uint8 numpy array (H, W)."""
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    return img


def binarize_ink(gray, dpi=REFERENCE_DPI):
    """
    Return a binary mask where ink == 255, background == 0.

    Uses ADAPTIVE (local) thresholding rather than a single global Otsu cut.
    A global threshold breaks on phone-photo pages: when a form is photographed
    on a dark desk, the histogram is dominated by the bright-paper/dark-surround
    split, so Otsu lands far above the paper/ink split and the whole page
    collapses into one giant blob (no glyphs survive). A Gaussian adaptive
    threshold compares each pixel to its local neighbourhood instead, so it
    isolates ink on both flatbed scans and unevenly-lit photographs, and a
    uniformly dark background stays background (it matches its own local mean).

    A small morphological opening then removes isolated speckle without eroding
    genuine strokes.
    """
    # Block size must be odd and scale with DPI (~51px at the 200-DPI reference).
    block = int(_scale(51, dpi))
    if block % 2 == 0:
        block += 1
    block = max(block, 3)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, block, 10,
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
    return opened


def remove_table_lines(binary, dpi=REFERENCE_DPI):
    """
    Erase long printed rules (table gridlines, underlines) from an ink mask.

    Detects near-continuous horizontal and vertical runs with long 1-D
    morphological openings, then subtracts them — including their intersections.
    Without this, the small chunk of ink where two rules CROSS survives as a
    compact, high-ink-density, blob-like component and gets flagged as a
    suspicious "glyph" (a false positive), even though the whole-rule size gates
    in _is_candidate correctly drop the lines themselves.

    Handwriting that merely touches a line is only nicked where it crosses, so
    genuine glyphs remain intact after a final speckle-removing opening.
    """
    h_len = int(_scale(40, dpi))
    v_len = int(_scale(40, dpi))
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(h_len, 1), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(v_len, 1)))
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel, iterations=1)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel, iterations=1)
    lines = cv2.bitwise_or(horizontal, vertical)
    # Dilate slightly so a junction is fully covered, not just its centre line.
    lines = cv2.dilate(lines, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    cleaned = cv2.bitwise_and(binary, cv2.bitwise_not(lines))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
    return cv2.morphologyEx(cleaned, cv2.MORPH_OPEN, kernel, iterations=1)


# Local ink-coverage above this fraction marks a "dense" region (QR/barcode/
# seal/photo/halftone-shaded scan block) rather than sparse handwriting/text.
DENSE_COVERAGE_FRAC = 0.42
DENSE_WINDOW_PX = 34
# A dense patch is only erased if its connected extent is at least this large.
# This is the crucial guard: an individual BOLD glyph (or an overwritten, doubly
# inked digit) is locally dense too, but tiny — erasing it would delete the very
# thing we're trying to detect. QR/barcode/seal/photo blocks are far larger, so
# gating on extent removes the graphics while preserving bold/tampered glyphs.
MIN_DENSE_AREA_PX = 4000  # ≈ a 63×63-px block at the 200-DPI reference


def remove_dense_regions(binary, dpi=REFERENCE_DPI):
    """
    Erase LARGE dense graphic blocks — QR codes, barcodes, seals/stamps, pasted
    photos and halftone-shaded scan areas — from an ink mask.

    These structures survive the per-component size gates because they shatter
    into many small compact blobs (QR/barcode modules, seal stipple), each of
    which individually looks glyph-sized and near-solid → a flood of false
    positives (see the fr6/fr7 shaded-corner and barcode cases). What sets them
    apart from real writing is LOCAL DENSITY over a LARGE EXTENT: sparse
    text/handwriting covers a small fraction of any window, and even a bold glyph
    is dense only over a tiny area. We build a coverage map, then remove only
    dense regions whose connected extent exceeds MIN_DENSE_AREA_PX — so graphics
    go but individual bold/overwritten glyphs are preserved.
    """
    win = max(int(_scale(DENSE_WINDOW_PX, dpi)), 3)
    coverage = cv2.boxFilter((binary > 0).astype(np.float32), -1, (win, win), normalize=True)
    dense = (coverage > DENSE_COVERAGE_FRAC).astype(np.uint8)

    # Keep only dense blobs large enough to be a graphic block, not a bold glyph.
    min_area = _scale(MIN_DENSE_AREA_PX, dpi) * (dpi / REFERENCE_DPI)  # area ∝ dpi²
    n, lbl, st, _ = cv2.connectedComponentsWithStats(dense, connectivity=8)
    keep = np.zeros_like(dense)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] >= min_area:
            keep[lbl == i] = 255

    # Grow the mask so the sparse fringe of a dense block is removed too.
    keep = cv2.dilate(keep, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    return cv2.bitwise_and(binary, cv2.bitwise_not(keep))


# ── Stroke-width estimation ───────────────────────────────────────────────────

def estimate_stroke_width(component_mask):
    """
    Estimate average stroke width (in pixels) for a single component.

    Primary method: average stroke width ≈ 2 × mean distance-transform value on
    the skeleton pixels. The distance transform gives, at each ink pixel, the
    distance to the nearest background pixel; on the medial axis (skeleton) that
    distance is the stroke's half-width, so doubling the skeleton mean recovers
    the full width.

    Fallbacks, in order, if skeletonization is unavailable or degenerate:
      - area / skeleton-length  (skeleton length ≈ number of skeleton pixels)
      - 2 × area / perimeter    (for a long thin stroke this reduces to width)
    """
    mask = (component_mask > 0).astype(np.uint8)
    if mask.sum() == 0:
        return 0.0

    # Pad with a 1-px background border. cv2.distanceTransform measures the
    # distance to the nearest background pixel, so a blob that fills its entire
    # bbox (touching every edge) would otherwise have NO background to measure
    # against and return garbage/inf. The border guarantees a valid transform.
    mask = np.pad(mask, 1, mode="constant", constant_values=0)

    dist = cv2.distanceTransform(mask * 255, cv2.DIST_L2, 5)

    skel = None
    try:
        from skimage.morphology import skeletonize
        skel = skeletonize(mask.astype(bool))
    except Exception:
        skel = None

    if skel is not None and skel.any():
        skel_vals = dist[skel]
        if skel_vals.size > 0 and skel_vals.mean() > 0:
            return float(2.0 * skel_vals.mean())
        # Degenerate distances → fall through to area/length below.
        skel_len = int(skel.sum())
        if skel_len > 0:
            return float(mask.sum() / skel_len)

    # Fallback: area / perimeter based width estimate.
    contours, _ = cv2.findContours(mask * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    perimeter = sum(cv2.arcLength(c, True) for c in contours)
    if perimeter > 0:
        return float(2.0 * mask.sum() / perimeter)
    return 0.0


# ── Per-component metric extraction ───────────────────────────────────────────

def _component_metrics(mask, gray_roi):
    """
    Compute geometry + appearance signals for one component.

    `mask`     — binary (0/255) ink mask of the component, cropped to its bbox.
    `gray_roi` — grayscale pixels for the same bbox (for dark-blob detection).

    Returns a dict of raw (not-yet-compared) metrics.
    """
    ink_pixels = int((mask > 0).sum())
    h, w = mask.shape
    bbox_area = int(w * h)

    stroke_width = estimate_stroke_width(mask)
    ink_density = ink_pixels / bbox_area if bbox_area else 0.0

    # Dark-blob score: size of the largest sub-region that is markedly darker
    # than the glyph's OWN typical ink, as a fraction of the glyph's ink.
    # Absolute darkness is useless here (all printed ink is dark); what betrays
    # a retouched/double-inked stroke is a concentrated patch darker than the
    # rest of the SAME character. Uniform glyphs → scattered noise → low score.
    ink_bool = mask > 0
    dark_blob_score = 0.0
    if ink_pixels >= 12:
        ink_vals = gray_roi[ink_bool].astype(np.float32)
        mean_ink = float(ink_vals.mean())
        std_ink = float(ink_vals.std())
        # "Much darker" = at least ~1 std below the glyph mean, and never a
        # threshold so shallow that antialiasing noise qualifies.
        dark_thresh = mean_ink - max(std_ink, 6.0)
        dark_mask = ((gray_roi.astype(np.float32) < dark_thresh) & ink_bool).astype(np.uint8)
        if dark_mask.any():
            n_blobs, _, blob_stats, _ = cv2.connectedComponentsWithStats(dark_mask, connectivity=8)
            if n_blobs > 1:
                largest = int(blob_stats[1:, cv2.CC_STAT_AREA].max())
                dark_blob_score = largest / ink_pixels

    # Contour complexity + solidity from the largest external contour.
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    solidity = 1.0
    complexity = 1.0
    if contours:
        cnt = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(cnt)
        perimeter = cv2.arcLength(cnt, True)
        hull = cv2.convexHull(cnt)
        hull_area = cv2.contourArea(hull)
        if hull_area > 0:
            solidity = float(area / hull_area)
        if area > 0:
            # Isoperimetric complexity: 1.0 for a circle, higher for ragged /
            # self-intersecting outlines typical of overwritten characters.
            complexity = float((perimeter * perimeter) / (4.0 * np.pi * area))

    return {
        "ink_pixels": ink_pixels,
        "bbox_area": bbox_area,
        "stroke_width": round(stroke_width, 3),
        "ink_density": round(ink_density, 4),
        "dark_blob_score": round(dark_blob_score, 4),
        "solidity": round(solidity, 4),
        "contour_complexity_score": round(complexity, 4),
    }


def _is_candidate(x, y, w, h, area, page_w, page_h, dpi):
    """
    Decide whether a connected component looks like a handwritten/digit glyph
    worth analysing — filtering out the categories we must NOT flag.
    """
    min_h = _scale(MIN_HEIGHT_PX, dpi)
    min_w = _scale(MIN_WIDTH_PX, dpi)
    min_area = _scale(MIN_AREA_PX, dpi)
    max_h = _scale(MAX_HEIGHT_PX, dpi)
    max_w = _scale(MAX_WIDTH_PX, dpi)

    # Tiny dots / speckle / noise.
    if h < min_h or w < min_w or area < min_area:
        return False

    # Stamps, seals, logos, photos, large graphic blocks.
    if h > max_h or w > max_w:
        return False
    if (w * h) > (MAX_AREA_FRAC * page_w * page_h):
        return False
    if w > _scale(BIG_BLOB_W_PX, dpi) and h > _scale(BIG_BLOB_H_PX, dpi):
        return False

    # Full-width printed headings / table rules / underlines.
    if w > MAX_WIDTH_FRAC * page_w:
        return False

    # Long thin rules (table lines, underlines, signature strokes).
    aspect = max(w / h, h / w) if h and w else 999
    if aspect > LINE_ASPECT_RATIO:
        return False

    return True


def _group_into_lines(components):
    """
    Group components into text lines by vertical proximity of their centres.

    Returns a list of lines, each a list of indices into `components`.
    """
    if not components:
        return []

    heights = [c["bbox"][3] - c["bbox"][1] for c in components]
    median_h = float(np.median(heights)) if heights else 0.0
    tol = max(median_h * LINE_Y_TOLERANCE, 1.0)

    order = sorted(range(len(components)), key=lambda i: _y_center(components[i]))
    lines = []
    current = [order[0]]
    current_y = _y_center(components[order[0]])

    for idx in order[1:]:
        yc = _y_center(components[idx])
        if abs(yc - current_y) <= tol:
            current.append(idx)
            # Running mean keeps the line anchor stable across the row.
            current_y = np.mean([_y_center(components[i]) for i in current])
        else:
            lines.append(current)
            current = [idx]
            current_y = yc
    lines.append(current)
    return lines


def _y_center(component):
    b = component["bbox"]
    return (b[1] + b[3]) / 2.0


# Height band (as a multiple of the page's median glyph height) that counts as
# ordinary "body / field" text. Glyphs outside it are headings/large marks and
# are excluded both from the norm and from being flagged.
FIELD_HEIGHT_LO = 0.55
FIELD_HEIGHT_HI = 1.7


def _attach_reference_ratios(components):
    """
    Attach each glyph's boldness/density/solidity RATIO against the document's
    printed-body norm, plus an `is_field_text` flag.

    Boldness is size-invariant: stroke_width / glyph_height. Printed body text
    has a stable boldness (thin strokes relative to letter height); an inserted
    or overwritten field rendered in a heavier font has a higher one. The norm
    is the median over body-sized glyphs, which are the dominant population, so
    a minority of tampered glyphs cannot drag it up to hide themselves.

    `local_average_stroke_width` is reported as the stroke width a *normal* glyph
    of this glyph's height would have (norm_boldness × height), so the schema's
    stroke_width_ratio = stroke_width / local_average_stroke_width still holds and
    stays meaningful across text sizes.
    """
    if not components:
        return

    heights = np.array([c["h"] for c in components], dtype=np.float32)
    median_h = float(np.median(heights)) if len(heights) else 0.0
    lo = FIELD_HEIGHT_LO * median_h
    hi = FIELD_HEIGHT_HI * median_h

    body = [c for c in components if lo <= c["h"] <= hi] or components

    def _median(items, key, default):
        vals = [f for f in (it[key] for it in items) if f > 1e-6]
        return float(np.median(vals)) if vals else default

    # Size-invariant boldness norm: median of (stroke_width / height).
    boldness = [c["stroke_width"] / c["h"] for c in body if c["h"] > 0 and c["stroke_width"] > 0]
    norm_boldness = float(np.median(boldness)) if boldness else 0.0
    norm_density = _median(body, "ink_density", 0.0)
    norm_solidity = _median(body, "solidity", 1.0)

    for c in components:
        c["is_field_text"] = bool(lo <= c["h"] <= hi)

        expected_sw = norm_boldness * c["h"] if norm_boldness > 0 else c["stroke_width"]
        c["local_average_stroke_width"] = round(expected_sw, 3)
        c["local_average_ink_density"] = round(norm_density, 4)
        c["local_average_solidity"] = round(norm_solidity, 4)

        c["stroke_width_ratio"] = round(
            c["stroke_width"] / expected_sw, 3) if expected_sw > 1e-6 else 0.0
        c["ink_density_ratio"] = round(
            c["ink_density"] / norm_density, 3) if norm_density > 1e-6 else 0.0
        # <1.0 means this glyph is more ragged/concave than the body norm, which
        # is what an overwrite adds — not just "a naturally holey letter".
        c["solidity_ratio"] = round(
            c["solidity"] / norm_solidity, 3) if norm_solidity > 1e-6 else 1.0


# ── Public entry point ────────────────────────────────────────────────────────

def extract_components(pdf_path, dpi=REFERENCE_DPI, debug_dir=None):
    """
    Render every page, detect handwriting/digit candidate components, and return
    a fully-populated metric bundle that both feature modules consume.

    Returns:
        {
          "pages_checked": int,
          "components": [
              {
                "page": int,
                "bbox": [x1, y1, x2, y2],           # page-image pixel coords
                "area": int,                         # bbox area in px
                "ink_pixels": int,
                "stroke_width": float,
                "local_average_stroke_width": float,
                "stroke_width_ratio": float,
                "ink_density": float,
                "local_average_ink_density": float,
                "ink_density_ratio": float,
                "dark_blob_score": float,
                "solidity": float,
                "contour_complexity_score": float,
              }, ...
          ],
        }

    `debug_dir`, if given (opt-in only), receives an annotated PNG per page.
    Nothing is written unless the caller explicitly passes a directory.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(str(pdf_path))
    all_components = []
    pages_checked = 0

    debug_path = None
    if debug_dir is not None:
        debug_path = Path(debug_dir)
        debug_path.mkdir(parents=True, exist_ok=True)

    try:
        for page_index in range(len(doc)):
            page = doc[page_index]
            gray = render_page_gray(page, dpi)
            page_h, page_w = gray.shape
            pages_checked += 1

            binary = binarize_ink(gray, dpi=dpi)
            binary = remove_table_lines(binary, dpi=dpi)
            binary = remove_dense_regions(binary, dpi=dpi)
            num, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)

            page_components = []
            for label in range(1, num):  # 0 is background
                x = int(stats[label, cv2.CC_STAT_LEFT])
                y = int(stats[label, cv2.CC_STAT_TOP])
                w = int(stats[label, cv2.CC_STAT_WIDTH])
                h = int(stats[label, cv2.CC_STAT_HEIGHT])
                area = int(stats[label, cv2.CC_STAT_AREA])  # ink pixel count

                if not _is_candidate(x, y, w, h, area, page_w, page_h, dpi):
                    continue

                comp_mask = (labels[y:y + h, x:x + w] == label).astype(np.uint8) * 255
                gray_roi = gray[y:y + h, x:x + w]

                metrics = _component_metrics(comp_mask, gray_roi)
                page_components.append({
                    "page": page_index + 1,
                    "bbox": [x, y, x + w, y + h],
                    "area": int(w * h),
                    "h": h,
                    **metrics,
                })

            _attach_reference_ratios(page_components)

            if debug_path is not None:
                _write_debug_image(debug_path, pdf_path, page_index + 1, gray, page_components)

            all_components.extend(page_components)
    finally:
        doc.close()

    return {"pages_checked": pages_checked, "components": all_components}


def _write_debug_image(debug_path, pdf_path, page_number, gray, components):
    """Opt-in only: save an annotated page image with candidate bboxes drawn."""
    canvas = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for c in components:
        x1, y1, x2, y2 = c["bbox"]
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 0, 255), 1)
    out = debug_path / f"{pdf_path.stem}_page{page_number}.png"
    cv2.imwrite(str(out), canvas)
