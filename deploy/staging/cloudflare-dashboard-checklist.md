<!--
BOX:      none — this is clicks in the Cloudflare dashboard, from any browser
INSTALLS: docs/history/BEFORE-SHIP.md Phase 5, the block headed "Cloudflare dashboard
          settings — do this BEFORE Phase 6, or Phase 6 lies to you"
ZONE:     vidtheque.dev (Free plan)

Detail and citations: research/release-staging-2026-08-11.md §6.5, §6.3, §7.2,
§7.4; docs/deploy-public.md §2.6, §4.
-->

# Cloudflare dashboard — the exact clicks, in order

Everything here is **zone-level configuration for `vidtheque.dev`**, and all of
it must be done **before the Phase 6 smoke tests**. That ordering is not
tidiness: Browser Integrity Check alone will fail half of Phase 6 for a reason
that has nothing to do with the application, and the hour you spend debugging
the application is the hour the launch does not have.

Sections 1–4 are **required**. Section 5 is the one deliberate *addition*
(Phase 1 decision 9). Section 6 is an experiment. Section 7 is what to check
rather than change.

Free-plan allowances used below: **10 Configuration Rules**, **1 rate-limiting
rule**, **5 WAF custom rules**. Counts are as of 2026-08-11; the UI is the
authority.

---

## 1. Turn off everything that challenges a non-browser — REQUIRED

Every item in this section exists because vidtheque's primary client is **not a
browser**. `/mcp` is an MCP transport, `/api/*` is a JSON facade, and the
launch audience is people pointing agents at a URL.

### 1.1 Browser Integrity Check → Off, scoped

**This is the single most likely way to lose an hour on launch morning.** BIC
is **ON by default on the Free plan** and challenges *"visitors without a user
agent or with a non-standard user agent"* — which is the exact description of
an MCP client, a `curl` smoke test, and most agent HTTP stacks. Unlike Bot
Fight Mode it **is** scopable.

> **Rules → Configuration Rules → Create rule**
>
> - Name: `vidtheque — no BIC on programmatic paths`
> - Expression (use *Edit expression*):
>   ```
>   (http.request.uri.path eq "/mcp") or
>   (starts_with(http.request.uri.path, "/mcp/")) or
>   (starts_with(http.request.uri.path, "/api/")) or
>   (starts_with(http.request.uri.path, "/frames/"))
>   ```
> - Setting: **Browser Integrity Check → Off**
> - Deploy.

One rule covers all three path families, so this costs 1 of the 10.

