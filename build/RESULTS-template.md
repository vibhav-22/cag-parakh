# Pilot result — <laptop name>

Copy this file per laptop. Fill it in while testing, not afterwards.

## Machine

| | |
|---|---|
| Tester | |
| Windows edition and build | `winver` |
| CPU / RAM | |
| Discrete GPU | |
| Corporate-managed (Intune/AD/GPO) | |
| Python or Node already installed | `python --version`, `node --version` — "not found" is the interesting answer |
| Antivirus | |
| Network at first launch | online / offline / behind proxy |

## Installer

| | |
|---|---|
| `BUILD.json` gitSha | |
| `BUILD.json` builtAt | |
| Installer SHA-256 | |
| Authenticode status | Valid / unsigned internal pilot |

## Checklist

Record what happened, not just pass/fail. A pass that took four minutes is a
finding.

| # | Step | Result | Notes |
|---|---|---|---|
| P1 | Transfer, hash, and install | ☐ pass ☐ fail | Method, duration, hash, AV/MotW behavior |
| P2 | Shortcut launch | ☐ pass ☐ fail | Desktop + Start Menu, time to window, SmartScreen text |
| P3 | No firewall prompt | ☐ pass ☐ fail | If prompted, capture the exact dialog — it means something bound a non-loopback address |
| P4 | Offline auth cases | ☐ pass ☐ fail | Success, wrong password, lockout, missing/tampered/expired file |
| P5 | `complete: true` | ☐ pass ☐ fail | Paste the full JSON below |
| P6 | Golden set matches baseline | ☐ pass ☐ fail | Any differing document + check |
| P7 | Batch of __ documents | ☐ pass ☐ fail | Wall-clock, peak RAM |
| P8 | Clean shutdown | ☐ pass ☐ fail | Leftover processes, if any |
| P9 | Relaunch, data intact | ☐ pass ☐ fail | |
| P10 | Resume after force-quit | ☐ pass ☐ fail | |
| P11 | Offline screening + VLM fallback | ☐ pass ☐ fail | Model absent, GPU if supported, CPU fallback |
| P12 | Upgrade and uninstall | ☐ pass ☐ fail | Data/auth retained; shortcuts and program removed |

## P5 — dependency diagnostics

```json
<paste GET /api/v1/diagnostics/dependencies here>
```

## P6 — golden set comparison

| Document | Baseline verdict | This laptop | Differing checks |
|---|---|---|---|
| | | | |

Any row that differs is a blocker. Name the specific check, not just the
document — a changed verdict with unchanged ticks means something different
from a changed tick.

## Findings

Number them `ISSUE-NNN` continuing from the latest report in
`.gstack/qa-reports/`. For each: what happened, what you expected, whether it
blocks rollout, and the log excerpt.

## Verdict

☐ Pass — P5, P6, P8, P11 all clean
☐ Pass with findings — exit criteria met, other issues logged
☐ Blocked — P5 or P6 failed; do not roll out
