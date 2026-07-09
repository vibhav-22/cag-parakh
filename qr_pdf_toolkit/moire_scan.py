#!/usr/bin/env python3
"""
moire_scan.py — frequency-domain recapture (moiré) detector for document images.

Detects the periodic display-grid / halftone-screen signature left in photos of
screens or reprints, by looking for anomalous peaks in the 2D FFT of a high-pass
residual, after excluding the JPEG 8x8 lattice and the low-frequency content core.

Verdicts per image: RECAPTURE / CLEAN / INCONCLUSIVE.
A file-level rollup is reported for multi-image PDFs.

Usage:
    python moire_scan.py FILE [FILE ...] [--report DIR] [--json OUT.json]

Accepts PDFs (embedded rasters are extracted at native resolution) and image
files (jpg/png/tif/...). Prints a table; optionally writes JSON and PNG reports.

Exit code: 0 = ran fine (see verdicts in output), 2 = a file failed to process.
"""
import argparse, io, json, os, sys, tempfile
import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, maximum_filter

Image.MAX_IMAGE_PIXELS = None  # allow large recaptures

# ----------------------------- tunables -------------------------------------
CROP           = 2048   # analysis window (native px); auto-shrunk if image smaller
MIN_CROP       = 512    # below this the high band is too thin to trust
LORES_DIM      = 1000   # a would-be-CLEAN image smaller than this -> INCONCLUSIVE
SIGMA_HP       = 6.0    # high-pass gaussian sigma (content removal)
MIN_FREQ       = 0.0625 # inner search bound (cyc/px, period<=16px): drop content combs
JPEG_TOL       = 3      # +/- px band excluded around each JPEG lattice line
PEAK_SIGMA     = 6.0    # peak must exceed baseline by this many sigma
PEAK_MINSEP    = 20     # px, de-dup radius for peaks
ORIENT_TOL     = 12.0   # deg, peaks within this are one orientation family
COMB_MIN       = 3      # a single-axis family with >=this many peaks = content comb
BANDLIMIT_FRAC = 0.60   # if usable spectrum ends before this frac of Nyquist -> bandlimited
# decision thresholds on the composite score (reported as confidence)
SCORE_POS      = 0.60
SCORE_CLEAN    = 0.25
# ----------------------------------------------------------------------------


