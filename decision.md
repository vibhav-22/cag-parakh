# Decisions log

Chronological record of notable code changes in this repo — why they were made, not just what changed. Newest entries at the top.

---

## 2026-07-29 — The Excel export is rebuilt around the questions an auditor actually asks

**What:** Four changes to `backend/reporting.py`, from reading the workbook as the audit officer who receives it rather than as the reviewer who produced it:

1. **A Metadata sheet.** `_check_metadata` already reported Producer, Creator, Created, Modified, re-save structure, page/image/annotation counts and editing signals — none of it reached Excel, because `metadata` grades `info` and `_CHECK_SHEETS` only listed graded checks. It now leads the check tabs, with a derived **"Edited after creation (Y/N)"** column (`_edited_flag`) reading either an incremental-update structure or a modification timestamp that differs from creation.
2. **A Duplicates sheet.** One row per matched *pair*, grouped into clusters by union-find (`_Clusters`), sourced from three signals: SHA-256 equality computed across the batch, `photo_identity` face matches, and `qr_identity` payload matches.
3. **Summary gains the case-record columns** — SHA-256, screening test, both timestamps, reviewer/reviewed-at/assigned-to, notes, unanalyzable reason — plus two derived triage columns, **"Duplicate signals"** and a numeric **"Failed checks"**.
4. **Scores are written as numbers** (`_NUMERIC_FACTS`, `_numeric_fact`), with display supplied by a number format.

**Why:** The workbook served per-document triage and almost nothing else. Three specific defects:

- *Provenance was suppressed by a grading decision.* `info` correctly means "assigns no blame"; it was being read as "says nothing". Provenance is what a batch gets read **across** — sorting a hundred documents by Producer or Created clusters the ones made by one hand at one sitting, which no per-document verdict can show.
- *Copying evidence existed and was discarded.* `sha256` is on every job and was never printed. Byte-identical resubmission under a new name is the cheapest fraud to prove, and the proof was being thrown away at export.
- *Scores sorted lexically.* Facts are stored as display strings, so "9%" filed after "37%" — putting the borderline rows, the only ones worth reading, in an arbitrary place. `_PERCENT_FORMAT` had already fixed this for Similarity alone; the same treatment now covers every scalar.

**Two judgements worth recording:**

*Near misses are listed but never clustered.* A face score above `_NEAR_MISS_FLOOR` (25%, just above the measured different-person 95th percentile of 0.252) that did not reach the 35% match threshold gets its own row typed "Face near miss", with an empty Cluster. Folding it into a cluster would launder a lead into a claim.

*Rows are ordered by strength of evidence, not by score.* `_MATCH_RANK` puts an identical digest above a QR payload above a face score, because sorting on the number alone filed the weaker row first whenever the stronger one carried no score — a hash match has no percentage.

*The closing notes are load-bearing.* Face matching yields `{}` silently when no identity model is installed, so an empty Duplicates sheet has two readings — "nothing matched" and "nothing was compared" — and only one is good news. The sheet states which it is, per signal.

**Not done in this pass:** the Overview cover sheet, the Exceptions worklist, a Whitener/tamper tab (`tamper_scan` still has no sheet of its own), conditional formatting, and carrying `PreflightFile.prior_screenings` — cross-*batch* resubmission — into the export. Duplicate detection remains scoped to one batch.

**Files:** [backend/reporting.py](backend/reporting.py), [backend/tests/test_reporting_xlsx.py](backend/tests/test_reporting_xlsx.py). No API change: `batch_xlsx` already received both duplicate maps from [backend/app.py](backend/app.py).

---

## 2026-07-29 — The wide QR sweep needs its own timeout, and a cut-short sweep is inconclusive

**What:** Two changes, both found by re-running the QR check on the real batch `dc13f4ce…` rather than trusting the unit tests:

