import type { NextConfig } from "next";

// Python owns every path that is not a page. In production both processes sit
// behind one reverse proxy on one origin: exact page GETs (`/`, `/demo`,
// `/videos`, `/videos/{id}`) reach Next, and `/api/*`, `/frames/*`, `/mcp`,
// `/auth/*`, `/.well-known/*`, `/healthz`, `/dashboard/*` and
// `/videos/{id}/export.md` reach Python. Routing that split is the reverse
// proxy's job, not this file's.
//
// Development runs the two on separate ports, and the browser still has to see
// one origin: Python reads a request from `localhost:3000` against its own
// `localhost:8080` as cross-site and refuses the write, so CORS would not buy
// the ask stream anything — it would only move the refusal. So in dev the Next
// server forwards Python's prefixes itself and every browser request stays
// same-origin, `POST /api/ask` included.
const PYTHON_PATHS = [
  "/api/:path*",
  "/frames/:path*",
  "/mcp",
  "/mcp/:path*",
  "/auth/:path*",
  "/.well-known/:path*",
  "/healthz",
  "/dashboard",
  "/dashboard/:path*",
  // Three segments, so the `/videos/[id]` page never matched it anyway; it is
  // listed for the same reason the proxy lists it — the export is Python's.
  "/videos/:id/export.md",
];

// Cache Components is deliberately absent. It was on, and it is what made
// `/demo`, `/videos` and `/videos/[id]` partial prerenders — a static shell
// with the request-time part streamed in. A shell built at build time carries
// scripts stamped with no nonce, and `proxy.ts` mints a new one per request,
// so the two cannot both be true (Next's own CSP guide says as much). The
// pages render per request instead, and the reads that were `"use cache"` are
// `unstable_cache` in `src/lib/library.ts`.
const nextConfig: NextConfig = {
  async rewrites() {
    const base = process.env.VIDTHEQUE_API_URL?.replace(/\/+$/, "");
    if (process.env.NODE_ENV === "production" || !base) return [];
    return {
      beforeFiles: PYTHON_PATHS.map((source) => ({ source, destination: base + source })),
      afterFiles: [],
      fallback: [],
    };
  },
};

export default nextConfig;
