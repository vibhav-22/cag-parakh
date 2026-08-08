# Parakh 1.0.0-pilot installer runbook

The objective is to prove that a clean Windows laptop can install, authorize,
screen, upgrade, and uninstall Parakh without Python, Node, Docker, WSL,
internet access, or terminal configuration.

## Build-machine gate

1. Stage Tesseract, Poppler, and InsightFace with `build/stage-vendor.ps1`.
2. Generate the offline authorization key pair on the controlled administrator
   workstation. Keep the encrypted private key outside the repository. Build
   with only the public PEM.
3. Build `Parakh-1.0.0-pilot-Setup-x64.exe` using
   `build/make-installer.ps1`. Prefer a reviewed offline wheelhouse.
4. Require `build/verify-release.ps1` to pass. Retain
   `release/windows/RELEASE-MANIFEST.json` and publish its SHA-256 through a
   separate trusted channel.
5. Confirm no installer or staging directory contains `authorization.json`, a
   private-key marker, user PDF, or SQLite job database.
6. Capture a baseline from 8–10 non-production golden documents that exercise
   every detector:

```powershell
$env:PARAKH_GOLDEN_PASSWORD = '<temporary test password>'
python build/golden_regression.py capture --base-url http://127.0.0.1:<port> `
  --documents C:\ParakhGolden --manifest C:\ParakhGolden\baseline.json `
  --email pilot@example.com
```

The manifest stores hashes and verdict/check outputs, not document bytes. Keep
the source documents in approved test storage and do not commit them.

## Clean-machine matrix

Use at least two laptops: one company-managed and one without a supported GPU.
Neither should have development runtimes installed.

| # | Test | Required result |
|---|---|---|
| P1 | Verify installer SHA-256 | Matches `RELEASE-MANIFEST.json` |
| P2 | Install by double-click | Normal wizard completes; desktop and Start Menu shortcuts exist |
| P3 | Inspect listeners | Python, Vinext, gateway, and optional VLM bind only `127.0.0.1`; no firewall prompt |
| P4 | Missing authorization | Login is rejected with a clear configuration error |
| P5 | Tampered signature | Modified `authorization.json` is rejected |
| P6 | Expired file/user | Login is rejected |
| P7 | Wrong password and lockout | Throttling and temporary lockout trigger; valid login later recovers |
| P8 | Approved login offline | Succeeds with network disconnected |
| P9 | Native diagnostics | `complete: true`, `missing: []` |
| P10 | Golden comparison | `golden_regression.py compare` exits 0 |
| P11 | 25–50 document batch | Completes; record time and peak RAM |
| P12 | Close and relaunch | No child processes remain; history persists |
| P13 | Force-quit and relaunch | SQLite recovers and interrupted work behaves as documented |
| P14 | Model pack absent | Normal screening works; VLM reports unavailable, never silently ready |
| P15 | Model pack present, GPU | Pinned version is detected; diagnostics name Vulkan/GPU and model location |
| P16 | Forced/unsupported GPU | Launcher falls back to CPU; diagnostics name CPU |
| P17 | Upgrade over prior pilot | Documents, results, settings, session, and authorization remain |
| P18 | Uninstall | Program, shortcuts, and registration are removed; `%LOCALAPPDATA%\Parakh` is retained as documented |

Use `build/RESULTS-template.md` per laptop. Collect launcher logs from
`%APPDATA%\Parakh\logs`, dependency/VLM diagnostics, golden comparison output,
installation/upgrade screenshots, and timing results.

## Signing gate

An unsigned build is allowed only for a named internal pilot. Record
Authenticode status `NotSigned`, expected SmartScreen behavior, and installer
hash. Company-wide release is blocked until both the installer and installed
executable have valid, timestamped Authenticode signatures. Re-run
`make-installer.ps1 -RequireSigned` for that release.

## Exit criteria

Rollout is blocked by any authorization bypass, non-loopback listener, missing
native dependency, golden-result drift, loss of data/authorization on upgrade,
failure to fall back from unsupported GPU, or private/user material in the
installer. All other findings must still be recorded and assigned an owner.