1. `QR_DEEP_TIMEOUT_SECONDS` (default 1200) now budgets the wide sweep separately from `TIMEOUT_SECONDS` (360), which sizes a single fast analyzer pass.
2. `_deep_qr_rescan` returns `(result, outcome)` where outcome is `ok` / `empty` / `timeout` / `error`. When the sweep does not finish, `deep_rescan_incomplete` is stamped on the result and `_check_qr_presence` returns **inconclusive** rather than fail.

**Why:** The first real re-run produced "found 0" on a document that demonstrably carries a QR code. The wide sweep takes **474s** on the 5-page marksheet with a 5-rung ladder, over the 360s budget, so it was killed — and `_deep_qr_rescan` swallowed the `TimeoutExpired` and returned `None`, leaving the fast pass's "found 0" in place.

The damaging part was the reason text: it read *"then widened to a resolution ladder built from this document's own scan resolution and all four rotations, and still decoded nothing"* — describing a search that never completed. The previous entry added that sentence to make a miss meaningful; without knowing the sweep was cut short it made the miss **actively misleading** instead. A sweep killed part-way is not evidence of absence, and the verdict now says so and asks for a re-run.

**Verified on the real batch:** `Copy of 12.pdf` now decodes its page-5 code at 360 DPI, `rot180-adaptive`, on the derived ladder `260,310,360,420,470` — outcome `review` → `clear`, payload `HS/1221057946/2022/…/PRIYANSHI DEVI/…/PASS/…`. `Copy of 12 (1).pdf` genuinely carries no code: 400s over its own ladder `360,440,510,580,650` x four rotations, confirmed three separate ways.

**Note on the 400s/474s figures:** the deep sweep is expensive enough that a 4-5 page document takes 7-8 minutes. That is the cost of the Medium default on a miss, and the reason Low exists.

**Files:** [backend/service.py](backend/service.py), [backend/checks.py](backend/checks.py), [backend/tests/test_service.py](backend/tests/test_service.py).

---

## 2026-07-29 — QR search effort is a Low/Medium/High setting, not a fixed cost

**What:** Added a `qr_presence.effort` setting (`low` / `medium` / `high`, default `medium`), surfaced as a "Search effort" dropdown in advanced settings:

| Effort | Behaviour | Cost |
|--------|-----------|------|
| Low | Fast pass only, never escalates | 1 render pass |
| Medium | Fast pass; wide sweep only when it finds nothing | 1 pass, +5-rung ladder x 4 rotations on a miss |
| High | Always sweeps, on a 10-rung ladder, even after the fast pass finds a code | Always the full sweep |

The chosen level is stamped onto the result as `search_effort`, and `_check_qr_presence` now words a "no QR code" verdict differently for each — at Low it says outright that a code needing another resolution or angle would not have been found, and points at the setting.

**Why:** The deep sweep was made unconditional in the previous entry, which imposed its cost on every document. The fast pass had in fact been decoding codes successfully on most documents screened to date; the marksheet was the exception, not the rule. Making the trade a setting keeps the cheap path available for batches that do not need the expensive one, rather than paying worst-case cost everywhere.

The reason text matters as much as the setting: at Low, "no QR code was decoded" is a statement about how hard the system looked, not about the document, and a reviewer cannot act on it without knowing which. Reporting the effort level alongside the verdict is what keeps the two apart.

**Guards:** the deep result replaces the fast one only when it decoded *at least as many* codes, so escalation can never lose a code the fast pass found. An unrecognised effort value falls back to `medium` rather than erroring.

**Files:** [backend/service.py](backend/service.py), [backend/checks.py](backend/checks.py), [frontend/app/advanced-settings.tsx](frontend/app/advanced-settings.tsx), [backend/tests/test_service.py](backend/tests/test_service.py).

---

## 2026-07-29 — "Decoded payloads" read the wrong field and was always empty

**What:** `_check_qr_presence` in `backend/checks.py` built its "Decoded payloads" fact from `hit.get("data")`, but the scanner's field is `payload` (`QRHit` in `tools/qr_analysis/qr_local_lib.py`, serialized by `hits_to_dicts` as `asdict`). It now reads `payload` with a `data` fallback for results stored under the older key.

