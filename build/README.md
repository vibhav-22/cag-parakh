# Build and release system

This folder produces and validates Parakh's offline Windows deliverables. It is the supported release entry point: raw frontend electron-builder commands do not stage or verify the complete application.

## Deliverables

- Main Parakh NSIS installer under `release/windows/`
- Standalone authorization administrator utility under `release/admin/`
- Optional versioned Qwen/llama.cpp model pack, distributed separately
- Release manifest, hashes, logs, and pilot/golden-regression evidence

## Scripts

| File | Purpose |
| --- | --- |
| `stage-vendor.ps1` | Stage and validate native Tesseract, Poppler, and model dependencies |
| `make-bundle.ps1` | Assemble the frozen backend/runtime bundle and run probes |
| `make-installer.ps1` | Build the verified frontend, backend, Electron, and NSIS release |
| `make-admin-tool.ps1` | Build the separate offline authorization manager |
| `verify-release.ps1` | Verify release files, manifests, hashes, and required contents |
| `golden_regression.py` | Compare a running build against approved detector results |
| `requirements-runtime.lock` | Exact CPython 3.13 x64 Windows runtime dependencies |
| `PILOT.md` | Clean-machine release acceptance procedure |
| `RESULTS-template.md` | Pilot result record template |
| `model-pack/` | Optional VLM pack builder, installer, pins, licenses, and smoke test |

## Main installer build

Prerequisites include x64 Python 3.13, Node.js 22+, an approved Python embed ZIP, staged native dependencies, the matching authorization public key, and optionally an offline wheelhouse.

```powershell
powershell -File build\stage-vendor.ps1
powershell -File build\make-installer.ps1 `
  -PythonEmbedZip tmp\packaging-inputs\python-3.13.14-embed-amd64.zip `
  -BuildPythonExe C:\Path\To\Python313\python.exe `
  -PublicKeyFile D:\ParakhKeys\authorization-public.pem
```

For a controlled release, provide the script's offline wheelhouse option and `-RequireSigned`. The build checks Python archive hashes/ABI, dependencies, tests, forbidden secrets/user data, packaged resources, and final installer hashes.

## Authorization manager

```powershell
powershell -File build\make-admin-tool.ps1 -BuildPythonExe C:\Path\To\Python313\python.exe
```

Keep this utility, the Ed25519 private key, DPAPI passphrase file, and recovery passphrase out of the employee installer and source transfer. See [`../ADMINISTRATION.md`](../ADMINISTRATION.md).

## Optional model pack

The VLM pack is separate so deterministic screening never depends on AI availability and the main installer stays smaller. Follow [`model-pack/README.md`](model-pack/README.md) and its pinned hashes/licenses. The desktop launcher prefers Vulkan when available and falls back to CPU.

## Release acceptance

1. Build from a clean, reviewed commit without customer data or credentials.
2. Run backend tests, frontend tests/lint, bundle probes, and `verify-release.ps1`.
3. Install on a clean approved Windows laptop without developer Python, Node, Tesseract, or Poppler.
4. Follow [`PILOT.md`](PILOT.md), including dependency diagnostics and golden regression.
5. Verify Authenticode signatures, or label an unsigned internal pilot, and distribute SHA-256 separately.
6. Archive the source commit, locks, manifests, hashes, and completed results template.

Generated `vendor/`, `release/`, `frontend/release/`, `.packaging/`, and temporary inputs are ignored. They are reproducible inputs/outputs, not source-of-truth code.
