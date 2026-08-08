const { app, BrowserWindow, dialog, shell } = require("electron");
const { randomBytes } = require("node:crypto");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

// Without this, userData follows the npm package name and logs land under
// %APPDATA%\document-suspicion-frontend -- a path no tester would think to look
// in, and not the one the support docs name.
app.setName("Parakh");

const frontendDir = path.resolve(__dirname, "..");
// Running from source, the backend and detector scripts live one level above
// frontend/. In the portable bundle they are copied under resources/parakh,
// which becomes the ROOT that backend/app_paths.py resolves against.
const backendRoot = app.isPackaged
  ? path.join(process.resourcesPath, "parakh")
  : path.resolve(frontendDir, "..");
const children = new Set();
let shuttingDown = false;
let mainWindow = null;
let logStream = null;
let logPath = "";

function openLog() {
  logPath = path.join(app.getPath("userData"), "logs", `parakh-${new Date().toISOString().slice(0, 10)}.log`);
  try {
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    logStream = fs.createWriteStream(logPath, { flags: "a" });
    logStream.write(`\n=== launch ${new Date().toISOString()} ===\n`);
  } catch {
    // A launcher that cannot write its own log must still start.
    logStream = null;
  }
}

function record(line) {
  process.stdout.write(line);
  logStream?.write(line);
}

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

function commandForPython() {
  // An explicit override always wins, so a support case on a pilot laptop can
  // be redirected at a working interpreter without rebuilding the bundle.
  if (process.env.PARAKH_PYTHON) return process.env.PARAKH_PYTHON;
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "runtime", "python", "python.exe");
  }
  return process.platform === "win32"
    ? path.join(backendRoot, "backend", ".venv", "Scripts", "python.exe")
    : path.join(backendRoot, "backend", ".venv", "bin", "python");
}

// Electron embeds Node, and ELECTRON_RUN_AS_NODE turns this same executable
// into a plain Node interpreter. Using it for the Vinext server and the gateway
// means the bundle ships one runtime instead of two, and dev and packaged runs
// exercise the identical code path.
function startNode(scriptArgs, options, label) {
  return startChild(process.execPath, scriptArgs, {
    ...options,
    env: { ...options.env, ELECTRON_RUN_AS_NODE: "1" },
  }, label);
}

