"""
pdf_readability_checker.py
==========================
Checks whether a PDF is machine-readable (digital/selectable text)
or a low-quality scanned image — and grades it accordingly.

Tests are calibrated against a real-world failing example:
  A UP Board High School marksheet scanned via phone camera,
  saved as PDF via PDFium. That document fails Tests 3, 5, and 6.

Dependencies:
    pip install pymupdf pdfplumber pytesseract pillow opencv-python-headless
    apt install tesseract-ocr poppler-utils   # (or brew install on Mac)

Usage:
    python pdf_readability_checker.py path/to/file.pdf
    python pdf_readability_checker.py path/to/file.pdf --verbose
"""

import sys
import os
import json
import subprocess
import tempfile
import argparse
from dataclasses import asdict, dataclass, field
from typing import Optional

# ── third-party ───────────────────────────────────────────────────────────────
try:
    import fitz                     # PyMuPDF
    import pdfplumber
    import pytesseract
    import cv2
    import numpy as np
    from PIL import Image
except ImportError as e:
    sys.exit(f"Missing dependency: {e}\n"
             "Run: pip install pymupdf pdfplumber pytesseract pillow opencv-python-headless")


# ═══════════════════════════════════════════════════════════════════════════════
#  Image helpers
# ═══════════════════════════════════════════════════════════════════════════════

def autocrop_whitespace(img: Image.Image, threshold: int = 240) -> Image.Image:
    """
    Crop large white borders around the actual document content.
    Needed for phone-photographed PDFs where the document occupies
    only a small portion of the page canvas (e.g. dark pixel % < 2%).
    """
    arr = np.array(img.convert('L'))
    mask = arr < threshold
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any() or not cols.any():
        return img   # nothing to crop, return as-is
    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]
    h, w = arr.shape
    rmin = max(0, rmin - 20)
    rmax = min(h, rmax + 20)
    cmin = max(0, cmin - 20)
    cmax = min(w, cmax + 20)
    return img.crop((cmin, rmin, cmax, rmax))


# ═══════════════════════════════════════════════════════════════════════════════
#  Result containers
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TestResult:
    name: str
    passed: bool
    value: str          # human-readable measured value
    threshold: str      # what we required
    detail: str = ""    # extra context / why it matters
    # False when the check could not execute at all, usually because Tesseract
    # or poppler is absent. A check that did not run is neither a pass nor a
    # fail, and counting it as either produces a verdict the evidence does not
    # support. Everything below filters on this before reading `passed`.
    ran: bool = True


@dataclass
class ReadabilityReport:
    pdf_path: str
    tests: list = field(default_factory=list)
    overall: str = "UNKNOWN"   # READABLE | POOR | UNREADABLE
    score: int = 0             # 0-100

    def add(self, result: TestResult):
        self.tests.append(result)

    def not_run(self) -> list:
        """Checks that could not execute, usually a missing Tesseract/poppler."""

        return [t for t in self.tests if not t.ran]

    def summarise(self):
        # Only checks that actually ran can contribute. Scoring against the
        # full list let a machine with no OCR tooling score HIGHER than one
        # with it, because every check it could not run counted as a pass.
        executed = [t for t in self.tests if t.ran]
        passed = sum(1 for t in executed if t.passed)
        total  = len(executed)
        self.score = int(100 * passed / total) if total else 0

        # Check OCR word count — this is the strongest signal. It only counts
        # as a signal if OCR actually ran.
        ocr_word_test = next((t for t in executed if t.name == "OCR Word Count"), None)
        ocr_conf_test = next((t for t in executed if t.name == "OCR Confidence"), None)
        ocr_succeeded = ocr_word_test is not None and ocr_word_test.passed

        # For image-based PDFs (no text layer), missing text/pdfplumber is expected — don't penalise
        text_layer_tests = {"Extractable Text Words", "pdfplumber Character Quality"}
        has_text_layer = any(
            t.passed for t in executed if t.name in text_layer_tests
        )

        critical_fails = [
            t for t in executed
            if not t.passed
            and t.name in ("OCR Confidence", "Extractable Text Words", "OCR Word Count")
            # If OCR found words, failing text-layer tests are expected — not critical
            and not (ocr_succeeded and t.name == "Extractable Text Words")
        ]

        # An incomplete screening is reported as incomplete. Anything else lets
        # a document that was never fully examined be handed to a reviewer as
        # a finished verdict, which is the one output this tool must not
        # produce. Named checks go in the label so the gap is actionable.
        missing = self.not_run()
        if missing:
            names = ", ".join(t.name for t in missing[:3])
            more = f" +{len(missing) - 3} more" if len(missing) > 3 else ""
            self.overall = (
                f"INCOMPLETE ⚠️  ({len(missing)} of {len(self.tests)} checks could not run: "
                f"{names}{more})"
            )
        elif ocr_succeeded and has_text_layer and self.score >= 70:
            self.overall = "READABLE ✅"
        elif ocr_succeeded and not has_text_layer:
            # Image-based PDF — readable via OCR, no native text layer (normal for scanned docs)
            self.overall = "READABLE ✅ (image-based PDF, OCR required to extract text)"
        elif self.score >= 80 and not critical_fails:
            self.overall = "READABLE ✅"
        elif self.score >= 50 or len(critical_fails) <= 1:
            self.overall = "POOR ⚠️  (needs OCR / manual review)"
        else:
            self.overall = "UNREADABLE ❌"


