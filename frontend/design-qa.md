# Document Viewer Spacing Design QA

- Source visual truth: `design-qa-source/document-viewer-v2-reference.png`
- Implementation screenshot: `design-qa-source/document-viewer-spacing-final.png`
- Review-form screenshot: `design-qa-source/document-viewer-spacing-review.png`
- Side-by-side comparison: `design-qa-source/document-viewer-spacing-comparison.png`
- Route: `http://localhost:3000/batches/5101816aab2746b389ab0cdea53a9e6c/documents/e927c59ae39143b696304a5159f4d995`
- CSS viewport: 1191 × 848
- State: clear PDF, duplicate-history disclosure collapsed, zoom 100%, evidence rail visible

## Full-view comparison

The viewer now uses one consistent 16 px inset and gap system. The document canvas and evidence rail are separate bordered cards with matching 10 px radii instead of touching edge-to-edge. The rail, page header, and viewer toolbar retain their hierarchy while the document remains the dominant surface.

The live case contains a different PDF and detector result than the sample reference. Those content differences are intentional; layout, density, and workspace hierarchy are the comparison targets.

## Review-flow comparison

The document Q&A and final review card now follow the same 16 px vertical rhythm as the primary workspace. The file header and viewer toolbar remain visible while moving to the decision form, so the screen does not lose its document context.

Duplicate-file history is collapsed into a 50 px disclosure row by default. It can still be opened for the full list without permanently taking space from the document.

## Required fidelity surfaces

- Typography: Existing Geist hierarchy is preserved.
- Spacing: 16 px outer insets, column gap, and lower-panel gaps.
- Surfaces: Matching white cards, subtle border, and 10 px radius for the viewer and evidence rail.
- Scrolling: Sticky file header at 0 px and sticky viewer toolbar at 58 px.
- Content: Live document data, checks, result key, Q&A, and review controls remain intact.

## Interaction and runtime checks

- Complete review scrolls to the decision card.
- Header and toolbar remain visible during the review transition.
- Duplicate-file history is collapsed by default and remains available as a disclosure.
- Measured canvas-to-rail gap: 16 px.
- Measured viewer-to-Q&A gap: 16 px.
- Measured Q&A-to-review gap: 16 px.
- Production build: passed.
- Frontend tests: 29/29 passed.

## Findings

No actionable P0, P1, or P2 visual findings remain. The document and evidence rail now have consistent spacing at both the viewer and decision-form states.

final result: passed