function startChild(command, args, options, label, fatal = true) {
  const child = spawn(command, args, { ...options, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  children.add(child);
  child.stdout.on("data", (chunk) => record(`[${label}] ${chunk}`));
  child.stderr.on("data", (chunk) => record(`[${label}] ${chunk}`));
  child.once("exit", (code) => {
    children.delete(child);
    if (fatal && !shuttingDown && code !== 0) {
      // A tester who double-clicked an icon has no terminal to look at, so
      // point them at the file that actually holds the failure.
      dialog.showErrorBox(`${label} stopped`, `${label} exited with code ${code}.\n\nDetails: ${logPath}`);
      app.quit();
    }
  });
  child.once("error", (error) => {
    if (fatal) {
      dialog.showErrorBox(`Could not start ${label}`, `${error.message}\n\nDetails: ${logPath}`);
      app.quit();
    }
  });
  return child;
}

function modelPackContract() {
  if (!app.isPackaged) return { ready: false };
  const probe = spawnSync(commandForPython(), [
    "-c",
    "import json; from backend.vlm_model_pack import launcher_contract; print(json.dumps(launcher_contract()))",
  ], { cwd: backendRoot, windowsHide: true, encoding: "utf8" });
  if (probe.status !== 0) {
    record(`[vlm] model-pack discovery failed: ${probe.stderr || `exit ${probe.status}`}\n`);
    return { ready: false };
  }
  try {
    return JSON.parse(probe.stdout.trim());
  } catch (error) {
    record(`[vlm] invalid model-pack launcher contract: ${error.message}\n`);
    return { ready: false };
  }
}

async function startOptionalVlm(port) {
  const contract = modelPackContract();
  if (!contract.ready) return {};
  const packEnvironment = contract.environment || {};
  for (const backend of contract.preference || ["vulkan", "cpu"]) {
    const executable = contract.executables?.[backend];
    if (!executable) continue;
    const gpu = backend === "vulkan";
    const args = [
      ...(contract.arguments || []),
      "--host", "127.0.0.1",
      "--port", String(port),
      "--gpu-layers", gpu ? "99" : "0",
    ];
    const child = startChild(executable, args, {
      cwd: path.dirname(executable),
      env: { ...process.env },
    }, `vlm-${backend}`, false);
    try {
      await waitFor(`http://127.0.0.1:${port}/health`, `VLM ${backend}`);
      record(`[vlm] selected ${backend} runtime on 127.0.0.1:${port}\n`);
      return {
        ...packEnvironment,
        VLM_BASE_URL: `http://127.0.0.1:${port}/v1`,
        PARAKH_VLM_RUNTIME_BACKEND: backend,
        PARAKH_VLM_RUNTIME_PATH: executable,
        PARAKH_VLM_DEVICE: gpu ? "gpu" : "cpu",
      };
    } catch (error) {
      record(`[vlm] ${backend} runtime unavailable; trying fallback: ${error.message}\n`);
      children.delete(child);
      if (process.platform === "win32") {
        spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], { windowsHide: true, stdio: "ignore" });
      } else child.kill("SIGTERM");
      await new Promise((resolve) => setTimeout(resolve, 750));
    }
  }
  return packEnvironment;
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
  // Every port is allocated at startup. The backend used to be pinned to 8000
  // because Vinext bakes next.config.ts rewrites into the build, but the
  // gateway now routes /api and /health itself, so nothing depends on a
  // specific port being free on the user's machine.
  const [frontendPort, vinextPort, backendPort, vlmPort] = await Promise.all([
    freePort(), freePort(), freePort(), freePort(),
  ]);
  const launchToken = randomBytes(32).toString("base64url");
  const backendUrl = `http://127.0.0.1:${backendPort}`;
  const frontendUrl = `http://127.0.0.1:${frontendPort}`;
  const vinextUrl = `http://127.0.0.1:${vinextPort}`;

  // Mutable authorization is company data, so upgrades and uninstall never
  // place or remove it under the installation directory. The public key is
  // immutable application material shipped inside the installer.
  const authorizationDir = path.join(
    process.env.LOCALAPPDATA || app.getPath("userData"),
    "Parakh",
    "authorization",
  );
  if (app.isPackaged) fs.mkdirSync(authorizationDir, { recursive: true });
  const authorizationFile = path.join(authorizationDir, "authorization.json");
  const authorizationPublicKey = app.isPackaged
    ? path.join(process.resourcesPath, "authorization", "public-key.pem")
    : process.env.PARAKH_AUTH_PUBLIC_KEY_FILE || "";
  if (app.isPackaged && !fs.existsSync(authorizationPublicKey)) {
    throw new Error(`The installer is missing its authorization verification key: ${authorizationPublicKey}`);
  }
  const vlmEnvironment = await startOptionalVlm(vlmPort);

  startChild(commandForPython(), ["-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", String(backendPort)], {
    cwd: backendRoot,
    env: {
      ...process.env,
      PARAKH_PACKAGED: app.isPackaged ? "1" : "",
      PARAKH_AUTH_FILE: app.isPackaged
        ? authorizationFile
        : process.env.PARAKH_AUTH_FILE || "",
      PARAKH_AUTH_PUBLIC_KEY_FILE: authorizationPublicKey,
      ...vlmEnvironment,
      PARAKH_LAUNCH_TOKEN: launchToken,
      // Packaged builds deliberately leave PARAKH_DATA_DIR unset so the backend
      // resolves %LOCALAPPDATA%\Parakh\data itself -- that default is part of
      // what the pilot is testing. Source-tree runs keep their own sandbox.
      ...(app.isPackaged ? {} : { PARAKH_DATA_DIR: path.join(app.getPath("userData"), "demo-data") }),
    },
  }, "backend");
  await waitFor(`${backendUrl}/health`, "Backend");

  const vinextCli = path.join(frontendDir, "node_modules", "vinext", "dist", "cli.js");
  startNode([vinextCli, "start", "-p", String(vinextPort), "-H", "127.0.0.1"], {
    cwd: frontendDir,
    env: { ...process.env, BACKEND_URL: backendUrl },
  }, "vinext");
  await waitFor(vinextUrl, "Vinext");

  const gateway = path.join(frontendDir, "electron", "frontend-gateway.cjs");
  startNode([gateway, String(frontendPort), String(vinextPort), String(backendPort)], {
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
    // blob: URLs are created in-page by viewReport() (see lib/format.ts) to show
    // a fetched report that already carried the launch token -- they never hit
    // the backend directly, so they carry no request of their own to protect.
    if (url.startsWith(frontendUrl) || url.startsWith("blob:")) return { action: "allow" };
    void shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith(frontendUrl)) event.preventDefault();
  });
  mainWindow.once("closed", () => { mainWindow = null; });
  await loadWithRetry(mainWindow, `${frontendUrl}/welcome?lt=${encodeURIComponent(launchToken)}`);
}

// Two copies would allocate separate ports but share one SQLite job store and
// one data directory. Ports no longer collide to stop that, so say it directly.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.whenReady().then(() => {
    openLog();
    return launch();
  }).catch((error) => {
    record(`[launcher] ${error.stack || error.message}\n`);
    dialog.showErrorBox(
      "Parakh could not start",
      `${error.message}\n\nDetails: ${logPath}`,
    );
    app.quit();
  });
  app.on("before-quit", stopChildren);
  app.on("window-all-closed", () => app.quit());
}
