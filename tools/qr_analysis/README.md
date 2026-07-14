# Local PDF QR Checker

This toolkit runs OCR and QR detection locally. It renders each PDF page as an image, detects QR codes, decodes the QR payload, OCRs the document locally, and compares QR values against text found in the document.

By default, the verification script now also visits `http` or `https` URLs found inside QR codes and cross-checks the web page text against both the QR details and document OCR text. Use `--no-visit-url` when you want a fully offline run.

It also supports direct image files such as JPG and PNG, which is useful when a PDF page is just a photo of a document.

## Files

- `qr_exists.py` - checks whether a QR code exists and decodes it.
- `verify_qr_against_pdf.py` - decodes QR details, OCRs the document locally, and compares them.
- `qr_local_lib.py` - shared local PDF, QR, OCR, and matching logic.
- `requirements.txt` - Python packages.

## Setup

Use Python 3.10 or newer.

```powershell
cd tools\qr_analysis
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

For GPU OCR, install a CUDA-enabled PyTorch build first. Use the current command from the official PyTorch install selector for your driver/CUDA version. Then install the remaining packages:

```powershell
pip install -r requirements.txt
```

If you skip the CUDA PyTorch step, EasyOCR still runs locally on CPU, just slower.

For QR web pages that load details with JavaScript, install Playwright's local Chromium browser once:

```powershell
python -m playwright install chromium
```

If URL checking fails with a certificate-chain error on Windows, update the certificate bundle:

```powershell
pip install --upgrade certifi
```

## Script 1: Check Whether QR Exists

```powershell
python .\qr_exists.py "C:\path\to\document.pdf" --out qr_report.json --save-crops qr_crops
```

Useful options:

```powershell
python .\qr_exists.py "file.pdf" --dpi 250 350 500 --max-pages 2
python .\qr_exists.py "file.pdf" --rotations 0 90 180 270
```

The output includes `qr_found`, `qr_count`, decoded payloads, page number, DPI, the detection engine used, and processing time.

## Script 2: Verify QR Details Against Document

```powershell
python .\verify_qr_against_pdf.py "C:\path\to\document.pdf" --out verify_report.json --save-crops qr_crops --gpu auto
```

For documents that normally contain one QR code, this faster command stops scanning after the first QR is found:

```powershell
python .\verify_qr_against_pdf.py "C:\path\to\document.pdf" --out verify_report.json --gpu auto --stop-after-first
```

If the URL opens but the report only shows a generic title such as `Civil Registration System`, force browser rendering:

```powershell
python .\verify_qr_against_pdf.py "C:\path\to\document.pdf" --out verify_report.json --gpu auto --stop-after-first --url-mode browser
```

If the QR URL still fails with `CERTIFICATE_VERIFY_FAILED`, you can retry without TLS certificate verification. The report will mark `tls_verified` as `false`:

```powershell
python .\verify_qr_against_pdf.py "C:\path\to\document.pdf" --out verify_report.json --allow-insecure-url
```

For Hindi plus English OCR:

```powershell
python .\verify_qr_against_pdf.py "file.pdf" --langs en hi --gpu auto --out verify_report.json
```

For a fully offline check:

```powershell
python .\verify_qr_against_pdf.py "file.pdf" --no-visit-url --out verify_report.json
```

The verification script:

1. Finds and decodes QR codes.
2. Parses QR content if it is JSON, XML, URL query data, base64 text, or key-value text.
3. OCRs the visible document locally with EasyOCR.
4. Compares each useful QR value against the OCR text using exact and fuzzy matching.
5. Visits QR URLs when present, extracts visible page text, and checks whether QR/document values appear there.
6. Returns a verdict and timing:
   - `pass` - all comparable QR fields were found in the document text.
   - `pass_url_cross_checked` - QR URL opened and the web page matched QR/document values.
   - `review_partial_match` - some fields matched, some did not.
   - `review_url_check_failed` - QR exists but the URL could not be checked.
   - `review_url_opened_no_details_found` - QR URL opened but no comparable certificate details were visible.
   - `fail_no_details_matched` - QR exists but comparable details were not found in OCR text.
   - `review_no_structured_qr_details` - QR exists but only contains a URL or unstructured payload.
   - `fail_no_qr` - no QR code found.

The report includes `timing.qr_scan_seconds`, `timing.ocr_seconds`, `timing.url_check_seconds`, and `timing.total_seconds`.

When browser mode reaches a certificate details page, check:

- `payload_reports[].url_checks[].web_details` - fields extracted from the QR website.
- `payload_reports[].url_checks[].document_details` - fields extracted from OCR text.
- `payload_reports[].url_checks[].structured_matches` - direct field-by-field comparison.
- `summary.matched_fields`, `summary.total_comparable_fields`, and `summary.match_rate_percent` - overall compact match result.
- `summary.qr_payload_matched_fields` and `summary.qr_payload_total_comparable_fields` - QR payload only. These can be `0` when the QR contains only a URL.
- `summary.web_structured_field_matches` and `summary.web_structured_fields_total` - website-vs-document structured field count.

## URL Check Notes

The URL check uses `--url-mode auto` by default. It first performs a simple web page fetch. If the result is too sparse, it tries local Chromium rendering through Playwright when installed. It does not solve captchas, log in, click buttons, or call private APIs.

Some older CRS QR codes contain `http://` links. If that connection is reset, the verifier automatically retries the same URL as `https://`.