def extract_rasters(path, workdir):
    """Return list of (label, PIL.Image) at native resolution."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import fitz  # PyMuPDF; extracts embedded rasters at native resolution
        out = []
        with fitz.open(path) as doc:
            seen = set()
            for pno in range(doc.page_count):
                for img in doc.get_page_images(pno, full=True):
                    xref = img[0]
                    if xref in seen:          # same image reused across pages
                        continue
                    seen.add(xref)
                    try:
                        info = doc.extract_image(xref)
                        im = Image.open(io.BytesIO(info["image"]))
                        im.load()
                        out.append((f"p{pno+1}-x{xref}", im))
                    except Exception:
                        pass
        return out
    else:
        im = Image.open(path); im.load()
        return [(os.path.basename(path), im)]


def pick_crop(g, size):
    """Choose the centered-ish square window with the most high-freq energy."""
    H, W = g.shape
    size = min(size, H, W)
    # candidate top-left positions on a small grid, keep the highest-variance HP residual
    best, best_v = (max(0,(H-size)//2), max(0,(W-size)//2)), -1
    ys = np.linspace(0, H-size, 3).astype(int)
    xs = np.linspace(0, W-size, 3).astype(int)
    for r in ys:
        for c in xs:
            patch = g[r:r+size, c:c+size]
            v = float(np.var(patch - gaussian_filter(patch, 4)))
            if v > best_v:
                best_v, best = v, (r, c)
    r, c = best
    return g[r:r+size, c:c+size], size


def analyze(im):
    """Run the FFT pipeline on one image. Returns a dict of features + verdict."""
    rgb = np.asarray(im.convert("RGB")).astype(np.float64)
    g = rgb[:, :, 1]                       # green channel
    H, W = g.shape
    if min(H, W) < MIN_CROP:
        return {"verdict": "INCONCLUSIVE", "reason": "image too small",
                "dims": [W, H], "score": 0.0, "n_peaks": 0, "n_orient": 0,
                "max_sigma": 0.0, "bandlimit": None, "lores": True, "peaks": []}

    crop, S = pick_crop(g, CROP)
    resid = crop - gaussian_filter(crop, SIGMA_HP)
    win = np.hanning(S)[:, None] * np.hanning(S)[None, :]
    F = np.fft.fftshift(np.fft.fft2(resid * win))
    logmag = np.log1p(np.abs(F))

    cy = cx = S // 2
    yy, xx = np.indices((S, S))
    rr = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    rr_int = rr.astype(int)
    radial = np.bincount(rr_int.ravel(), logmag.ravel()) / \
             np.maximum(np.bincount(rr_int.ravel()), 1)
    baseline = radial[rr_int]
    excess = logmag - baseline

    # effective bandlimit: largest radius whose ring still has real structure.
    # ring std of the residual spectrum falls to noise when content is bandlimited.
    ring_std = np.array([logmag[(rr_int == k)].std() if np.any(rr_int == k) else 0.0
                         for k in range(S // 2)])
    ref = np.median(ring_std[int(0.1*S/2):int(0.3*S/2)]) + 1e-9
    usable = np.where(ring_std > 0.5 * ref)[0]
    r_cut = (usable.max() / (S / 2.0)) if len(usable) else 0.0

    # exclusion masks: low-freq content annulus + JPEG 8x8 lattice
    freq = rr / S                                    # cycles per pixel
    core = freq < MIN_FREQ
    jpeg = np.zeros((S, S), bool)
    step = S / 8.0
    for k in range(1, 5):
        for p in (int(round(cx + k*step)), int(round(cx - k*step))):
            if 0 <= p < S: jpeg[:, max(0, p-JPEG_TOL):p+JPEG_TOL+1] = True
        for p in (int(round(cy + k*step)), int(round(cy - k*step))):
            if 0 <= p < S: jpeg[max(0, p-JPEG_TOL):p+JPEG_TOL+1, :] = True
    valid = (~core) & (~jpeg)

    vv = excess[valid]
    mu, sd = float(vv.mean()), float(vv.std()) + 1e-9
    thr = mu + PEAK_SIGMA * sd

    exc = np.where(valid, excess, -np.inf)
    peaks = (exc == maximum_filter(exc, size=9)) & np.isfinite(exc)
    ys, xs = np.where(peaks & (exc > thr))
    sig = (excess[ys, xs] - mu) / sd
    order = np.argsort(sig)[::-1]
    ys, xs, sig = ys[order], xs[order], sig[order]

    kept = []
    for i in range(len(xs)):
        if all((xs[i]-xs[j])**2 + (ys[i]-ys[j])**2 >= PEAK_MINSEP**2 for j in kept):
            kept.append(i)
    kept = kept[:12]
    ys, xs, sig = ys[kept], xs[kept], sig[kept]

    peaklist = []
    angles = []
    for yv, xv, sv in zip(ys, xs, sig):
        fx, fy = (xv - cx) / S, (yv - cy) / S
        fr = float(np.hypot(fx, fy))
        ang = float(np.degrees(np.arctan2(fy, fx))) % 180.0
        angles.append(ang)
        peaklist.append({"sigma": round(float(sv), 1),
                         "freq_cyc_px": round(fr, 4),
                         "period_px": round(1/fr, 2) if fr > 0 else None,
                         "angle_deg": round(ang, 1)})

    n_peaks = len(peaklist)
    max_sigma = float(sig.max()) if n_peaks else 0.0

    # group peaks into orientation families (mod 180). A 2-D display/halftone
    # lattice populates >=2 families; 1-D content periodicity (line/stroke
    # pitch) forms a single-axis comb.
    families = []  # each: list of peak indices
    for i, a in enumerate(angles):
        placed = False
        for fam in families:
            fa = angles[fam[0]]
            d = abs(a - fa); d = min(d, 180 - d)      # circular distance on 180
            if d <= ORIENT_TOL:
                fam.append(i); placed = True; break
        if not placed:
            families.append([i])
    n_orient = len(families)
    largest_fam = max((len(f) for f in families), default=0)
    is_comb = (n_orient == 1 and largest_fam >= COMB_MIN)   # single-axis content comb

    # composite score, reported as confidence (orientation diversity, not concentration)
    peak_term   = min(n_peaks / 4.0, 1.0)
    sigma_term  = float(np.clip((max_sigma - PEAK_SIGMA) / (12.0 - PEAK_SIGMA), 0, 1))
    orient_term = min(n_orient, 2) / 2.0                    # 0, .5, 1
    score = 0.40*peak_term + 0.35*sigma_term + 0.25*orient_term

    bandlimited = r_cut < BANDLIMIT_FRAC
    lores = min(W, H) < LORES_DIM

    # ---- decision tree ----
    # A confident positive needs a genuine 2-D lattice: >=2 orientations, each
    # with a strong peak. Single-axis combs are content; isolated single-axis
    # peaks are ambiguous.
    grid = n_orient >= 2 and n_peaks >= 2 and max_sigma >= PEAK_SIGMA
    if grid and score >= SCORE_POS:
        verdict, reason = "RECAPTURE", \
            f"2-D periodic lattice, {n_orient} orientations (display/halftone signature)"
    elif is_comb:
        verdict, reason = "CLEAN", "single-axis periodicity consistent with document content"
    elif n_orient == 1 and max_sigma >= PEAK_SIGMA:
        verdict, reason = "INCONCLUSIVE", "isolated single-axis peak (content or 1-D screen)"
    else:
        # no periodic evidence; decide clean vs inconclusive on quality guards
        if lores:
            verdict, reason = "INCONCLUSIVE", \
                f"low resolution ({min(W,H)}px); recapture evidence may be lost to downscaling"
        elif bandlimited:
            verdict, reason = "INCONCLUSIVE", \
                f"spectrum bandlimited to {r_cut:.2f} Nyquist (possible downsample)"
        else:
            verdict, reason = "CLEAN", "no anomalous periodic peaks"

    return {"verdict": verdict, "reason": reason, "dims": [W, H],
            "score": round(score, 3), "n_peaks": n_peaks, "n_orient": n_orient,
            "max_sigma": round(max_sigma, 1), "bandlimit": round(r_cut, 2),
            "lores": lores, "peaks": peaklist,
            "_arrays": (crop, logmag, excess, ys, xs, cx, cy, S)}


def rollup(image_results):
    v = [r["verdict"] for r in image_results]
    if "RECAPTURE" in v: return "RECAPTURE"
    if all(x == "CLEAN" for x in v): return "CLEAN"
    return "INCONCLUSIVE"


def save_report(path, label, res):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    crop, logmag, excess, ys, xs, cx, cy, S = res["_arrays"]
    z = min(512, S // 2)
    sl = (slice(cy-z, cy+z), slice(cx-z, cx+z))
    fig, ax = plt.subplots(1, 3, figsize=(18, 6.2))
    ax[0].imshow(crop, cmap="gray"); ax[0].axis("off")
    ax[0].set_title(f"{label}  ({res['dims'][0]}x{res['dims'][1]})")
    d = logmag[sl]
    ax[1].imshow(d, cmap="inferno", vmin=np.percentile(d,60), vmax=np.percentile(d,99.9))
    ax[1].axis("off"); ax[1].set_title("log |FFT| high-pass residual")
    e = excess[sl]
    ax[2].imshow(e, cmap="inferno", vmin=np.percentile(e,60), vmax=np.percentile(e,99.9))
    for yv, xv in zip(ys, xs):
        y2, x2 = yv-(cy-z), xv-(cx-z)
        if 0 <= y2 < 2*z and 0 <= x2 < 2*z:
            ax[2].add_patch(plt.Circle((x2, y2), 14, ec="cyan", fc="none", lw=1.5))
    ax[2].axis("off")
    ax[2].set_title(f"excess+peaks — {res['verdict']} (score {res['score']})")
    plt.tight_layout(); plt.savefig(path, dpi=110, bbox_inches="tight"); plt.close()


def main():
    try:                                  # Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Frequency-domain recapture/moiré detector.")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--report", metavar="DIR", help="write PNG spectra per image")
    ap.add_argument("--json", metavar="OUT", help="write full results as JSON")
    args = ap.parse_args()

    all_out, failed = [], False
    print(f"{'file':<24}{'image':<16}{'verdict':<14}{'score':>6} {'pk':>3} "
          f"{'ornt':>4} {'σmax':>5} {'band':>5}  reason")
    print("-" * 112)
    for f in args.files:
        try:
            with tempfile.TemporaryDirectory() as wd:
                rasters = extract_rasters(f, wd)
                if not rasters:
                    print(f"{os.path.basename(f):<24}{'(none)':<16}"
                          f"{'INCONCLUSIVE':<14}{'-':>6}  no raster images found")
                    all_out.append({"file": f, "file_verdict": "INCONCLUSIVE",
                                    "images": []})
                    continue
                results = []
                for label, im in rasters:
                    r = analyze(im)
                    if args.report:
                        os.makedirs(args.report, exist_ok=True)
                        if "_arrays" in r:
                            save_report(os.path.join(args.report,
                                        f"{os.path.splitext(os.path.basename(f))[0]}_{label}.png"),
                                        label, r)
                    r.pop("_arrays", None)
                    results.append(r)
                    print(f"{os.path.basename(f):<24}{label:<16}{r['verdict']:<14}"
                          f"{r['score']:>6} {r['n_peaks']:>3} {r['n_orient']:>4} "
                          f"{r['max_sigma']:>5} {str(r['bandlimit']):>5}  {r['reason']}")
                fv = rollup(results)
                all_out.append({"file": f, "file_verdict": fv, "images": results})
                if len(results) > 1:
                    print(f"{'  -> file verdict':<40}{fv}")
        except Exception as e:
            failed = True
            print(f"{os.path.basename(f):<24}{'ERROR':<16}{str(e)[:50]}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(all_out, fh, indent=2)
        print(f"\nwrote {args.json}")
    sys.exit(2 if failed else 0)


if __name__ == "__main__":
    main()
