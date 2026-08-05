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

Model weights are not committed to or bundled with this repository. A software installer can either deliver a separately licensed offline model pack or download the pinned model automatically on first use. See `backend/README.md` and `backend/.env.example` for configuration and privacy controls.

## Local analysis with approved accounts

The web app supports multi-document batches, stored history, individual accounts, and per-laptop approval. Documents and detector work remain local; only account and device authorization use the central service.

```powershell
# Backend connected to the authorization service:
$env:PARAKH_AUTH_URL = "https://accounts.example.com"
python -m uvicorn backend.app:app --host 127.0.0.1

# Frontend:
cd frontend; npm run dev
```

Open `http://localhost:3000`, sign in with an approved account, and drop up to 50 PDFs or images at once. Development remains open when `PARAKH_AUTH_URL` and `PARAKH_PACKAGED` are unset. Installed builds set `PARAKH_PACKAGED=1` and fail closed unless an authorization service is configured. See `license_server/README.md` for setup and account management.

## Extract fonts from a PDF

```powershell
python tools/font_analysis/pp.py "C:\path\to\document.pdf"
```

This writes `pdf_fonts.json` with distinct typefaces and embedded font details, plus `pdf_font_usage.csv` with page-level usage records.
