import type { NextConfig } from "next";

// Python owns every path that is not a page. In production both processes sit
// behind one reverse proxy on one origin: exact page GETs (`/`, `/demo`,
// and under the dashboard `/dashboard`,
// `/dashboard/ledger`, `/dashboard/search`, `/dashboard/videos`, `/dashboard/videos/{video_id}`,
// `/dashboard/jobs`, `/dashboard/jobs/{job_id}`, `/dashboard/following`,
// `/dashboard/following/{slug}`, `/dashboard/index` and `/dashboard/login`)
// reach Next, and
// `/api/*`, `/frames/*`,
// `/mcp`, `/auth/*`, `/.well-known/*`, `/healthz`, `/videos/{id}/export.md`
// and the rest of `/dashboard/*` reach Python. Routing that split is the
// reverse proxy's job, not this file's.
//
// **`/dashboard/following`, `/dashboard/index` and `/dashboard/login` are the
// three paths both own, and the proxy has to split each by method**: the `GET`
// is the page this app serves, and the `POST` to the identical path is that
// page's write, which stays Python's like every other write on this surface
// (frontend-migration.md §1d). Every other write has a segment its page does
// not — three under its section rather than two — so these are the only three,
// and none can be written as a path anywhere in this repo, because a rewrite
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
  // React pages read with the session cookie and `/dashboard/logout` is the
  // write that ends a session — both things Python owns for good. A Next page
  // must never shadow one of these.
  //
  // `/dashboard/index` and `/dashboard/login` were here until their pages
  // landed. Their `GET`s are Next's now and their `POST`s are still Python's,
  // which is a split by method and not by path — so they are in
  // `PYTHON_FORM_POSTS` below rather than here. `logout` never had a page of
  // its own and stays. `/dashboard/static/*` was here until 2026-09-06: it
  // served the deleted stylesheet, the two scripts and a `fonts/` alias, the
  // route is gone with them, and this app self-hosts its faces out of
  // `src/fonts` (dashboard.md §23).
  "/dashboard/api/:path*",
  "/dashboard/logout",
  // The one path left under `/videos` since the library pages went on
  // 2026-09-07: the Markdown export, which was always Python's.
  "/videos/:id/export.md",
];

// The three writes whose path is a *ported page's* path: `POST
// /dashboard/following` is the add form's route beside the follows table,
// `POST /dashboard/index` is the index form's beside the form page, and `POST
// /dashboard/login` is the one that mints the session cookie beside the
// sign-in page — the last of them the sharpest, because what makes it Python's
// is the `Set-Cookie` on its response, and an `HttpOnly` cookie is not a thing
// a React shell can mint.
//
// A rewrite cannot key on a method — `has` reads headers, cookies, the query
// and the host, and nothing else — and these have to be in `beforeFiles`,
// because in `afterFiles` the router has already found the page and Next
// answers the POST itself with a document. Measured rather than assumed: a form
// POST to `/dashboard/index` forwarded from `afterFiles` rather than from here
// comes back `200 text/html` — the page — and the write never reaches Python
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
  { source: "/dashboard/login", has: FORM_ENCODED },
];

// The rest of `/dashboard` is this app's, and nothing else about the prefix is
// forwarded. A path with no React page 404s here in development rather than
// travelling to a process that would 404 it too: the Jinja surface was deleted
// on 2026-09-06 and what Python still answers under this prefix is
// `/dashboard/api/*`, the thirteen POSTs and the `/dashboard/` redirect
// (dashboard.md §23) — every one of them named above.
//
// An `afterFiles` catch-all on `/dashboard` and `/dashboard/:path*` stood here
// until 2026-09-06, so that a page not ported yet could still be served by
// Python. `afterFiles` runs after static pages and public files but *before*
// dynamic routes, so it matched `/dashboard/videos/{video_id}`,
// `/dashboard/jobs/{job_id}` and `/dashboard/following/{slug}` before their own
// segments were ever tried, and every detail page in development came back a
// `404` off Python. With no unported page left there was nothing on the other
// side of that to weigh, so it is gone; `src/next.config.test.ts` holds the
// shape.
//
// `/dashboard/` with the trailing slash needs nothing here either. Python's
// answer to it is a `308` to `/dashboard`, which is a page this app serves —
// and Next's own default (`trailingSlash: false`) already redirects the one to
// the other.

