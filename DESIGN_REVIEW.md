# Design Review — Document Suspicion System Web App

Date: 2026-07-16 · Branch: `website-reorganization` · Designer's-eye pass
Target: the live frontend ([page.tsx](frontend/app/page.tsx), [globals.css](frontend/app/globals.css))

Initial score **7/10** → post-decision target **9/10**. Every proposed fix was
accepted (decisions 1A, 2A, 3A, 4A, 5A, 6A, 7A). All four future concepts approved
for spec.

## What already exists (reuse, don't reinvent)

- Token system: sage-green forensic palette (`--ink #17211d`, `--accent #145f45`,
  warning/danger + soft variants), radii, three named easing curves, z-scale.
- Conventions: Geist Mono for case refs/metrics/evidence numbers; hairline borders
  carry structure (no decorative shadows); hover gated behind `(hover: hover)`.
- Crafted states: designed empty state (document ghost + 3-step guidance), scanner
  loading animation, skeletons, `@starting-style` entrances, reduced-motion support.
- Pass 4 (AI-slop) verdict: **9/10, no hard rejections** — this is not template UI.

## Accepted fixes

### 1A — Designed findings report (P1)
Replace the raw-JSON result detail (`page.tsx:280`) per analyzer with:
verdict sentence (from `summary`), key-value evidence table (mono values),
severity chip (reuse status-pill vocabulary), links that set `currentPage` +
`activeMarker` for each region. Raw JSON demoted behind a "Raw output"
`<details>` disclosure. Readability's stdout report gets typographic treatment
(mono block with heading), not a bare `<pre>` dump.

### 2A — Complete the failure states (P1)
- **Save review:** on `!response.ok` show error toast, retain notes, offer retry
  (`page.tsx:182` currently does nothing).
- **Poll stall:** if polling fails N consecutive times or job exceeds a sanity
  window, replace the scanner overlay's note with an honest message + Retry/Cancel
  ("Screening is taking longer than expected — the analysis service may be down").
- **Drag-over:** visible dropzone highlight state while a file is dragged over
  (copy already promises drag-and-drop).

### 3A — Honest service status light (P1)
Derive the topbar pill (`page.tsx:204`, currently hardcoded green) from the
analyzer fetch + periodic `/health` re-check. Green "Screening service available",
amber "Checking service…", red "Screening service unreachable". Never decorative.

### 4A — Case-closed state (P2)
After a saved decision: summary block (decision, notes excerpt, timestamp,
filename) + primary "Screen next document" action that resets the workspace.
Full case history deferred until backend persistence lands (eng review A1).

### 5A — DESIGN.md + type-scale sweep (P2)
Codify the existing system in DESIGN.md, then sweep globals.css onto:
`caption 11px / body 13px / ui 14px / heading 16px / title 20px / display 28px`,
weights regular/600/750 only. Kills the current 12-size, 7-weight dust; 11px floor
(current 9–10px text fails readability for evidence content).

### 6A — A11y hardening (P2)
- 44px effective hit areas: invisible padding on `.evidence-marker`
  (min-width: 8px today), viewer toolbar buttons to ≥38px.
- Contrast floor: audit `--muted`/`--subtle` at small sizes (`#7a8781` at 10px
  ≈ 3.6:1 — fails); fix via scale bump + token adjustment.
- Severity legend: chip row in the viewer toolbar naming green/amber/red/blue
  (`severity-unknown` blue is currently unexplained).

### 7A — System dark mode (P3)
One `prefers-color-scheme: dark` block remapping the token set; re-check the four
severity marker colors against dark surfaces. Chrome dims, document page stays
white — standard viewer-tool behavior for long review sessions.

## Interaction-state table (post-fix contract)

```
FEATURE            | LOADING     | EMPTY      | ERROR              | SUCCESS      | PARTIAL
-------------------|-------------|------------|--------------------|--------------|----------
Analyzer list      | skeleton    | n/a        | toast              | list         | n/a
Upload / dropzone  | bar         | dropzone   | toast              | job view     | n/a
  drag-over        | —           | —          | —                  | highlight ✚  | —
Job polling        | overlay     | n/a        | stall msg+retry ✚  | results      | per-run
Save review        | button busy✚| n/a        | toast+retain ✚     | toast+close ✚| n/a
Service status     | amber ✚     | —          | red + copy ✚       | green live ✚ | —
Document preview   | skeleton    | n/a        | message            | pages        | n/a
```
✚ = added by this review.

## Future concepts (all four approved for spec)

### F1 — X-ray slider (build next; CC ~1–2h)
Draggable divider over the document page: original scan on one side, analysis
layer on the other (noise map / ELA / spectrum PNGs the tamper and scanner-noise
tools already emit as artifacts). Today those images hide behind "Open visual
report ↗" links; this puts them under the reviewer's cursor. Needs: artifact
image aligned to page coordinates, clip-path on drag.

### F2 — Guided evidence tour (CC ~2h)
"Play tour" in the viewer toolbar: auto-zoom/pan marker-to-marker in severity
order with finding captions, using the existing easing curves. Doubles as the
escalation artifact (screen-record the tour). Needs: camera transform on
`.document-stage`, ordered region list, caption overlay.

### F3 — Keyboard-first review mode (cheapest; CC ~45min)
J/K step findings, Enter zoom to region, V/N/I record decision
(verified / needs investigation / inconclusive), ? for shortcut overlay.
Code-review ergonomics for document forensics; power reviewers stop touching
the mouse.

