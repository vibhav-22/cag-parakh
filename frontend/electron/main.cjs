const { app, BrowserWindow, dialog, shell } = require("electron");
const { randomBytes } = require("node:crypto");
const { spawn } = require("node:child_process");
const net = require("node:net");
const path = require("node:path");

const frontendDir = path.resolve(__dirname, "..");
const repoRoot = path.resolve(frontendDir, "..");
const children = new Set();
let shuttingDown = false;
let mainWindow = null;

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => resolve(address.port));
    });
  });
}

function assertPortAvailable(port) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", (error) => {
      if (error.code === "EADDRINUSE") {
        reject(new Error(`Port ${port} is already in use. Close the earlier Parakh demo and try again.`));
      } else reject(error);
    });
    server.listen(port, "127.0.0.1", () => server.close(resolve));
  });
}

function commandForPython() {
  if (process.env.PARAKH_PYTHON) return process.env.PARAKH_PYTHON;
  return process.platform === "win32"
    ? path.join(repoRoot, "backend", ".venv", "Scripts", "python.exe")
    : path.join(repoRoot, "backend", ".venv", "bin", "python");
}

function startChild(command, args, options, label) {
  const child = spawn(command, args, { ...options, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  children.add(child);
  child.stdout.on("data", (chunk) => process.stdout.write(`[${label}] ${chunk}`));
  child.stderr.on("data", (chunk) => process.stderr.write(`[${label}] ${chunk}`));
  child.once("exit", (code) => {
    children.delete(child);
    if (!shuttingDown && code !== 0) {
      dialog.showErrorBox(`${label} stopped`, `${label} exited with code ${code}. See the terminal for details.`);
      app.quit();
    }
  });
  child.once("error", (error) => {
    dialog.showErrorBox(`Could not start ${label}`, error.message);
    app.quit();
  });
  return child;
}

async function waitFor(url, label) {
  const deadline = Date.now() + 60_000;
  let lastError = "not ready";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(3_000) });
      if (response.ok) {
        await response.body?.cancel();
        return;
      }
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error.message;
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  throw new Error(`${label} did not become ready: ${lastError}`);
}

async function waitForFrontend(url) {
  const deadline = Date.now() + 60_000;
  let lastError = "not ready";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(5_000) });
      if (!response.ok) throw new Error(`page returned HTTP ${response.status}`);
      const html = await response.text();
      const assetPath = html.match(/["'](\/assets\/[^"']+\.(?:js|css))["']/)?.[1];
      if (!assetPath) throw new Error("page did not contain a built JS or CSS asset");
      const asset = await fetch(new URL(assetPath, url), { signal: AbortSignal.timeout(5_000) });
      if (!asset.ok) throw new Error(`${assetPath} returned HTTP ${asset.status}`);
      await asset.body?.cancel();
      return;
    } catch (error) {
      lastError = error.message;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Frontend assets did not become ready: ${lastError}`);
}

async function loadWithRetry(window, url) {
  const deadline = Date.now() + 30_000;
  let lastError = new Error("Page was not ready");
  while (Date.now() < deadline) {
    try {
      await window.loadURL(url);
      return;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  throw new Error(`Frontend page did not load: ${lastError.message}`);
}

function stopChildren() {
  shuttingDown = true;
  for (const child of children) {
    if (process.platform === "win32") {
      spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], { windowsHide: true, stdio: "ignore" });
    } else child.kill("SIGTERM");
  }
  children.clear();
}

async function launch() {
  // Vinext resolves the backend rewrite while building, and the current build
  // configuration targets port 8000. Keep that port stable for this demo;
  // the installable version will inject a generated port into its build.
  const backendPort = 8000;
  await assertPortAvailable(backendPort);
  const [frontendPort, vinextPort] = await Promise.all([freePort(), freePort()]);
  const launchToken = randomBytes(32).toString("base64url");
  const backendUrl = `http://127.0.0.1:${backendPort}`;
  const frontendUrl = `http://127.0.0.1:${frontendPort}`;
  const vinextUrl = `http://127.0.0.1:${vinextPort}`;

  startChild(commandForPython(), ["-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", String(backendPort)], {
    cwd: repoRoot,
    env: {
      ...process.env,
      // This is a source-tree demo, so leave packaged-mode authorization off.
      // The real installer must set PARAKH_PACKAGED=1 and configure auth.
      PARAKH_PACKAGED: "",
      PARAKH_LAUNCH_TOKEN: launchToken,
      PARAKH_DATA_DIR: path.join(app.getPath("userData"), "demo-data"),
    },
  }, "backend");
  await waitFor(`${backendUrl}/health`, "Backend");

  // Electron cannot reliably spawn a .cmd shim on Windows. npm passes its
  // actual Node executable to this process, so invoke Vinext's JS CLI directly.
  const node = process.env.npm_node_execpath || "node";
  const vinextCli = path.join(frontendDir, "node_modules", "vinext", "dist", "cli.js");
  startChild(node, [vinextCli, "start", "-p", String(vinextPort), "-H", "127.0.0.1"], {
    cwd: frontendDir,
    env: { ...process.env, BACKEND_URL: backendUrl },
  }, "vinext");
  await waitFor(vinextUrl, "Vinext");

  const gateway = path.join(frontendDir, "electron", "frontend-gateway.cjs");
  startChild(node, [gateway, String(frontendPort), String(vinextPort)], {
    cwd: frontendDir,
    env: { ...process.env },
  }, "frontend");
  await waitForFrontend(frontendUrl);

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 940,
    minWidth: 1080,
    minHeight: 700,
    show: true,
    backgroundColor: "#f4f1ea",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  mainWindow.removeMenu();
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(frontendUrl)) return { action: "allow" };
    void shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(frontendUrl)) event.preventDefault();
  });
  mainWindow.once("closed", () => { mainWindow = null; });
  await loadWithRetry(mainWindow, `${frontendUrl}/welcome?lt=${encodeURIComponent(launchToken)}`);
}

app.whenReady().then(launch).catch((error) => {
  dialog.showErrorBox("Parakh demo could not start", error.stack || error.message);
  app.quit();
});
app.on("before-quit", stopChildren);
app.on("window-all-closed", () => app.quit());
