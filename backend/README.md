# Parakh backend

This local FastAPI service handles document intake, analyzer orchestration, persistent projects/batches/jobs, document rendering, reviewer decisions, exports, offline authorization, diagnostics, and optional Visual AI document questions. JPG, JPEG, PNG, WebP, and TIFF inputs are validated and normalized to PDF so every detector and review view uses one format.

## Stack and code map

Python 3.13 is the release target. FastAPI, Uvicorn, and Pydantic provide the API; SQLite stores indexed state and complete JSON payloads. Processing uses PyMuPDF, pypdf, pdfplumber, Pillow, OpenCV, NumPy, SciPy, Tesseract/Poppler, and zxing-cpp. InsightFace/ONNX Runtime handle face embeddings and openpyxl builds XLSX reports.

| File | Responsibility |
| --- | --- |
| `app.py` | Routes, validation, sessions, image conversion/rendering |
| `service.py` | SQLite store, queues, analyzer subprocesses, recovery, settings |
| `models.py`, `checks.py` | State, normalized results, and deterministic criteria |
| `reporting.py` | JSON/HTML/CSV/XLSX exports |
| `access_control.py`, `auth_manifest.py` | Signed authorization and protected sessions |
| `dependencies.py` | Native binary/model discovery and diagnostics |
| `qr_identity.py`, `photo_identity.py` | Cross-document identity matching |
| `vlm*.py` | Optional multimodal Q&A and model-pack integration |
| `tests/` | API, store, detector, authorization, reporting, and VLM tests |

## Run

```powershell
# From the repository root:
python -m pip install -r backend/requirements.txt
$env:PARAKH_DEV_AUTH_BYPASS = "1"
python -m uvicorn backend.app:app --reload

# Or, when your current directory is backend/:
python -m uvicorn --app-dir .. backend.app:app --reload
```

Use `http://127.0.0.1:8000/docs` for the API interface. Submit a PDF or supported image to `POST /api/v1/jobs`, then poll `GET /api/v1/jobs/{job_id}`.

`PARAKH_DEV_AUTH_BYPASS=1` creates a source-only local development identity and is ignored in packaged mode. Without it, valid signed authorization and Windows DPAPI support are required. Packaged mode also disables `/docs` and `/redoc`.

## Visual AI model

Visual AI is optional and disabled by default. It uses an OpenAI-compatible multimodal chat-completions endpoint, allowing the application to work with a local llama.cpp-compatible server or a separately managed private inference service.

Example local configuration:

```powershell
# Start a multimodal model server separately on port 8080, then:
$env:VLM_ENABLED = "1"
$env:VLM_BASE_URL = "http://127.0.0.1:8080/v1"
$env:VLM_MODEL = "Qwen3-VL-4B-Instruct"
python -m uvicorn backend.app:app --reload
```

Configuration is documented in `.env.example`. Non-loopback endpoints are rejected unless `VLM_ALLOW_REMOTE=1` is explicitly set; enabling that option means page images and extracted document text leave the application host.

- `GET /api/v1/vlm/status` reports whether the configured model service is ready.
- `POST /api/v1/vlm/documents` opens a document in the standalone visual Q&A operation; it does not create a screening job or affect a case verdict.
- `POST /api/v1/vlm/documents/{document_id}/questions` accepts `{"question":"...", "current_page":1}` and returns an answer, confidence, retrieved pages, and page citations.
- Question retrieval caches embedded PDF text and applies bounded OCR to image-only pages (`VLM_OCR_MAX_PAGES`, default 80). Large scans beyond that budget are sampled and the answer reports partial indexing as a limitation.
- VLM calls are serialized by default with `VLM_MAX_CONCURRENCY=1` to avoid exhausting local GPU or system memory.

The model is an advisory review layer. It does not replace the deterministic detectors and must not be used as a final authenticity or fraud determination.

## Batches, history, and access control

- `POST /api/v1/batches` accepts many PDFs or images at once (`files` form fields, up to 50 per batch, 25 MB each) and creates one job per document. Images are limited to 50 megapixels after decoding. Poll `GET /api/v1/batches/{batch_id}` for the combined state, or `GET /api/v1/batches` for the stored history.
- Job, batch, and project state is persisted in `job-store.sqlite3` using WAL transactions and indexed lookup columns. PDFs are stored under `documents/<hash-prefix>/<hash-prefix>/` so hundreds of thousands of offline documents do not accumulate in one directory. Development uses `backend/data`; installed builds use `%LOCALAPPDATA%/Parakh/data`. `PARAKH_DATA_DIR` can override the location.
- On the first launch after upgrading, legacy `*.job.json`, `*.batch.json`, `*.project.json`, and flat PDFs are imported into SQLite and the sharded document tree. The old files are retained as a rollback copy. Jobs interrupted while queued or running are reset to the queue at analyzer granularity, preserving checks that already reached a terminal state.
- `GET /api/v1/batches` and `GET /api/v1/projects/{project_id}/batches` accept `limit` (up to 200), `cursor`, or `offset`. Paginated responses retain the existing JSON-array body and return `X-Total-Count` plus `X-Next-Cursor` headers, so older clients remain compatible while large local histories can use index-backed keyset pagination.
- Analysis runs on a bounded worker pool (`ANALYSIS_WORKERS`, default 2) so a large batch queues instead of launching every detector at once.
- Authorization is fully offline. Clients sign in against an Ed25519-signed `authorization.json`; the installation registers a random device ID and browser cookies contain only an opaque local session ID. Installed builds set `PARAKH_PACKAGED=1` and fail closed when authorization is missing. Source development requires the explicit bypass above or real signed files.
- QR scanning effort is tunable with `QR_DPIS` / `QR_ROTATIONS` (defaults `300` and `0,90` for responsiveness).
- Document-photo search effort is a per-run setting (`photo_detection.effort`: `low` / `medium` / `high`, default `medium`). `low` reads only the images embedded in the PDF and never renders a page, so a miss is reported as inconclusive rather than a fail; `medium` renders every page only when the embedded pass finds nothing; `high` always renders at 400 dpi and sweeps densely, which is what finds a second portrait beside the first. Measured on this corpus: roughly 2–6 s, 3–9 s, and 8–35 s per short document, with a 43-page file taking 166 s at `high` against the 360 s detector timeout.
- Face-identity matching across a batch uses `PHOTO_SIMILARITY_THRESHOLD` (default `0.35`) and `PHOTO_IDENTITY_DET_THRESHOLD` (default `0.3`, the face-detector floor used when re-detecting inside an already-cropped photo).