### F4 — Case dossier (blocked on backend persistence, eng A1)
Multi-document cases with cross-document signals — the `same_phone` analyzer is
built for exactly this and the UI has nowhere to express it. Case list → dossier
view → per-document verdicts + cross-links ("same capture device as document 2").
Spec only until jobs persist.

## NOT in scope (considered, deferred with reason)

- Case history / queue inbox — blocked on backend persistence (eng A1).
- Reviewer identity on decisions — blocked on auth (eng A4).
- Manual dark-mode toggle — system preference only for now (decision 7A over 7B).
- Print/PDF export of the findings report — natural follow-up to 1A, not started.

## Implementation Tasks

- [x] **T1 (P1, human: ~2d / CC: ~45min)** — results column — Designed findings report per analyzer, raw JSON behind disclosure — **DONE 2026-07-16, verified live**
  - Surfaced by: Pass 1 — raw JSON dump at page.tsx:280
  - Files: frontend/app/page.tsx, frontend/app/globals.css
  - Verify: open each of 8 analyzer results; no bare JSON visible by default; region links move viewer
- [x] **T2 (P1, human: ~1d / CC: ~20min)** — states — Save-error toast + retained notes, poll-stall escape, drag-over highlight — **DONE 2026-07-16; save-error + drag-over verified live, stall path code-reviewed only**
  - Surfaced by: Pass 2 — silent failures (page.tsx:104, page.tsx:182)
  - Files: frontend/app/page.tsx, frontend/app/globals.css
  - Verify: kill backend mid-job → stall message with retry; fail save → toast, notes intact
- [x] **T3 (P1, human: ~2h / CC: ~10min)** — topbar — Live service status pill (green/amber/red) — **DONE 2026-07-16, verified live (green → red → green)**
  - Surfaced by: Pass 2 — hardcoded "Screening service available" (page.tsx:204)
  - Files: frontend/app/page.tsx, frontend/app/globals.css
  - Verify: stop backend → pill turns red with honest copy
- [ ] **T4 (P2, human: ~4h / CC: ~15min)** — decision flow — Case-closed summary + "Screen next document" reset
  - Surfaced by: Pass 3 — post-decision dead end
  - Files: frontend/app/page.tsx, frontend/app/globals.css
  - Verify: save decision → closure block; reset returns to fresh workspace
- [ ] **T5 (P2, human: ~1d / CC: ~30min)** — system — DESIGN.md + 6-step type scale sweep (11px floor)
  - Surfaced by: Pass 5 — 12 sizes / 7 weights census
  - Files: DESIGN.md (new), frontend/app/globals.css
  - Verify: grep font-size in globals.css → only scale values remain
- [ ] **T6 (P2, human: ~4h / CC: ~20min)** — viewer — 44px hit areas, contrast floor, severity legend
  - Surfaced by: Pass 6 — 8px markers, 3.6:1 subtle text, unexplained blue
  - Files: frontend/app/page.tsx, frontend/app/globals.css
  - Verify: tap smallest marker on tablet-width viewport; legend visible in toolbar
- [ ] **T7 (P3, human: ~1d / CC: ~25min)** — theme — System dark mode via token remap
  - Surfaced by: Pass 6 — no prefers-color-scheme
  - Files: frontend/app/globals.css
  - Verify: OS dark mode → dimmed chrome, white document page, legible severity colors
- [ ] **T8 (P3)** — future — Build F1 X-ray slider, then F3 keyboard mode; F2 tour after; F4 dossier when persistence lands
  - Surfaced by: Pass 7 — approved concepts F1–F4

## Completion Summary

```
+====================================================================+
|         DESIGN PLAN REVIEW — COMPLETION SUMMARY                    |
+====================================================================+
| System Audit         | No DESIGN.md (now T5); full app UI scope    |
| Step 0               | 7/10 initial; all focus areas selected      |
| Pass 1  (Info Arch)  | 7/10 → 9/10 (1A accepted)                   |
| Pass 2  (States)     | 6/10 → 9/10 (2A + 3A accepted)              |
| Pass 3  (Journey)    | 8/10 → 9/10 (4A accepted)                   |
| Pass 4  (AI Slop)    | 9/10 → 9/10 (no findings)                   |
| Pass 5  (Design Sys) | 6/10 → 9/10 (5A accepted)                   |
| Pass 6  (Responsive) | 6/10 → 9/10 (6A + 7A accepted)              |
| Pass 7  (Decisions)  | 8 resolved, 2 deferred (blocked on backend) |
+--------------------------------------------------------------------+
| NOT in scope         | written (4 items)                           |
| What already exists  | written                                     |
| TODOS.md updates     | 0 (all findings became accepted tasks)      |
| Approved Mockups     | 0 generated (designer needs OpenAI key)     |
| Decisions made       | 8                                           |
| Decisions deferred   | 2 (case history, reviewer identity)         |
| Overall design score | 7/10 → 9/10 (once T1–T7 land)               |
+====================================================================+
```

Deferred decisions are blocked on the engineering backlog
([ENGINEERING_REVIEW.md](ENGINEERING_REVIEW.md) A1 persistence, A4 auth), not on
design. Run `/design-review` after T1–T7 land for a live visual QA pass.