# ═══════════════════════════════════════════════════════════════════════════════
#  Individual tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_file_exists(path: str) -> TestResult:
    ok = os.path.isfile(path)
    return TestResult(
        name="File Exists",
        passed=ok,
        value=str(ok),
        threshold="True",
        detail="PDF file must be present at the given path.",
    )


def test_pdf_openable(path: str) -> tuple[bool, Optional[fitz.Document]]:
    """Returns (success, doc). doc is None on failure."""
    try:
        doc = fitz.open(path)
        return True, doc
    except Exception:
        return False, None


def test_page_count(doc: fitz.Document) -> TestResult:
    n = len(doc)
    ok = n >= 1
    return TestResult(
        name="Page Count",
        passed=ok,
        value=str(n),
        threshold=">= 1",
        detail="PDF must have at least one page.",
    )


def test_stream_errors(path: str) -> TestResult:
    """
    Runs pdfinfo and checks stderr for flate/stream errors.
    The marksheet PDF had 3 'Bad block header in flate stream' errors —
    a sign the PDF was created from a compressed photo, not a digital source.
    """
    try:
        result = subprocess.run(
            ["pdfinfo", path],
            capture_output=True, text=True
        )
        errors = [l for l in result.stderr.splitlines() if "Error" in l or "error" in l]
        ok = len(errors) == 0
        return TestResult(
            name="PDF Stream Integrity",
            passed=ok,
            value=f"{len(errors)} stream error(s)",
            threshold="0 errors",
            detail=(
                "Stream errors suggest the PDF was generated from a compressed "
                "photo (e.g. phone camera → PDFium). Real digital PDFs have clean streams."
                f"\n    Errors found: {errors[:3]}" if errors else ""
            ),
        )
    except FileNotFoundError:
        return TestResult(
            name="PDF Stream Integrity",
            passed=False,
            ran=False,
            value="pdfinfo not available — not run",
            threshold="0 errors",
            detail="Install poppler-utils for this check.",
        )


def test_font_embedding(doc: fitz.Document) -> TestResult:
    """
    The marksheet had 7 fonts, ALL non-embedded (Helvetica, Times-Roman, etc.)
    PLUS one HiddenHorzOCR font — typical of PDF-from-scan workflows.
    Non-embedded standard fonts with an OCR hidden layer = scanned document.
    """
    page = doc[0]
    fonts = page.get_fonts()

    non_embedded = [f for f in fonts if not f[3]]
    has_ocr_font = any("ocr" in (f[3] or "").lower() or "hidden" in (f[3] or "").lower()
                       for f in fonts)

    ok = len(non_embedded) == 0 and not has_ocr_font
    return TestResult(
        name="Font Embedding",
        passed=ok,
        value=(
            f"{len(non_embedded)} non-embedded font(s)"
            + (" + HiddenOCR font detected" if has_ocr_font else "")
        ),
        threshold="All fonts embedded, no OCR-layer fonts",
        detail=(
            "Non-embedded fonts + an OCR layer font (HiddenHorzOCR) strongly indicate "
            "the PDF is a scanned image with a hidden, often garbled text layer added by "
            "PDF converters. Text extraction from such PDFs is unreliable."
        ),
    )


