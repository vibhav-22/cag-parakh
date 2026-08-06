const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const listenPort = Number(process.argv[2]);
const vinextPort = Number(process.argv[3]);
const clientRoot = path.resolve(__dirname, "..", "dist", "client");

if (!Number.isInteger(listenPort) || !Number.isInteger(vinextPort)) {
  throw new Error("Usage: frontend-gateway.cjs <listen-port> <vinext-port>");
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

function proxy(req, res) {
  const upstream = http.request({
    hostname: "127.0.0.1",
    port: vinextPort,
    path: req.url,
    method: req.method,
    headers: { ...req.headers, host: `127.0.0.1:${listenPort}` },
  }, (upstreamResponse) => {
    res.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
    upstreamResponse.pipe(res);
  });
  upstream.on("error", (error) => {
    if (!res.headersSent) res.writeHead(502, { "Content-Type": "text/plain; charset=utf-8" });
    res.end(`Frontend service unavailable: ${error.message}`);
  });
  req.pipe(upstream);
}

http.createServer((req, res) => {
  const file = (req.method === "GET" || req.method === "HEAD") && localFile(req.url);
  if (file) return serveFile(req, res, file);
  proxy(req, res);
}).listen(listenPort, "127.0.0.1", () => {
  console.log(`[desktop] Frontend gateway running at http://127.0.0.1:${listenPort}`);
});
