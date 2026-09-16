# web — the Next.js front end

A separate deployable that talks to a vidtheque instance over its public
`/api/*` facade (`docs/design/demo-site.md` §2). It serves the public front doors:
the landing at `/`, the reader at `/demo` (§1), and the AI Engineer Paris 2026
edition at `/paris`. They read the instance only
through the facade, and the whole of `/dashboard`, which reads
`/dashboard/api/*` in the browser with the session cookie. **Every page on both
surfaces is served from here.** Python's own copies of the front doors left on
2026-09-05 and its dashboard HTML on 2026-09-06; that package renders nothing
now (`docs/design/dashboard.md` §23).

Next.js 16 (App Router), React 19, TypeScript, CSS Modules on the design
tokens from `DESIGN.md`. No Tailwind, no component library.

```
pnpm install --frozen-lockfile   # once
pnpm dev                         # http://localhost:3000
pnpm build && pnpm start
```

Copy `.env.example` to `.env.local` before `pnpm dev`: `VIDTHEQUE_API_URL` is
required and the app throws without it. `deploy/.env.example` at the repo root
is the document of record for all three variables.

Reaching the dev server from another machine takes one more: `next dev`
answers `localhost` and refuses a cross-origin request from anything else, so
`VIDTHEQUE_DEV_ORIGINS` (comma separated, `127.0.0.1` when unset) names the
addresses that box is reached on. Without it the pages load over the LAN and
never hydrate.

## The layout

`src/` is split by surface and the two halves do not import each other
(`docs/design/DECISIONS.md`, 2026-09-16). `pnpm lint` fails on a crossing.

```
src/app/layout.tsx            fonts, tokens, globals — the one shared root
src/app/error.tsx             the root boundaries, and `not-found.tsx` beside it
src/app/(public)/             page.tsx (landing), demo/, paris/
src/app/(dashboard)/dashboard/  every /dashboard page, URLs unchanged
src/components/public/        landing/ (with its canned data/), console/,
                              frames, rail, receipt, result group, shell
src/components/dashboard/     kit/, the chassis, session, polling, ported
src/components/ui/            Pill, RetryIn — the primitives both halves draw
src/lib/api/                  the public facade client, its schemas and reads
src/lib/dashboard/            the /dashboard/api client, resources and schemas
src/lib/format/               index.ts and landing.ts, read from both halves
src/lib/schemas/              the /api/search payload, read from both halves
src/styles/                   generated tokens, the type scale, the ornament
src/test/                     next.ts and setup.ts, then public/ and dashboard/
```

A route group carries no URL, so `(public)` and `(dashboard)` change no path
and `proxy.ts`'s matcher is untouched. There is no `(public)/layout.tsx`: the
landing wears no shell, and `/demo` and `/paris` each pass `PublicShell`
different props and carry their own metadata.

## One origin, two servers

In production a reverse proxy puts both behind one origin and routes by path:
the exact page GETs — `/`, `/demo`, `/paris`, and under the
dashboard `/dashboard`, `/dashboard/ledger`, `/dashboard/search`,
`/dashboard/videos`, `/dashboard/videos/{id}`, `/dashboard/jobs`,
`/dashboard/jobs/{id}`, `/dashboard/following`, `/dashboard/following/{slug}`,
`/dashboard/index` and `/dashboard/login` — reach Next, and everything else
reaches Python (`/api/*`, `/frames/*`, `/mcp`, `/auth/*`, `/.well-known/*`,
`/healthz`, `/videos/{id}/export.md`, and the rest of `/dashboard/*`, which is
`/dashboard/api/*`, the thirteen POSTs and the `/dashboard/` redirect). The
browser therefore calls Python directly, and this server owns no endpoint of
its own.

