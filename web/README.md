# web — the Next.js front end

A separate deployable that talks to a vidtheque instance over its public
`/api/*` facade (`docs/design/demo-site.md` §2). The Python-served pages at
`/`, `/demo` and `/dashboard` are untouched by anything in here.

Next.js 16 (App Router), React 19, TypeScript, CSS Modules on the design
tokens from `DESIGN.md`. No Tailwind, no component library.

```
pnpm install          # once
pnpm dev              # http://localhost:3000, against VIDTHEQUE_API_URL
pnpm build && pnpm start
pnpm lint
pnpm exec tsc --noEmit
```

`AGENTS.md` and `CLAUDE.md` in this directory are written by `next dev` and
point coding agents at the bundled docs for this exact Next.js version.
