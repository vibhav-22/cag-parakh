# Parakh — Document Suspicion System

Parakh is a local-first document screening and review application for PDF and image files. It combines deterministic document-analysis tools with a reviewer workspace, batch/project management, evidence overlays, exports, offline employee authorization, and an optional local vision-language model (VLM) for document questions.

> Parakh produces screening signals, not a legal or final fraud determination. A human reviewer must interpret the evidence and make the final decision.

## What the application does

- Accepts PDF, JPG/JPEG, PNG, WebP, and TIFF documents (up to 25 MB each; 50 files per batch).
- Normalizes image uploads to PDF so every analyzer and review screen uses one document format.
- Runs independent checks for metadata, QR codes, fonts, moiré/recapture patterns, capture consistency, correction-fluid/tamper regions, readability, document photos, and signatures.
- Stores analyzer state separately, allowing interrupted jobs to resume without discarding completed checks.
- Groups cases into batches and projects, detects repeated document hashes, QR identities, and face identities, and records reviewer decisions.
- Renders the original document locally with page-level evidence regions.
- Exports case JSON/HTML and batch CSV/XLSX/HTML reports.
- Optionally answers document questions through an OpenAI-compatible local multimodal model. This feature is separate from screening and never changes a verdict.
- Runs as a browser-based development stack or as a self-contained offline Windows/Electron application.

## Technology stack

| Layer | Technologies |
| --- | --- |
| Frontend | TypeScript, React 19, Next-compatible App Router, Vinext, Vite, CSS, Phosphor Icons |
| Desktop | Electron 39, electron-builder, NSIS, Node.js 22+ |
| API | Python 3.13 for release builds, FastAPI, Uvicorn, Pydantic |
| Persistence | SQLite with WAL transactions; JSON payloads stored inside SQLite; sharded local PDF/artifact files |
| PDF/image processing | PyMuPDF, pypdf, pdfplumber, Pillow, OpenCV, NumPy, SciPy, Matplotlib |
| OCR and codes | Tesseract/pytesseract, Poppler, zxing-cpp |
| Face analysis | InsightFace, ONNX Runtime |
| Reporting | openpyxl, HTML, CSV, JSON |
| Authorization | Ed25519-signed authorization manifests, Argon2id password hashes, Windows DPAPI-protected session state |
| Optional Visual AI | Qwen3-VL model pack, llama.cpp-compatible OpenAI API, Vulkan/CPU runtime |
| Quality/release | pytest/unittest, Node test runner, ESLint, PowerShell build verification, PyInstaller-based backend bundle |

Exact frontend versions are in [`frontend/package.json`](frontend/package.json). Python development ranges are in [`backend/requirements.txt`](backend/requirements.txt); reproducible Windows release versions are pinned in [`build/requirements-runtime.lock`](build/requirements-runtime.lock).

## Architecture and data flow

```text
Reviewer
   │
   ▼
React/Vinext UI ── same-origin /api requests ──► FastAPI
   ▲                                             │
   │                                             ├─► SQLite job/project/batch store
Electron gateway (desktop)                      ├─► sharded PDFs and artifacts
   │                                             ├─► Python detector subprocesses
   ├─ launches Vinext + FastAPI                  └─► optional local VLM server
   └─ allocates loopback ports and launch token
```

1. The UI preflights selected files and submits a batch with chosen analyzers/settings.
2. FastAPI validates each file, converts supported images to PDF, computes a SHA-256 digest, persists the job, and queues it.
3. A bounded job worker pool runs each job; a second bounded pool runs that job's detector scripts concurrently in isolated Python processes.
4. Detector-specific output is normalized into a shared `clear`, `review`, `inconclusive`, or `error` contract while retaining the raw result.
5. SQLite is updated after every analyzer transition. The frontend polls the batch/job APIs and renders results and evidence.
6. Reviewer decisions and notes are stamped with the authenticated local user and included in reports.

The detector scripts remain independent command-line tools. The API orchestrates them rather than duplicating their analysis logic.

## Repository map

