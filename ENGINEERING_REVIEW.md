# Engineering Review — Document Suspicion System

Date: 2026-07-16 · Branch: `website-reorganization` · Reviewer: eng-manager pass

Scope: whole project. Three layers — Python detectors (`tools/`), FastAPI
`backend/`, Next.js/Cloudflare `frontend/`.

## What's already good (don't regress these)

- **Normalized envelope** (`backend/models.py`): eight detectors with different
  output shapes collapse into one `{outcome, risk, summary, findings_count,
  regions, raw}` contract; `raw` preserved for forward compat. Right abstraction.
- **Subprocess-per-detector boundary** (`backend/service.py`): keeps web code out
  of detector logic, as the README promises.
- **Lifecycle validation** (`AnalyzerRunState.validate_lifecycle`): illegal run
  states are unrepresentable. Thoughtful.

## Data flow

```
Browser (React SPA, polls every 1.8s)
  └─ fetch NEXT_PUBLIC_API_URL (default http://127.0.0.1:8000)
       └─ FastAPI: POST /jobs → BackgroundTasks → run_job
            └─ JobStore: in-memory dict + PDFs on disk (backend/data/)   ⚠ not durable
                 └─ subprocess.run per detector (360s each, SEQUENTIAL)   ⚠ up to 48 min
                      ├─ metadata/qr/font/moire/tamper/readability → fitz (PyMuPDF)
                      └─ scanner_noise, same_phone → pdftoppm (POPPLER)   ⚠ external dep
                           └─ normalize_analyzer_result → NormalizedAnalyzerResult
                                └─ regions drawn over page PNGs in the UI
```

## Prioritized backlog

### P0 — Critical

**A1. Persist jobs + review decisions.**
`JobStore` is an in-memory dict (`service.py:57`); `/review` writes the reviewer's
verdict there (`app.py:169`). On restart/crash/second-worker, all audit decisions
are lost and every job 404s while uploaded PDFs orphan on disk. A review workspace
whose reviews don't survive a restart fails at its core purpose.
Fix: move JobStore to SQLite (stdlib, single file — boring by default). Cannot run
`--workers >1` until this lands. Est CC: ~30 min.

### P1 — High

**A2. Retention sweep for uploaded PDFs + report dirs.**
Nothing deletes `backend/data/{job_id}.pdf` or `-report/` dirs. These carry PII
(names, DOB, parents' names — see `qr_local_lib.py:496`). Add a TTL-based delete,
bundled with A1's persistence change.

**A4. Auth is scaffolded but not enforced.**
`frontend/app/chatgpt-auth.ts` implements `requireChatGPTUser`, but `page.tsx`
never calls it and no backend endpoint checks identity. Upload, results, raw PDF,
and page renders are all open. CORS hard-codes a prod `chatgpt.site` origin
(`app.py:37`). Decide: internal-only (lock the network, document it) or wire the
ChatGPT auth through. Currently neither.

**A3. Poppler dependency is Windows-shaped and inconsistent.**
`scanner_noise` and `same_phone` shell out to `pdftoppm` with a bundled Windows
path (`locate_pdftoppm`); the other six detectors and the page renderer use `fitz`.
On Linux these two fail at runtime with a generic error. Fix: port both to `fitz`
(kills the external dep), or document Poppler as a hard requirement and make the
failure message name the missing binary. Recommend porting to fitz.

### P2 — Medium

**C1. Path-traversal hardening on the artifact endpoint.**
`get_artifact` (`app.py:155`) validates `filename` but not `analyzer`, which is
interpolated into `f"{job_id}-{analyzer}-report"`. Validate `analyzer` against the
known `ANALYZERS` keys (same guard `create()` already uses). ~5 min.

**A5. Delete dead D1/drizzle scaffolding.**
`worker/index.ts` declares `DB: D1Database`, `db/schema.ts` is a stub, and
`drizzle-orm`/`drizzle-kit` are deps — nothing uses a DB. Template residue. Remove
it, or actually adopt it if frontend-side persistence is chosen for A1.

**Tests.**
- Parametrize `run_job` across all five `kind`s (`json`/`font`/`report_dir`/
  `artifacts`/`stdout`). Today only the `json` happy path is mocked
  (`test_service.py:39`); the `crashed`/`Traceback`-sniffing logic at
  `service.py:154-166` is untested — that's where a real regression hides.
- Add QR rotation cases 90/180/270 for `_qr_point_to_original` (`models.py:229`);
  only rot0 is covered today.
- Add a `tamper_scan` region-extraction test (`models.py:313`).

### P3 — Low / cleanup

- **C3.** Add an ASCII "ANALYZER OUTPUT CONTRACT" comment above the `ANALYZERS`
  dict in `service.py` documenting the five `kind` contracts, so analyzer #9 has a
  map to follow.
- **C2.** Inline `resultSummary` (`page.tsx:65`) — it just returns `r.summary`.
- **Perf:** run detectors concurrently (`ThreadPoolExecutor` over the independent
  subprocesses) instead of the sequential loop (`service.py:116`); trim QR's
  3 DPI × 4 rotation × 4 variant matrix (48 decode passes/page). Deep-copy in
  `JobStore.get` (`service.py:94`, json round-trip per poll) is wasteful but not
  urgent.

## Suggested sequence

1. A1 + A2 together (persistence + retention) — unblocks multi-worker and fixes
   the core durability gap.
2. A4 auth decision — protects the PII.
3. A3 fitz port — makes Linux deploys reliable.
4. C1 + test parametrization — cheap correctness/hardening.
5. A5 + C2 + C3 — cleanup.
6. Perf (concurrency) — when wall-time becomes a real complaint.
