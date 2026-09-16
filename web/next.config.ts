import type { NextConfig } from "next";

// Routing between Next and Python is the reverse proxy's job in production
// (frontend-migration.md §1a, §1d). In development the two run on separate
// ports, so Next forwards Python's paths itself and the browser sees one
// origin (§1c).

// Never pages, so forwarded before the router looks for one.
const PYTHON_PATHS = [
  "/api/:path*",
  "/frames/:path*",
  "/mcp",
  "/mcp/:path*",
  "/auth/:path*",
  "/.well-known/:path*",
  "/healthz",
  "/dashboard/api/:path*",
  "/dashboard/logout",
  "/videos/:id/export.md",
];

// Paths that are a page on GET and Python's form write on POST. A rewrite can
// not key on the method, so this development shim keys on the form encoding
// every write sends; production splits by method at the proxy (§1d).
const FORM_ENCODED = [
  {
    type: "header" as const,
    key: "content-type",
    value: "application/x-www-form-urlencoded.*",
  },
];

const PYTHON_FORM_POSTS = [
  { source: "/dashboard/following", has: FORM_ENCODED },
  { source: "/dashboard/index", has: FORM_ENCODED },
  { source: "/dashboard/login", has: FORM_ENCODED },
];

/** `VIDTHEQUE_DEV_ORIGINS`, comma separated; `127.0.0.1` when unset
 *  (deploy/.env.example). */
function devOrigins(): string[] {
  const named = (process.env.VIDTHEQUE_DEV_ORIGINS ?? "")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean);
  return named.length ? named : ["127.0.0.1"];
}

// No cacheComponents: a prerendered shell cannot carry proxy.ts's per-request
// nonce (§1b).
const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: devOrigins(),
  // Here rather than in proxy.ts: a header the proxy sets on a rendered
  // document is overwritten by the render (docs/LESSONS.md).
  async headers() {
    return [
      {
        source: "/dashboard/:path*",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
      {
        // Stills are added or removed, never edited under their own name.
        source: "/landing/:path*",
        headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }],
      },
    ];
  },
  async rewrites() {
    const base = process.env.VIDTHEQUE_API_URL?.replace(/\/+$/, "");
    if (process.env.NODE_ENV === "production" || !base) return [];
    return {
      // beforeFiles only: by afterFiles a static page has already won the path,
      // and afterFiles still runs ahead of the dynamic routes it would shadow.
      beforeFiles: [
        ...PYTHON_PATHS.map((source) => ({ source, destination: base + source })),
        ...PYTHON_FORM_POSTS.map((entry) => ({ ...entry, destination: base + entry.source })),
      ],
      afterFiles: [],
      fallback: [],
    };
  },
};

export default nextConfig;
