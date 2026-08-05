"""Render the CAG Parakh "document verification flight" to a scrub-ready video.

The landing page scrubs this clip with scroll, so the camera move has to be
baked in. Everything is drawn per frame in page coordinates and transformed by
the camera, rather than cropped out of one big raster: a zoom then re-renders
the type at its final size instead of magnifying pixels, which is the whole
reason the roll number is still legible at 4x.

Two layers come out of this file and only one of them is video. Overlays that
are locked to a place on the sheet (corner brackets, the scan sweep, evidence
markers) are baked, because they have to move with the camera. The prose that
explains each module is NOT baked -- app/welcome draws it as HTML on the
timeline this script emits, so the copy stays selectable and translatable.

    python tools/flight/render_flight.py            # frames + encode
    python tools/flight/render_flight.py --probe    # 6 stills, for iterating

Reads  frontend/app/welcome/flight-stops.json
Writes frontend/public/flight/verification-flight.mp4
       frontend/public/flight/poster.jpg
       frontend/app/welcome/flight-timeline.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
STOPS_FILE = ROOT / "frontend" / "app" / "welcome" / "flight-stops.json"
TIMELINE_FILE = ROOT / "frontend" / "app" / "welcome" / "flight-timeline.json"
OUT_DIR = ROOT / "frontend" / "public" / "flight"
FRAME_DIR = ROOT / "tmp" / "flight-frames"

# Supersample factor. PIL antialiases glyphs but not strokes, so every
# rectangle and rule would crawl during the zoom without this.
SS = 2

# --- palette -----------------------------------------------------------------
# Mirrors the pen-design tokens the workspace itself runs on
# (frontend/app/styles/pen-design.css), so the film reads as the product rather
# than as an advert sitting next to it. Blue is action, green is cleared, red
# is a finding -- a passed check must never take the accent, or it reads as a
# flag.
# A neutral grey bed rather than the app's blue-tinted surface: the landing page
# sits on pure white, and a blue-grey platen next to white reads as a colour
# cast rather than as a deliberate surface.
PLATEN = (233, 233, 230)
PLATEN_HI = (221, 221, 217)
PAPER = (255, 255, 255)
PAPER_EDGE = (203, 211, 222)  # --pen-border-strong
INK = (20, 26, 36)  # --pen-ink
INK_SOFT = (90, 100, 114)  # --pen-text-2
INK_FAINT = (139, 149, 165)  # --pen-text-3
RULE = (203, 211, 222)
ACCENT = (47, 91, 234)  # --pen-blue
GOOD = (21, 128, 61)  # --pen-clean
DANGER = (211, 47, 47)  # --pen-flag
CAUTION = (180, 83, 9)  # --pen-caution
NEUTRAL = (108, 122, 145)  # metadata: reported, never scored
PEN = (30, 46, 104)
CHROME = (255, 255, 255)  # HUD plates match the workspace's white cards
CHROME_EDGE = (227, 232, 239)  # --pen-border

# A stop's tone is the status its detector would really return for this
# specimen. `info` is metadata's own category -- it is reported and never
# scored -- and `inconclusive` is a test that could not run, which must never
# be dressed up as a pass.
TONE = {
    "neutral": ACCENT,
    "clear": GOOD,
    "flag": DANGER,
    "info": NEUTRAL,
    "inconclusive": CAUTION,
}
STATUS_COLOUR = {
    "pass": GOOD,
    "warn": CAUTION,
    "fail": DANGER,
    "info": NEUTRAL,
    "inconclusive": CAUTION,
}

FONT_DIR = Path("C:/Windows/Fonts")
FONT_FILES = {
    "sans": "arial.ttf",
    "sans_bold": "arialbd.ttf",
    "sans_narrow": "ARIALN.TTF",
    "serif": "times.ttf",
    "serif_bold": "timesbd.ttf",
    "serif_italic": "timesi.ttf",
    "mono": "consola.ttf",
    "mono_bold": "consolab.ttf",
}
_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(kind: str, px: float) -> ImageFont.FreeTypeFont:
    """Truetype at a pixel size, cached. Sizes are continuous during a zoom,
    so they get rounded -- that quantisation is invisible next to the motion."""
    size = max(1, int(round(px)))
    key = (kind, size)
    hit = _font_cache.get(key)
    if hit is None:
        hit = ImageFont.truetype(str(FONT_DIR / FONT_FILES[kind]), size)
        _font_cache[key] = hit
    return hit


# --- camera ------------------------------------------------------------------


@dataclass(frozen=True)
class Cam:
    """Scale plus the page-space point pinned to the centre of the frame."""

    s: float
    cx: float
    cy: float

    def pt(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.cx) * self.s + HALF_W, (y - self.cy) * self.s + HALF_H)

    def box(self, x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        x0, y0 = self.pt(x, y)
        return (x0, y0, x0 + w * self.s, y0 + h * self.s)


def ease_in_out(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 3) / 2


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def smoothstep(v: float, lo: float, hi: float) -> float:
    t = clamp01((v - lo) / (hi - lo)) if hi != lo else (1.0 if v >= hi else 0.0)
    return t * t * (3 - 2 * t)


def cam_for(stop: dict, cfg: dict) -> Cam:
    """Camera for a stop. Frames `frame` if the stop names one, else `rect`;
    a stop with neither frames the whole sheet.

    The two are separate on purpose. `rect` is the evidence region and the
    brackets hug it exactly; `frame` is what the lens sees. The photograph is
    portrait and the signature band is a long letterbox, so fitting the frame
    to the evidence region alone would park the camera in front of a screenful
    of blank paper. Whatever is chosen is then grown to the frame's own aspect
    about its centre, so the shot always fills.
    """
    page, video, camera = cfg["page"], cfg["video"], cfg["camera"]
    aspect = video["w"] / video["h"]
    box = stop.get("frame") or stop.get("rect")
    if box is None:
        s = min(video["w"] / (page["w"] + 150), video["h"] / (page["h"] + 90))
        return Cam(s, page["w"] / 2, page["h"] / 2)
    pad = camera["pad"]
    w, h = box["w"] + pad * 2, box["h"] + pad * 2
    if w / h < aspect:
        w = h * aspect
    else:
        h = w / aspect
    s = video["w"] / w
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2

    # Keep the sheet in the shot. A region near an edge -- the photograph is
    # the obvious one -- centres the lens so close to the margin that a third
    # of the frame fills with bare platen. Slide back until the paper covers
    # the frame, tolerating a thin margin so the sheet's edge can still read.
    margin = 34.0
    if w <= page["w"] + margin * 2:
        cx = min(max(cx, w / 2 - margin), page["w"] - w / 2 + margin)
    if h <= page["h"] + margin * 2:
        cy = min(max(cy, h / 2 - margin), page["h"] - h / 2 + margin)
    return Cam(s, cx, cy)


def build_timeline(cfg: dict) -> list[dict]:
    """Alternating dwell/move schedule, normalised to the clip duration.

    A stop's `weight` buys dwell time only. Travel between stops is constant,
    so the camera reads as one instrument moving at a steady hand rather than
    racing through the cheap sections.
    """
    stops = cfg["stops"]
    move_unit = 1.0
    spans: list[tuple[str, int, float]] = []
    for i, stop in enumerate(stops):
        spans.append(("dwell", i, stop["weight"] * 1.35))
        if i < len(stops) - 1:
            spans.append(("move", i, move_unit))

    total = sum(span[2] for span in spans)
    duration = cfg["video"]["duration"]
    timeline, cursor = [], 0.0
    for kind, index, raw in spans:
        length = raw / total * duration
        timeline.append({"kind": kind, "index": index, "start": cursor, "end": cursor + length})
        cursor += length
    return timeline


def sample(timeline: list[dict], cfg: dict, t: float) -> tuple[Cam, int, float, float]:
    """Camera at time t, plus which stop owns the frame, how far through its
    dwell it is, and how settled it is (1.0 = parked, 0.0 = mid-flight)."""
    cams = [cam_for(stop, cfg) for stop in cfg["stops"]]
    for span in timeline:
        if t < span["end"] or span is timeline[-1]:
            i = span["index"]
            local = clamp01((t - span["start"]) / max(1e-6, span["end"] - span["start"]))
            if span["kind"] == "dwell":
                return cams[i], i, local, 1.0
            e = ease_in_out(local)
            a, b = cams[i], cams[i + 1]
            # Scale interpolates geometrically: a linear ramp from 0.9x to 6x
            # spends most of its time already zoomed in and feels like a lurch.
            cam = Cam(
                math.exp(lerp(math.log(a.s), math.log(b.s), e)),
                lerp(a.cx, b.cx, e),
                lerp(a.cy, b.cy, e),
            )
            # Hand the frame to whichever stop is nearer, so overlays fade with
            # the destination they belong to.
            owner = i if e < 0.5 else i + 1
            return cam, owner, 0.0, 0.0
    raise AssertionError("timeline exhausted")


# --- the document ------------------------------------------------------------
# Everything below is authored in page space (794 x 1123, A4 proportions) and
# only ever drawn through a Cam, so the same code serves the wide establishing
# shot and the 6x push into the photograph.


def text(d: ImageDraw.ImageDraw, cam: Cam, x: float, y: float, s: str, kind: str,
         px: float, fill, anchor: str = "la", spacing: float = 0.0) -> None:
    """Draw page-space text. Skipped once it is too small to read, which keeps
    the wide shots from turning into grey mush."""
    size = px * cam.s
    if size < 3.2:
        return
    sx, sy = cam.pt(x, y)
    f = font(kind, size)
    if spacing:
        # PIL has no letter-spacing, so tracked labels are stepped by hand.
        gap = spacing * cam.s
        cursor = sx
        for ch in s:
            d.text((cursor, sy), ch, font=f, fill=fill, anchor=anchor)
            cursor += d.textlength(ch, font=f) + gap
        return
    d.text((sx, sy), s, font=f, fill=fill, anchor=anchor)


def line(d: ImageDraw.ImageDraw, cam: Cam, x0, y0, x1, y1, fill, width=1.0) -> None:
    p0, p1 = cam.pt(x0, y0), cam.pt(x1, y1)
    d.line([p0, p1], fill=fill, width=max(1, int(round(width * cam.s))))


def rect(d: ImageDraw.ImageDraw, cam: Cam, x, y, w, h, outline=None, fill=None,
         width=1.0, radius=0.0) -> None:
    box = cam.box(x, y, w, h)
    stroke = max(1, int(round(width * cam.s))) if outline else 0
    if radius:
        d.rounded_rectangle(box, radius=radius * cam.s, outline=outline, fill=fill, width=stroke)
    else:
        d.rectangle(box, outline=outline, fill=fill, width=stroke)


def draw_page(d: ImageDraw.ImageDraw, cam: Cam) -> None:
    """The sheet itself: soft shadow, paper, then a hairline edge."""
    # Restrained: the workspace defines a card as a hairline border, not a drop
    # shadow, so the sheet gets just enough lift to separate from the platen.
    for i in range(8, 0, -1):
        spread = i * 2.6
        d.rounded_rectangle(
            cam.box(-spread, -spread + 4, 794 + spread * 2, 1123 + spread * 2),
            radius=6 * cam.s,
            fill=(31, 45, 68, 8),
        )
    rect(d, cam, 0, 0, 794, 1123, fill=PAPER)
    rect(d, cam, 0, 0, 794, 1123, outline=PAPER_EDGE, width=1.0)


def draw_barcode(d: ImageDraw.ImageDraw, cam: Cam, x, y, w, h) -> None:
    rng = random.Random(8841)
    cursor = x + 6
    while cursor < x + w - 8:
        bar = rng.choice([1.2, 1.2, 2.0, 3.2])
        if rng.random() > 0.32:
            rect(d, cam, cursor, y, bar, h, fill=INK)
        cursor += bar + rng.choice([1.2, 1.8, 2.6])


def draw_qr(d: ImageDraw.ImageDraw, cam: Cam, x, y, size) -> None:
    """A 21-module QR-style matrix: three finder patterns, timing rows, and a
    deterministic data field. The qr_presence detector is specifically about
    decoding a QR code, so the specimen has to actually carry one rather than
    relying on the linear barcode beside it."""
    modules = 21
    m = size / modules
    rng = random.Random(412)

    def finder(fx, fy):
        rect(d, cam, x + fx * m, y + fy * m, m * 7, m * 7, fill=INK)
        rect(d, cam, x + (fx + 1) * m, y + (fy + 1) * m, m * 5, m * 5, fill=PAPER)
        rect(d, cam, x + (fx + 2) * m, y + (fy + 2) * m, m * 3, m * 3, fill=INK)

    def reserved(cx, cy):
        # The three finder patterns plus their one-module separators.
        return ((cx < 8 and cy < 8) or (cx > modules - 9 and cy < 8)
                or (cx < 8 and cy > modules - 9))

    for cy in range(modules):
        for cx in range(modules):
            if reserved(cx, cy):
                continue
            if cx == 6 or cy == 6:  # timing patterns alternate
                if (cx + cy) % 2 == 0:
                    rect(d, cam, x + cx * m, y + cy * m, m, m, fill=INK)
                continue
            if rng.random() < 0.47:
                rect(d, cam, x + cx * m, y + cy * m, m, m, fill=INK)

    finder(0, 0)
    finder(modules - 7, 0)
    finder(0, modules - 7)


def draw_photograph(d: ImageDraw.ImageDraw, cam: Cam, x, y, w, h) -> None:
    """A deliberately abstract specimen portrait -- a tonal bust, not a
    likeness. This is a synthetic document; it should not carry a real face."""
    rect(d, cam, x, y, w, h, fill=(226, 226, 224))
    bands = 13
    for i in range(bands):
        shade = int(lerp(214, 186, i / (bands - 1)))
        rect(d, cam, x, y + h * i / bands, w, h / bands + 0.6, fill=(shade, shade, shade - 2))
    # Shoulders and head are clipped to the box: an ellipse that runs past the
    # frame edge onto the paper stops reading as a pasted-on photograph.
    cx = x + w / 2
    box = cam.box(x, y, w, h)
    shoulder = cam.box(cx - w * 0.40, y + h * 0.60, w * 0.80, h * 0.44)
    d.ellipse([shoulder[0], shoulder[1], shoulder[2], min(shoulder[3], box[3])], fill=(138, 140, 148))
    d.ellipse(cam.box(cx - w * 0.20, y + h * 0.22, w * 0.40, h * 0.38), fill=(150, 152, 160))
    rect(d, cam, x, y, w, h, outline=(168, 168, 172), width=1.0)


def draw_signature(d: ImageDraw.ImageDraw, cam: Cam, x, y, w, h, seed: int) -> None:
    """A parametric scrawl. Same seed, same signature, every frame."""
    rng = random.Random(seed)
    a, b, c = rng.uniform(1.6, 2.4), rng.uniform(2.6, 3.6), rng.uniform(0.4, 0.9)
    pts = []
    steps = 150
    for i in range(steps + 1):
        t = i / steps
        px = x + w * t
        py = (y + h / 2
              + math.sin(t * math.pi * a) * h * 0.30
              + math.sin(t * math.pi * b + c) * h * 0.17
              - math.sin(t * math.pi) * h * 0.10)
        pts.append(cam.pt(px, py))
    d.line(pts, fill=PEN, width=max(1, int(round(1.7 * cam.s))), joint="curve")


def draw_seal(d: ImageDraw.ImageDraw, cam: Cam, cx, cy, r) -> None:
    ring = (108, 96, 156)
    d.ellipse(cam.box(cx - r, cy - r, r * 2, r * 2), outline=ring, width=max(1, int(round(2.0 * cam.s))))
    d.ellipse(cam.box(cx - r * 0.78, cy - r * 0.78, r * 1.56, r * 1.56), outline=ring, width=max(1, int(round(1.0 * cam.s))))
    text(d, cam, cx, cy - r * 0.34, "CAG", "serif_bold", 11, ring, anchor="mm")
    text(d, cam, cx, cy - r * 0.02, "EXAMINATION", "sans", 4.6, ring, anchor="mm")
    text(d, cam, cx, cy + r * 0.24, "CENTRE 0412", "sans", 4.6, ring, anchor="mm")
    text(d, cam, cx, cy + r * 0.52, "28 JUL 2026", "mono", 5.0, ring, anchor="mm")


MARKS = [
    ("Paper I", "General Studies & Aptitude", "200", "164"),
    ("Paper II", "Accountancy and Audit", "200", "171"),
    ("Paper III", "Public Finance", "150", "118"),
    ("Paper IV", "Statutory Audit Practice", "150", "137"),
    ("Paper V", "Case Analysis (Descriptive)", "100", "74"),
]

PARTICULARS = [
    ("Name of candidate", "Ananya Raghunathan"),
    ("Parent / guardian", "R. Raghunathan"),
    ("Examination centre", "Regional Audit Academy, Chennai — 0412"),
    ("Examination", "Assistant Audit Officer, Session 2026"),
    ("Date of birth", "14 / 08 / 2001"),
]


def draw_document(d: ImageDraw.ImageDraw, cam: Cam) -> None:
    M = 44

    # Masthead
    text(d, cam, 397, 42, "COMPTROLLER & AUDITOR GENERAL OF INDIA", "sans", 8.4,
         INK_SOFT, anchor="ma", spacing=1.4)
    text(d, cam, 397, 62, "Candidate Verification & Evaluation Sheet", "serif_bold", 21,
         INK, anchor="ma")
    text(d, cam, 397, 96, "Form CAG-P/17 (Rev. 2026) — to be completed by the invigilator",
         "serif_italic", 9, INK_SOFT, anchor="ma")
    line(d, cam, M, 128, 794 - M, 128, INK, 1.6)
    line(d, cam, M, 133, 794 - M, 133, INK, 0.6)

    # Identity block: barcode over the roll boxes
    text(d, cam, M, 148, "DOCUMENT SERIAL", "sans_bold", 6.4, INK_FAINT, spacing=0.9)
    draw_barcode(d, cam, M, 162, 262, 52)
    text(d, cam, M, 220, "8841 · CAG · 2026 · 0412", "mono", 8.2, INK_SOFT)

    # Verification QR, beside the serial. This is what qr_presence decodes;
    # the linear barcode above is a separate human-readable serial.
    text(d, cam, 360, 148, "VERIFICATION CODE", "sans_bold", 6.4, INK_FAINT, spacing=0.9)
    draw_qr(d, cam, 360, 162, 120)
    text(d, cam, 360, 292, "Scan to verify", "serif_italic", 6.4, INK_FAINT)

    text(d, cam, M, 246, "ROLL NUMBER", "sans_bold", 6.4, INK_FAINT, spacing=0.9)
    for i, digit in enumerate("24088173"):
        bx = M + i * 33
        rect(d, cam, bx, 260, 28, 36, outline=RULE, width=1.0)
        text(d, cam, bx + 14, 268, digit, "mono_bold", 17, INK, anchor="ma")

    # Photograph
    draw_photograph(d, cam, 596, 162, 154, 180)
    text(d, cam, 673, 350, "AFFIX RECENT PHOTOGRAPH", "sans", 5.0, INK_FAINT,
         anchor="ma", spacing=0.5)
    text(d, cam, 673, 362, "Attested by the invigilator", "serif_italic", 6.0,
         INK_FAINT, anchor="ma")

    # Particulars
    y = 392
    text(d, cam, M, y, "PARTICULARS OF CANDIDATE", "sans_bold", 7.0, INK_SOFT, spacing=1.1)
    y += 20
    for label, value in PARTICULARS:
        text(d, cam, M, y + 4, label, "sans", 8.0, INK_FAINT)
        text(d, cam, M + 168, y + 2, value, "serif_italic", 11.5, PEN)
        line(d, cam, M + 164, y + 20, 794 - M, y + 20, RULE, 0.7)
        y += 32

    # Marks table. Row 4's obtained figure is set in the wrong typeface and
    # sits a hair high -- that is the tell the font module is meant to catch,
    # and it has to be genuinely visible on the sheet for the film to be honest.
    ty = 596
    text(d, cam, M, ty - 18, "RECORD OF MARKS AWARDED", "sans_bold", 7.0, INK_SOFT, spacing=1.1)
    cols = [M, M + 96, M + 470, M + 580, 794 - M]
    rect(d, cam, M, ty, 794 - M * 2, 26, fill=(232, 232, 228))
    heads = ["PAPER", "SUBJECT", "MAXIMUM", "OBTAINED"]
    for i, head in enumerate(heads):
        text(d, cam, cols[i] + 8, ty + 8, head, "sans_bold", 6.6, INK_SOFT, spacing=0.8)
    ry = ty + 26
    for idx, (paper, subject, maximum, obtained) in enumerate(MARKS):
        rect(d, cam, M, ry, 794 - M * 2, 30, outline=RULE, width=0.7)
        text(d, cam, cols[0] + 8, ry + 9, paper, "sans_bold", 8.6, INK)
        text(d, cam, cols[1] + 8, ry + 9, subject, "sans", 8.6, INK_SOFT)
        text(d, cam, cols[2] + 8, ry + 9, maximum, "mono", 8.8, INK_SOFT)
        if idx == 3:
            text(d, cam, cols[3] + 8, ry + 7, obtained, "sans_bold", 9.4, INK)
        else:
            text(d, cam, cols[3] + 8, ry + 9, obtained, "mono", 8.8, INK)
        ry += 30
    rect(d, cam, M, ry, 794 - M * 2, 30, fill=(240, 240, 236), outline=RULE, width=0.7)
    text(d, cam, cols[1] + 8, ry + 9, "TOTAL", "sans_bold", 8.6, INK)
    text(d, cam, cols[2] + 8, ry + 9, "800", "mono_bold", 8.8, INK)
    text(d, cam, cols[3] + 8, ry + 9, "664", "mono_bold", 8.8, INK)
    for cx in cols[1:-1]:
        line(d, cam, cx, ty, cx, ry + 30, RULE, 0.7)

    # Declaration + signatures
    text(d, cam, M, 806, "DECLARATION", "sans_bold", 7.0, INK_SOFT, spacing=1.1)
    text(d, cam, M, 822,
         "I certify that the particulars entered above are correct and that the marks recorded",
         "serif", 8.6, INK_SOFT)
    text(d, cam, M, 838,
         "have been transcribed from the original evaluation record without alteration.",
         "serif", 8.6, INK_SOFT)

    draw_signature(d, cam, M + 4, 886, 168, 36, seed=17)
    line(d, cam, M, 928, M + 210, 928, INK_SOFT, 0.8)
    text(d, cam, M, 934, "Signature of candidate", "sans", 7.2, INK_FAINT)

    draw_signature(d, cam, M + 268, 888, 150, 32, seed=91)
    line(d, cam, M + 258, 928, M + 448, 928, INK_SOFT, 0.8)
    text(d, cam, M + 258, 934, "Signature of invigilator", "sans", 7.2, INK_FAINT)

    draw_seal(d, cam, 660, 936, 58)

    line(d, cam, M, 1052, 794 - M, 1052, RULE, 0.7)
    text(d, cam, M, 1060, "CAG-P/17 · Page 1 of 1", "mono", 6.6, INK_FAINT)
    text(d, cam, 794 - M, 1060, "SPECIMEN — GENERATED FOR DEMONSTRATION", "mono", 6.6,
         INK_FAINT, anchor="ra")


# --- baked overlays ----------------------------------------------------------


def draw_brackets(d: ImageDraw.ImageDraw, cam: Cam, r: dict, colour, reveal: float) -> None:
    """Corner brackets that snap in around the region under inspection."""
    if reveal <= 0.01:
        return
    x0, y0, x1, y1 = cam.box(r["x"], r["y"], r["w"], r["h"])
    # Overshoot slightly on arrival, then settle -- the brackets read as
    # something clamping onto the region rather than fading up.
    slack = (1 - ease_in_out(clamp01(reveal))) * 26 * SS
    x0, y0, x1, y1 = x0 - slack, y0 - slack, x1 + slack, y1 + slack
    arm = min(x1 - x0, y1 - y0) * 0.16
    w = max(1, int(round(2.4 * SS)))
    a = int(255 * clamp01(reveal))
    c = (*colour, a)
    for cx, cy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d.line([(cx, cy), (cx + arm * dx, cy)], fill=c, width=w)
        d.line([(cx, cy), (cx, cy + arm * dy)], fill=c, width=w)
    d.rectangle([x0, y0, x1, y1], outline=(*colour, int(a * 0.18)), width=max(1, int(round(1.0 * SS))))


def draw_scan(d: ImageDraw.ImageDraw, cam: Cam, r: dict, colour, dwell: float) -> None:
    """One sweep down the region during the dwell, with a soft trail."""
    pass_t = clamp01((dwell - 0.12) / 0.52)
    if pass_t <= 0 or pass_t >= 1:
        return
    x0, y0, x1, y1 = cam.box(r["x"], r["y"], r["w"], r["h"])
    y = lerp(y0, y1, ease_in_out(pass_t))
    fade = math.sin(pass_t * math.pi)
    trail = (y - y0) * 0.55
    for i in range(12):
        t = i / 11
        ty = y - trail * t
        if ty < y0:
            break
        d.line([(x0, ty), (x1, ty)], fill=(*colour, int(26 * (1 - t) * fade)),
               width=max(1, int(round(3 * SS))))
    d.line([(x0, y), (x1, y)], fill=(*colour, int(210 * fade)), width=max(1, int(round(1.6 * SS))))


def draw_markers(d: ImageDraw.ImageDraw, cam: Cam, stop: dict, dwell: float) -> None:
    """Compact status dots pinned to the region. The sentence that explains
    each one is HTML, not video -- these are only the spatial anchor."""
    r = stop["rect"]
    if not r or not stop["checks"]:
        return
    x0, y0, x1, y1 = cam.box(r["x"], r["y"], r["w"], r["h"])
    # Anchored inside the bracket's top-right and right-aligned. Hanging them
    # outside the region reads better but silently walks off the frame edge
    # once the region already fills it, which is most of them.
    dot_x = min(x1, WIDTH - 40 * SS) - 26 * SS
    for i, check in enumerate(stop["checks"]):
        pop = smoothstep(dwell, 0.30 + i * 0.09, 0.48 + i * 0.09)
        if pop <= 0.01:
            continue
        colour = STATUS_COLOUR[check["status"]]
        cy = max(y0, 0) + 34 * SS + i * 38 * SS
        rad = 5.0 * SS * pop
        f = font("mono", 15 * SS)
        label = check["value"]
        tw = d.textlength(label, font=f)
        # Bordered white chip, the same object the workspace uses for a status.
        d.rounded_rectangle(
            [dot_x - tw - 40 * SS, cy - 15 * SS, dot_x + 14 * SS, cy + 15 * SS],
            radius=5 * SS, fill=(*CHROME, int(242 * pop)),
            outline=(*CHROME_EDGE, int(255 * pop)), width=max(1, int(round(1 * SS))),
        )
        d.ellipse([dot_x - rad, cy - rad, dot_x + rad, cy + rad], fill=(*colour, int(255 * pop)))
        d.text((dot_x - 18 * SS, cy), label, font=f,
               fill=(*colour, int(250 * pop)), anchor="rm")


def draw_hud(d: ImageDraw.ImageDraw, cfg: dict, index: int, t: float) -> None:
    """Persistent instrument chrome, in frame space rather than page space."""
    stops = cfg["stops"]
    pad = 46 * SS
    # The chrome sits on its own dark plates rather than a full-width scrim.
    # A scrim has to cover the whole edge, and dark-over-cream across that much
    # area just reads as a grey smear on the document.
    brand = font("sans_bold", 17 * SS)
    tag = font("mono", 12 * SS)
    brand_w = d.textlength("PARAKH", font=brand)
    tag_w = d.textlength("DOCUMENT VERIFICATION FLIGHT", font=tag)
    plate_w = brand_w + tag_w + 34 * SS
    d.rounded_rectangle([pad - 18 * SS, pad - 13 * SS, pad + plate_w + 18 * SS, pad + 30 * SS],
                        radius=6 * SS, fill=(*CHROME, 240),
                        outline=(*CHROME_EDGE, 255), width=max(1, int(round(1 * SS))))
    d.text((pad, pad + 8 * SS), "PARAKH", font=brand, fill=(*INK, 255), anchor="lm")
    d.text((pad + brand_w + 22 * SS, pad + 9 * SS), "DOCUMENT VERIFICATION FLIGHT",
           font=tag, fill=(*INK_FAINT, 255), anchor="lm")

    # Progress ticks, one per stop, on a plate of their own.
    tick_y = HEIGHT - pad
    tick_w, gap = 26 * SS, 10 * SS
    total = len(stops) * tick_w + (len(stops) - 1) * gap
    label = stops[index]["title"].upper()
    label_w = d.textlength(label, font=tag)
    plate = max(total, label_w) + 56 * SS
    d.rounded_rectangle(
        [(WIDTH - plate) / 2, tick_y - 52 * SS, (WIDTH + plate) / 2, tick_y + 14 * SS],
        radius=6 * SS, fill=(*CHROME, 240),
        outline=(*CHROME_EDGE, 255), width=max(1, int(round(1 * SS))),
    )
    d.text((WIDTH / 2, tick_y - 32 * SS), label, font=tag, fill=(*INK_SOFT, 255), anchor="mm")
    x = (WIDTH - total) / 2
    for i, stop in enumerate(stops):
        on = i <= index
        colour = TONE[stop["tone"]] if on else (196, 204, 218)
        alpha = 255 if i == index else (170 if on else 255)
        d.rounded_rectangle([x, tick_y - 5 * SS, x + tick_w, tick_y - 1 * SS],
                            radius=2 * SS, fill=(*colour, alpha))
        x += tick_w + gap


def draw_platen(d: ImageDraw.ImageDraw) -> None:
    d.rectangle([0, 0, WIDTH, HEIGHT], fill=PLATEN)
    # A slack grid, so the zoom has something to register motion against.
    step = 120 * SS
    for gx in range(0, WIDTH, step):
        d.line([(gx, 0), (gx, HEIGHT)], fill=(*PLATEN_HI, 90), width=1)
    for gy in range(0, HEIGHT, step):
        d.line([(0, gy), (WIDTH, gy)], fill=(*PLATEN_HI, 90), width=1)


# --- frame loop --------------------------------------------------------------


def render_frame(cfg: dict, timeline: list[dict], t: float) -> Image.Image:
    cam, index, dwell, settled = sample(timeline, cfg, t)
    stop = cfg["stops"][index]

    img = Image.new("RGB", (WIDTH, HEIGHT), PLATEN)
    d = ImageDraw.Draw(img, "RGBA")
    draw_platen(d)

    # Page geometry is authored at 1x; scale into the supersampled frame here
    # so nothing downstream has to think about SS.
    scam = Cam(cam.s * SS, cam.cx, cam.cy)
    draw_page(d, scam)
    draw_document(d, scam)

    if stop["rect"] and settled > 0:
        colour = TONE[stop["tone"]]
        reveal = smoothstep(dwell, 0.0, 0.22)
        draw_brackets(d, scam, stop["rect"], colour, reveal)
        draw_scan(d, scam, stop["rect"], colour, dwell)
        draw_markers(d, scam, stop, dwell)

    draw_hud(d, cfg, index, t)
    return img.resize((cfg["video"]["w"], cfg["video"]["h"]), Image.LANCZOS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="stills only, no encode")
    parser.add_argument("--stills", action="store_true",
                        help="write per-stop jpgs for the mobile carousel, no encode")
    args = parser.parse_args()

    cfg = json.loads(STOPS_FILE.read_text(encoding="utf-8"))
    global WIDTH, HEIGHT, HALF_W, HALF_H
    WIDTH, HEIGHT = cfg["video"]["w"] * SS, cfg["video"]["h"] * SS
    HALF_W, HALF_H = WIDTH / 2, HEIGHT / 2

    timeline = build_timeline(cfg)

    # Hand the frontend the exact schedule the video was cut on, so the HTML
    # copy can never drift out of sync with the camera.
    duration = cfg["video"]["duration"]
    marks = []
    for i, stop in enumerate(cfg["stops"]):
        dwell = next(s for s in timeline if s["kind"] == "dwell" and s["index"] == i)
        move_in = next((s for s in timeline if s["kind"] == "move" and s["index"] == i - 1), None)
        marks.append({
            "id": stop["id"],
            "enter": round((move_in["start"] if move_in else 0.0) / duration, 6),
            "settle": round(dwell["start"] / duration, 6),
            "leave": round(dwell["end"] / duration, 6),
        })
    TIMELINE_FILE.write_text(
        json.dumps({"duration": duration, "marks": marks}, indent=2) + "\n", encoding="utf-8"
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.stills:
        # Phones get the stops as cards rather than a scrubbed 1080p clip, so
        # each one needs the frame the camera settles on.
        stills_dir = OUT_DIR / "stops"
        stills_dir.mkdir(parents=True, exist_ok=True)
        for i, stop in enumerate(cfg["stops"]):
            dwell = next(s for s in timeline if s["kind"] == "dwell" and s["index"] == i)
            t = lerp(dwell["start"], dwell["end"], 0.66)
            frame = render_frame(cfg, timeline, t)
            frame.resize((1280, 720), Image.LANCZOS).save(
                stills_dir / f"{stop['id']}.jpg", quality=82, optimize=True
            )
            print(f"  still {stop['id']}")
        print(f"stop stills -> {stills_dir}")
        return 0

    if args.probe:
        probe_dir = ROOT / "tmp" / "flight-probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        for i, stop in enumerate(cfg["stops"]):
            dwell = next(s for s in timeline if s["kind"] == "dwell" and s["index"] == i)
            t = lerp(dwell["start"], dwell["end"], 0.62)
            render_frame(cfg, timeline, t).save(probe_dir / f"stop_{i}_{stop['id']}.png")
            print(f"  probe {i} {stop['id']}")
        print(f"probe stills -> {probe_dir}")
        return 0

    if FRAME_DIR.exists():
        shutil.rmtree(FRAME_DIR)
    FRAME_DIR.mkdir(parents=True, exist_ok=True)

    fps = cfg["video"]["fps"]
    count = int(round(duration * fps))
    for n in range(count):
        frame = render_frame(cfg, timeline, n / fps)
        frame.save(FRAME_DIR / f"f_{n:05d}.png", compress_level=1)
        if n % 30 == 0:
            print(f"  frame {n}/{count}", flush=True)

    out = OUT_DIR / "verification-flight.mp4"
    # Small GOP rather than all-intra: the page fetches this as a Blob, which
    # is always fully seekable, so keyframe density only has to keep the seek
    # cheap -- it does not have to rescue a host that ignores byte ranges.
    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(fps), "-i", str(FRAME_DIR / "f_%05d.png"),
        "-an", "-vf", "unsharp=5:5:0.6:5:5:0.0",
        "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
        "-g", "8", "-keyint_min", "8", "-sc_threshold", "0",
        "-movflags", "+faststart", str(out),
    ], check=True)

    render_frame(cfg, timeline, 0.35).save(OUT_DIR / "poster.jpg", quality=88)
    print(f"\nwrote {out} ({out.stat().st_size / 1_000_000:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
