# Vendored web fonts

Two `.woff2` files, latin subset, committed deliberately. They are the only
binaries in this directory and they exist because the project has **no build
step and makes no external requests at runtime** (`docs/design/dashboard.md`
§10.2, `demo-site.md` §6): a font served from a CDN is a runtime external
request, and a font that is not served at all leaves the dashboard on whatever
the operating system happens to have.

**This directory is the document of record** (DESIGN.md, *Fonts — one
canonical location*) — and since 2026-08-11 it is the *only* copy. The
dashboard used to carry a byte-identical duplicate because `public/static/*`
is only routed under `VIDTHEQUE_PUBLIC_READONLY=1` (`public/__init__.py`)
while the dashboard's own asset route is always registered; now the dashboard
route aliases its `fonts/` prefix onto this directory
(`dashboard/__init__.py`, `_FONTS_DIR`), so a private deployment still serves
`/dashboard/static/fonts/…` and there is no second copy to drift.

Both are SIL Open Font License 1.1. The OFL requires the licence to travel with
the files, so it does — see `Archivo-OFL.txt` and `JetBrainsMono-OFL.txt`,
copied verbatim from the packages below.

| file | family | axis | bytes | md5 | source |
|---|---|---|---|---|---|
| `archivo-latin-wght-normal.woff2` | Archivo | `wght` 100–900 | 34,928 | `cd56e2ec…` | `@fontsource-variable/archivo@5.2.8`, `files/archivo-latin-wght-normal.woff2` |
| `jetbrains-mono-latin-wght-normal.woff2` | JetBrains Mono | `wght` 100–800 | 40,404 | `b058178d…` | `@fontsource-variable/jetbrains-mono@5.2.8`, `files/jetbrains-mono-latin-wght-normal.woff2` |

Total 75,332 bytes for the two, under the 200 KB budget the research memo set
(`research/dashboard-design-research-2026-08-09.md` §1.5 step 6). No npm
dependency was added; the files are extracted and the rest of each package
discarded. JetBrains Mono was downloaded 2026-08-09 and is unchanged since;
Archivo arrived with lab v4 (commit `1251269`) and was copied here 2026-08-10.

## What was retired, 2026-08-10

`inter-latin-wght-normal.woff2`, `instrument-serif-latin-400-normal.woff2` and
their two licence texts came out with the warm-paper system. The text face is
Archivo now, and **there is no third face**: the wordmark is set in the text
face (DESIGN.md, *The Font-Logo Rule*), so the display serif has no job left.
Git history keeps both files; a retired face left in the tree is a face
somebody re-imports.

## Two things that were checked in the files themselves

1. **JetBrains Mono ships `calt`,** which is what draws its code ligatures
   (`->`, `!=`, `==`). Machine strings on this surface are ids, error codes and
   timecodes, where the literal characters are the information, so
   `dashboard.css` sets `font-variant-ligatures: none` on every mono context.
2. **`tnum` is present in both**, which is the feature that actually matters for
   a table of numbers and is what `font-variant-numeric: tabular-nums` reaches
   for. Nothing else is set: DESIGN.md's **Dead-Feature Rule** forbids naming a
   stylistic set nobody has opened the file to confirm.

## Why these two faces

Archivo for human text: a grotesque with a wide weight axis that stays sturdy
at 200 across a large headline, which is what lets one family carry both the
display voice and the body without a second family — and it is what the
wordmark is set in.

JetBrains Mono for machine text: a taller x-height and, the deciding argument
here, visibly distinct `0`/`O` and `1`/`l`/`I`, which is not a preference when
the machine strings on this surface include YouTube video ids like
`kCc8FmEb1nY` that a human sometimes has to read across to a terminal.

## Serving

Served twice from this one directory: the public asset route
(`GET /static/fonts/<file>`, public mode only) and the dashboard route's
`fonts/` alias (`GET /dashboard/static/fonts/<file>`, always registered).
`@font-face` `src:` values in `dashboard.css` are **relative**
(`fonts/…woff2`), never built from `PUBLIC_URL` — the SSH-tunnel rule
(`docs/design/dashboard.md` §8) applies to fonts exactly as it applies to
keyframes. `font-display: block` for both, matching the reference
implementation: these faces carry the display voice and a FOUT is worse than
100 ms of nothing. Only the text face is preloaded.