/** What `next dev` accepts a cross-origin request from besides `localhost`.
 *
 *  `VIDTHEQUE_DEV_ORIGINS`, comma separated, `127.0.0.1` alone when unset —
 *  which is right for everyone but the person running the dev server on a box
 *  they reach over the LAN, and for them it is one variable rather than a
 *  patch. The list a box's own address was hardcoded into lived here until
 *  2026-09-06; a public repo is no place for one machine's address, and the
 *  next person to run this on a different network had to edit the file.
 *
 *  `deploy/.env.example` is the document of record for the name. */
function devOrigins(): string[] {
  const named = (process.env.VIDTHEQUE_DEV_ORIGINS ?? "")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean);
  return named.length ? named : ["127.0.0.1"];
}

// Cache Components is deliberately absent. It was on, and it is what made
// `/demo` and the library pages partial prerenders — a static shell with the
// request-time part streamed in. A shell built at build time carries scripts
// stamped with no nonce, and `proxy.ts` mints a new one per request, so the
// two cannot both be true (Next's own CSP guide says as much). The pages
// render per request instead. The `unstable_cache` reads the library kept its
// data in went with it on 2026-09-07; `/demo` was never cached and says why
// in `src/lib/search.ts`.
const nextConfig: NextConfig = {
  // `web/Dockerfile`'s runtime stage copies the traced server bundle instead of
  // a node_modules tree. It is an output format and nothing else: the routes,
  // the rewrites below and the per-request headers `proxy.ts` sends are the
  // same either way, and `next start` on a full build still works.
  output: "standalone",
  // Dev only: the hosts this box is reached on besides `localhost`, without
  // which the dev server refuses their requests and the pages never hydrate.
  // Which hosts those are is a fact about one machine and not about this repo,
  // so it is named in the environment rather than written down here.
  allowedDevOrigins: devOrigins(),
  // The two `Cache-Control` answers this app owes, and both of them are the
  // Python instance's own, ported.
  //
  // They are here and not in `proxy.ts` because a header the middleware sets on
  // a document is overwritten by the dynamic render underneath it — measured:
  // `proxy.ts` has set `no-store` on `/dashboard` since the port and what came
  // back on the wire was Next's own `no-cache, must-revalidate`, capitalised
  // the way Next capitalises it. `headers()` is applied to the finished
  // response, which is the only place this can be said and stick. (The one
  // exception Next documents is a truly immutable asset — the hashed files
  // under `/_next/static`, which it already serves at the year below and which
  // no config can override. The vendored faces are among them: `next/font`
  // emits them into `/_next/static/media`, so their year is Next's and not
  // this block's.)
  async headers() {
    return [
      {
        // A management page describes state that changes under the reader, and
        // a shared cache must never hold one: `dashboard/views.py` sent
        // `no-store` with every document it rendered (§19), and losing it is a
        // proxy holding one operator's corpus for the next reader.
        source: "/dashboard/:path*",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
      {
        // The landing's 155 stills, and only them. Every one of them is a
        // frame lifted out of a talk and named after where it came from — a
        // video id, a moment's second, a plate's number — so a still is added
        // or removed and never edited under its own name, which is what makes
        // the year honest. It is the value `public/__init__.py:59,183` served
        // them at; `public/` is otherwise served `max-age=0`, so all 155
        // revalidated on every load of the front door.
        source: "/landing/:path*",
        headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }],
      },
    ];
  },
  async rewrites() {
    const base = process.env.VIDTHEQUE_API_URL?.replace(/\/+$/, "");
    if (process.env.NODE_ENV === "production" || !base) return [];
    return {
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
