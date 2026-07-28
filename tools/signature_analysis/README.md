# Signature presence analyzer

Finds likely visible signatures in scanned and digitally generated PDFs and
returns normalized regions for the document review UI.

The analyzer is fully local and combines:

- PDF signature widgets;
- embedded raster objects;
- PDF text and optional Tesseract OCR anchors;
- conservative ink-shape analysis; and
- signing-zone evidence for photographed register/table forms.

It creates an annotated PDF plus a PNG crop for every candidate. Signature
presence is informational and remains low risk; the backend only requests review
when a document-specific `min_signatures` expectation is not met.

## Run directly

```powershell
python signature_presence_check.py document.pdf `
  --threshold 0.55 `
  --artifact-dir signature-report `
  --output signature.json
```

Useful options:

```text
--dpi 200
--threshold 0.55
--no-ocr
--ocr-lang eng
--tesseract PATH
--poppler-bin PATH
```

Poppler is preferred for PDF rendering. If it is unavailable, the analyzer
automatically uses PyMuPDF, which is already part of the backend dependencies.
Tesseract is optional; digital PDF text and the remaining visual branches still
run when it is absent.

The `weights/` directory is retained only as legacy project data. The current
analyzer does not load those weights and does not require Ultralytics or Torch.

## Limitations

Detections are review candidates, not signer authentication or cryptographic
signature validation. Stamps, dense handwriting, very faint pencil, unusual
rotations, and unseen form layouts can still cause false positives or misses.
Validate changes on an independent, representative holdout set before relying
on signature counts operationally.