The last three of those page paths are also POST routes Python keeps, so the
proxy splits each by method: the `GET` is the page here, the `POST` is the
page's write. `proxy.ts`'s matcher gives the document policy to every path
under `/dashboard` except `/dashboard/api/*` and `/dashboard/logout`, and
`src/components/dashboard/ported.ts` lists the pages every link into the surface asks
about. Its data is not this server's — the
browser reads `/dashboard/api/*` itself, same-origin, with the session cookie
(`src/lib/dashboard/`), so Next never sees a credential and caches nothing per
reader.

`POST /api/ask` is Python's, and only Python's. The ask pane posts to a
same-origin `/api/ask` and reads the event stream the API sends; nothing here
re-frames it, so the event vocabulary in `demo-site.md` §3.5 has one owner and
the ninety seconds of a streamed answer cross one process fewer.

`pnpm dev` runs the two on separate ports, so `next.config.ts` reproduces the
same split with `rewrites()` and the browser stays on one origin there too —
Python reads a request from `localhost:3000` against `localhost:8080` as
cross-site and refuses the write, which no CORS header would fix. The rewrites
are off in production, where the proxy is doing it.

The reads this server does for itself — search, videos, meta in
`src/lib/api/client.ts` — go straight to `VIDTHEQUE_API_URL` and are the only
requests that forward the visitor's address under `VIDTHEQUE_CLIENT_IP_HEADER`,
so the API's per-IP limiter keys on the visitor rather than on this process.

## Headers, and what they cost

`src/proxy.ts` sends every document the headers the Python instance sent with
its own pages while it had them — `frame-ancestors 'none'` with the
`X-Frame-Options: DENY` twin, `Referrer-Policy: no-referrer`,
`X-Content-Type-Options: nosniff` — and a CSP of the same shape, in the nonce
form a React page needs:

```
default-src 'self'; script-src 'self' 'nonce-<fresh per request>'
'strict-dynamic'; style-src 'self' 'unsafe-inline'; img-src 'self';
font-src 'self'; connect-src 'self'; frame-ancestors 'none';
form-action 'self'; base-uri 'none'; object-src 'none'
```

`style-src` is the one directive looser than Python's was, because React renders
the OCR boxes' coordinates and the 16:9 frame as `style=` attributes.
Development adds `'unsafe-eval'` (React rebuilds server stacks with it), the
API origin on `img-src` and `connect-src` (two ports, so frame URLs point at
the other one) and `ws:` for the HMR socket.

The nonce is what the price is paid for: it is new every request, so every
document is rendered per request — `connection()` in the root layout says so —
and Cache Components is off, since a partial prerender would serve a shell
whose scripts were stamped with a nonce that was never issued. Nothing on this
surface caches data: `src/lib/library.ts` held the library's two reads in
`unstable_cache` until the library pages were removed on 2026-09-07, and search
is deliberately uncached and says why in its own file.

## Checks

`make web-check` from the repo root runs all of them, in this order, which is
also the order of the `ci-web` workflow. Any one can be run on its own:

```
pnpm tokens:check   # src/styles is in sync with DESIGN.md (pnpm tokens rewrites it)
pnpm format:check   # Prettier (pnpm format rewrites)
pnpm lint           # ESLint, with eslint-config-prettier so it stays out of formatting
pnpm test           # Vitest: unit tests in Node, component tests in jsdom
pnpm typecheck      # next typegen, then tsc --noEmit
pnpm build          # production build
```

CI runs on GitHub-hosted runners: Node 24.18.0, pnpm from `packageManager`, no
GPU and no live backend. Nothing here needs one — the landing renders from a
checked-in corpus readout, and every page that does read the corpus reads it at
request time inside a `<Suspense>` boundary, so the build never calls the API.

`vitest.config.mts` pins `NODE_ENV=test` at config load. Vitest only defaults
it when it is unset, and a shell exporting `NODE_ENV=production` otherwise
gets React's production build, where `React.act` is missing and every
component test fails.

`AGENTS.md` and `CLAUDE.md` in this directory are written by `next dev` and
point coding agents at the bundled docs for this exact Next.js version.
