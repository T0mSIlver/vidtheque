# web — the Next.js front end

A separate deployable that talks to a vidtheque instance over its public
`/api/*` facade (`docs/design/demo-site.md` §2). It serves the same two front
doors — the landing at `/`, the reader at `/demo` (§1) — and reads the instance
only through the facade. The Python-served copies of those pages, and
`/dashboard`, which has no counterpart here, are untouched.

Next.js 16 (App Router), React 19, TypeScript, CSS Modules on the design
tokens from `DESIGN.md`. No Tailwind, no component library.

```
pnpm install --frozen-lockfile   # once
pnpm dev                         # http://localhost:3000
pnpm build && pnpm start
```

Copy `.env.example` to `.env.local` before `pnpm dev`: `VIDTHEQUE_API_URL` is
required and the app throws without it. `deploy/.env.example` at the repo root
is the document of record for both variables.

## One origin, two servers

In production a reverse proxy puts both behind one origin and routes by path:
the exact page GETs — `/`, `/demo`, `/videos`, `/videos/{id}` — reach Next, and
everything else reaches Python (`/api/*`, `/frames/*`, `/mcp`, `/auth/*`,
`/.well-known/*`, `/healthz`, `/dashboard/*`, `/videos/{id}/export.md`). The
browser therefore calls Python directly, and this server owns no endpoint of
its own.

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

`src/proxy.ts` sends every document the headers the Python instance sends with
its own two pages — `frame-ancestors 'none'` with the `X-Frame-Options: DENY`
twin, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff` — and a
CSP of the same shape, in the nonce form a React page needs:

```
default-src 'self'; script-src 'self' 'nonce-<fresh per request>'
'strict-dynamic'; style-src 'self' 'unsafe-inline'; img-src 'self';
font-src 'self'; connect-src 'self'; frame-ancestors 'none';
form-action 'self'; base-uri 'none'; object-src 'none'
```

`style-src` is the one directive looser than Python's, because React renders
the OCR boxes' coordinates and the 16:9 frame as `style=` attributes.
Development adds `'unsafe-eval'` (React rebuilds server stacks with it), the
API origin on `img-src` and `connect-src` (two ports, so frame URLs point at
the other one) and `ws:` for the HMR socket.

The nonce is what the price is paid for: it is new every request, so every
document is rendered per request — `connection()` in the root layout says so —
and Cache Components is off, since a partial prerender would serve a shell
whose scripts were stamped with a nonce that was never issued. Data caching
survives that: `src/lib/library.ts` holds the library's two reads in
`unstable_cache` with the periods the named lifetimes had, 60s for the list and
an hour for a video, both serving the stale copy while the fresh one is
fetched. Search is deliberately uncached, and says why in its own file.

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
