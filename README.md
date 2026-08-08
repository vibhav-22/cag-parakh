# Document Suspicion System

Reusable PDF and image screening tools for document capture consistency checks.

## Repository layout

- `tools/font_analysis`: extracts embedded font objects, typefaces, and usage details.
- `tools/ink_analysis`: overwriting and stroke-thickness checks.
- `tools/qr_analysis`: QR detection and document cross-checking.
- `tools/moire_analysis`: frequency-domain moire and recapture checks.
- `tools/metadata_analysis`: PDF metadata and structural-editing checks.
- `tools/readability_analysis`: PDF readability and OCR-quality checks.
- `tools/tamper_analysis`: deterministic correction-fluid (whitener) detection with local optical cues and annotated evidence.
- `tools/capture_analysis`: scanner-noise and same-phone consistency checks.
- `tools/photo_analysis`: passport-style document photo extraction and face-quality checks.

The analysis tools remain command-line programs. `backend/` and `frontend/` can be added as separate application layers without mixing web code into detector logic.

These tools produce screening signals only. They should be combined with visual review, OCR, QR, metadata, and document-template checks before drawing conclusions.

The photo extractor works immediately with OpenCV. For the higher-recall
InsightFace backend added by the upstream tool, install
`tools/photo_analysis/requirements.txt`; its model is downloaded locally on
first use. Extracted photos are stored with the job and shown directly in the
web result.

## Backend API

`backend/` provides a FastAPI layer for uploading PDFs or common document images and polling asynchronous screening jobs. JPG, JPEG, PNG, WebP, and TIFF uploads are normalized to PDF locally before the existing detectors run. Install and run it with:

```powershell
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.app:app --reload
```

The API documentation is available at `http://127.0.0.1:8000/docs`. See `backend/README.md` for endpoints and usage.

## Standalone Visual AI document questions

The backend can connect to an OpenAI-compatible vision-language model server. When configured, the web app provides a separate **Ask documents** operation for uploading a PDF or image and asking evidence-grounded questions with page citations. It is not a screening check and does not influence case results or verdicts. Large PDFs are indexed page-wise and questions retrieve only the most relevant pages.

Start a local multimodal server, then enable the integration before starting FastAPI:

```powershell
$env:VLM_ENABLED = "1"
$env:VLM_BASE_URL = "http://127.0.0.1:8080/v1"
$env:VLM_MODEL = "Qwen3-VL-4B-Instruct"
python -m uvicorn backend.app:app --reload
```

Model weights are not committed to the repository or main installer. The pinned
Qwen model, projector, tokenizer metadata, and llama.cpp runtimes ship only in a
separate versioned offline model pack; the employee application never downloads
them on first use. See `build/model-pack/README.md` for licensing and packaging.

## Offline desktop application

The Windows application performs document screening and authorization locally.
Approved employees are stored as Argon2id password records in a company-issued,
Ed25519-signed authorization file. The installer contains only the public
verification key; the private signing key remains with an administrator.

The application, embedded Python, detector dependencies, and optional Qwen
runtime need no Python, Node, Docker, WSL, or command prompt on employee
laptops. See `INSTALLATION.md` for employees, `ADMINISTRATION.md` for
authorization and releases, and `build/PILOT.md` for release acceptance.

## Extract fonts from a PDF

```powershell
python tools/font_analysis/pp.py "C:\path\to\document.pdf"
```

This writes `pdf_fonts.json` with distinct typefaces and embedded font details, plus `pdf_font_usage.csv` with page-level usage records.
