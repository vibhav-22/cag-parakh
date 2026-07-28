# Reports & Analytics design QA

- Source visual truth: `C:\Users\SAO-DAC\Documents\Codex\2026-07-14\ca\document-suspicion-system\frontend\design-qa-source\reports-reference.png`
- Implementation screenshot: `C:\Users\SAO-DAC\Documents\Codex\2026-07-14\ca\document-suspicion-system\frontend\design-qa-source\reports-implementation.png`
- Full-view comparison: `C:\Users\SAO-DAC\Documents\Codex\2026-07-14\ca\document-suspicion-system\frontend\design-qa-source\reports-comparison.png`
- Viewport: 1023 × 786 CSS px
- Source pixels: 1023 × 786
- Implementation pixels: 1023 × 786
- Density normalization: 1:1 pixel comparison; the black presentation margin and “Screen / Reports & Analytics” label around the source product frame are treated as reference-board chrome, not app UI.
- State: Reports route loaded with real local screening data, Last 30 days selected.

## Full-view comparison evidence

The implementation preserves the source hierarchy and proportions: dark navigation rail, pale-blue analytics canvas, compact header actions, four equal KPI cards, large stacked-volume chart beside a flag-reason donut, and a full-width performance table. The implementation intentionally uses the product’s actual navigation destinations and real batch-derived values rather than the mock figures in the reference.

## Focused region comparison evidence

A separate crop was not required. At 1:1, KPI labels and values, chart legends, donut labels, table headings, icons, and controls are readable in the full-view comparison. DOM inspection additionally confirmed semantic headings, table structure, chart labels, active navigation, and the date selector.

## Required fidelity surfaces

- Fonts and typography: Geist matches the compact geometric sans character of the source; the mono eyebrow and table labels preserve the technical reporting hierarchy. Weight, line height, truncation, and wrapping remain legible at the reference viewport.
- Spacing and layout rhythm: Header, KPI strip, two-column chart row, and table align to one 20 px content grid. Card gaps, radii, and density closely match the reference.
- Colors and visual tokens: Dark navy rail, pale-blue canvas, white surfaces, blue actions, green clean states, and red flagged states map directly to the source visual language with accessible foreground contrast.
- Image and asset quality: The screen has no photographic or illustrative assets. Phosphor supplies all UI icons; charts are rendered sharply on canvas at device density, with no placeholder or handcrafted SVG substitutes.
- Copy and content: App-specific labels are concise and contextual. Mock document categories were replaced with real file-type groupings because the current data model does not store semantic document classes.
- Responsiveness and accessibility: At 820 px the rail becomes the product’s horizontal navigation, KPI cards and charts stack without horizontal overflow, controls retain labels, canvases expose accessible names, and keyboard focus uses the shared product treatment.

## Comparison history

### Iteration 1

- [P2] Navigation rail was 240 px instead of the source’s compact 168 px, compressing the report canvas.
- [P2] The chart row stacked too early at the reference width.
- Fixes: Applied a route-specific 168 px shell rail and moved the chart stacking breakpoint to 860 px.
- Post-fix evidence: `reports-implementation.png` and `reports-comparison.png`; the final 1023 × 786 capture fits without horizontal or vertical overflow.

### Iteration 2

- No actionable P0, P1, or P2 differences remain.
- Remaining intentional differences: live metric values, five recent volume buckets for the 30-day range, and two real file-type rows instead of six mock document-type rows.

## Interaction and runtime checks

- Reporting-period selector changed from 30 to 90 days and updated the date heading.
- Export control is enabled when performance rows exist.
- Reports is present in primary navigation and marked as the active route.
- Reference and responsive viewport checks showed zero horizontal overflow.
- No page errors were observed in the final development preview.

## Follow-up polish

- [P3] When the data model gains semantic document classification, replace file-type grouping with passport, certificate, contract, and invoice groupings like the source.

final result: passed
