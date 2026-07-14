# Same Phone PDF Check Toolkit

`same_phone_pdf_check.py` is separate from `scanner_noise_fingerprint_check.py`.
It asks a narrower question:

```text
Are the PDF pages compatible with being clicked from the same phone or capture workflow?
```

It does not try to prove fraud, and it does not prove the exact phone model.
Most PDF/image-to-PDF apps strip EXIF and device identity.

## Why This Tool Is Different

The older scanner-noise checker was strict about capture consistency. That means
same-phone photos could score medium when lighting, angle, crop, or focus changed.

This tool downweights:

- lighting/shadow differences
- crop and page angle differences
- handwriting and local document content
- row/column scanner streak patterns

It emphasizes:

- PDF creator/producer metadata
- embedded image compression family
- image dimensions and aspect compatibility
- normalized background noise texture
- broad sensor/capture texture compatibility

## Usage

```powershell
python same_phone_pdf_check.py "C:\path\to\document.pdf" --output-dir "C:\path\to\same_phone_report"
```

In this Codex workspace:

```powershell
C:\Users\SAO-DAC\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe same_phone_pdf_check.py "C:\path\to\document.pdf" --keep-images
```

Or drag a PDF onto:

```text
run_same_phone_check.bat
```

## Output

The report folder contains:

- `same_phone_pdf_report.json`
- `same_phone_pdf_report.txt`
- `rendered_pages/` when `--keep-images` is used

Verdicts:

- `likely_same_phone_or_workflow`: strong compatibility, not proof.
- `compatible_with_same_phone`: same phone is plausible despite capture differences.
- `uncertain_same_phone`: not enough stable evidence either way.
- `possibly_different_phone_or_workflow`: review carefully with other checks.

## Important Limitation

This cannot identify a phone like a forensic lab. It only checks whether pages are
compatible with the same phone/workflow after PDF conversion.
