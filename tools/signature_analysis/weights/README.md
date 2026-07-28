# Legacy signature-model weights

`signature_detector.pt` is retained as legacy project data so previous
experiments remain reproducible. The active signature analyzer does not load
this file and does not require Ultralytics or Torch.

The current implementation lives in `../signature_locator/` and combines PDF
structure, OCR/text anchors, embedded-image evidence, ink analysis, and
register-table signing zones.

The legacy weight originated from an AGPL-3.0 detector. Do not redistribute or
serve it without reviewing its license obligations. It can be removed in a
separate cleanup after confirming that no historical benchmark depends on it.
