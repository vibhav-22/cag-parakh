# Document Suspicion System

Reusable PDF screening tools for document capture consistency checks.

## Repository layout

- `tools/font_analysis`: extracts embedded font objects, typefaces, and usage details.
- `tools/ink_analysis`: overwriting and stroke-thickness checks.
- `tools/qr_analysis`: QR detection and document cross-checking.
- `tools/moire_analysis`: frequency-domain moire and recapture checks.
- `tools/metadata_analysis`: PDF metadata and structural-editing checks.
- `tools/readability_analysis`: PDF readability and OCR-quality checks.
- `tools/tamper_analysis`: multi-signal local tamper detection.
- `tools/capture_analysis`: scanner-noise and same-phone consistency checks.

The analysis tools remain command-line programs. `backend/` and `frontend/` can be added as separate application layers without mixing web code into detector logic.

These tools produce screening signals only. They should be combined with visual review, OCR, QR, metadata, and document-template checks before drawing conclusions.

## Extract fonts from a PDF

```powershell
python tools/font_analysis/pp.py "C:\path\to\document.pdf"
```

This writes `pdf_fonts.json` with distinct typefaces and embedded font details, plus `pdf_font_usage.csv` with page-level usage records.