**Why:** The fact rendered empty in every case report and in the batch Excel's QR Code sheet even when a code decoded successfully. The existing test fixture used `{"page": 1, "data": "x"}`, which matched the wrong key and so masked the bug; a regression test now asserts the payload survives from a `payload`-keyed hit.

**Files:** [backend/checks.py](backend/checks.py), [backend/tests/test_checks.py](backend/tests/test_checks.py).

---

## 2026-07-29 — QR check escalates to a wider sweep instead of reporting "no QR code"

**What:** `backend/service.py` re-scans for QR codes when the fast pass decodes nothing, over all four rotations and a **DPI ladder derived from the document's own scan resolution** (`native_scan_dpi` / `qr_dpi_ladder`: native × 1.0/1.2/1.4/1.6/1.8). The fixed `QR_DEEP_DPIS` ladder remains the fallback for born-digital PDFs with no raster page. The deep result is kept only if it actually decoded something. `QR_DEEP_RESCAN=0` disables it.

**Why:** A real document in batch `dc13f4ce…` (a UP board marksheet, page 5) carries a QR code the web path reported as absent — it decodes as `HS/1221057946/2022/…/PRIYANSHI DEVI/…/PASS/…`, exactly the payload this system exists to cross-check. Measured per-DPI on that page, across all four rotations and all four preprocessing variants:

| DPI | 200 | 250 | 300 | 350 | 400 | 450 | 500 | 600 |
|-----|-----|-----|-----|-----|-----|-----|-----|-----|
| variants decoding | 0 | 0 | **0** | 1 | 2 | 0 | 0 | 0 |

Three findings, each of which contradicted a plausible first guess and was only settled by measuring:

- **It is not a "higher DPI is better" problem.** The window is a narrow band — 450, 500 and 600 all fail, as do 250 and 300. Raising the fixed DPI would have traded one blind spot for another.
- **It is not the adaptive-threshold block size.** Sweeping `blockSize` over 15–201 at both 300 and 600 DPI decoded nothing, so the fixed 41px window is not what pins the band.
- **It is not "QR is rotation-invariant, so rotations are waste".** Rotation-invariance is a property of the QR *format* — the finder patterns exist so a reader can recover orientation. It is not a property of a *decoder working on a degraded raster*, where finder-pattern search is scanline-based and error correction is already at its limit. Measured on this symbol: cropped out of the page and fed to the decoder on its own, it decodes in **exactly one of four orientations** (`cv2.rotate` by 90/180/270 is lossless, so all four hold identical content). In the full page it decodes only at rot270 — the same physical orientation. This code has one working orientation and it is not the page's natural one, so without the rotation sweep it is unreachable at any DPI.

  Across all 13 DPI values tested, **rot0 never decoded once**. A pipeline that scanned only the page's natural orientation would never have found this code.

The band sits roughly 1.4x–1.8x above the resolution the scan actually holds (page 5 is natively ~232–250 DPI), and it is jittery inside that range — 400 decodes, 420 does not, 450 does not, 470 does. Since native resolution differs per document, **no fixed absolute ladder can hit it reliably**; the ladder has to be computed per document. The derived ladder for this file (260, 310, 360, 420, 470) decodes at two independent rungs where the previous fixed ladder hit only one.

The fast pass stays the first attempt because QR scanning is the most expensive check in the set; only a miss pays for the wide sweep, so "no QR code found" now describes the document rather than the settings.

**Where the fault actually lay:** not in the QR tool. `qr_exists.py`'s own CLI defaults (`250,350,450` x all four rotations) decode this code. `service.py` narrowed them to `300` x `[0,90]` for web responsiveness, and **both narrowings were independently fatal** — at 300 DPI nothing decodes at any rotation, and at the correct 350 DPI only rot270 decodes, which `[0,90]` excludes. Correcting either one alone would still have missed the code. The miss was a configuration decision in the backend wiring, not a defect in the detection code.

