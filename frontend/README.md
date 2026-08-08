# Parakh frontend

The reviewer-facing interface. A [vinext](https://github.com/cloudflare/vinext)
app (Vite + React 19 with Next-compatible routing) that talks to the local
FastAPI backend in `../backend`, plus the Electron shell in `electron/` that
runs both as a desktop app.

## Prerequisites

- Node.js `>=22.13.0`
- The backend running locally — see `../backend/README.md`

## Development

```bash
npm install
npm run dev
```

`npm run dev` serves on port 3000 and proxies `/api/*` and `/health` to
`BACKEND_URL` (default `http://127.0.0.1:8000`) via the rewrites in
`next.config.ts`. Copy `.env.example` to `.env` if the backend listens
elsewhere. Leave `NEXT_PUBLIC_API_URL` empty: API calls must go through the
frontend origin so the session cookie applies.

## Desktop app

```bash
npm run demo
```

Builds the frontend and opens the Electron window, which starts the backend and
the Vinext server itself on ports chosen at launch. See `../DESKTOP_DEMO.md`.

Employee releases are built from the repository root with
`build/make-installer.ps1`; do not invoke electron-builder directly because the
script first stages embedded Python, detectors, native dependencies, the
packaged-mode marker, and the authorization public key. The resulting NSIS
installer is written to `../release/windows`.

The desktop path does **not** use the `next.config.ts` rewrites. Vinext resolves
those while building, which would pin the backend to one port on every machine;
`electron/frontend-gateway.cjs` routes `/api/*` and `/health` to the backend
directly instead, so the launcher can use whatever port is free.

## Layout

- `app/` — routes and components. `app/lib/` holds the shared pieces:
  `session.tsx` (login gate), `launch-token.ts` (the per-launch header the
  packaged backend requires), `format.ts`.
- `electron/` — `main.cjs` launches and supervises the child processes;
  `frontend-gateway.cjs` serves `dist/client` and proxies everything else.
- `scripts/patch-vinext-windows.cjs` — narrow fix for a Vinext 0.0.50 bug that
  404s `/assets/*` on Windows. Remove once Vinext normalizes those paths.
- `public/fonts/` — self-hosted Geist. `next/font` does not work under vinext.
- `dist/client`, `dist/server` — build output.

## Commands

- `npm run dev` — development server
- `npm run build` — production build
- `npm test` — build, then run the contract tests in `tests/`
- `npm run lint` — eslint
- `npm run demo` — patch, build, and launch the desktop app