Every job includes an `analyzer_runs` entry for each requested check. A run moves through `queued`, `running`, and either `completed` or `failed`, with timestamps, a normalized result, and an execution error when applicable. Completed results are also indexed by analyzer ID in `results` and use one stable shape:

```json
{
  "analyzer_id": "metadata",
  "outcome": "clear | review | inconclusive | error",
  "risk": "low | medium | high | unknown",
  "summary": "Low risk",
  "findings_count": 0,
  "artifacts": [],
  "exit_code": 0,
  "raw": {}
}
```

`raw` retains the analyzer's original report for detailed evidence and forward compatibility.

The web review surface uses `GET /api/v1/jobs/{job_id}/document/manifest` and
`GET /api/v1/jobs/{job_id}/document/pages/{page}.png` to render the PDF inside
the page and place normalized `regions` over the relevant areas. The original
PDF endpoint uses inline content disposition, so opening it does not force a
download.

The API exposes these single-document screening checks: metadata, QR presence, fonts, moire, same-phone consistency, correction-fluid (whitener) detection, readability, and document-photo detection. Visual document Q&A is a separate operation. The photo result retains a detected passport-style crop as a job artifact so the review workspace can show it inline; a missing or low-quality photo is sent for review. The whitener result includes a document probability, per-page candidates, normalized overlay regions, and links to annotated page/PDF artifacts. Reference/batch tools and the standalone ink-analysis utilities remain command-line workflows.

## Processing, storage, and configuration

Preflight validates file type, size, decoded image dimensions, and duplicate hashes. Intake normalizes the document, persists it, and queues analyzer work. `ANALYSIS_WORKERS` controls concurrent jobs (default 2); `ANALYZER_WORKERS` controls detector subprocesses per job (default 8). Interrupted queued/running work is recovered on startup at analyzer granularity.

SQLite tables are `projects`, `batches`, `jobs`, and `store_meta`. Indexed lookup fields sit beside a complete `payload_json`. The store uses WAL, foreign keys, a 30-second busy timeout, and `synchronous=FULL`. Source data defaults to `backend/data/`; installed data defaults to `%LOCALAPPDATA%\Parakh\data\`; `PARAKH_DATA_DIR` overrides both. Stop the service before copying the entire directory for backup or restore.

Important variables are:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PARAKH_DEV_AUTH_BYPASS` | off | Source-only development identity |
| `PARAKH_AUTH_FILE`, `PARAKH_AUTH_PUBLIC_KEY_FILE` | mode-specific | Offline authorization files |
| `PARAKH_DATA_DIR` | mode-specific | Mutable data location |
| `PARAKH_PACKAGED`, `PARAKH_LAUNCH_TOKEN` | launcher-managed | Packaged security controls |
| `CORS_ORIGINS`, `COOKIE_SECURE` | loopback/off | Browser and cookie policy |
| `ANALYSIS_WORKERS`, `ANALYZER_WORKERS` | `2`, `8` | Job and analyzer concurrency |
| `QR_DPIS`, `QR_ROTATIONS` | `300`, `0,90` | Fast QR scan |
| `QR_DEEP_DPIS`, `QR_DEEP_ROTATIONS` | `250,350,450`, all rotations | QR miss escalation |
| `QR_DEEP_RESCAN`, `QR_DEEP_TIMEOUT_SECONDS` | `1`, `1200` | Deep QR behavior |
| `PHOTO_SIMILARITY_THRESHOLD` | `0.35` | Batch face match |
| `PHOTO_IDENTITY_DET_THRESHOLD` | `0.3` | Face detection floor |

Native overrides are `PARAKH_TESSERACT`, `PDFTOPPM`, `PARAKH_PDFINFO`, `PARAKH_INSIGHTFACE_MODEL`, and `INSIGHTFACE_HOME`. Inspect resolved paths and lost coverage at `GET /api/v1/diagnostics/dependencies`. VLM options also include `VLM_API_KEY`, `VLM_TIMEOUT_SECONDS`, `VLM_MAX_CONCURRENCY`, `VLM_OCR_MAX_PAGES`, `VLM_OCR_DPI`, `VLM_OCR_LANGUAGES`, and `VLM_JSON_MODE`.

## Tests

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests
```

Use the exact release dependencies in [`../build/requirements-runtime.lock`](../build/requirements-runtime.lock) and the acceptance procedure in [`../build/PILOT.md`](../build/PILOT.md) for installer releases.
