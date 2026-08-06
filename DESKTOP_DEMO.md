# Electron desktop demo

This proof of concept runs the existing Vinext frontend and FastAPI backend in
an Electron window. It is a development demo, not a portable installer.

## One-time setup

From the repository root:

```powershell
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd frontend
npm install
```

If Python lives somewhere else, set `PARAKH_PYTHON` to its full executable
path before starting the demo.

## Run

```powershell
cd frontend
npm run demo
```

The launcher builds the frontend, selects free loopback ports, starts both
local services, and opens the desktop window. Closing the window stops both
services. Demo data is stored in Electron's per-user application-data folder.

The demo command also applies a narrow Windows compatibility patch to Vinext
0.0.50 before building. That release records static asset cache keys with
Windows backslashes, causing valid `/assets/*` requests to return 404. Remove
the compatibility script after upgrading to a Vinext release that normalizes
static-file paths on Windows.

Only run one demo at a time. Do not run `npm run build` while the desktop demo
is open: a rebuild replaces hashed frontend assets that the running production
server is still referencing. Close the demo before rebuilding or restarting it.

The source-tree demo leaves packaged-mode authorization off so the screening
flow can be exercised locally. The real installer will set `PARAKH_PACKAGED=1`
and must configure the HTTPS authorization service, preserving fail-closed
installed-build behavior.

The desktop window opens on `/welcome`. To exercise login in the demo, start
the local authorization service and set `PARAKH_AUTH_URL=http://127.0.0.1:8100`
in the PowerShell session before running `npm run demo`. Without that variable,
the demo uses its intentional local development account and the sign-in link
opens the workspace immediately.
