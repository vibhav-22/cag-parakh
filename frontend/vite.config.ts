import vinext from "vinext";
import { defineConfig } from "vite";
import { sites } from "./build/sites-vite-plugin";

// No Cloudflare plugin here on purpose. Parakh ships as a desktop app: the
// Electron main process runs `vinext start` (a plain Node server) and fronts it
// with electron/frontend-gateway.cjs, so the Worker/D1/R2 bindings the template
// shipped with had nothing to bind to. Restore @cloudflare/vite-plugin, wrangler
// and worker/index.ts only if this is ever hosted on Cloudflare.

// macOS Seatbelt blocks FSEvents, so Codex previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === "seatbelt";

export default defineConfig({
  server: isCodexSeatbeltSandbox
    ? { watch: { useFsEvents: false, usePolling: true } }
    : undefined,
  plugins: [vinext(), sites()],
});
