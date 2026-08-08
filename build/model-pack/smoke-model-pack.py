from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def main() -> int:
    parser = argparse.ArgumentParser(description="Start and probe a staged Parakh model pack.")
    parser.add_argument("pack", type=Path)
    parser.add_argument("--backend", choices=("vulkan", "cpu"), default="vulkan")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--image", type=Path, help="Also run one offline multimodal completion.")
    args = parser.parse_args()

    pack = args.pack.resolve()
    runtime = pack / "runtime" / args.backend / "llama-server.exe"
    model = pack / "model" / "Qwen3VL-4B-Instruct-Q4_K_M.gguf"
    mmproj = pack / "model" / "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf"
    for required in (runtime, model, mmproj):
        if not required.is_file():
            parser.error(f"missing pack file: {required}")

    port = free_port()
    output = pack.parent.parent / f"smoke-{args.backend}.out.log"
    errors = pack.parent.parent / f"smoke-{args.backend}.err.log"
    command = [
        str(runtime),
        "--model", str(model),
        "--mmproj", str(mmproj),
        "--alias", "Qwen3-VL-4B-Instruct",
        "--ctx-size", "8192",
        "--parallel", "1",
        "--jinja",
        "--no-webui",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--gpu-layers", "99" if args.backend == "vulkan" else "0",
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with output.open("wb") as stdout, errors.open("wb") as stderr:
        process = subprocess.Popen(
            command,
            cwd=runtime.parent,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )
        try:
            deadline = time.monotonic() + max(10, args.timeout)
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/v1/models", timeout=3
                    ) as response:
                        payload = json.load(response)
                    result: dict[str, object] = {
                        "backend": args.backend,
                        "port": port,
                        "models": payload,
                    }
                    if args.image:
                        image = args.image.resolve()
                        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
                        request = urllib.request.Request(
                            f"http://127.0.0.1:{port}/v1/chat/completions",
                            data=json.dumps(
                                {
                                    "model": "Qwen3-VL-4B-Instruct",
                                    "temperature": 0,
                                    "max_tokens": 48,
                                    "messages": [
                                        {
                                            "role": "user",
                                            "content": [
                                                {"type": "text", "text": "Describe this image briefly."},
                                                {
                                                    "type": "image_url",
                                                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                                                },
                                            ],
                                        }
                                    ],
                                }
                            ).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urllib.request.urlopen(request, timeout=args.timeout) as response:
                            completion = json.load(response)
                        answer = completion["choices"][0]["message"]["content"]
                        result["multimodal_answer"] = answer
                    print(json.dumps(result))
                    return 0
                except (OSError, TimeoutError, ValueError):
                    time.sleep(1)
            tail = errors.read_text(encoding="utf-8", errors="replace")[-4000:]
            raise RuntimeError(f"model server did not become ready; stderr tail:\n{tail}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