**Verification, and it is the cheapest analogue of somebody's homegrown agent
— it is also Phase 6's item 14:**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -A '' https://vidtheque.dev/api/meta
curl -sS -o /dev/null -w '%{http_code}\n' -A '' -X POST https://vidtheque.dev/mcp
```

A `403` or an HTML body where JSON was expected means BIC (or 1.2) is still
armed on that path.

### 1.2 Bot Fight Mode → Off

> **Security → Bots → Bot Fight Mode → Off**

Understand *why* this is a switch and not a rule: Cloudflare's own docs say
*"You cannot bypass or skip Bot Fight Mode using WAF custom rules or Page
Rules"* — it runs outside the Ruleset Engine, so Configuration Rules and Skip
rules **cannot reach it**. It is whole-domain, no path scoping, no exceptions,
and it force-enables JavaScript Detections. A Python MCP client has no browser
fingerprint and no clearance token, so it gets a 403 or an HTML challenge body.
The scopable version (Super Bot Fight Mode) is Pro+.

### 1.3 Security Level → Medium; Under Attack → never

> **Security → Settings → Security Level → Medium** (not High, not
> *I'm Under Attack*)

Challenge Pages return a full HTML page, which fails whenever the client
expects a non-HTML response, and the recommended remedy (Turnstile
pre-clearance) is impossible for a headless client. Mostly inert these days —
the legacy threat score is now *"always 0"*, so every old "set Security Level
to High" guide does nothing — but *Under Attack* is not inert and must never
be turned on for this zone while `/mcp` is the product.

### 1.4 0-RTT Connection Resumption → Off

> **SSL/TLS → Edge Certificates → 0-RTT Connection Resumption → Off**

Only GET/HEAD/OPTIONS ride as early data, and the MCP transport opens its
server-initiated listening stream with a **GET** — exactly the replayable
class.

---

## 2. Protect the client IP — REQUIRED, and every failure here is silent

The per-IP rate limiter reads `CF-Connecting-IP`. Anything that removes or
rewrites that header puts **the whole internet in one bucket**, with no error
anywhere: `search`, `ask` and `frames` limits all void, leaving only the
server-wide daily ask budget as a backstop.

### 2.1 "Remove visitor IP headers" Managed Transform → OFF

> **Rules → Transform Rules → Managed Transforms →
> "Remove visitor IP headers" → make sure it is OFF**

It is available on **all plans**, it is one click, and it removes
`cf-connecting-ip`, `x-forwarded-for` and `true-client-ip`.

### 2.2 Pseudo IPv4 → Off

> **Network → Pseudo IPv4 → Off**

In *Overwrite Headers* mode Cloudflare *"overwrites the existing
Cf-Connecting-IP and X-Forwarded-For headers with a pseudo IPv4 address"* — the
rate-limit key becomes a hash that collides across IPv6 visitors.

### 2.3 No request-header Transform Rule on this hostname. Ever.

> **Rules → Transform Rules → Modify Request Header** → the list must be empty
> for `vidtheque.dev`.

Write it down somewhere Tom will find it in six months: this is the one edit
that breaks rate limiting invisibly.

**Verification is Phase 6's item 7 and it is the whole client-IP story in one
test:** 35 rapid searches from one device → 200s then 429s carrying
`E_RATE_LIMIT` and `retry_after_s`; then a **second device on a different
network** gets a fresh bucket immediately. If the second device is already
limited, something in this section is wrong.

### 2.4 The DNS record is **proxied**

> **DNS → Records** → `vidtheque.dev` must be a **CNAME** to
> `<TUNNEL_ID>.cfargotunnel.com` with the **orange cloud on**.

Do not hand-create it — `cloudflared tunnel route dns vidtheque vidtheque.dev`
creates it correctly (Phase 5). Proxied is not cosmetic: proxied is what puts
`CF-Connecting-IP` on the request in the first place.

---

## 3. TLS — leave it alone, deliberately

> **SSL/TLS → Overview → encryption mode: leave as-is** (Full is the modern
> default for a new zone).

Cloudflare issues and terminates the edge certificate; the tunnel's hop to the
origin is plain http on **loopback inside the container** and needs no
certificate. Flexible/Full/Strict is a statement about how Cloudflare reaches
an origin *over the internet* — a tunnel origin is not what that setting is
about, and "hardening" it to Full (strict) does not describe anything real
here.

Optional and harmless: **Edge Certificates → Always Use HTTPS → On**. Nothing
in the product speaks http, and `PUBLIC_URL` is https.

Do **not** raise Minimum TLS Version on launch day. It is a change with a
client-compatibility tail and zero launch benefit.

---

## 4. Caching — one deliberate non-action, one accepted risk

### 4.1 Do NOT add a zone-wide "Cache Everything" rule

It would cache `/api/*` and `/mcp`. Cloudflare caches by **extension**, not
content type: `.jpg` is cached by default, HTML and JSON are not, and that
default is exactly the behaviour we want.

### 4.2 Do NOT add a Cache Rule for `/frames/*` either

The origin already sends `Cache-Control: public, max-age=86400` and the default
cache key includes the **full query string**, so `?w=…&q=…` variants cache
separately and correctly. Every rule added here is a chance to break that cache
key. The right action is **none**, and then verify:

```bash
curl -sSI 'https://vidtheque.dev/frames/<some-id>.jpg?w=192&q=70' | grep -i 'cf-cache-status\|cache-control'
# second fetch of the same URL should report cf-cache-status: HIT
```

### 4.3 Why this matters more than it looks — the §2.6 arithmetic

`docs/deploy-public.md` §2.6, remeasured 2026-08-11: at ~12,351 keyframes the
five widths the two surfaces actually request cost **≈135 KB per keyframe**, so
a full crawl materialises **≈1.6 GB of variants against a 256 MB
`VIDTHEQUE_DERIVED_CACHE_MB` cap — ≈6.4× over**. The origin's LRU is therefore
**permanently hot under any crawl**, and every eviction is a decode plus a
re-encode on the request path. Only the 192px width (≈39 MB corpus-wide) fits
comfortably; the two lightbox widths (960 + 1280) are ≈1.3 GB on their own.

So the edge cache is not a nice-to-have here — it is what keeps that re-encode
off the origin. Leaving the default `.jpg` caching intact **is** the mitigation.

### 4.4 The accepted risk, recorded rather than discovered

**An edge cache hit never reaches the origin, so it is never charged to
`VIDTHEQUE_RATE_FRAMES_PER_MIN`.** Once one crawler has warmed a PoP, a second
crawler behind the same colo walks the corpus at edge speed with the per-IP
limiter never seeing it. The corpus is public keyframes of public talks, so
this is an **accept**, not a fix — but it is an accept that belongs in
`audit-2026-08-11.md` with a sentence of reasoning, per Phase 7.

### 4.5 And the rollback consequence

`/frames/*.jpg` at `max-age=86400` means **stopping the connector does not
un-publish the keyframes** — edge copies survive the origin for up to a day. If
a rollback is about *content* rather than an outage, also do
**Caching → Configuration → Purge Everything**, and accept honestly that copies
already downloaded are gone for good. docs/history/BEFORE-SHIP.md's rollback card carries
this; it is repeated here because this is the page you will be on.

---

## 5. The one rate-limiting rule, spent on `/mcp` — Phase 1 decision 9

The free plan gets **one** rate-limiting rule. It goes on `/mcp`.

**Why**, in one paragraph, because the reasoning has moved: codex's review
found that anonymous MCP callers can exhaust the GPU queue — under `AUTH=none`
anyone opens a session, each `search` with `content_type=all` submits **two**
jobs to an **unbounded** worker FIFO, and `VIDTHEQUE_MAX_CONCURRENT_SEARCHES`
does not save it because the semaphore guards the SQLite work *after* the
embedding. The victims are every other visitor's search and the shared 3090.

> **Security → WAF → Rate limiting rules → Create rule**
>
> - Name: `vidtheque — /mcp burst brake`
> - When incoming requests match (*Edit expression*):
>   ```
>   (http.host eq "vidtheque.dev" and
>     (http.request.uri.path eq "/mcp" or
>      starts_with(http.request.uri.path, "/mcp/")))
>   ```
> - Characteristics: **IP** (the only option on Free)
> - Period: **10 seconds** (the only option on Free)
> - Requests threshold: **10**  → **≈60 req/min/IP**
> - Action: **Block**
> - Duration / mitigation timeout: **10 seconds** (the only option on Free)
> - Deploy.

**Four things to know about what this rule is and is not.**

1. **It is not the only limiter on `/mcp` any more.** The runbook's "`/mcp` is
   deliberately never rate limited" is **stale**: the G1 security merge added
   `VIDTHEQUE_RATE_MCP_PER_MIN`, shipped at 120. This edge rule at ~60/min is
   the tighter of the two and therefore the one that fires — which is the right
   way round, because it fires *before* the request reaches the box.
2. **A long-lived SSE stream counts as ONE request, at open.** Edge rate
   limiting protects against connection floods, not against long-hold
   concurrency. It is a brake on loops, not a quota.
3. **Counters are implicitly per-datacenter**, and the free rule cannot key on
   the path — the expression *matches* on it, the counter does not.
4. **60/min is generous for a real consumer and a wall for a loop.** Answering
   one question is legitimately a burst of tool calls; a `while true` is not.

**Verification:** loop >10 `POST /mcp` inside 10 s from one address and expect
Cloudflare's block page; then confirm a normal MCP session (`tools/list`, one
`search`) is untouched. Do this **before** the tunnel-side smoke tests of
Phase 6, so a 429/403 during those is never ambiguous.

---

## 6. Response Body Buffering — an experiment, not a step

Only if the ask stream stalls late (Phase 6's SSE timestamp-spread test).

> **Rules → Configuration Rules → Create rule**
>
> - Expression: `(http.request.uri.path eq "/api/ask") or (http.request.uri.path eq "/mcp")`
> - Setting: **Response Body Buffering → None**

`Response Body Buffering` was added as a Configuration Rules setting on
2026-01-27 and defaults to `Standard`, which lets Cloudflare *"inspect a prefix
of the request body when necessary"*. That is the best available explanation
for persistent reports of SSE being held until ~100 KB accumulates despite
correct headers. `None` streams *"directly to the client without inspection"*,
with an explicit Cloudflare warning that it *"may impact the effectiveness of
the WAF"*.

**Plan availability on Free is undocumented.** If the setting is not offered,
that is the answer — do not go looking for a workaround on launch morning.
`/mcp` already emits a 15-second heartbeat via the pinned `sse-starlette`, so
only `/api/ask` is really exposed to this.

---

## 7. Leave these alone — checked, not changed

| where | setting | do |
|---|---|---|
| Speed → Optimization → Content Optimization | Rocket Loader | **Off** |
| Scrape Shield | Hotlink Protection | **Off** — it would break `/frames/*` for anyone embedding a citation |
| Scrape Shield | Email Obfuscation | **leave On** — HTML-only, never touches JSON |
| Network | HTTP/3 (with QUIC) | **Off** — purely to keep one variable out of streaming debugging |
| Security → WAF | Cloudflare Free Managed Ruleset | **leave On** — it cannot be disabled on Free anyway |
| Security → WAF → Custom rules | a `Skip → all managed rules` rule for `/mcp` | **hold in reserve**; deploy only on an observed false positive (Free gets 5) |
| Zero Trust → Networks → Tunnels → `vidtheque` | Public Hostname tab | **must be EMPTY** — ingress is local (`/etc/cloudflared/config.yml`). Rules in both places is a documented source of "routes to the wrong thing / 502" |

---

## 8. Terms of service — the citation to stop repeating

`docs/deploy-public.md` §9 cites *"Cloudflare's general Terms §2.8 on non-HTML
content"*. **Section 2.8 no longer exists** — deleted May 2023; the live
Self-Serve Subscription Agreement runs 2.1 → 2.7. The clause actually in force
is in the **Service-Specific Terms**, under *Content Delivery Network (Free,
Pro, or Business)*, and reserves the right to limit CDN access if it is used
*"to serve video or a disproportionate percentage of pictures, audio files, or
other large files"*, with *"reasonable efforts to provide you with notice"*
first. No numeric threshold is published.

Serving keyframe JPEGs is not the target of that clause; **proxying or
re-serving source video bytes would be.** vidtheque deletes the source media
after indexing and cites `youtu.be/ID?t=…` rather than re-hosting — the
ToS-safe posture and the product design are the same posture. Keep it that way.

---

## Done-check before Phase 6

- [ ] §1.1 Configuration Rule deployed; the two `-A ''` curls answer normally
- [ ] §1.2 Bot Fight Mode reads **Off**
- [ ] §1.3 Security Level reads **Medium**
- [ ] §1.4 0-RTT reads **Off**
- [ ] §2.1 "Remove visitor IP headers" reads **Off**
- [ ] §2.2 Pseudo IPv4 reads **Off**
- [ ] §2.3 Modify Request Header rule list is **empty**
- [ ] §2.4 DNS record is a **proxied CNAME** to `<TUNNEL_ID>.cfargotunnel.com`
- [ ] §4 no Cache Everything rule, no `/frames/*` cache rule; a second fetch
      reports `cf-cache-status: HIT`
- [ ] §5 rate-limiting rule deployed and tested from one address
- [ ] §7 Zero Trust Public Hostname tab is **empty**