| Path | Purpose |
| --- | --- |
| [`frontend/`](frontend/) | React/Vinext reviewer UI and Electron desktop shell. See [`frontend/README.md`](frontend/README.md). |
| [`backend/`](backend/) | FastAPI, orchestration, persistence, access control, reports, and tests. See [`backend/README.md`](backend/README.md). |
| [`tools/`](tools/) | Standalone detector implementations and utility scripts. See [`tools/README.md`](tools/README.md). |
| [`build/`](build/) | Windows bundle, installer, model-pack, release verification, and pilot scripts. See [`build/README.md`](build/README.md). |
| [`license_server/`](license_server/) | Offline authorization-management source (the former network server is retired). |
| `vendor/` | Locally staged Tesseract, Poppler, and model dependencies; generated/ignored. |
| `release/` | Installer, admin utility, and model-pack outputs; generated/ignored. |
| `tmp/` | Disposable packaging inputs and generated working files; ignored. |
| `license-data/` | Local authorization development data; ignored and never transfer as source. |
| `.agents/`, `.codex/`, `.claude/`, `.gstack/` | Local development-agent metadata; not application runtime code. |

The root operational documents cover [`INSTALLATION.md`](INSTALLATION.md), [`ADMINISTRATION.md`](ADMINISTRATION.md), [`DESKTOP_DEMO.md`](DESKTOP_DEMO.md), [`PACKAGING_SECURITY.md`](PACKAGING_SECURITY.md), and release acceptance in [`build/PILOT.md`](build/PILOT.md).

## Development setup

### Prerequisites

- Windows is the supported desktop/release platform.
- Python 3.13 x64 is recommended (the release lock is generated for it).
- Node.js 22.13 or newer and npm.
- Tesseract and Poppler on `PATH` for complete OCR/readability coverage, or set the overrides documented in the backend README.
- The InsightFace `buffalo_l` model for batch face-identity matching. Release builds stage it under `vendor/`.

### 1. Backend

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install --upgrade pip
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
$env:PARAKH_DEV_AUTH_BYPASS = "1"
backend\.venv\Scripts\python.exe -m uvicorn backend.app:app --reload
```

The API runs at `http://127.0.0.1:8000`; Swagger is at `http://127.0.0.1:8000/docs`. The bypass creates a local development identity only and is ignored in packaged mode. For real authorization testing, omit it and configure signed files as described in [`ADMINISTRATION.md`](ADMINISTRATION.md).

### 2. Frontend

In a second terminal:

```powershell
cd frontend
Copy-Item .env.example .env
npm install
npm run dev
```

Open `http://127.0.0.1:3000`. Keep `NEXT_PUBLIC_API_URL` empty: the browser must call the same frontend origin so the HTTP-only session cookie and proxy behavior remain correct.

### Desktop development demo

After installing both backend and frontend dependencies:

```powershell
$env:PARAKH_DEV_AUTH_BYPASS = "1"
cd frontend
npm run demo
```

Electron selects free loopback ports, launches FastAPI and Vinext, proxies `/api/*` and `/health`, and shuts the children down with the window. See [`DESKTOP_DEMO.md`](DESKTOP_DEMO.md).

## Where data is stored

Parakh is local-first; normal screening does not upload documents to a cloud service.

| Mode/data | Location |
| --- | --- |
| Backend from source | `backend/data/` |
| Electron source demo | Electron user-data directory under `demo-data/` |
| Installed app data | `%LOCALAPPDATA%\Parakh\data\` |
| Installed authorization file | `%LOCALAPPDATA%\Parakh\authorization\authorization.json` |
| Immutable authorization public key | Installed application resources |
| Optional model pack | Separate versioned installation; resolved by its pack manifest/environment |

`PARAKH_DATA_DIR` overrides the mutable data directory. Within it:

- `job-store.sqlite3` stores projects, batches, jobs, ownership, status, review data, settings, and complete JSON payloads. SQLite runs in WAL mode with full synchronization.
- `documents/<2-char>/<2-char>/<job-id>.pdf` stores normalized source documents in a two-level hash shard.
- `<job-id>-<analyzer>.json` and `<job-id>-<analyzer>-report/` store detector reports and evidence artifacts.
- `vlm-documents/` stores documents opened in the standalone Ask Documents workflow plus metadata/index caches.
- `.device-id` is the random installation identifier; the protected access-state file holds DPAPI-encrypted sessions/throttling state on Windows.

Legacy flat JSON/PDF storage is imported once into SQLite and the sharded tree; original legacy files are retained as a rollback copy. Back up the entire data directory while the application is stopped. Do not commit it: `backend/data/`, authorization data, models, vendor binaries, releases, logs, and local environment files are intentionally ignored.

If `VLM_ALLOW_REMOTE=1` points the VLM at a non-loopback service, selected page images and extracted text leave the machine. This is the main exception to the local-only data path and must be approved as a privacy decision.

## Tests and validation

```powershell
# Backend
backend\.venv\Scripts\python.exe -m pytest backend\tests

