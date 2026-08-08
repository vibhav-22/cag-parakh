const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const listenPort = Number(process.argv[2]);
const vinextPort = Number(process.argv[3]);
const backendPort = Number(process.argv[4]);
const clientRoot = path.resolve(__dirname, "..", "dist", "client");

if (![listenPort, vinextPort, backendPort].every(Number.isInteger)) {
  throw new Error("Usage: frontend-gateway.cjs <listen-port> <vinext-port> <backend-port>");
}

const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".gif": "image/gif",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".jpeg": "image/jpeg",
  ".jpg": "image/jpeg",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};

function localFile(url) {
  let pathname;
  try {
    pathname = decodeURIComponent(new URL(url, "http://127.0.0.1").pathname);
  } catch {
    return null;
  }
  const candidate = path.resolve(clientRoot, `.${pathname}`);
  const relative = path.relative(clientRoot, candidate);
  if (relative.startsWith("..") || path.isAbsolute(relative)) return null;
  try {
    return fs.statSync(candidate).isFile() ? candidate : null;
  } catch {
    return null;
  }
}

function serveFile(req, res, file) {
  const headers = {
    "Content-Type": contentTypes[path.extname(file).toLowerCase()] || "application/octet-stream",
    "Cache-Control": file.includes(`${path.sep}assets${path.sep}`)
      ? "public, max-age=31536000, immutable"
      : "public, max-age=3600",
  };
  res.writeHead(200, headers);
  if (req.method === "HEAD") return res.end();
  fs.createReadStream(file).on("error", () => res.destroy()).pipe(res);
}

// Vinext resolves next.config.ts rewrites while building, which bakes the
// backend port into the bundle and forces every machine to have that one port
// free. Routing the backend's own paths here instead means the launcher can
// hand the backend any free port at startup, so a laptop that already has
// something on 8000 still works.
function isBackendPath(url) {
  const pathname = url.split("?")[0];
  return pathname === "/health" || pathname === "/api" || pathname.startsWith("/api/");
}

function proxy(req, res, port, label) {
  const upstream = http.request({
    hostname: "127.0.0.1",
    port,
    path: req.url,
    method: req.method,
    headers: { ...req.headers, host: `127.0.0.1:${listenPort}` },
  }, (upstreamResponse) => {
    res.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
    upstreamResponse.pipe(res);
  });
  upstream.on("error", (error) => {
    if (!res.headersSent) res.writeHead(502, { "Content-Type": "text/plain; charset=utf-8" });
    res.end(`${label} service unavailable: ${error.message}`);
  });
  req.pipe(upstream);
}

http.createServer((req, res) => {
  // Checked before the static lookup so a built asset can never shadow an API
  // route, and so backend paths skip the filesystem stat entirely.
  if (isBackendPath(req.url)) return proxy(req, res, backendPort, "Backend");
  const file = (req.method === "GET" || req.method === "HEAD") && localFile(req.url);
  if (file) return serveFile(req, res, file);
  proxy(req, res, vinextPort, "Frontend");
}).listen(listenPort, "127.0.0.1", () => {
  console.log(`[desktop] Frontend gateway running at http://127.0.0.1:${listenPort}`);
});
