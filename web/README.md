# web — the Next.js front end

A separate deployable that talks to a vidtheque instance over its public
`/api/*` facade (`docs/design/demo-site.md` §2). It serves the same two front
doors on its own origin — the landing at `/`, the reader at `/demo` (§1) — and
reads the instance only through the facade. The Python-served copies of those
pages, and `/dashboard`, which has no counterpart here, are untouched.

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
GPU and no live backend. Nothing here needs one — the landing prerenders to
static HTML from a checked-in corpus readout, and every page that does read the
corpus reads it at request time inside a `<Suspense>` boundary, so the build
never calls the API.

`vitest.config.mts` pins `NODE_ENV=test` at config load. Vitest only defaults
it when it is unset, and a shell exporting `NODE_ENV=production` otherwise
gets React's production build, where `React.act` is missing and every
component test fails.

`AGENTS.md` and `CLAUDE.md` in this directory are written by `next dev` and
point coding agents at the bundled docs for this exact Next.js version.
