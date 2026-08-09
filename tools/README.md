# Detector tools

This folder contains Parakh's reusable command-line analyzers. They are intentionally independent of FastAPI and React: each accepts a local document, writes machine-readable output and evidence artifacts, and can be tested on its own. `backend/service.py` maps these tools into subprocesses and normalizes their results.

## Tool map

| Folder | Purpose | API analyzer |
| --- | --- | --- |
| `metadata_analysis/` | PDF metadata and structural-editing signals | `metadata` |
| `qr_analysis/` | QR detection, decoding, and payload comparison | `qr_presence` |
| `font_analysis/` | Embedded fonts, typefaces, usage, and baselines | `font_analysis` |
| `moire_analysis/` | Frequency-domain moiré/recapture screening | `moire` |
| `capture_analysis/same_phone/` | Capture-workflow and same-phone consistency | `same_phone` |
| `capture_analysis/scanner_noise/` | Scanner-noise fingerprint comparison | standalone |
| `tamper_analysis/` | Correction-fluid/whitener probability and regions | `tamper_scan` |
| `readability_analysis/` | OCR, stream integrity, sharpness, noise, and readability | `readability` |
| `photo_analysis/` | Document-photo extraction, face quality, and embeddings | `photo_detection` |
| `signature_analysis/` | Signature presence and location | `signature` |
| `ink_analysis/` | Overwriting and stroke-thickness investigations | standalone |
| `flight/` | Generates frontend welcome-flight media | build utility |
| `fonts/` | Synchronizes self-hosted frontend fonts | build utility |

Several subfolders have their own README with detector-specific arguments and setup.

## Runtime contract

- Tools run locally and produce screening signals, not final authenticity claims.
- Keep CLI arguments backward compatible because `backend/service.py` constructs them directly.
- Write output to the path supplied on the command line; do not write into source folders during screening.
- Return page/evidence coordinates where possible so the backend can build normalized overlays.
- Put artifacts in the provided report directory and return safe relative names.
- A nonzero exit, timeout, missing report, or malformed output becomes an analyzer `error`. Express legitimate coverage limitations as `inconclusive` data.

## Dependencies

Shared packages are declared in [`../backend/requirements.txt`](../backend/requirements.txt). Some tools also have a local `requirements.txt` for standalone use. Complete coverage requires Tesseract, Poppler, and the InsightFace `buffalo_l` model; releases stage verified copies under `vendor/`.

Do not commit downloaded models or generated reports. Signature weights have separate instructions in [`signature_analysis/weights/README.md`](signature_analysis/weights/README.md).

## Adding or changing an analyzer

1. Keep the detector usable as a standalone CLI with deterministic output.
2. Add or update its entry and command in `backend/service.py`.
3. Add criteria in `backend/checks.py` and normalization/evidence mapping in `backend/models.py` as needed.
4. Test success, no-signal/inconclusive behavior, malformed output, and artifacts.
5. Add frontend labels, types, and settings if it is reviewer-selectable.
6. Update reports and the golden regression corpus before release.

Run the full backend suite after detector changes:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests
```
