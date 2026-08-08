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

The launcher builds the frontend, selects free loopback ports for all three
local services, starts them, and opens the desktop window. Closing the window
stops them. Demo data is stored in Electron's per-user application-data folder,
and child process output is teed to `logs/parakh-<date>.log` beside it — error
dialogs name that file.

No fixed port is required. The backend used to be pinned to 8000 because Vinext
resolves the `next.config.ts` rewrites while building; `electron/frontend-gateway.cjs`
now routes `/api/*` and `/health` to the backend itself, so the launcher can use
whatever ports are free.

The demo command also applies a narrow Windows compatibility patch to Vinext
0.0.50 before building. That release records static asset cache keys with
Windows backslashes, causing valid `/assets/*` requests to return 404. Remove
the compatibility script after upgrading to a Vinext release that normalizes
static-file paths on Windows.

A second launch exits immediately rather than starting: two copies would share
one SQLite job store and one data directory. Do not run `npm run build` while
the desktop demo is open either — a rebuild replaces hashed frontend assets that
the running production server is still referencing. Close the demo first.

The source-tree demo permits its development account only when
`PARAKH_DEV_AUTH_BYPASS=1` is set explicitly. Packaged builds ignore that
bypass and verify `%LOCALAPPDATA%\Parakh\authorization\authorization.json`
against `resources\authorization\public-key.pem`.

The desktop window opens on `/welcome`. For a quick source demo set
`PARAKH_DEV_AUTH_BYPASS=1`. To exercise real offline login, set
`PARAKH_AUTH_FILE` and `PARAKH_AUTH_PUBLIC_KEY_FILE` to administrator-generated
files before running `npm run demo`.
