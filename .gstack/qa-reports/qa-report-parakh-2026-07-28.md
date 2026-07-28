# QA report — Parakh (localhost:3001)

Date: 2026-07-28 · Branch: website-reorganization · Tier: Standard
Routes exercised: /, /new, /history, /reports, /ask, /settings, /batches/[id], /batches/[id]/documents/[jobId], unknown-batch fallback
Console errors across all routes: 0 · Broken links: 0 · Backend endpoints checked: 7/7 → 200

Note: the Browser pane was not compositing during this run, so screenshots and
synthetic mouse input were unavailable. Evidence is DOM/text based; interactions
were driven with element.click() and verified by reading resulting state.

## ISSUE-001 — Dashboard score inflated a flagged document to 100 (high, functional)
Repro: open /, read the Score column for 10thmarksheet1.pdf.
Before: "Needs review · 100". The same document reads 67% Authenticity on its batch page.
Cause: app/page.tsx scoreFor() counted `clear` twice — once explicitly and again
inside `(results.length - review - errors)`. 6 clear of 10 scored 105, clamped to 100.
Fix: use the shared authenticityPercent() helper. Commit d988322f.
After: 44 / 67 / 44 / 44 for batch 2a8e8f8b, matching the batch page exactly.
Status: verified.

## ISSUE-002 — Flagged card subtitle counted the wrong set (medium, content)
Repro: open /. Before: "FLAGGED 222 · 4 ready for review". On a dashboard whose 8
most recent documents are all clean it read "Queue is clear" with 222 flagged.
Cause: subtitle used reviewQueue.length (capped at 4, drawn from the 8 most recent
documents) instead of the flagged total.
Fix: mirror the Clean card. Commit d988322f.
After: "FLAGGED 222 · 76% of total" (24% + 76% = 100%).
Status: verified.

## ISSUE-003 — Batch page labelled the check-error bucket "Flagged" (medium, content)
Repro: open /batches/2a8e8f8bd38143efb2db9055ceec0918.
Before: stat row "DOCUMENTS 4 | CLEAN 0 | FLAGGED 0 | REVIEW 4" and a "Flagged 0" tab,
directly above prose reading "4 flagged · 4 without a recorded decision"; the history
card for the same batch reads "4 FLAGGED".
Cause: stats.flaggedDocs counts tone === "danger", which docVerdict assigns to
documents whose checks could not complete.
Fix: rename both labels to "Check errors". Commit e72ff8a6.
After: "CLEAN 0 | CHECK ERRORS 0 | REVIEW 4"; tabs "All 4 / Check errors 0 / Needs review 4 / Clean 0".
Status: verified.

## Deferred

- ISSUE-004 (medium, functional gap) — Signature detector thresholds are missing from
  /settings. The backend carries signature dpi / confidence / min_signatures (visible in
  the batch Test Key as "DPI: 200 · Confidence threshold: 55% · Minimum signatures: 0")
  and the batch page already has the "Minimum signatures" label, but the Detection
  thresholds tab lists only 9 detectors while the header says "10 of 10 checks enabled".
  Deferred: adding a settings panel is a feature, not a bug fix.
- ISSUE-005 (low, cosmetic) — Dashboard "Processing" renders `String(n).padStart(2,"0")`,
  so zero shows as "00" beside 292 / 70 / 222. Below the Standard fix tier.
- ISSUE-006 (low, content) — Signature evidence reads "Likely visible signature (66%
  confidence). ink the page does not explain as print; ...". The reason fragments from
  tools/signature_analysis/signature_locator/detectors.py:516 are lowercase and get
  joined after a full stop. Backend copy change, deferred.

## Verification

- npm test (build + 31 contract/render tests): 31 pass, 0 fail.
- eslint on both changed files: 2 pre-existing react-hooks/set-state-in-effect errors
  (app/page.tsx:67, batches/[batchId]/page.tsx:186), neither on a changed line.
- History filters and sort re-checked after the change: All 43 / Flagged 36 / All clear 7
  / In progress 0 / Decision recorded 1; "Most flagged" sorts 50, 19, 19, 18, 16, 14.
- Unknown batch id renders "This case is not on this device" rather than an error.

PR summary: QA found 6 issues, fixed 3, health score 74 → 92.
