from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict

from qr_local_lib import (
    common_qr_args,
    compare_qr_details_to_document,
    extract_urls_from_payload_report,
    fail,
    hits_to_dicts,
    ocr_pdf_or_image,
    parse_int_list,
    parse_qr_payload,
    scan_for_qr,
    seconds_since,
    visit_url_and_cross_check,
    write_json,
)


def verdict_from_matches(
    qr_found: bool,
    match_count: int,
    matches: int,
    total: int,
    web_checks_total: int = 0,
    web_checks_ok: int = 0,
    web_qr_field_matches: int = 0,
    web_document_line_matches: int = 0,
    web_structured_field_matches: int = 0,
    web_structured_fields_total: int = 0,
) -> str:
    if not qr_found:
        return "fail_no_qr"
    if web_checks_total and not web_checks_ok:
        return "review_url_check_failed"
    if web_checks_ok and web_structured_fields_total and web_structured_field_matches == web_structured_fields_total:
        return "pass_url_cross_checked"
    if web_checks_ok and web_structured_field_matches:
        return "review_url_partial_structured_match"
    if web_checks_ok and (web_qr_field_matches or web_document_line_matches):
        return "pass_url_cross_checked"
    if web_checks_ok and total == 0:
        return "review_url_opened_no_details_found"
    if total == 0:
        return "review_no_structured_qr_details"
    if matches == total:
        return "pass"
    if match_count > 0:
        return "review_partial_match"
    return "fail_no_details_matched"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decode QR code details and compare them with locally OCR'd document text."
    )
    common_qr_args(parser)
    parser.add_argument("--ocr-dpi", type=int, default=300, help="Render DPI for OCR. Default: 300")
    parser.add_argument("--langs", nargs="+", default=["en"], help="EasyOCR language codes. Default: en")
    parser.add_argument(
        "--gpu",
        default="auto",
        choices=["auto", "yes", "no", "cuda", "cpu"],
        help="Use GPU for OCR when available. Default: auto",
    )
    parser.add_argument("--threshold", type=float, default=82.0, help="Fuzzy match threshold from 0-100. Default: 82")
    parser.add_argument("--include-ocr-items", action="store_true", help="Include OCR boxes/confidence in JSON output.")
    parser.add_argument(
        "--visit-url",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Visit http/https URLs found in QR payloads and compare the web page text too. Default: enabled.",
    )
    parser.add_argument("--url-timeout", type=float, default=20.0, help="Seconds to wait for each QR URL. Default: 20")
    parser.add_argument(
        "--allow-insecure-url",
        action="store_true",
        help="Retry QR URL without TLS certificate verification if the verified request fails. Report marks tls_verified=false.",
    )
    parser.add_argument(
        "--url-mode",
        choices=["auto", "simple", "browser"],
        default="auto",
        help="How to open QR URLs. auto tries simple fetch, then browser if page text is sparse. Default: auto",
    )
    parser.add_argument(
        "--browser-wait-ms",
        type=int,
        default=2500,
        help="Extra wait after browser-rendered QR URL load, in milliseconds. Default: 2500",
    )
    args = parser.parse_args()

    input_path = args.input.expanduser().resolve()
    if not input_path.exists():
        fail(f"Input file not found: {input_path}")

    total_start = time.perf_counter()
    dpis = parse_int_list(args.dpi, default=[250, 350, 450])
    rotations = parse_int_list(args.rotations, default=[0, 90, 180, 270])

    scan_start = time.perf_counter()
    hits = scan_for_qr(
        input_path=input_path,
        dpis=dpis,
        max_pages=args.max_pages,
        rotations=rotations,
        save_crops_dir=args.save_crops,
        stop_after_first=args.stop_after_first,
    )
    scan_seconds = seconds_since(scan_start)

    ocr_start = time.perf_counter()
    ocr_pages = ocr_pdf_or_image(
        input_path=input_path,
        languages=args.langs,
        gpu=args.gpu,
        dpi=args.ocr_dpi,
        max_pages=args.max_pages,
    )
    ocr_seconds = seconds_since(ocr_start)
    document_lines = [line for page in ocr_pages for line in page["lines"]]

    payload_reports = []
    total_fields = 0
    matched_fields = 0
    web_seconds = 0.0
    web_checks_total = 0
    web_checks_ok = 0
    web_qr_field_matches = 0
    web_document_line_matches = 0
    web_structured_field_matches = 0
    web_structured_fields_total = 0
    for hit in hits:
        details = parse_qr_payload(hit.payload)
        comparisons = compare_qr_details_to_document(details, document_lines, threshold=args.threshold)
        total_fields += len(comparisons)
        matched_fields += sum(1 for item in comparisons if item.matched)
        url_checks = []
        if args.visit_url:
            for url in extract_urls_from_payload_report(hit.payload, details):
                web_start = time.perf_counter()
                check = visit_url_and_cross_check(
                    url=url,
                    qr_details=details,
                    document_lines=document_lines,
                    threshold=args.threshold,
                    timeout=args.url_timeout,
                    allow_insecure=args.allow_insecure_url,
                    render_mode=args.url_mode,
                    browser_wait_ms=args.browser_wait_ms,
                )
                web_seconds += seconds_since(web_start)
                web_checks_total += 1
                web_checks_ok += 1 if check.ok else 0
                web_qr_field_matches += sum(1 for item in check.qr_field_matches if item.matched)
                web_document_line_matches += sum(1 for item in check.document_line_matches if item.matched)
                web_structured_field_matches += sum(1 for item in check.structured_matches if item.matched)
                web_structured_fields_total += len(check.structured_matches)
                url_checks.append(asdict(check))
        payload_reports.append(
            {
                "hit": asdict(hit),
                "parsed_details": details,
                "comparisons": [asdict(item) for item in comparisons],
                "url_checks": url_checks,
            }
        )

    if web_structured_fields_total:
        overall_matched_fields = web_structured_field_matches
        overall_total_fields = web_structured_fields_total
    else:
        overall_matched_fields = matched_fields + web_qr_field_matches + web_document_line_matches
        overall_total_fields = total_fields

    match_rate_percent = (
        round((overall_matched_fields / overall_total_fields) * 100, 1)
        if overall_total_fields
        else None
    )
    total_seconds = seconds_since(total_start)
    report = {
        "input": str(input_path),
        "qr_found": bool(hits),
        "qr_count": len(hits),
        "hits": hits_to_dicts(hits),
        "ocr": {
            "pages": [
                page if args.include_ocr_items else {"page": page["page"], "lines": page["lines"]}
                for page in ocr_pages
            ],
            "line_count": len(document_lines),
            "languages": args.langs,
            "dpi": args.ocr_dpi,
            "gpu": args.gpu,
        },
        "payload_reports": payload_reports,
        "summary": {
            "matched_fields": overall_matched_fields,
            "total_comparable_fields": overall_total_fields,
            "match_rate_percent": match_rate_percent,
            "qr_payload_matched_fields": matched_fields,
            "qr_payload_total_comparable_fields": total_fields,
            "web_checks_total": web_checks_total,
            "web_checks_ok": web_checks_ok,
            "web_qr_field_matches": web_qr_field_matches,
            "web_document_line_matches": web_document_line_matches,
            "web_structured_field_matches": web_structured_field_matches,
            "web_structured_fields_total": web_structured_fields_total,
            "verdict": verdict_from_matches(
                bool(hits),
                matched_fields,
                matched_fields,
                total_fields,
                web_checks_total=web_checks_total,
                web_checks_ok=web_checks_ok,
                web_qr_field_matches=web_qr_field_matches,
                web_document_line_matches=web_document_line_matches,
                web_structured_field_matches=web_structured_field_matches,
                web_structured_fields_total=web_structured_fields_total,
            ),
        },
        "timing": {
            "qr_scan_seconds": scan_seconds,
            "ocr_seconds": ocr_seconds,
            "url_check_seconds": round(web_seconds, 3),
            "total_seconds": total_seconds,
        },
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.out:
        write_json(args.out, report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