**Note:** `native_scan_dpi` measures the raster against the page box in whichever orientation fits. These scans are frequently landscape rasters placed on a portrait page via a rotation transform, and measuring width-against-width reports a meaningless anisotropic figure (doc B's page 3 reads as "645x200 DPI" that way, versus ~359 measured correctly).

**Files:** [backend/service.py](backend/service.py), [backend/tests/test_service.py](backend/tests/test_service.py).

---

## 2026-07-29 — Face matching reports the score it measured, not just verdicts above a threshold

**What:** Three changes to `backend/photo_identity.py` and the Photo Module sheet, all driven by running the feature on a real two-document batch (`dc13f4ce…`, two Kanya Sumangla Yojana application forms):

1. **The threshold was too strict.** It was 0.45, inherited from the source `face_db.py` tool. Measured on that batch: the *same* girl photographed twice within one document scored **0.378**, while two different people scored **0.149** and **0.194**. At 0.45 a genuine same-person match was being missed. Default is now 0.35, overridable with `PHOTO_SIMILARITY_THRESHOLD`.
2. **The closest score is now always reported**, whether or not it clears the threshold, with `status` (`duplicate` / `no_match` / `first`) saying which side it fell on. One measured pair is not a calibration, so the design deliberately stops the threshold from being load-bearing: a reviewer sees "closest 38% → document X" and judges it themselves. This is the same stance as `CALIBRATION_NOTICE` — report signals, not conclusions.
3. **"Not compared" and "compared, no match" now render differently** ("Not compared" vs "No match" + score). Previously both were blank cells, so a reviewer could not tell a working comparison from one that silently never ran — which is precisely how the first run of this feature looked when it was reported as broken.

Also fixed: `_get_embedding` took `faces[0]` from InsightFace, which is detection order, not confidence order. These forms carry a "joint photo of the applicant and girl", so a single crop can hold two faces and the embedding was not deterministic. It now takes the highest-confidence detection.

Sheet columns are now "Same face as" / "Similarity" / "Closest match" (was "Duplicate of" / "Similarity").

**Why:** The feature reported blank cells on its first real run and read as broken. It was in fact correct — the two documents hold different people (Priyanshi Devi and Shivani Kumari) — but nothing in the output distinguished "compared, 19%, different people" from "never ran", and the threshold would have missed a real match anyway.

**Files:** [backend/photo_identity.py](backend/photo_identity.py), [backend/reporting.py](backend/reporting.py), [backend/tests/test_photo_identity.py](backend/tests/test_photo_identity.py), [backend/tests/test_reporting_xlsx.py](backend/tests/test_reporting_xlsx.py).

---

## 2026-07-29 — Batch-wide QR-code duplicate detection by payload hash

**What:** Added `backend/qr_identity.py`. `batch_qr_duplicates(jobs)` walks a batch's documents in upload order and flags a document whose decoded QR payload (`qr_presence`'s `hits[].payload`) exactly matches one already decoded earlier in the same batch — hashed (SHA-256) as the comparison key rather than compared as a raw string, matching the pattern `photo_identity.py` set. No file reads or ML involved: the QR scanner has already decoded the code to text, so this is a plain dict of hashes, not a similarity search. `batch_xlsx()` and `GET /api/v1/batches/{batch_id}/report.xlsx` wire it into the QR Code sheet the same way as the photo duplicate columns: "Duplicate of" and "Matched QR value".

**Why:** Same motivation as the photo matching — the same QR payload (a verification URL, an encoded reference number) appearing on more than one document in a batch is a stronger signal than either document flagging it alone, and nothing surfaced that across documents before.

**Note:** While wiring this up, found that `checks.py`'s `_check_qr_presence` reads `hit.get("data")` for the "Decoded payloads" fact, but the QR scanner's actual field (`QRHit` in `tools/qr_analysis/qr_local_lib.py`) is `payload` — so that fact has always rendered empty. Fixed the same day (see below) once the deep-rescan change meant QR codes actually started reaching the report.