def test_extractable_text(doc: fitz.Document) -> TestResult:
    """
    The marksheet yielded only 44 'words' (len>1), almost all garbage:
    '.', ',,~•', '_liiial■I', etc. — OCR artefacts from the image noise.
    A real digital document yields hundreds of clean words per page.
    """
    text = ""
    for page in doc:
        text += page.get_text()

    words = [w for w in text.split() if len(w) > 2
             and any(c.isalpha() for c in w)]
    ok = len(words) >= 20

    # Garble check: many short or symbol-heavy tokens relative to real words
    all_tokens = [w for w in text.split() if w.strip()]
    garble_ratio = 1 - (len(words) / max(len(all_tokens), 1))

    return TestResult(
        name="Extractable Text Words",
        passed=ok and garble_ratio < 0.7,
        value=f"{len(words)} clean words  |  garble ratio: {garble_ratio:.0%}",
        threshold=">= 20 clean ASCII words, garble < 70%",
        detail=(
            "Scanned PDFs produce either zero words or garbled symbols "
            "(e.g. ',,~•', '_liiial■I') rather than real text. "
            "A high garble ratio exposes this even when word count looks OK."
        ),
    )


def test_pdfplumber_chars(path: str) -> TestResult:
    """
    pdfplumber found 353 characters but all garbled.
    A real document with the same page area would have 1000–5000 characters.
    """
    try:
        with pdfplumber.open(path) as pdf:
            chars = pdf.pages[0].chars if pdf.pages else []
            text  = pdf.pages[0].extract_text() or "" if pdf.pages else ""

        # Count alphanumeric characters specifically
        alpha_chars = sum(1 for c in text if c.isalnum())
        ok = alpha_chars >= 100

        return TestResult(
            name="pdfplumber Character Quality",
            passed=ok,
            value=f"{len(chars)} total chars  |  {alpha_chars} alphanumeric",
            threshold=">= 100 alphanumeric characters",
            detail=(
                "Even when pdfplumber finds characters, they may be noise/symbols "
                "from a scanned image's hidden OCR layer. Alphanumeric count filters this."
            ),
        )
    except Exception as e:
        return TestResult(
            name="pdfplumber Character Quality",
            passed=False,
            value=f"Error: {e}",
            threshold=">= 100 alphanumeric characters",
        )


def test_ocr_confidence(pdf_path: str, dpi: int = 150) -> TestResult:
    """
    The marksheet got mean OCR confidence 28.4, with 71% of tokens < 50.
    A clean printed document scores > 70 mean confidence.
    This is the most reliable single test for scan quality.
    """
    try:
        # Rasterize first page
        with tempfile.TemporaryDirectory() as tmpdir:
            prefix = os.path.join(tmpdir, "page")
            subprocess.run(
                ["pdftoppm", "-jpeg", f"-r", str(dpi), "-f", "1", "-l", "1",
                 pdf_path, prefix],
                capture_output=True
            )
            files = [f for f in os.listdir(tmpdir) if f.endswith(".jpg")]
            if not files:
                return TestResult(
                    name="OCR Confidence",
                    passed=False,
                    value="Could not rasterize page",
                    threshold="mean > 60",
                    detail="pdftoppm failed or produced no output.",
                )

            img_path = os.path.join(tmpdir, sorted(files)[0])
            img = autocrop_whitespace(Image.open(img_path))

            data = pytesseract.image_to_data(img, lang='eng+hin', output_type=pytesseract.Output.DICT)
            confs = [
                int(c) for c in data["conf"]
                if str(c).lstrip("-").isdigit() and int(c) >= 0
            ]

            MIN_TOKENS = 10
            if len(confs) < MIN_TOKENS:
                return TestResult(
                    name="OCR Confidence",
                    passed=False,
                    value=f"Only {len(confs)} token(s) found — too few to score",
                    threshold=f"mean > 40 with >= {MIN_TOKENS} tokens",
                    detail="High confidence on near-zero tokens is meaningless. "
                           "The image is likely too blurry for Tesseract to detect text regions."
                )

            mean_conf = sum(confs) / len(confs)
            low_conf_pct = 100 * sum(1 for c in confs if c < 50) / len(confs)
            # Threshold is lower for mixed-language docs (Hindi+English) since
            # Tesseract scores Devanagari script lower than Latin by design
            ok = mean_conf > 40 and low_conf_pct < 65

            return TestResult(
                name="OCR Confidence",
                passed=ok,
                value=f"mean={mean_conf:.1f}  |  low-conf tokens={low_conf_pct:.0f}%",
                threshold="mean > 40, low-conf tokens < 65%",
                detail=(
                    "Tesseract confidence directly measures how legible the text is. "
                    "The marksheet scored 28.4 mean with 71% low-confidence — "
                    "caused by background patterns, blur, and overlapping stamps."
                ),
            )
    except FileNotFoundError:
        return TestResult(
            name="OCR Confidence",
            passed=False,
            ran=False,
            value="pdftoppm/tesseract not available — not run",
            threshold="mean > 60",
            detail="Install poppler-utils and tesseract-ocr for this check.",
        )


