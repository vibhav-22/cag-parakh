# Scanner Noise Fingerprint Toolkit

`scanner_noise_fingerprint_check` is a first-pass PDF screening tool for scanned
or photographed document PDFs. It checks whether pages or local regions appear
to come from a different scanner, camera, printer, or capture source.

It is a review signal, not proof of fraud.

## What It Looks For

- Page-to-page differences in capture noise residuals.
- Scanner-like row/column streak or banding differences.
- Local tiles with unusual noise, blur, lighting, or compression texture.
- Camera-photo-like lighting variation and blockiness signals.

This makes it useful for PDFs containing scanner pages, phone photos of
documents, or mixed scan/photo content.

## Usage

From this folder:

```powershell
python scanner_noise_fingerprint_check.py "C:\path\to\input.pdf" --output-dir "C:\path\to\report"
```

On Windows, you can also drag a PDF onto:

```text
run_scanner_noise_check.bat
```

In this Codex workspace, the bundled Python can be used directly:

```powershell
C:\Users\SAO-DAC\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe scanner_noise_fingerprint_check.py "C:\path\to\input.pdf" --output-dir "C:\path\to\report"
```

Useful options:

```powershell
python scanner_noise_fingerprint_check.py input.pdf --dpi 220 --grid 4 --keep-images
```

- `--dpi`: render quality. Use `220` or `300` for serious review.
- `--grid`: local region grid size. `4` means 4x4 tiles per page.
- `--keep-images`: saves rendered page PNGs beside the report.

## Outputs

The report folder contains:

- `scanner_noise_fingerprint_report.json`
- `scanner_noise_fingerprint_report.txt`
- `rendered_pages/` only when `--keep-images` is used

The main fields are:

- `overall_risk`: `low`, `medium`, or `high`
- `suspicious_pages`: pages whose fingerprint differs from the document group
- `suspicious_regions`: local boxes that look unlike the rest of the page
- `pairwise_page_comparisons`: page-to-page mismatch scores and reasons

## How To Read The Result

- `low`: no strong capture-source mismatch found.
- `medium`: review the flagged page or region manually.
- `high`: strong mismatch signal; verify with visual, OCR, QR, metadata, and
  document-template checks before deciding.

For two-page PDFs, the tool can say that the pair is inconsistent, but it cannot
reliably decide which one is the outlier.

For one-page PDFs, the tool cannot test page-to-page source consistency at all.
It may still report local review areas, but those findings are capped to a
limited-confidence review signal because text density, photos, seals, stamps,
QR codes, borders, and signatures can naturally look different from blank paper.

For multi-page PDFs where many pages differ from each other and there is no
stable majority group, the result should be read as capture-condition
variability, not as proof that every page came from a different device.

## Limitations

- It cannot prove tampering by itself.
- Clean recompression, heavy filtering, screenshots, or low-resolution PDFs can
  hide useful noise signals.
- Natural content differences, photos, stamps, seals, shadows, and textured paper
  may trigger local-region warnings.
- Best results come from comparing multiple pages from the same expected source.
