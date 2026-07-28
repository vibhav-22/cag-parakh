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
- Job and batch state is persisted as `*.job.json` / `*.batch.json` beside the uploads in `backend/data`, so history survives restarts. Jobs interrupted by a restart are closed out with failed runs rather than left hanging.
- Analysis runs on a bounded worker pool (`ANALYSIS_WORKERS`, default 2) so a large batch queues instead of launching every detector at once.
- Set `ACCESS_CODE` to require sign-in: clients call `POST /api/v1/session` with `{"access_code": "..."}` once (an HTTP-only cookie is set) or send `X-Access-Code` per request. When `ACCESS_CODE` is unset the API stays open for local development. Set `COOKIE_SECURE=1` behind HTTPS.
- QR scanning effort is tunable with `QR_DPIS` / `QR_ROTATIONS` (defaults `300` and `0,90` for responsiveness).

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

The API exposes these single-document screening checks: metadata, QR presence, fonts, moire, scanner noise, same-phone consistency, correction-fluid (whitener) detection, readability, and document-photo detection. Visual document Q&A is a separate operation. The photo result retains a detected passport-style crop as a job artifact so the review workspace can show it inline; a missing or low-quality photo is sent for review. The whitener result includes a document probability, per-page candidates, normalized overlay regions, and links to annotated page/PDF artifacts. Reference/batch tools and the standalone ink-analysis utilities remain command-line workflows.
