# Vendored web fonts

Three `.woff2` files, latin subset, committed deliberately. They are the only
binaries in this repo and they exist because the project has **no build step and
makes no external requests at runtime** (`docs/design/dashboard.md` §10.2,
`demo-site.md` §6): a font served from a CDN is a runtime external request, and
a font that is not served at all leaves the dashboard on whatever the operating
system happens to have.

All three are SIL Open Font License 1.1. The OFL requires the licence to travel
with the files, so it does — see `Inter-OFL.txt`, `JetBrainsMono-OFL.txt` and
`InstrumentSerif-OFL.txt`, copied verbatim from the packages below.

| file | family | axis | bytes | source |
|---|---|---|---|---|
| `inter-latin-wght-normal.woff2` | Inter | `wght` 100–900 | 48,256 | `@fontsource-variable/inter@5.2.8`, `files/inter-latin-wght-normal.woff2` |
| `jetbrains-mono-latin-wght-normal.woff2` | JetBrains Mono | `wght` 100–800 | 40,404 | `@fontsource-variable/jetbrains-mono@5.2.8`, `files/jetbrains-mono-latin-wght-normal.woff2` |
| `instrument-serif-latin-400-normal.woff2` | Instrument Serif | static 400 | 21,032 | `@fontsource/instrument-serif@5.2.8`, `files/instrument-serif-latin-400-normal.woff2` |

Downloaded 2026-08-09 from `https://cdn.jsdelivr.net/npm/<package>@<version>/…`.
Total 109,692 bytes for the three, under the 200 KB budget the research memo set
(`research/dashboard-design-research-2026-08-09.md` §1.5 step 6, which recorded
the sizes as unmeasured — these are the measured numbers).

`research/…-2026-08-09.md` §5 item 2 recorded Instrument Serif's licence as
**unverified** ("reported as OFL via Google Fonts / Fontsource, but I did not
open either `OFL.txt`"). It is verified now: the package's `LICENSE` is the SIL
OFL 1.1 verbatim, and `metadata.json` declares `"license": {"type": "OFL-1.1"}`
with the Instrument Serif Project Authors' copyright line, which is the line
reproduced at the top of `InstrumentSerif-OFL.txt`.

No npm dependency was added. The files are extracted, the rest of each package
discarded.

## Two things that were checked in the files themselves

1. **`cv01` and `ss03` are not in this Inter build.** The research memo §5 item 7
   flagged this as unverified and it is now verified: the latin `wght` subset's
   GSUB carries `calt, ccmp, dnom, frac, locl, numr, pnum, tnum` and nothing
   else. `font-feature-settings: "cv01" 1, "ss03" 1` would be a silent no-op, so
   `dashboard.css` does not set it. `tnum` *is* present, which is the feature
   that actually matters for a table of numbers, and it is what
   `font-variant-numeric: tabular-nums` reaches for.
2. **JetBrains Mono ships `calt`,** which is what draws its code ligatures
   (`->`, `!=`, `==`). Machine strings on this surface are ids, error codes and
   timecodes, where the literal characters are the information, so
   `dashboard.css` sets `font-variant-ligatures: none` on every mono context.

## Why these three faces

Inter for human text: the workhorse UI face, drawn for screens, with number
forms good enough that Grafana's design system picked it for exactly that
reason. JetBrains Mono for machine text: a taller x-height and — the deciding
argument here — visibly distinct `0`/`O` and `1`/`l`/`I`, which is not a
preference when the machine strings on this surface include YouTube video ids
like `kCc8FmEb1nY` that a human sometimes has to read across to a terminal.
(The memo offered Geist Mono or JetBrains Mono; §1.5 of the same memo notes
JetBrains Mono is the better of the two below ~13px, which is where these sit.)

Instrument Serif for the **wordmark and nothing else**. It is Direction A's
display voice (`research/…-2026-08-09.md` §1.2, "the archive slip"), which Tom
authorised mixing into Direction C's chassis for brand moments only. It is a
single 400 weight with no italic here, which is a feature: there is no weight
ladder to reach for, so it cannot quietly become a UI face. The rule that binds
it is written into `DESIGN.md` — **The Wordmark-Only Rule** — and the only
selectors that name `--font-display` are `.wordmark` and `.footmark`.

At 21 KB for one word it is the cheapest of the three, and it is deliberately
**not** preloaded. It sets eight glyphs, twice per page; `font-display: swap`
means the wordmark paints in Inter first and re-paints in the serif, and the
lockup reserves a fixed height either way, so nothing on the page moves when it
does. Spending a third preload — a request that competes with the text face the
whole surface is set in — to save one swap on one word is the same trade
`base.html` already refuses for the mono.

## Serving

Served from the existing dashboard static route
(`GET /dashboard/static/fonts/<file>`), which grew a `.woff2` media type for
them. `@font-face` `src:` values in `dashboard.css` are **relative**
(`fonts/…woff2`), never built from `PUBLIC_URL` — the SSH-tunnel rule
(`docs/design/dashboard.md` §8) applies to fonts exactly as it applies to
keyframes.
