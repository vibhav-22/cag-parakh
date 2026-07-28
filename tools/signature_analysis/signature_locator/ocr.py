from __future__ import annotations

import csv
import io
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image


def run_ocr(
    image: Image.Image, tesseract: str | None = None, lang: str = "eng"
) -> list[dict[str, Any]]:
    executable = tesseract or shutil.which("tesseract")
    if not executable:
        return []
    import tempfile

    with tempfile.TemporaryDirectory(prefix="signature-ocr-") as temp:
        image_path = Path(temp) / "page.png"
        image.save(image_path)
        command = [
            executable,
            str(image_path),
            "stdout",
            "-l",
            lang,
            "--psm",
            "6",
            "tsv",
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=180, check=False
        )
    if result.returncode:
        return []
    words: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
        text = (row.get("text") or "").strip()
        try:
            confidence = float(row.get("conf", "-1"))
            if text and confidence >= 15:
                left, top = int(row["left"]), int(row["top"])
                width, height = int(row["width"]), int(row["height"])
                words.append(
                    {
                        "text": text,
                        "confidence": confidence,
                        "bbox_px": (left, top, left + width, top + height),
                        "line_key": (
                            row.get("block_num"),
                            row.get("par_num"),
                            row.get("line_num"),
                        ),
                    }
                )
        except (TypeError, ValueError, KeyError):
            continue
    return words