def test_ocr_word_count(pdf_path: str, dpi: int = 150) -> TestResult:
    """
    The marksheet produced only 2 OCR words of length > 2.
    A clean printed A4 page of text yields 200–800 words.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            prefix = os.path.join(tmpdir, "page")
            subprocess.run(
                ["pdftoppm", "-jpeg", f"-r", str(dpi), "-f", "1", "-l", "1",
                 pdf_path, prefix],
                capture_output=True
            )
            files = [f for f in os.listdir(tmpdir) if f.endswith(".jpg")]
            if not files:
                return TestResult(
                    name="OCR Word Count",
                    passed=False, value="No image", threshold=">= 30 words",
                )

            img = autocrop_whitespace(Image.open(os.path.join(tmpdir, sorted(files)[0])))
            text = pytesseract.image_to_string(img, lang='eng+hin')
            words = [w for w in text.split() if len(w) > 2 and any(c.isalpha() for c in w)]
            ok = len(words) >= 30

            return TestResult(
                name="OCR Word Count",
                passed=ok,
                value=f"{len(words)} words extracted by OCR",
                threshold=">= 30 words (per page)",
                detail=(
                    "Even applying OCR to the rasterized page, the marksheet yielded only "
                    "2 readable words — the background pattern and compression artefacts "
                    "prevent Tesseract from isolating text regions."
                ),
            )
    except FileNotFoundError:
        return TestResult(
            name="OCR Word Count",
            passed=False,
            ran=False,
            value="pdftoppm/tesseract not available — not run",
            threshold=">= 30 words",
        )


def test_image_noise(pdf_path: str, dpi: int = 150, threshold: float = 8.0) -> TestResult:
    """
    The marksheet had noise level 9.63 (threshold < 8).
    Camera-captured documents have more noise than scanner-captured ones.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            prefix = os.path.join(tmpdir, "page")
            subprocess.run(
                ["pdftoppm", "-jpeg", f"-r", str(dpi), "-f", "1", "-l", "1",
                 pdf_path, prefix],
                capture_output=True
            )
            files = [f for f in os.listdir(tmpdir) if f.endswith(".jpg")]
            if not files:
                return TestResult(
                    name="Image Noise Level",
                    passed=False, value="No image", threshold=f"< {threshold:g}",
                )

            img_gray = np.array(Image.open(
                os.path.join(tmpdir, sorted(files)[0])
            ).convert("L"))

            blur = cv2.GaussianBlur(img_gray.astype(np.float32), (5, 5), 0)
            noise = float(np.sqrt(((img_gray.astype(np.float32) - blur) ** 2).mean()))
            ok = noise < threshold

            return TestResult(
                name="Image Noise Level",
                passed=ok,
                value=f"{noise:.2f}",
                threshold=f"< {threshold:g}",
                detail=(
                    "Estimated per-pixel noise vs a Gaussian-smoothed reference. "
                    "Camera-scanned documents score higher due to sensor noise, "
                    "motion blur, and uneven lighting."
                ),
            )
    except FileNotFoundError:
        return TestResult(
            name="Image Noise Level",
            passed=False,
            ran=False,
            value="pdftoppm not available — not run",
            threshold=f"< {threshold:g}",
        )