# Frontend (build + rendered HTML/UI/flight contracts)
cd frontend
npm test
npm run lint
```

For release validation, follow [`build/PILOT.md`](build/PILOT.md) and run the clean-machine and golden-regression checks. Detector outputs are sensitive to native dependency/model versions, so do not refresh the runtime lock casually.

## Building and releasing

The system was built as three deliberately separated layers: reusable CLI detectors, an orchestration/storage API, and a reviewer UI. Electron then packages the UI and launches a frozen local backend. This keeps detector research testable outside the web app and keeps employee machines offline and dependency-free.

Do not run `electron-builder` directly for a production handoff. The root build scripts stage and verify embedded Python, detectors, native binaries, authorization material, resources, and hashes first.

```powershell
powershell -File build\stage-vendor.ps1
powershell -File build\make-installer.ps1 `
  -PythonEmbedZip tmp\packaging-inputs\python-3.13.14-embed-amd64.zip `
  -BuildPythonExe C:\Path\To\Python313\python.exe `
  -PublicKeyFile D:\ParakhKeys\authorization-public.pem
```

Outputs go to `release/windows/`. The optional Qwen/llama.cpp model is built and delivered separately; screening remains functional without it. See [`build/README.md`](build/README.md) and [`build/model-pack/README.md`](build/model-pack/README.md).

## Security and ownership notes

- Never commit or transfer an Ed25519 private key, its passphrase, real `authorization.json`, employee passwords, customer documents, `backend/data/`, or VLM model weights inside the source repository.
- The employee installer contains only the authorization public key. Keep the encrypted private key and recovery passphrase on a controlled administrator workstation with a separate offline backup.
- Packaged mode requires the signed authorization file and a per-launch secret header; the development bypass is explicitly disabled.
- Browser cookies contain only an opaque local session ID and use HTTP-only, strict same-site handling.
- Production installers should be Authenticode-signed. Unsigned builds are internal pilots and may trigger SmartScreen.
- Uninstall intentionally preserves `%LOCALAPPDATA%\Parakh`; retention and secure deletion are an operational responsibility (see [`PACKAGING_SECURITY.md`](PACKAGING_SECURITY.md)).

## Transfer checklist

Before handing the project to a new maintainer:

1. Transfer the Git repository without ignored runtime data or secrets.
2. Transfer authorization signing custody through a separate approved channel; verify the public key used by the build matches the held private key.
3. Record the last accepted installer, `RELEASE-MANIFEST.json`, SHA-256, model-pack version, and [`build/PILOT.md`](build/PILOT.md) results.
4. Give the recipient access to approved Python embed archives, offline wheelhouse, vendor binaries/models, code-signing credentials, and CI secrets separately.
5. Back up any production `%LOCALAPPDATA%\Parakh` data according to policy; restore it only while Parakh is stopped.
6. Run backend tests, frontend tests/lint, release verification, and a clean-laptop smoke test before ownership changes.
7. Review the known production gaps in [`PACKAGING_SECURITY.md`](PACKAGING_SECURITY.md).

## Troubleshooting quick reference

- **Sign-in/configuration error in development:** set `PARAKH_DEV_AUTH_BYPASS=1` before starting the backend, or configure valid signed authorization files.
- **Frontend cannot reach the API:** keep `NEXT_PUBLIC_API_URL` empty and check `BACKEND_URL` plus `http://127.0.0.1:8000/health`.
- **OCR/readability or capture checks are degraded:** call `GET /api/v1/diagnostics/dependencies` and install/configure Tesseract, Poppler, and the InsightFace model.
- **Electron demo uses the wrong Python:** set `PARAKH_PYTHON` to the desired interpreter.
- **Second desktop window does not open:** this is intentional; one instance protects the shared SQLite store.
- **Visual AI is unavailable:** deterministic screening still works. Check the model-pack status or VLM variables in [`backend/README.md`](backend/README.md).