**Later correction (same day):** the QR sheet originally left "Duplicate of" blank for every non-duplicate, conflating "decoded no QR, so nothing was compared" with "compared, and this payload is unique in the batch" — the same defect corrected for the Photo Module sheet. Those now read "No QR code to compare" and "Unique in this batch" respectively, via `_qr_duplicate_cells`.

**Scope limits worth knowing:** matching is exact string equality on the decoded payload (after `.strip()`), scoped to one batch, and computed at export time — so it appears in the `.xlsx` only, never in the CSV, the HTML report, or the app UI.

**Files:** [backend/qr_identity.py](backend/qr_identity.py) (new), [backend/reporting.py](backend/reporting.py), [backend/app.py](backend/app.py), [backend/tests/test_qr_identity.py](backend/tests/test_qr_identity.py) (new), [backend/tests/test_reporting_xlsx.py](backend/tests/test_reporting_xlsx.py).

---

## 2026-07-29 — Batch-wide face identity matching, linked in the Excel report

**What:** Added `backend/photo_identity.py`, adapted from a face-identity-database tool the user supplied (InsightFace embeddings + cosine similarity, originally backed by a persistent FAISS/pickle index). `batch_photo_duplicates(jobs, data_dir)` re-implements the matching in-memory and scoped to a single batch: it walks a batch's documents in upload order, embeds every face crop `photo_detection` already extracted and saved as an artifact, and flags a document whose photo matches a face seen earlier in the same batch (cosine similarity ≥ 0.45, the threshold from the source tool). No index is written to disk — a batch is small enough for brute-force comparison, and a process-wide database would leak matches across unrelated batches. `backend/reporting.py`'s `batch_xlsx()` takes the resulting dict and adds "Duplicate of" / "Similarity" columns to the Photo Module sheet; `GET /api/v1/batches/{batch_id}/report.xlsx` computes it before building the workbook. `insightface`/`onnxruntime` moved from the photo tool's optional extras into `backend/requirements.txt` — without them `extract_photo.py`'s OpenCV fallback has no identity embeddings, so the feature is a documented no-op (blank columns) rather than a failure.

**Why:** The batch review flow had no way to notice that the same person's photo shows up on more than one document in a run of 50 PDFs — exactly the reuse signal a reviewer needs surfaced, not buried across 100 separate crops.

**Files:** [backend/photo_identity.py](backend/photo_identity.py) (new), [backend/reporting.py](backend/reporting.py), [backend/app.py](backend/app.py), [backend/requirements.txt](backend/requirements.txt), [backend/tests/test_photo_identity.py](backend/tests/test_photo_identity.py) (new), [backend/tests/test_reporting_xlsx.py](backend/tests/test_reporting_xlsx.py).

---

## 2026-07-29 — Batch report gets an Excel (.xlsx) export

**What:** Added `GET /api/v1/batches/{batch_id}/report.xlsx` alongside the existing `.html` and `.csv` batch reports. The workbook has a `Summary` tab (one row per document, one column per check, same shape as the CSV) plus one tab per check type (Font Analysis, Photo Module, Same Phone, Moire, QR Code, Signature), each listing only the documents that ran that analyzer with the detector's own `facts` broken into real columns, a derived Y/N presence column where the fact supports it, and the reason text.

**Why:** A reviewer working a whole batch needs to sort/filter one check's results across every document (e.g. "show me every document with a QR code found"), which a one-row-per-document CSV can't do without manual spreadsheet surgery.

**Files:** [backend/reporting.py](backend/reporting.py), [backend/app.py](backend/app.py), [backend/requirements.txt](backend/requirements.txt) (added `openpyxl`), [frontend/app/batches/[batchId]/page.tsx](frontend/app/batches/[batchId]/page.tsx) (added "Excel" export link), [backend/tests/test_reporting_xlsx.py](backend/tests/test_reporting_xlsx.py) (new).

