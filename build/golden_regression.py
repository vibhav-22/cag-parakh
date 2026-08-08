"""Capture or compare privacy-safe Parakh golden-result manifests.

The manifest stores document SHA-256 values and stable verdict/check outputs,
never document bytes. Run against the source backend to capture a baseline,
then against an installed build to compare it.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


class Client:
    def __init__(self, base_url: str, launch_token: str) -> None:
        self.base = base_url.rstrip("/")
        self.launch_token = launch_token
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(self, method: str, path: str, body: bytes | None = None, content_type: str | None = None) -> dict:
        headers = {"Accept": "application/json"}
        if self.launch_token:
            headers["X-Parakh-Launch-Token"] = self.launch_token
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=300) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} returned HTTP {exc.code}: {detail}") from exc

    def login(self, email: str, password: str) -> None:
        body = json.dumps({"email": email, "password": password}).encode("utf-8")
        state = self.request("POST", "/api/v1/session", body, "application/json")
        if not state.get("authenticated"):
            raise RuntimeError("Parakh did not establish an authenticated session.")

    def submit(self, document: Path) -> dict:
        boundary = "----parakh-golden-" + uuid.uuid4().hex
        payload = document.read_bytes()
        mime = "application/pdf" if document.suffix.lower() == ".pdf" else "application/octet-stream"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{document.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8") + payload + f"\r\n--{boundary}--\r\n".encode("ascii")
        return self.request("POST", "/api/v1/jobs", body, f"multipart/form-data; boundary={boundary}")


def stable_result(report: dict) -> dict:
    checks = []
    for check in report.get("checks", []):
        checks.append({key: check.get(key) for key in (
            "analyzer_id", "status", "outcome", "risk", "findings_count",
            "result", "criterion", "reason", "facts", "error",
        )})
    return {
        "sha256": report.get("sha256"),
        "machine_verdict": report.get("machine_verdict"),
        "unanalyzable": report.get("unanalyzable"),
        "unanalyzable_reason": report.get("unanalyzable_reason"),
        "checks": checks,
    }


def screen(client: Client, document: Path, timeout: int) -> dict:
    created = client.submit(document)
    job_id = created["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.request("GET", f"/api/v1/jobs/{job_id}")
        if job.get("status") == "completed":
            report = client.request("GET", f"/api/v1/jobs/{job_id}/report.json")
            return stable_result(report)
        time.sleep(0.75)
    raise TimeoutError(f"{document.name} did not complete within {timeout} seconds")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("capture", "compare"))
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password-env", default="PARAKH_GOLDEN_PASSWORD")
    parser.add_argument("--launch-token-env", default="PARAKH_LAUNCH_TOKEN")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    password = os.getenv(args.password_env) or getpass.getpass("Parakh password: ")
    client = Client(args.base_url, os.getenv(args.launch_token_env, ""))
    client.login(args.email, password)
    documents = sorted(path for path in args.documents.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED)
    if not documents:
        raise RuntimeError(f"No supported documents found under {args.documents}")

    results = {}
    for document in documents:
        print(f"Screening {document.name}...", flush=True)
        digest = hashlib.sha256(document.read_bytes()).hexdigest()
        results[digest] = {"result": screen(client, document, args.timeout)}

    current = {"schemaVersion": 1, "documents": results}
    if args.mode == "capture":
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Captured {len(results)} privacy-safe golden records in {args.manifest}")
        return 0

    expected = json.loads(args.manifest.read_text(encoding="utf-8"))
    if expected != current:
        expected_path = args.manifest.with_suffix(".actual.json")
        expected_path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Golden regression FAILED. Actual results: {expected_path}", file=sys.stderr)
        return 1
    print(f"Golden regression passed for {len(results)} documents.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
