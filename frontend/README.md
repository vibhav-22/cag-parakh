# Parakh frontend and desktop shell

This folder contains the reviewer-facing React application and the Electron shell that turns the complete local stack into a Windows desktop application.

## Stack

- TypeScript 5.9, React 19, React DOM 19
- Next 16-compatible App Router conventions
- Vinext 0.0.50 and Vite 8 for development/production rendering
- Phosphor Icons and hand-authored CSS with locally hosted fonts
- Electron 39 and electron-builder/NSIS for Windows packaging
- Node.js 22.13 or newer

Vinext provides Next-compatible routing on Vite. `next.config.ts` supplies development rewrites; desktop production uses a dedicated gateway because build-time rewrites cannot follow Electron's dynamically selected backend port.

## Folder map

| Path | Purpose |
| --- | --- |
| `app/` | Routes, shared components, client-side session/API helpers, and styles |
| `app/lib/` | Types, session gate, batch polling, profiles, formatting, launch-token helpers |
| `app/components/` | Shell/navigation, controls, authorized images, Ask Documents panel |
| `app/styles/` | Route-level and global CSS |
| `app/welcome/` | Product tour/verification-flight components and timeline data |
| `electron/main.cjs` | Single-instance Electron launcher and child-process supervisor |
| `electron/frontend-gateway.cjs` | Same-origin static/Vinext/API gateway for the desktop app |
| `scripts/patch-vinext-windows.cjs` | Compatibility patch for Vinext 0.0.50 Windows asset paths |
| `tests/` | Rendered HTML, UI contract, and welcome-flight tests |
| `public/` | Fonts, icons, images, and welcome-flight media |
| `dist/`, `.next/`, `release/`, `.packaging/` | Generated outputs; do not edit |

## Application routes

- `/welcome` — product introduction
- `/` — dashboard and recent work
- `/new` — new screening workspace
- `/projects` and `/projects/[projectId]` — project organization
- `/batches/[batchId]` — batch progress/results
- `/batches/[batchId]/documents/[jobId]` — evidence review and final decision
- `/history` — stored screening history
- `/reports` — report-oriented view
- `/ask` — standalone Visual AI document questions
- `/settings` — analyzer settings and dependency information

## Run for development

Start the backend first with its development authorization bypass (see [`../backend/README.md`](../backend/README.md)), then:

```powershell
Copy-Item .env.example .env
npm install
npm run dev
```

Open `http://127.0.0.1:3000`.

Environment variables:

| Variable | Default | Notes |
| --- | --- | --- |
| `BACKEND_URL` | `http://127.0.0.1:8000` | Server-side rewrite destination in browser development |
| `NEXT_PUBLIC_API_URL` | empty | Keep empty for same-origin cookies and Electron compatibility |

All protected fetches use the frontend origin. The HTTP-only session cookie is managed by FastAPI, so it is not readable from React. Packaged desktop requests also carry a per-launch token obtained from the initial Electron URL and attached by the shared request helpers.

## Desktop runtime

```powershell
$env:PARAKH_DEV_AUTH_BYPASS = "1"
npm run demo
```

At launch, Electron:

1. Enforces a single application instance.
2. Allocates free loopback ports for the public gateway, Vinext, FastAPI, and optional VLM.
3. Generates a random per-launch token.
4. Starts the optional model-pack runtime when installed.
5. Starts FastAPI using embedded Python in packaged builds or `PARAKH_PYTHON`/the source virtual environment in development.
6. Starts Vinext, then the frontend gateway that proxies `/api/*` and `/health` to FastAPI.
7. Opens a sandboxed Electron window and terminates all child processes on exit.

Source demo data is stored in Electron's user-data directory under `demo-data`. Packaged data is resolved by the backend under `%LOCALAPPDATA%\Parakh\data`. Logs are written under the Electron user-data `logs/` directory.

## Commands

| Command | Purpose |
| --- | --- |
| `npm run dev` | Vinext development server |
| `npm run build` | Production client/server build |
| `npm run start` | Serve an existing production build |
| `npm test` | Build and run all frontend contract tests |
| `npm run lint` | ESLint source validation |
| `npm run demo` | Patch Vinext, build, and launch Electron from source |
| `npm run desktop:pack` | Unpacked local Electron artifact (development only) |
| `npm run desktop:installer` | Raw NSIS command; use the root release script for real releases |

## Packaging

Do not use the frontend's raw installer command for an employee release. Run `build/make-installer.ps1` from the repository root. It stages embedded Python, detector source, native dependencies, packaged-mode marker, authorization public key, and verified frontend assets before electron-builder runs.

The production output is written to `../release/windows`. See [`../build/README.md`](../build/README.md), [`../INSTALLATION.md`](../INSTALLATION.md), and [`../DESKTOP_DEMO.md`](../DESKTOP_DEMO.md).

The Windows patch script modifies the installed Vinext 0.0.50 package so static asset cache keys use URL separators. Re-evaluate and remove the patch only after upgrading Vinext and passing `npm test` on Windows.

## Frontend maintenance rules

- Keep API requests same-origin; do not expose the backend or session tokens directly to browser code.
- Use shared fetch/download/image helpers so Electron's launch-token requirement is honored.
- Do not edit generated `dist`, `.next`, `.packaging`, or `release` contents.
- Close the Electron demo before rebuilding; rebuilding replaces hashed assets used by the running process.
- Update rendered HTML/UI contracts when a deliberate route or accessibility contract changes.
