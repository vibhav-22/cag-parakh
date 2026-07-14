from __future__ import annotations

import argparse
import json
import time

from qr_local_lib import common_qr_args, fail, hits_to_dicts, parse_int_list, scan_for_qr, seconds_since, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether a PDF or document photo contains a QR code.")
    common_qr_args(parser)
    parser.add_argument("--strict-exit", action="store_true", help="Return exit code 2 when no QR code is found.")
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

    report = {
        "input": str(input_path),
        "qr_found": bool(hits),
        "qr_count": len(hits),
        "hits": hits_to_dicts(hits),
        "timing": {
            "qr_scan_seconds": scan_seconds,
            "total_seconds": seconds_since(total_start),
        },
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.out:
        write_json(args.out, report)

    if args.strict_exit and not hits:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
