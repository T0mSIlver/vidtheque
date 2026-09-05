import type { NextConfig } from "next";

// Python owns every path that is not a page. In production both processes sit
// behind one reverse proxy on one origin: exact page GETs (`/`, `/demo`,
// `/videos`, `/videos/{id}`, and under the dashboard `/dashboard`,
// `/dashboard/ledger`, `/dashboard/search`, `/dashboard/videos`, `/dashboard/videos/{video_id}`,
// `/dashboard/jobs`, `/dashboard/jobs/{job_id}`, `/dashboard/following`,
// `/dashboard/following/{slug}` and `/dashboard/index`) reach Next, and
// `/api/*`, `/frames/*`,
// `/mcp`, `/auth/*`, `/.well-known/*`, `/healthz`, `/videos/{id}/export.md`
// and the rest of `/dashboard/*` reach Python. Routing that split is the
// reverse proxy's job, not this file's.
//
// **`/dashboard/following` and `/dashboard/index` are the two paths both own,
// and the proxy has to split each by method**: the `GET` is the page this app
// serves, and the `POST` to the identical path is that page's form route, which
// stays Python's like every other write on this surface
// (frontend-migration.md §1d). Every other write has a segment its page does
// not — three under its section rather than two — so these are the only two,
// and neither can be written as a path anywhere in this repo, because a rewrite
// and a middleware matcher both match paths and not methods.
//
// Development runs the two on separate ports, and the browser still has to see
// one origin: Python reads a request from `localhost:3000` against its own
// `localhost:8080` as cross-site and refuses the write, so CORS would not buy
// the ask stream anything — it would only move the refusal. So in dev the Next
// server forwards Python's prefixes itself and every browser request stays
// same-origin, `POST /api/ask` included.
//
// Python's, always, whatever this app grows: nothing under these is a page, so
// they are forwarded *before* the router looks for one.
const PYTHON_PATHS = [
  "/api/:path*",
  "/frames/:path*",
  "/mcp",
  "/mcp/:path*",
  "/auth/:path*",
  "/.well-known/:path*",
  "/healthz",
  // The dashboard's non-pages, by name. `/dashboard/api/*` is the JSON the
  // React pages read with the session cookie, `/dashboard/static/*` is the
  // stylesheet and the fonts the pages Python still renders load, and the two
  // routes after them are the session flow — every one of them a thing Python
  // owns for good. A Next page must never shadow one of these.
  //
  // `/dashboard/index` was here until its page landed. Its `GET` is Next's now
  // and its `POST` is still Python's, which is a split by method and not by
  // path — so it is in `PYTHON_FORM_POSTS` below rather than here.
  "/dashboard/api/:path*",
  "/dashboard/static/:path*",
  "/dashboard/login",
  "/dashboard/logout",
  // Three segments, so the `/videos/[id]` page never matched it anyway; it is
  // listed for the same reason the proxy lists it — the export is Python's.
  "/videos/:id/export.md",
];

// The two writes whose path is a *ported page's* path: `POST /dashboard/following`
// is the add form's route beside the follows table, and `POST /dashboard/index`
// is the index form's beside the form page.
//
// A rewrite cannot key on a method — `has` reads headers, cookies, the query
// and the host, and nothing else — and these have to be in `beforeFiles`,
// because in `afterFiles` the router has already found the page and Next
// answers the POST itself with a document. Measured rather than assumed: a form
// POST to `/dashboard/index` with only the `afterFiles` catch-all in front of
// it comes back `200 text/html` — the page — and the write never reaches Python
// at all. So they key on the header every write on this surface sends and no
// document navigation ever sends: the form encoding of its body
// (frontend-migration.md §9 — a form-encoded body is one of the three things a
// write carries, along with the cookie and an `Accept` that prefers JSON).
//
// **This is a development shim, not the rule.** In production the two
// processes sit behind one proxy and that proxy splits these paths by method,
// which is what §1d records. Nothing in this repo can express that, because
// both configurations that route them here match paths.
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
];

// The rest of `/dashboard`, which is being ported one page at a time
// (docs/ROADMAP.md). `afterFiles` is the whole point: it is consulted *after*
// the router has looked for a page, so the nine pages in `src/app/dashboard/`
// win their own paths, and everything with no page yet falls through to Python
// exactly as before — every POST behind these pages included:
// `/dashboard/jobs/{id}/cancel`, `/dashboard/videos/{id}/tags` and the five
// `/dashboard/following/{slug}/…` writes. Each port deletes nothing here; it
// just adds a page the router finds first.
//
// **Two exceptions, and they are `PYTHON_FORM_POSTS` above.** `POST
// /dashboard/following` and `POST /dashboard/index` share their paths with
// ported pages, so this catch-all never sees them: the router finds the page
// first and Next answers the write with a document. Those two are forwarded in
// `beforeFiles` under a header condition instead.
const DASHBOARD_UNPORTED = ["/dashboard", "/dashboard/:path*"];

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
      beforeFiles: [
        ...PYTHON_PATHS.map((source) => ({ source, destination: base + source })),
        ...PYTHON_FORM_POSTS.map((entry) => ({ ...entry, destination: base + entry.source })),
      ],
      afterFiles: DASHBOARD_UNPORTED.map((source) => ({ source, destination: base + source })),
      fallback: [],
    };
  },
};

export default nextConfig;