def test_image_sharpness(pdf_path: str, dpi: int = 150, threshold: float = 500.0) -> TestResult:
    """
    Laplacian variance measures focus. The marksheet scored 889 — borderline.
    Set threshold at 500; heavily blurred scans fall below.
    """
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            prefix = os.path.join(tmpdir, "page")
            subprocess.run(
                ["pdftoppm", "-jpeg", f"-r", str(dpi), "-f", "1", "-l", "1",
                 pdf_path, prefix],
                capture_output=True
            )
            files = [f for f in os.listdir(tmpdir) if f.endswith(".jpg")]
            if not files:
                return TestResult(
                    name="Image Sharpness",
                    passed=False, value="No image", threshold=f"> {threshold:g}",
                )

            img_gray = np.array(Image.open(
                os.path.join(tmpdir, sorted(files)[0])
            ).convert("L"))

            lap_var = float(cv2.Laplacian(img_gray, cv2.CV_64F).var())
            ok = lap_var > threshold

            return TestResult(
                name="Image Sharpness",
                passed=ok,
                value=f"{lap_var:.1f}",
                threshold=f"> {threshold:g}",
                detail=(
                    "Laplacian variance of the rasterized page. "
                    "Low values indicate camera blur or out-of-focus scanning."
                ),
            )
    except FileNotFoundError:
        return TestResult(
            name="Image Sharpness",
            passed=False,
            ran=False,
            value="pdftoppm not available — not run",
            threshold=f"> {threshold:g}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  Main runner
# ═══════════════════════════════════════════════════════════════════════════════

def check_pdf(
    path: str,
    verbose: bool = False,
    dpi: int = 150,
    noise_threshold: float = 8.0,
    sharpness_threshold: float = 500.0,
) -> ReadabilityReport:
    report = ReadabilityReport(pdf_path=path)

    # 1. File existence
    r = test_file_exists(path)
    report.add(r)
    if not r.passed:
        report.summarise()
        return report

    # 2. Openability
    ok, doc = test_pdf_openable(path)
    report.add(TestResult(
        name="PDF Openable",
        passed=ok,
        value=str(ok),
        threshold="True",
        detail="PyMuPDF must be able to open and parse the file.",
    ))
    if not ok:
        report.summarise()
        return report

    # 3. Page count
    report.add(test_page_count(doc))

    # 4. Stream integrity (poppler-based)
    report.add(test_stream_errors(path))

    # 5. Font embedding
    report.add(test_font_embedding(doc))

    # 6. Extractable text
    report.add(test_extractable_text(doc))

    # 7. pdfplumber character quality
    report.add(test_pdfplumber_chars(path))

    # 8. OCR confidence (most important visual test)
    report.add(test_ocr_confidence(path, dpi=dpi))

    # 9. OCR word count
    report.add(test_ocr_word_count(path, dpi=dpi))

    # 10. Image noise
    report.add(test_image_noise(path, dpi=dpi, threshold=noise_threshold))

    # 11. Image sharpness
    report.add(test_image_sharpness(path, dpi=dpi, threshold=sharpness_threshold))

    doc.close()
    report.summarise()
    return report


# ═══════════════════════════════════════════════════════════════════════════════
#  Pretty printer
# ═══════════════════════════════════════════════════════════════════════════════

def print_report(report: ReadabilityReport, verbose: bool = False):
    PASS = "✅ PASS"
    FAIL = "❌ FAIL"
    W    = 55   # column width

    print()
    print("=" * 70)
    print(f"  PDF READABILITY REPORT")
    print(f"  File   : {report.pdf_path}")
    print(f"  Result : {report.overall}")
    print(f"  Score  : {report.score}/100  ({sum(t.passed for t in report.tests)}/{len(report.tests)} tests passed)")
    print("=" * 70)
    print(f"  {'TEST':<35} {'STATUS':<10} {'MEASURED VALUE'}")
    print(f"  {'-'*35} {'-'*10} {'-'*22}")

    for t in report.tests:
        status = PASS if t.passed else FAIL
        print(f"  {t.name:<35} {status:<10} {t.value}")
        if verbose and t.detail:
            for line in t.detail.split("\n"):
                print(f"    → {line.strip()}")
            print(f"    Threshold: {t.threshold}")

    print("=" * 70)

    failed = [t for t in report.tests if not t.passed]
    if failed:
        print("\n  Why this PDF failed:")
        for t in failed:
            print(f"  • {t.name}: got {t.value}  (need {t.threshold})")
            if t.detail:
                first_line = t.detail.split("\n")[0].strip()
                print(f"    {first_line}")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
#  Structured output
# ═══════════════════════════════════════════════════════════════════════════════

def report_to_dict(report: ReadabilityReport) -> dict:
    """Serialize a report to the JSON shape consumed by the backend API.

    The `verdict` / `passed` / `risk` keys drive the API's normalized outcome;
    `tests` carries every TestResult so UIs can render a real table instead of
    re-parsing the pretty-printed text report.
    """
    not_run = report.not_run()
    if not_run:
        # Reported ahead of the readable/poor/unreadable ladder on purpose: if
        # part of the screening never happened, the honest answer is that the
        # question is open, not a verdict derived from the half that ran.
        verdict, risk = "incomplete", "unknown"
    elif report.overall.startswith("READABLE"):
        verdict, risk = "readable", "low"
    elif report.overall.startswith("POOR"):
        verdict, risk = "poor_readability", "medium"
    elif report.overall.startswith("UNREADABLE"):
        verdict, risk = "unreadable", "high"
    else:
        verdict, risk = "unknown", "unknown"
    executed = [t for t in report.tests if t.ran]
    return {
        "pdf_path": report.pdf_path,
        "verdict": verdict,
        "note": report.overall,
        "passed": verdict == "readable",
        "risk": risk,
        "score": report.score,
        # Counted over checks that ran, so the score cannot be inflated by
        # checks that were impossible to perform.
        "tests_passed": sum(1 for t in executed if t.passed),
        "tests_total": len(executed),
        "tests_declared": len(report.tests),
        "complete": not not_run,
        "tests_not_run": [t.name for t in not_run],
        "tests": [asdict(t) for t in report.tests],
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Check whether a PDF is machine-readable."
    )
    parser.add_argument("pdf", help="Path to the PDF file")
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print threshold details and explanations for each test"
    )
    parser.add_argument(
        "--output", metavar="FILE", default=None,
        help="Also write the report as structured JSON to this path"
    )
    parser.add_argument("--dpi", type=int, default=150, help="Render DPI for OCR and image-quality tests. Default: 150")
    parser.add_argument("--noise-threshold", type=float, default=8.0, help="Maximum passing image-noise score. Default: 8")
    parser.add_argument("--sharpness-threshold", type=float, default=500.0, help="Minimum passing sharpness score. Default: 500")
    args = parser.parse_args()

    # Windows consoles often default to cp1252, which cannot print the ✅/❌
    # marks in the report. Re-encode stdout defensively so the pretty printer
    # never crashes the run.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    report = check_pdf(
        args.pdf,
        verbose=args.verbose,
        dpi=max(96, min(300, args.dpi)),
        noise_threshold=max(1.0, min(50.0, args.noise_threshold)),
        sharpness_threshold=max(50.0, min(5000.0, args.sharpness_threshold)),
    )

    # Write the machine-readable report FIRST: it is the contract consumed by
    # the backend API; the pretty console print below is cosmetic.
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(report_to_dict(report), handle, indent=2, ensure_ascii=False)

    print_report(report, verbose=args.verbose)

    # Exit code: 0 = readable, 1 = poor/unreadable
    sys.exit(0 if "READABLE ✅" in report.overall else 1)
