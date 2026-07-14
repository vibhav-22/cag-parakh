# Document Suspicion System

Reusable PDF screening tools for document capture consistency checks.

## Repository layout

- `tools/font_analysis`: font and template consistency checks.
- `tools/ink_analysis`: overwriting and stroke-thickness checks.
- `tools/qr_analysis`: QR detection and document cross-checking.
- `tools/readability_analysis`: PDF readability and OCR-quality checks.
- `tools/tamper_analysis`: multi-signal local tamper detection.
- `tools/capture_analysis`: scanner-noise and same-phone consistency checks.
- `data/baselines`: versioned detector reference data.

The analysis tools remain command-line programs. `backend/` and `frontend/` can be added as separate application layers without mixing web code into detector logic.

These tools produce screening signals only. They should be combined with visual review, OCR, QR, metadata, and document-template checks before drawing conclusions.