**Rule followed:** Sheet values are only ever drawn from facts the detector actually reported (`checks.py`'s `_facts()` output) — a fact a given result doesn't populate renders blank rather than being fabricated or defaulted.

---

## 2026-07-29 — Removed the `scanner_noise` check entirely

**What:** Deleted the `scanner_noise` analyzer end-to-end: the evaluator (`_check_scanner_noise` in `checks.py`), its evidence-region mapping in `models.py`, its command wiring, default settings, and sanitization in `service.py`, its advanced-settings UI card and label maps on the frontend (`advanced-settings.tsx`, `format.ts`, `profiles.ts`, `settings/page.tsx`), and its mention in `backend/README.md`.

**Why:** Not stated in the diff itself — this looks like a scope-narrowing decision (dropping a detector from the shipped check set). Confirm with the user if the underlying `tools/capture_analysis/scanner_noise/` script should also be removed, or is being kept for other callers.

**Files:** [backend/checks.py](backend/checks.py), [backend/models.py](backend/models.py), [backend/service.py](backend/service.py), [backend/tests/test_checks.py](backend/tests/test_checks.py), [backend/tests/test_models.py](backend/tests/test_models.py), [frontend/app/advanced-settings.tsx](frontend/app/advanced-settings.tsx), [frontend/app/lib/format.ts](frontend/app/lib/format.ts), [frontend/app/lib/profiles.ts](frontend/app/lib/profiles.ts), [frontend/app/settings/page.tsx](frontend/app/settings/page.tsx), [backend/README.md](backend/README.md).

---

## 2026-07-29 — Public `/welcome` landing page, gated app stays behind access code

**What:** Added a `/welcome` route (new `frontend/app/welcome/` tree with its own components, and `frontend/app/styles/welcome.css`, imported in `layout.tsx`) that renders before session auth resolves. `frontend/app/lib/session.tsx` now special-cases a `PUBLIC_ROUTES` allowlist (currently just `/welcome`): on those routes the session provider renders children immediately with a synthetic unauthenticated session, instead of blocking on the "checking access" splash or showing the sign-in card first.

**Why:** A first-time visitor with no access code needs to see what the product is before being asked to authenticate — gating the landing page behind the sign-in card, or blanking it while the access-check round-trip is in flight (worse if the backend is down), was the wrong first impression.

**Files:** [frontend/app/lib/session.tsx](frontend/app/lib/session.tsx), [frontend/app/layout.tsx](frontend/app/layout.tsx), [frontend/app/styles/welcome.css](frontend/app/styles/welcome.css), [frontend/app/welcome/](frontend/app/welcome/).

**Note:** Everything outside `/welcome` remains gated exactly as before — this is additive, not a change to the auth model for the rest of the app.

---

## 2026-07-29 — Landing-page "flight" cinematic asset pipeline

**What:** Added `tools/flight/render_flight.py` to render the landing page's animated flight sequence, plus its build outputs checked into `frontend/public/flight/` (`verification-flight.mp4`, `poster.jpg`, per-stop stills under `stops/`). Added `frontend/tests/flight-contract.test.mjs`, wired into `frontend/package.json`'s `test` script. `.gitignore` now excludes the intermediate render scratch dirs (`tmp/flight-frames/`, `tmp/flight-probe/`) while keeping the final encoded clip/poster/stills tracked, since those ship with the page.

**Why:** The rendered video/stills are build products the landing page depends on at runtime (not reproducible on every clone without the render step), so they're tracked; only the large intermediate frame dumps used during rendering are ignored.

**Files:** [tools/flight/render_flight.py](tools/flight/render_flight.py), [frontend/public/flight/](frontend/public/flight/), [frontend/tests/flight-contract.test.mjs](frontend/tests/flight-contract.test.mjs), [frontend/package.json](frontend/package.json), [.gitignore](.gitignore).

---

<!--
Template for new entries:

## YYYY-MM-DD — Short summary

**What:** What changed, concretely.
**Why:** The motivation/constraint driving it (not just a restatement of "what").
**Files:** [path](path), [path](path)
-->
