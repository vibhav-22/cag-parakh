import type { NextConfig } from "next";

// Proxy API calls through the frontend origin so the auth cookie works for
// fetch, <img> page renders, and artifact links without CORS complexity.
const backendUrl = process.env.BACKEND_URL || "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backendUrl}/api/:path*` },
      { source: "/health", destination: `${backendUrl}/health` },
    ];
  },
  // The dev server's default 1 MB body-size cap (meant for server actions)
  // also applies to proxied /api/* requests, so batch uploads need it raised
  // to match the backend's own per-file limit (50 files x 25 MB each).
  experimental: {
    serverActions: {
      bodySizeLimit: "1300mb",
    },
  },
};

export default nextConfig;
