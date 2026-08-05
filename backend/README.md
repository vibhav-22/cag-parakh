# Backend API

This FastAPI service queues PDF and image screening jobs and runs each detector in a separate Python process. JPG, JPEG, PNG, WebP, and TIFF inputs are validated and normalized to PDF locally so every existing detector and review view can process them consistently.

## Run

```powershell
# From the repository root:
python -m pip install -r backend/requirements.txt
python -m uvicorn backend.app:app --reload

# Or, when your current directory is backend/:
python -m uvicorn --app-dir .. backend.app:app --reload
```

Use `http://127.0.0.1:8000/docs` for the API interface. Submit a PDF or supported image to `POST /api/v1/jobs`, then poll `GET /api/v1/jobs/{job_id}`.

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
- Job and batch state is persisted as `*.job.json` / `*.batch.json` beside the uploads, so history survives restarts. Development uses `backend/data`; installed builds use `%LOCALAPPDATA%/Parakh/data`. `PARAKH_DATA_DIR` can override the location.
- Analysis runs on a bounded worker pool (`ANALYSIS_WORKERS`, default 2) so a large batch queues instead of launching every detector at once.
- Set `PARAKH_AUTH_URL` to the HTTPS URL of the authorization service. Clients sign in with an approved email/password and the installation registers a random device ID. Browser cookies contain only an opaque, short-lived local session ID; central access tokens are never exposed to the frontend. Installed builds set `PARAKH_PACKAGED=1` and fail closed when authorization is missing. Development stays open only when both settings are absent.
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
