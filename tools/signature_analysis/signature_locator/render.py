from __future__ import annotations

import os
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image


def find_executable(name: str, explicit_bin: str | None = None) -> str | None:
    suffix = ".exe" if os.name == "nt" else ""
    if explicit_bin:
        candidate = Path(explicit_bin)
        if candidate.is_dir():
            candidate = candidate / f"{name}{suffix}"
        if candidate.is_file():
            return str(candidate)

    found = shutil.which(name)
    if found and not found.lower().endswith((".cmd", ".bat")):
        return found

    # Codex desktop bundled runtime. This is a discovery fallback, not a requirement.
    cache = Path.home() / ".cache" / "codex-runtimes"
    if cache.is_dir():
        matches = list(cache.glob(f"**/poppler/**/{name}{suffix}"))
        if matches:
            return str(matches[0])
    return found


def render_pdf(
    pdf_path: Path, dpi: int = 160, poppler_bin: str | None = None
) -> list[Image.Image]:
    executable = find_executable("pdftoppm", poppler_bin)
    if not executable:
        return _render_with_pymupdf(pdf_path, dpi)
    with tempfile.TemporaryDirectory(prefix="signature-locator-") as temp:
        prefix = Path(temp) / "page"
        command = [
            executable,
            "-r",
            str(dpi),
            "-png",
            "-cropbox",
            str(pdf_path),
            str(prefix),
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=300, check=False
        )
        if completed.returncode:
            raise RuntimeError(
                f"Poppler could not render {pdf_path.name}: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        images: list[Image.Image] = []
        for image_path in sorted(
            Path(temp).glob("page-*.png"),
            key=lambda p: int(p.stem.rsplit("-", 1)[-1]),
        ):
            with Image.open(image_path) as image:
                images.append(image.convert("RGB").copy())
        if not images:
            raise RuntimeError(f"Poppler rendered no pages for {pdf_path.name}")
        return images


def _render_with_pymupdf(pdf_path: Path, dpi: int) -> list[Image.Image]:
    """Use the backend's existing PyMuPDF dependency when Poppler is absent."""

    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "No PDF renderer is available. Install Poppler or PyMuPDF."
        ) from exc

    document = fitz.open(str(pdf_path))
    if document.needs_pass:
        document.close()
        raise RuntimeError("Password-protected PDFs are not supported.")
    images: list[Image.Image] = []
    try:
        for page in document:
            pixmap = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
            with Image.open(io.BytesIO(pixmap.tobytes("png"))) as image:
                images.append(image.convert("RGB").copy())
    finally:
        document.close()
    if not images:
        raise RuntimeError(f"PyMuPDF rendered no pages for {pdf_path.name}")
    return images
