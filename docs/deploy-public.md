# Going public — the runbook

vidtheque is exposed to the internet by a Cloudflare tunnel from the box that
runs it: a welcome page, a read-only dashboard, and a demo over the AI Engineer
corpus, with the MCP server itself reachable so anyone can point their own
agent at it. This document turns that day into a checklist.

**Who runs this.** The operator, with an agent alongside. The tunnel, the
domain, the DNS records and the security audit were reserved for "together"
from the start, and that reservation is the point of §1: everything below §1 is
mechanical, and §1 is not.

**What this is not.** It is not a hosting guide for anyone else's box, and it
is not a substitute for the contracts. `docs/design/demo-site.md` is what public
mode *is*; `docs/design/dashboard.md` §2.3–§2.4 is what the read-only projection
shows; `deploy/.env.example` is the document of record for every variable named
here. When this file disagrees with one of those, they win and this file is
wrong.

**Order matters.** §1 gates §2. §2 gates §5. Do not create a DNS record for a
box you have not audited: a hostname that resolves is a hostname that gets
scanned, and the tunnel goes live the moment `cloudflared` connects.

---

## 1. The gate — the security audit is required before first public traffic

**This is a hard gate.** The deferred security audit happens **before**
the first public request, not after the URL is shared and not "next week". It
is run **with the operator**, as a decision-making session, not unilaterally by an agent:
several of the findings below are *policy* questions — how much of the corpus is
public, whether ask mode ships on day one, what a scraper is allowed to take —
and an agent cannot answer those.

Write the outcome to `research/security-audit-<date>.md` (research docs are
append-only; add a clearly-headed section). Every item below is either **pass**,
**accepted risk with a sentence of reasoning**, or **blocked**. A blocked item
means the URL is not shared.

### 1.1 What the audit must cover

**Auth surfaces.** `VIDTHEQUE_AUTH` is one branch chosen at boot
(`mcp/src/vidtheque_mcp/config.py:190`). For the public box the answer is
`none` — see §2.2 — which means *there is no 401 anywhere in the app*. So the
audit's question is not "is auth configured", it is **"is everything reachable
without a credential something we are content to publish?"** Enumerate the
surface and answer per route: `/` and `/static/*`, `/api/search|videos|meta`,
`/api/ask`, `/frames/*.jpg`, `/dashboard` and `/dashboard/api/*`, `/mcp`,
`/healthz`. Under `none`, `auth.routes` is empty and `/auth/*` and
`/.well-known/*` do not exist — confirm that, do not assume it.

**That list was incomplete, corrected 2026-08-11.** It omitted
`/dashboard/videos*`, `/dashboard/jobs*`, `/dashboard/api/jobs*`,
`/dashboard/static/*` and the `/dashboard/` redirect — all mounted anonymously
— and, on `/mcp`, the **three public resource URIs** and the session lifecycle.
Two of the audit's findings lived in exactly the gap: `/dashboard/videos/{id}`
published every stage's model id and raw error while the jobs view beside it
redacted both, and sessions accumulated without limit because nobody had
enumerated the lifecycle as part of the surface. Enumerate from the router, not
from this list.

**Readonly masking completeness.** Two independent mechanisms have to both be
on, and they are on for different reasons:

- `VIDTHEQUE_PUBLIC_READONLY=1` derives the masked tool set from the contract's
  `readOnlyHint` annotations (`public/readonly.py:16`), so the write tools are
  **never registered** rather than registered-and-refusing. Verify against a
  live `tools/list` through the tunnel, not against the source.
- `VIDTHEQUE_AUTH=none` means the dashboard's write side is never registered at
  all (`dashboard/access.py`, `write_side_enabled`), and so does
  `VIDTHEQUE_PUBLIC_READONLY=1` — the same predicate, either half sufficient.
  `WRITE_ROUTES` is five paths since phase 3 (login, logout, index, reindex,
  tags); what to confirm is that **none of them is in the router**, and that
  each 404s rather than 403ing:
  `for p in login logout index; do curl -s -o /dev/null -w "%{http_code} /$p\n" -X POST http://127.0.0.1:8100/dashboard/$p; done`
  — expect `404` three times.

Then check the redactions actually applied to data: `_redacted()` is
`assembled.public.enabled` (`dashboard/views.py:649-660`), and it drops
`error_message` from job cards (`:718`), `source_url` and `error_message` from
job items (`:765-767`), and `message` from job events (`:801`). Everything else
on that view — states, codes, counts, clocks — is deliberately kept
(`dashboard.md` §10.4). **Resolved in dashboard phase 4 (2026-08-09): fixed,
not accepted.** This runbook found the *corpus overview* unredacted — declared
embedding model ids, the vector-leg state including `vectors.reason`, database
and keyframe byte totals, and `auth={{ auth_mode }}` in the page chrome — against
§2.4's promise of "no settings, no paths". There were no filesystem paths in it,
but the model ids *are* settings. The projection now drops all four
(`dashboard/views.py:_redacted`, and the amendment under §2.4's table), keeping
the one thing a visitor can act on: that vector search is off, without the
mismatch that caused it. So the audit's job here is now **verification, not a
decision** — §2.5 has the grep.

**"Fixed" was true of the dashboard and not of the deployment — 2026-08-11.**
Three other anonymous read paths published the same class of data, and §2.5's
greps could not see any of them because they only ever read dashboard HTML:
MCP `job-status` handed back the submitted URL and the yt-dlp error text the
jobs view had just withheld (a visitor reads a job id off `/dashboard/jobs`,
which renders them as links, and quotes it at `/mcp`); `corpus-summary` printed
raw `video_stages.error`; `/dashboard/videos/{id}` published `model_key` and
stage errors. All three are fixed and the pairing now has a test, because
nothing tested it and that is why it survived.

The lesson worth keeping: redaction here is written per *view*, so each new view
starts unredacted and remembers on its own. §2.5's greps check one surface. The
question to ask of any new read path is what it prints, not whether the flag is
set.

**Clamp policy on `/dashboard/api/*` — RESOLVED 2026-08-09 (dashboard phase 5):
fixed, not accepted.** The three answers below are kept because the reasoning is
the record; what changed is that answer 3 was taken, in its honest form. **The
clamp policy now follows the credential, not the flag and not the prefix**
(`public/api.py:policy_for`, `auth/credential.py:is_owner`, dashboard.md §2.4):
a bearer, a `vidtheque_session` cookie or a socket peer inside
`VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS` gets `OWNER_CLAMPS` on either prefix in
every mode; everything else, **including every request in `AUTH=none`**, gets
`PUBLIC_CLAMPS` on either prefix. What is left for this runbook is verification:

```bash
# Both must now say "public" on the public deployment.
curl -s http://127.0.0.1:8100/api/meta           | jq .clamps
curl -s http://127.0.0.1:8100/dashboard/api/meta | jq .clamps
# The hatch, the thing that actually mattered: the reply must be truncated,
# with the `…` marker, not a full transcript.
curl -s 'http://127.0.0.1:8100/dashboard/api/search?q=transformer&max_text_chars=0' \
  | jq '[.results[].text | length] | max'          # expect <= ~480, never thousands
```

If the instance has a token configured, the contrast is the check that the fix
is credential-keyed rather than mode-keyed — the same URL with
`-H "Authorization: Bearer $VIDTHEQUE_TOKEN"` must come back `"owner"`.

**And the audit must check the allowlist itself.** `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS`
is now one of the three things that says "owner", and it is decided on the
**socket peer** — the address the kernel reports. Behind a tunnel that address
is *cloudflared's*, not the visitor's: the connector speaks from loopback, or
from a docker bridge if it runs in compose. So a CIDR covering the network the
connector connects from makes **every anonymous visitor through the tunnel an
owner** — owner clamps on both prefixes, the full-transcript hatch, and with
`AUTH=token` the credential-free write side (indexing, re-indexing, tagging).
`X-Forwarded-For`/`CF-Connecting-IP` cannot cause this — authorization never
reads them (§4) — but they also cannot save you from it, because the socket is
genuinely the proxy's.

On the public deployment the correct value is **empty**, which is the default.
Raised by the 2026-08-09 review; a fuller answer (rejecting proxy-origin CIDRs
outright, or requiring a credential when the request came through the tunnel)
is deferred to this audit. What ships tonight is a boot-time warning: the server
logs `WARNING … treated as the owner` when the allowlist overlaps loopback,
RFC1918 or IPv6 unique-local **and** `VIDTHEQUE_TRUSTED_IP_HEADER` is set, since
a trusted header is the tell that a proxy is in front. Verify both halves:

```bash
# 1. The config. On the public box this should print nothing at all.
grep -E '^VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS=.+' deploy/.env

# 2. The boot log. Any hit here is a finding, not a note.
docker compose logs mcp 2>&1 | grep -i 'treated as the owner'

# 3. The behaviour, from outside, with no credential: must be "public".
curl -s https://<hostname>/dashboard/api/meta | jq .clamps.policy
```

A LAN deployment with no proxy in front is the legitimate case the warning
cannot distinguish, which is why it is a warning and not a refusal. If that is
this box, record the reasoning in the audit write-up rather than ignoring the
line.

The original finding, and the three answers it landed on, follow.

Found while shipping phase 4, not fixed there, because it is the "what is a
scraper allowed to take" question this section says an agent cannot answer.
`/dashboard/api/*` is registered in **every** mode, and it was registered with
`OWNER_CLAMPS` (`dashboard/__init__.py`, `public/api.py`); in the intended
production combination (`READONLY=1` + `AUTH=none`) there is no credential in
front of it — so an anonymous visitor got the *owner's* bounds on the JSON
facade rather than the demo's:

```bash
curl -s http://127.0.0.1:8100/api/meta           | jq .clamps   # policy "public", 20/50
curl -s http://127.0.0.1:8100/dashboard/api/meta | jq .clamps   # policy "owner",  50/100
```

Two of the three differences are only "a bigger page of the same public
listing". The third is not: `OWNER_CLAMPS.search_text_chars is None` passes the
caller's `max_text_chars` through, **including the documented `0` opt-out**, so
`/dashboard/api/search?q=…&max_text_chars=0` returns untruncated transcript text
— the full-transcript escape hatch `demo-site.md` §2 reserves for "an owner's
agent, not anonymous traffic". At `VIDTHEQUE_RATE_DASHBOARD_PER_MIN=120` the
whole corpus's transcripts are a short crawl.

The audit lands on one of, in increasing order of cost:

1. **Accept it.** The corpus is talks that are public on YouTube and the
   transcripts are derived from them; if the answer to §1's first question is
   "all of it is public", this changes nothing.
2. **`VIDTHEQUE_DASHBOARD=0`**, or the ingress rule in
   `deploy/cloudflared.example.yml` that 404s `^/dashboard` at the edge — which
   also costs the browsable corpus, so it is the answer only if the dashboard
   was not wanted publicly anyway.
3. **Make the policy follow the mode**, one line:
   `api_routes(PUBLIC_CLAMPS if readonly else OWNER_CLAMPS, ROOT, ask=False)`.
   It is deliberately not done here because it rewrites a phase-1 assertion
   (`test_one_set_of_handlers_serves_both_prefixes` pins owner clamps on that
   prefix *in demo mode*), and because a read-only deployment that also has a
   credential configured would then clamp its own owner. If it is taken, the
   honest version keys off the credential rather than the flag, which is a
   design change and belongs in phase 5 beside search-and-ask moving in.

   **Taken — the honest version, and not the one-liner.** Both of the costs
   this entry priced were real and both were paid: two phase-1 assertions were
   rewritten (the one named here, and `test_the_dashboard_json_is_clamped_
   server_side`, which read the owner ceiling on an anonymous request), and
   the mode-keyed form was rejected on its own argument — the reference
   deployment is read-only *and* has a token. Trusted CIDRs were ruled authenticated-
   equivalent: that setting already grants the whole write side with no
   credential presented, so a network trusted to change the corpus but not to
   read a transcript of it would be a boundary with no shape, and it is what
   gives an `AUTH=none` LAN instance its owner back.

**Rate-limit bypass.** The limiter is in-memory, single-process, and charges
before the handler runs (`public/ratelimit.py`). Probe, through the tunnel:
per-IP `search` (30/min), `ask` (5/min), `frames` (120/min), `dashboard`
(120/min), and the server-wide `ask_global` (50/day). Check the bypasses
specifically: a forged client-IP header (§4), a path that maps to no bucket
(`bucket_for` returns `None` for anything outside `/api/`, `/frames/`,
`/dashboard` and `/mcp`), and whether the refund paths can be driven to mint
credit (they refill-then-cap, `Bucket.give_back`, so they should not).

**Corrected 2026-08-11: `/mcp` is no longer unlimited.** It was, deliberately,
on the argument that an agent's traffic is not a browser's — which is true and
still left the surface that reaches the GPU unmetered: MCP `search` embeds its
query on the worker, so concurrent anonymous calls were forward passes with
nothing bounding them. It now has its own loose bucket
(`VIDTHEQUE_RATE_MCP_PER_MIN`, 120), and query embedding happens inside search
admission rather than before it (2026-08-10 audit, F-1). Probe it like the
others.

**SSRF in the CIMD fetcher.** `auth/cimd.py:guard_ssrf` requires https, refuses
credentials in the URL, and refuses any host that resolves to a private,
loopback, link-local or reserved address — *unless* `allow_insecure` is set,
which returns early and skips the private-address check entirely
(`cimd.py:74`). `allow_insecure` is `not settings.public_url.startswith("https://")`
(`auth/modes.py:123`, `auth/provider.py:56`). **So an `http://` PUBLIC_URL turns
the SSRF guard off.** In `AUTH=none` the fetcher is never constructed and the
whole question is moot; the audit's job is to confirm that, and to confirm
PUBLIC_URL is https anyway (§3), so the guard is armed if the mode ever
changes. If the box ever runs `oauth`, re-audit: `_resolves_to_private` does a
DNS lookup and the fetch happens afterwards, which is a TOCTOU rebinding window,
and `follow_redirects=False` is what currently keeps it narrow.

**The frames signer.** Under `AUTH=none` there is no signer:
`thumb_url` emits unsigned `/frames/<id>.jpg?w=&q=` and the route answers
everyone (`http/frames.py:190`, `public/api.py:149-156`). That is the shipped
decision (`demo-site.md` §5) and the guard is the rate limiter, not a
signature. What the audit checks is that the *unsigned* path is still bounded:
`FRAME_ID` only matches `<video>-<NNNNN>` (`http/frames.py:50`), `_resolve`
refuses anything escaping `VIDTHEQUE_DATA_DIR` (`:175`), `w` clamps to 64..1280
and `q` to 20..95 server-side (`http/derived.py`), and the route never upscales.
If the box ever runs `token`/`oauth`, the signature covers the *clamped*
`(frame_id, width, quality, exp)` pair and not the origin — re-check that
widening the clamp floor cannot change what a live signed URL means.

**Ask budget exhaustion. Corrected 2026-08-11 — the two facts below were both
wrong.** `ask_global` is 50/day, keyed `"@global"`, and **persisted**: migration
0005 writes it to SQLite, so a restart resumes the day rather than handing it
back. §9 has had this right for a while and this paragraph did not.

`VIDTHEQUE_ASK_MAX_ROUNDS=50` (raised from 4 on 2026-08-15) means one ask is up
to **fifty-one** upstream completions, not fifty: fifty tool-enabled rounds and
then a forced final answer with tools off, so the visitor always gets prose
rather than a spinner. In practice the 180s wall clock ends a long ask well
before the round cap does — and with the per-round tool-call cap of six gone,
a single round can now be an arbitrary batch of searches. Re-check the daily
ask budget against real traffic before treating either number as safe.

And the thing neither number covered: until 2026-08-11 a visitor who
disconnected *during* a completion got the day's token back while OpenRouter had
already generated and billed the answer, so the cap could be held at zero
indefinitely. The flag is now raised before the request is dispatched
(2026-08-10 audit, B-3), and every completion carries an explicit `max_tokens`,
which it did not.
**The shipped model id is not free.** `deploy/.env.example` records the finding:
there is no `deepseek/*:free` on OpenRouter any more, that whole family is paid,
and `OPENROUTER_MODEL=deepseek/deepseek-v4-flash-0731` is what the demo runs.
So the daily bucket is a **money** guard, and the audit had to land on one of:
(a) a hard spend cap set on the OpenRouter key itself, using a dedicated key
scoped to this deployment; (b) a genuinely free model id from the list in
`.env.example`; or (c) `OPENROUTER_API_KEY=` empty, which turns the mode off
cleanly — `/api/meta` reports `ask_enabled: false` and the page hides the toggle
rather than offering a button that 503s.

**Decided 2026-08-11: (a), and the in-app budget as well.** Ask ships on day
one on `deepseek/deepseek-v4-flash-0731`, spent by a **dedicated key with a hard
provider-side cap**. The reasoning is that the provider cap is the only control
that speaks the same unit as the asset — the local counter counts asks, not
dollars, and a one-line model change alters the cost per ask without touching
the number 50. It is also the only one that survives a bug in the counter, which
B-3 was.

**Setting that cap is a manual step on OpenRouter and nothing in this repo can
check it.** §7.5 has the checklist line.

**Header spoofing through the tunnel.** See §4 in full. It is a real config
item, it has a code answer already, and getting it wrong in either direction is
a live bug.

**Two things the audit does not need to find, because they were checked while
this runbook was written.** `uvicorn` is started without `proxy_headers` /
`forwarded_allow_ips` (`__main__.py:22-27`) and that is harmless here: nothing
in the app derives a scheme or a host from the incoming request — every absolute
URL is built from `PUBLIC_URL` (§3) — so there is no `X-Forwarded-Proto` to
trust or mistrust. And `X-Forwarded-For` is never read anywhere; the limiter uses
its own named header (§4), which is also what Cloudflare recommends
(<https://developers.cloudflare.com/fundamentals/reference/http-headers/>).

---

## 2. Pre-flight on the box

Run every one of these **before** the tunnel exists, against
`http://127.0.0.1:<port>` locally. A thing that is wrong here is wrong through
the tunnel too, and cheaper to find.

### 2.1 The mode flags

```
VIDTHEQUE_PUBLIC_READONLY=1     # demo mode: write tools unregistered, / is the page,
                                # /api/* served, rate limiter installed, jobs view redacted
VIDTHEQUE_AUTH=none             # see §2.2
VIDTHEQUE_DASHBOARD=1           # the read-only projection (0 if the audit says otherwise)
```

Verify from outside the process, not by reading the file:

```bash
curl -s http://127.0.0.1:8100/api/meta | jq '{auth, ask_enabled, mcp_url, clamps, limits}'
```

`auth` must be `"none"`, `clamps.policy` must be `"public"` (not `"owner"`), and
`mcp_url` must already be the public hostname (§3). Then confirm the write tools
are gone from a real `tools/list` on `/mcp` — `index-video` and `tag-video` must
be **absent**, not present-and-refusing.

### 2.2 Auth mode for a public box, and why

**`VIDTHEQUE_AUTH=none`, combined with `VIDTHEQUE_PUBLIC_READONLY=1`.** This is
what `deploy/.env.example` calls "the intended production combination", and the
reasoning is worth stating because "no auth on a public box" reads wrong:

- The demo only works anonymously. `token` and `oauth` both put a credential in
  front of `/frames/*` and the dashboard, and a visitor who cannot load a
  thumbnail has no demo. The whole point of the public instance is that a
  stranger can search it and add `/mcp` to their own agent.
- Nothing that can write *to the corpus* exists in this combination, by two
  independent mechanisms. The readonly flag unregisters the write **tools**;
  `AUTH=none` unregisters the dashboard's write **routes**. Either one alone
  would be enough; both is the belt and the braces, and each is a one-line
  change away from the other being load-bearing — which is exactly why the audit
  checks both rather than reasoning about one.
  - **Qualified 2026-08-11.** This used to say "nothing that can *write*", flat.
    An anonymous frame GET writes and evicts derived-cache files, and `/api/ask`
    writes the budget row. Neither touches the corpus and both are bounded, but
    "no writes happen" was not true and a reader planning a read-only mount
    would have been misled by it.
  - **And 2026-08-11: the third mechanism is now the boot.** `AUTH=none` with
    writes enabled on a non-loopback hostname refuses to start, because two
    mechanisms that are both one env var away from off is not the same as a
    deployment that cannot come up wrong. `VIDTHEQUE_ALLOW_PUBLIC_WRITES=1` is
    the deliberate exception, for the §9 indexing workflow.
- The thing `AUTH=none` costs you is that everything readable is public. That is
  a *content* decision, not a security posture, and it is §1's first question.

**Do not reach for `token` as a compromise.** A shared bearer token on a public
demo is a credential that leaks the first time someone shares a curl command,
and it buys nothing the readonly flag does not already buy. If the decision is
"the dashboard should not be public", the answer is either
`VIDTHEQUE_DASHBOARD=0` or the ingress rule in `deploy/cloudflared.example.yml`
that 404s `^/dashboard` at the edge — not a token.

**If a private surface is wanted later**, the right shape is a second hostname
routed by the same tunnel into the same origin, gated by Cloudflare Access
(<https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/>),
with `originRequest.access` set so `cloudflared` validates the Access JWT before
proxying. That is a design change, not a runbook step; it is noted here so it is
not invented under time pressure on go-live day.

### 2.3 Rate limits and ask budgets, verified

Read them back from the running server rather than from `.env`:

```bash
curl -s http://127.0.0.1:8100/api/meta | jq .limits
# {"search_per_min": 30, "ask_per_min": 5, "ask_per_day": 50}
```

`frames_per_min` and `dashboard_per_min` are not in that payload — confirm those
by probing (`for i in $(seq 1 130); do ...; done` and expect a 429 with
`Retry-After`). Then confirm the ask budget decision from §1 is actually in the
environment: `OPENROUTER_API_KEY` either empty (mode off) or a dedicated,
spend-capped key.

The suites that assert this machinery, worth running green before shipping:

```bash
make test
uv run --no-sync pytest mcp/tests/test_public.py mcp/tests/test_dashboard.py -q
```

`test_public.py` covers the masking (`test_public_mode_never_registers_the_write_tools`),
the clamps, the bucket maths, the trusted header (`test_client_key_prefers_the_trusted_header`)
and the `/mcp`-is-never-limited rule. `test_dashboard.py:696`
(`test_the_demo_projection_keeps_the_clocks_and_drops_the_rest`) is the
redaction test.

### 2.4 Audit `deploy/.env` against `deploy/.env.example`

`.env.example` is the document of record; `.env` is what actually runs. Diff the
*key sets*, not the values:

```bash
cd deploy
diff <(grep -oE '^[A-Z_]+=' .env.example | sort -u) \
     <(grep -oE '^[A-Z_]+=' .env         | sort -u)
```

- A key in `.env` that is **not** in `.env.example` is a bug by CLAUDE.md's rule
  — either the entry is missing or the variable is dead. Fix it in the same
  commit as whatever introduced it.
- A key in `.env.example` that is **not** in `.env` is fine (defaults apply) but
  worth eyeballing for the ones that matter here: the two in §3, the two mode
  flags, `VIDTHEQUE_TRUSTED_IP_HEADER`, and the five rate-limit numbers.
- Then check secrets: `.env` must not be committed (it is in `.gitignore` —
  confirm), and `git log --oneline -- deploy/.env` must be empty.

**A dev-stack deployment does not read `deploy/.env` at all.** A box running
`scripts/dev_stack.sh` sources `$DATA_DIR/stack.env` with `set -a` — so
*that* file is the one that reaches the process, and it is the one to audit if
you are shipping the dev stack rather than compose. Do both diffs if both exist;
two configuration files that disagree is how a mode flag gets lost.

### 2.5 Demo-mode redactions, confirmed on the jobs view

Not "the flag is set" — look at the bytes:

```bash
curl -s http://127.0.0.1:8100/dashboard/jobs | grep -ciE 'youtube\.com|youtu\.be/|cookiefile|player_client|/home/'
# expect 0 for the operator-config strings; deep links to youtu.be are fine elsewhere
curl -s http://127.0.0.1:8100/dashboard/jobs/<a-failed-job-id> | grep -c 'Sign in to confirm'
# expect 0 — yt-dlp's error text is exactly what redaction drops
```

Pick a job you know failed. The view must still show its state, its error **code**, its
counts and all of its clocks; it must not show the submitted URL, the error
message, or any job-event message. If a failed job renders identically with the
flag on and off, the flag is not reaching the process — go to §6.1.

Then the *overview*, which phase 4 redacted after this runbook found it open:

```bash
curl -s http://127.0.0.1:8100/dashboard | grep -ciE 'Qwen/|Declared models|keyframe JPEGs|auth='
# expect 0 — model ids, byte totals and the auth line are the operator's console
curl -s http://127.0.0.1:8100/dashboard | grep -c 'read-only demo'
# expect 1 — the rail says what the reader may do, and nothing about the box
```

The corpus must still be all there on the same page: the five ledger counts, the
channel and tag rollups, the arrivals, the queue line and the gaps. A demo
overview that renders as an empty shell means the redaction grew past its four
fields, which the suite asserts both ways
(`test_the_overview_projection_keeps_the_corpus_and_drops_the_box`).

### 2.6 Frames byte-cap sanity

Under `AUTH=none` the keyframe directory is served unsigned to anyone, so the
caps are the only thing between a scraper and the whole corpus:

| knob | default | what it bounds |
|---|---|---|
| `VIDTHEQUE_RATE_FRAMES_PER_MIN` | 120 | requests per IP per minute to `/frames/*` |
| `VIDTHEQUE_DERIVED_CACHE_MB` | 256 | bytes on disk for resized variants, LRU |
| `VIDTHEQUE_FRAME_CACHE_MAX_AGE` | 86400 | `Cache-Control: public, max-age=` on a served frame |
| `VIDTHEQUE_INLINE_FRAME_MAX` / `_BYTES` | 4 / 6 MiB | `get-frames return="image"` — agents, not the page |

Two things to check, both arithmetic. **Both were modelled at 3,460 keyframes
and the corpus outgrew that by 3.6×**; the numbers below were remeasured on the
live database and the live `derived/` cache on **2026-08-11 ~01:45 Paris**, at
**12,351 keyframes over 199 `ready` videos**. That is a moving target while the
corpus grows, so take the *formula*, not the constant, and re-run
`select count(*) from keyframes` whenever you re-check.

1. **Walk time.** `keyframes ÷ 120 per minute` for a single IP. At 12,351
   that is **103 minutes**, not "under half an hour" — and 103 minutes is a
   `for` loop with a `sleep` in it, unattended. That is still the accepted
   design (`demo-site.md` §5: the limiter makes a crawl "slow enough to be
   pointless", not impossible), but the *reason it is accepted* has to survive
   the new number, so confirm it rather than inherit it. Two things make the
   real figure lower than 103 minutes, both worth knowing before agreeing:
   `/frames/*.jpg` is served `Cache-Control: public, max-age=86400` and `.jpg`
   is default-cached by Cloudflare, so **an edge hit never reaches the origin
   and is never charged to the limiter** (`research/release-staging-2026-08-11.md` §6.5) — a second crawler behind the
   same colo walks a warm cache at whatever rate it likes; and the bucket is
   per client IP, so *n* addresses divide the wall clock by *n*.
2. **Cache thrash.** The demo asks for three widths — 192 (list thumb), 320
   (frame hit), 960 (lightbox) — and the read-only projection, public since
   phase 4, asks for three more: 192 (shared), 512 (detail) and 1280
   (lightbox). Five distinct widths across the two surfaces. Measured average
   variant sizes in the live `derived/` cache:

   | width | measured average |
   |---|---:|
   | 192 | 3.2 KB |
   | 320 | 7.4 KB |
   | 512 | 15.4 KB |
   | 960 | 44.2 KB |
   | 1280 | 65.2 KB |
   | **all five, per keyframe** | **≈ 135 KB** |

   So a full crawl of both surfaces materialises 5 × 12,351 ≈ **62,000
   variants, ≈ 1.6 GB** — against a **256 MB** cap, i.e. **≈6.4× over**, not the
   "several hundred MB" this section used to say. The cache **will** evict
   under sustained public load, and every eviction is a re-encode on the
   request path. Only the cheapest width fits at all: 192 across the whole
   corpus is ≈ 39 MB, comfortably inside the cap — while the two lightbox
   widths alone (960 + 1280 ≈ 109 KB per keyframe) are ≈ 1.3 GB on their own.
   The list thumbnails will therefore survive in cache and the enlargements
   will not, which is the right way round and worth knowing is *why*.

   **And five is not the ceiling.** `w` and `q` are caller-controlled and only
   *clamped*, not enumerated: `MIN_WIDTH=64 … MAX_WIDTH=1280` and
   `MIN_QUALITY=20 … MAX_QUALITY=95` (`http/derived.py`), so the number of
   distinct materialisable variants per keyframe is in the tens of thousands,
   and the real bound on cache thrash is `VIDTHEQUE_RATE_FRAMES_PER_MIN`, not
   the number of widths the product happens to use. That is by design — the
   cache is byte-capped and disposable — but it means the honest statement is
   "the LRU is permanently hot under any crawl", not "the LRU holds the working
   set".

   Measure before deciding (`du -sh $DATA_DIR/derived`) and raise
   `VIDTHEQUE_DERIVED_CACHE_MB` if there is disk for it — 512 MB holds the two
   surfaces' five widths for about a third of the corpus, 2 GB holds all of it.
   This is a CPU/latency decision, not a correctness one: `derived/` is
   disposable by design and rebuilds on demand.

   **This arithmetic was about the wrong number, corrected 2026-08-11.** Five
   widths is what *the product* asks for. What a hostile caller could ask for
   was every integer in the clamp: `variant_key` carries the width and the
   quality, so 1,217 × 76 = 92,492 cache keys per frame, ~320 million across the
   corpus. Not "the cache will evict under load" but "the cache can be held
   permanently cold, on purpose, at 120 requests a minute, for as long as
   somebody feels like it" — and every miss is a decode plus a re-encode on the
   request path. `w` and `q` now snap to the five widths and two qualities that
   are real (2026-08-10 audit, F-5), which makes the paragraph above true as
   written rather than optimistic.

---

## 3. The hostname mapping — exactly what must be set, and what breaks

Two variables carry the public hostname, they are not interchangeable, and the
failure modes are different enough to be worth memorising.

| variable | value | shape |
|---|---|---|
| `PUBLIC_URL` (or `VIDTHEQUE_PUBLIC_URL`, prefixed wins) | `https://vidtheque.example.com` | full origin, **scheme included**, no path, no trailing slash needed |
| `VIDTHEQUE_PUBLIC_HOSTNAME` | `vidtheque.example.com` | bare host, comma-separated for several, **no scheme, no port** |

### 3.1 `PUBLIC_URL` — everything absolute is built from it

`Settings.public_url` (`config.py:189`) is the single source for every absolute
URL the server emits. It is never derived from the request, which is why there
is no `X-Forwarded-Host` to trust — and why getting it wrong is silent.

What reads it, and what breaks if it still says `http://127.0.0.1:8100`:

- **`config.resource_url` = `PUBLIC_URL + "/mcp"`** (`config.py:164`) is what
  `/api/meta` returns as `mcp_url` (`public/api.py:321`), which is what the demo
  page's copy button hands a visitor. Wrong → **every visitor copies a URL that
  points at their own laptop.** This is the most embarrassing failure on the
  list and the one nothing else catches.
- **`public/api.py:thumb_url(..., absolute=True)`** — the default — builds
  `thumb` and `thumb_large` on every `/api/search` hit (`:181`, `:185`), the
  cover image on every `/api/videos` row (`:283`), and the citation thumbnails
  in an ask answer (`public/ask.py:697-702`). Wrong → **every image on the demo
  page is broken**, because the browser dutifully fetches
  `http://127.0.0.1:8100/frames/...` and gets nothing. Note the asymmetry that
  makes this easy to miss: the **dashboard** passes `absolute=False`
  (`dashboard/views.py:73`) and therefore renders fine on any hostname, so a
  dashboard that looks perfect proves nothing about the demo page. That split
  exists because of the SSH-tunnel incident on 2026-08-09 — a preview on a
  tunnelled port rendered every thumbnail against a `PUBLIC_URL` that resolved
  to nothing — and absolute stays the default because it is the MCP contract: an
  agent gets a URL with no page around it to resolve against.
- **`tools/frames.py:193,196`** — the URLs `get-frames` hands to an agent.
  Wrong → an agent that finds the right frame cannot fetch it.
- **`Settings.issuer_url`** — OAuth metadata and the PRM `resource`
  (`auth/metadata.py:34,56`), the `WWW-Authenticate: resource_metadata=` on a
  401 from `/frames` (`http/frames.py:92`), the login redirect and `iss`
  (`auth/provider.py:123,148`). `oauth` mode only, but note that
  `Settings.validate()` **already refuses to boot** with `oauth` + a non-https
  `PUBLIC_URL` outside loopback (`config.py:243-252`) — the loud failure.
- **Scheme-dependent behaviour, both silent.** The login session cookie's
  `Secure` flag is `public_url.startswith("https://")` (`auth/login.py:108`),
  and the CIMD SSRF guard is disarmed by a non-https `PUBLIC_URL` (§1.1).
  Neither applies under `AUTH=none`; both are reasons `PUBLIC_URL` is `https://`
  even though the tunnel's hop to the origin is plain http.

Set it to the **public** origin even though TLS terminates at Cloudflare's edge
and cloudflared reaches the origin over http. `PUBLIC_URL` describes what the
world sees, not what the socket does.

### 3.2 `VIDTHEQUE_PUBLIC_HOSTNAME` — the 421 footgun

`Settings.allowed_hosts` (`config.py:169`) starts as a localhost-only allowlist
and gains whatever this names; `app.py:145-151` hands it to the MCP transport's
DNS-rebinding protection, along with `allowed_origins` of `https://<host>`.

Behind a tunnel, cloudflared forwards the **public hostname** as `Host` (which
is why `deploy/cloudflared.example.yml` tells you not to set `httpHostHeader`).
If this variable is empty:

> **`/mcp` answers `421 Misdirected Request` to every request, while `/`,
> `/api/*`, `/frames/*`, `/dashboard/*` and `/healthz` all work perfectly.**

Those are routes on the root Starlette app; only `/mcp` is inside
`Mount("/", mcp_app)` and only the mount arms the guard. So the demo looks
flawless and the MCP server — the actual product — is dead, and nothing on the
page says so. Under `oauth`, `Settings.validate()` catches it at boot with a
clear message (`config.py:253-257`); under `none` and `token` **it does not**.
This is why §7.2 tests `/mcp` explicitly and first.

### 3.3 The invocation

```bash
# scripts/dev_stack.sh path — $DATA_DIR/stack.env, sourced with `set -a`
PUBLIC_URL=https://vidtheque.example.com
VIDTHEQUE_PUBLIC_HOSTNAME=vidtheque.example.com
```

```bash
# compose path — deploy/.env, with the overlay from §6.1
PUBLIC_URL=https://vidtheque.example.com
VIDTHEQUE_PUBLIC_HOSTNAME=vidtheque.example.com
```

The compose file passes `VIDTHEQUE_PUBLIC_URL: ${PUBLIC_URL:-...}`, and
`_env` prefers the `VIDTHEQUE_`-prefixed spelling — so setting `PUBLIC_URL` in
`.env` is correct and sufficient on that path.

---

## 4. The trusted-header config item — client IP behind the tunnel

**Finding: the code already handles this. It is a configuration decision, not a
gap.** Written out because getting it wrong is silent in both directions.

`PublicSettings.trusted_ip_header` defaults to `CF-Connecting-IP` and is read
from `VIDTHEQUE_TRUSTED_IP_HEADER`, falling back to `TRUSTED_IP_HEADER`
(`public/settings.py:78-88`). It deliberately bypasses `config._env` because
**empty is a meaningful value** — the documented way to say "trust nothing but
the socket". It reaches the limiter through `public/__init__.py:136` and is used
by `ratelimit.client_key` (`ratelimit.py:134-146`): if the header is present,
take the first comma-separated entry; otherwise the ASGI `scope["client"]`
address; otherwise the literal string `"unknown"`.

**Keep the default.** Behind the tunnel, cloudflared connects to the origin from
loopback (or from the compose network), so *every* request has the same socket
address. Set the header empty and the entire internet shares one `search` bucket
of 30/min — the demo rate-limits itself into uselessness on the first busy hour.

**Then earn the right to trust it.** The middleware trusts the header
unconditionally; nothing checks that the request came from cloudflared. So:

> **The origin must not be reachable by any path except the tunnel.**

Otherwise one client sends a different `CF-Connecting-IP` per request and every
per-IP bucket is fresh — `search`, `ask` and `frames` are all void. Only
`ask_global` survives, because it is keyed `"@global"`, which makes the day's
OpenRouter budget the sole remaining backstop. Concretely:

- Bind the origin to loopback. `scripts/dev_stack.sh` already starts both
  services with `VIDTHEQUE_HOST=127.0.0.1`. The compose file does **not** — it
  publishes `"${MCP_PORT:-8080}:8080"` on `0.0.0.0`; `deploy/compose.public.example.yml`
  is the overlay that fixes it.
- Keep the worker off-box entirely. It answers an unauthenticated
  OpenAI-compatible API that will happily spend the GPU.
- Check the host firewall and the Proxmox/LXC forwarding rules by hand. "The
  port is only bound on loopback" and "nothing forwards to it" are two claims.

**What Cloudflare actually documents.** `CF-Connecting-IP` is "the client IP
address connecting to Cloudflare", available on all plans
(<https://developers.cloudflare.com/fundamentals/reference/http-headers/>).
`True-Client-IP` is byte-identical in meaning — "no difference besides the
name" — and is **Enterprise-only**, so on a free plan it will simply not be
present; do not switch to it. Cloudflare explicitly recommends `CF-Connecting-IP`
over `X-Forwarded-For`, which can carry client-supplied values. The nearest
thing to an explicit overwrite guarantee is the `CF-Connecting-IPv6` note, which
says Cloudflare "overwrites the existing Cf-Connecting-IP and X-Forwarded-For
headers" for IPv6 clients — worth knowing separately, because it means IPv6
visitors are bucketed by a pseudo-IPv4. There is **no** current doc sentence
saying "CF-Connecting-IP is always overwritten and therefore trustworthy", so do
not write one into a design doc. The defensible statement is the one above:
trust it exactly as far as the tunnel being the only way in.

One last trap: a Cloudflare Transform Rule *can* remove `cf-connecting-ip`
(<https://developers.cloudflare.com/rules/transform/request-header-modification/>).
If someone ever adds one, the limiter silently falls back to the cloudflared
socket address and the whole internet shares one bucket again — with no error
anywhere. Do not add request-header transforms on this hostname.

---

## 5. Domain and DNS

1. **Buy the domain, or pick a subdomain of a zone you already own.** A subdomain
   of an existing zone is strictly less work: the zone is already on Cloudflare
   and step 2 is done.
2. **Add the zone to Cloudflare** if it is new: add site, pick the Free plan,
   then change the registrar's nameservers to the two Cloudflare gives you.
   Propagation is usually minutes, occasionally hours. Wait for the zone to read
   **Active** before creating the tunnel — `cloudflared tunnel route dns` needs
   an active zone on the account.
3. **Do not hand-create the DNS record.** `cloudflared tunnel route dns` (§6.3)
   creates the proxied `CNAME` to `<UUID>.cfargotunnel.com` for you, and a
   hand-made record with the wrong proxy state is a confusing hour. If you must
   inspect it afterwards: it is a `CNAME`, it is **proxied** (orange cloud), and
   proxied is what puts the `CF-Connecting-IP` header on the request in the
   first place.
4. **Pick the hostname before touching any config**, because it is
   simultaneously the ingress `hostname`, the `route dns` argument, `PUBLIC_URL`
   and `VIDTHEQUE_PUBLIC_HOSTNAME`. Changing it later is four edits and a
   restart, and the one you will forget is `VIDTHEQUE_PUBLIC_HOSTNAME` (§3.2).
5. **TLS.** Cloudflare issues and terminates the edge certificate; the tunnel's
   hop to the origin is plain http on loopback and needs no certificate. Leave
   the zone's SSL/TLS mode alone — a tunnel origin is not what the
   Flexible/Full/Strict setting is about.

---

## 6. The tunnel

Docs cited below were verified on 2026-08-09. Note that Cloudflare moved the
Tunnel documentation under
`/cloudflare-one/networks/connectors/cloudflare-tunnel/…`; older
`/cloudflare-one/connections/connect-networks/…` links still resolve.

**Two flows.** Cloudflare now presents the **remotely-managed** tunnel (a token,
ingress in the dashboard) as the default, and files the **locally-managed** one
(a config file, ingress in your repo) under "do more with tunnels → local
management". `deploy/docker-compose.yml` already ships the remote flow behind
`--profile tunnel`; `deploy/cloudflared.example.yml` is the local one. **Prefer
local for this box**: "what is exposed" becomes a file you can grep, diff and
review, which is the same instinct that put `.env.example` under version
control. Use remote if you want the tunnel restartable from a phone.

### 6.1 First: close the compose env gap

**Resolved 2026-08-12: `env_file` moved into the base file.** The paragraph
below left "whether `env_file` belongs in the base file" to the audit session;
a field report from a clean *private* install answered it first — the same gap
the other way round, an operator who set `VIDTHEQUE_AUTH=token` in `.env` and
got a server that booted `AUTH=none` with the write tools registered, silently,
because on a LAN nothing visibly breaks. The base `deploy/docker-compose.yml`
now reads the whole `.env` on the `mcp` service (with `TUNNEL_TOKEN` blanked,
audit F-7) and a shape test pins it. The overlay's remaining job is the bind.

The mechanism, for whoever meets it elsewhere: `deploy/.env` is compose's
*interpolation* source; it is not the container's environment. Before the fix
the `mcp` service named four variables explicitly (`WORKER_URL`,
`VIDTHEQUE_DATA_DIR`, `VIDTHEQUE_PUBLIC_URL`, `VIDTHEQUE_LOG_LEVEL`) and
nothing else — so `VIDTHEQUE_PUBLIC_READONLY`, `VIDTHEQUE_AUTH`, every
rate-limit number, `OPENROUTER_API_KEY` and every pipeline knob sat in `.env`
being read by nobody, and a public deployment came up **in full read-write
mode, with `index-video` registered, on a public hostname.**

The overlay is still not optional for going public — it binds both published
ports to loopback (§4):

```bash
docker compose -f deploy/docker-compose.yml \
               -f deploy/compose.public.example.yml \
               --profile tunnel up -d
```

`scripts/dev_stack.sh` never had this problem: it sources `stack.env` with
`set -a`, so everything in it is exported.

Whichever path you take, prove it from outside the process before you go
further (§2.1). `curl /api/meta | jq .auth` is thirty characters and it is the
difference between a demo and an open indexing service.

### 6.2 Install cloudflared

Debian/Ubuntu, via Cloudflare's package repository (<https://pkg.cloudflare.com/>,
linked from the downloads page
<https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/>):

```bash
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null

echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list

sudo apt-get update && sudo apt-get install cloudflared
cloudflared --version
```

`pkg.cloudflare.com` carries a "Public Key Rollover (30 October 2025)" notice
affecting RPM systems and **Debian Trixie**; the `signed-by` line above is the
current documented form for older Debian/Ubuntu. If the box is on Trixie, re-read
that page first.

Docker instead of a package: `cloudflare/cloudflared:latest`, which is what the
docs show, paired with `--pull always` and `--no-autoupdate` so Docker does the
updating rather than cloudflared
(<https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/update-cloudflared/>).
Note the in-container home is `/home/nonroot/.cloudflared`, not `/root`.

### 6.3 Create the tunnel and route the hostname (locally-managed)

<https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/create-local-tunnel/>

```bash
cloudflared tunnel login          # browser: pick the zone; writes cert.pem
cloudflared tunnel create vidtheque
```

`create` prints the tunnel's UUID and the path to its credentials JSON. **Copy
both from the output.** The docs only promise "the default cloudflared
directory", which on Linux is searched as `~/.cloudflared`, then
`/etc/cloudflared`, then `/usr/local/etc/cloudflared` — and `sudo` resolves
`$HOME` to `/root`, so assuming the path is how you spend twenty minutes on a
"credentials file not found".

```bash
cp deploy/cloudflared.example.yml ~/.cloudflared/config.yml
$EDITOR ~/.cloudflared/config.yml     # tunnel UUID, credentials-file, hostname, service URL
cloudflared tunnel --config ~/.cloudflared/config.yml
cloudflared tunnel ingress rule --config ~/.cloudflared/config.yml https://vidtheque.example.com/mcp
```

`ingress rule` shows which rule a given URL hits — run it against `/mcp`,
`/dashboard` and `/frames/x-00000.jpg` if you enabled the optional 404 rule, so
you find out now rather than from a visitor.

```bash
cloudflared tunnel route dns vidtheque vidtheque.example.com
```

That creates the proxied `CNAME`. Then run it in the foreground **once**, and
watch the log:

```bash
cloudflared tunnel --config ~/.cloudflared/config.yml run vidtheque
```

Do §7 while it is in the foreground. Only once §7 passes, install the service:

```bash
sudo cloudflared --config /home/<USER>/.cloudflared/config.yml service install
sudo systemctl start cloudflared
sudo systemctl status cloudflared
```

The explicit `--config` is required with `sudo`, for the `$HOME` reason above —
it is called out in Cloudflare's own docs
(<https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/as-a-service/linux/>).
`systemctl restart cloudflared` after any config change.

### 6.4 The remotely-managed alternative

<https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/>,
<https://developers.cloudflare.com/tunnel/advanced/tunnel-tokens/>

Zero Trust dashboard → **Networking → Tunnels → Create a tunnel**, name it, pick
the OS, then add a **published application** route: hostname
`vidtheque.example.com`, service `http://mcp:8080` (compose network) or
`http://127.0.0.1:8100` (dev stack). No `cloudflared tunnel login`, no
`cert.pem`, no credentials JSON, no ingress YAML — the token is everything, and
**anyone holding the token can run the tunnel**, so treat it exactly like a
private key. Put it in `.env` as `TUNNEL_TOKEN` (the entry already exists) and
never on a command line; `--token-file` and the `TUNNEL_TOKEN` env var are both
documented for that reason.

```bash
docker compose -f deploy/docker-compose.yml \
               -f deploy/compose.public.example.yml \
               --profile tunnel up -d
```

Rotation is documented and worth scheduling: an old token cannot open new
connections, existing ones survive until restart, and running two replicas makes
rotation non-disruptive.

### 6.5 Do not test with a quick tunnel

`cloudflared tunnel --url ...` (`trycloudflare.com`) is documented as
testing-only, caps at 200 in-flight requests, and — decisively for us —
**does not support Server-Sent Events**: its edge buffers `text/event-stream`.
Since `/mcp` *is* an SSE transport (§7.2), a quick tunnel will make the MCP
server look broken in a way a real named tunnel is not
(<https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/>).

---

## 7. Smoke test through the tunnel, before the URL is shared

The lesson this section exists for: on 2026-08-09 a dashboard previewed through
an SSH tunnel on another port rendered every thumbnail against a `PUBLIC_URL`
that resolved to nothing, and it looked fine right up until the images did not
load. **Every test below runs against `https://vidtheque.example.com`, from a
machine that is not the box** — a phone on cellular is the honest test, because
it shares no DNS cache, no `/etc/hosts` entry and no local route.

### 7.1 Relative paths — already right, confirm anyway

The demo page's JS fetches `/api/meta`, `/api/videos`, `/api/search` and
`/api/ask` as root-relative paths (`public/static/demo/app.js`), and the dashboard's
templates and stylesheet are explicit that assets are relative and never built
from `PUBLIC_URL` (`dashboard/templates/base.html:15`,
`dashboard/static/dashboard.css:28`). So the *chrome* survives any hostname. What
does **not** come from the page's own origin is every URL that arrives inside a
JSON payload — thumbnails and `mcp_url` — because those are absolute by design
(§3.1). That is the whole of the risk, and it is why the two checks below are the
ones that matter.

```bash
# thumbnails must be absolute AND on the public hostname
curl -s 'https://vidtheque.example.com/api/search?q=agents&limit=3' \
  | jq -r '.results[].thumb'
# https://vidtheque.example.com/frames/<id>.jpg?w=192&q=70   <- right
# http://127.0.0.1:8100/frames/...                            <- PUBLIC_URL is wrong

# and the copy button's URL
curl -s https://vidtheque.example.com/api/meta | jq -r .mcp_url
# https://vidtheque.example.com/mcp
```

Then open the page in a browser with the network panel up and confirm **zero**
failed image requests. A `curl` that returns the right string and a page that
renders are not the same claim.

### 7.2 `/mcp` — the 421 check, first and explicitly

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST https://vidtheque.example.com/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
```

A `421` means `VIDTHEQUE_PUBLIC_HOSTNAME` does not name this hostname (§3.2) —
and note that nothing else on the site will have told you. Fix it, restart the
server (not the tunnel), retest.

Then add it to a real client, because that is the product:

```bash
claude mcp add --transport http vidtheque https://vidtheque.example.com/mcp
```

and confirm `tools/list` shows the **seven** read tools with `index-video` and
`tag-video` absent. The MCP transport streams over `text/event-stream`, which
Cloudflare Tunnel passes through unbuffered — this is the one streaming path
that is fine by construction.

### 7.3 The ask stream — fixed 2026-08-09, verify it stayed fixed

**The gap this section was written for.** Cloudflare Tunnel buffers a proxied
response **unless the origin sends `Content-Type: text/event-stream`**
(<https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/troubleshoot-tunnels/common-errors/>).
The ask activity stream sent `application/x-ndjson`, deliberately — `demo-site.md`
§3.5 chose NDJSON over SSE because `EventSource` is GET-only and ask is a POST —
so through the tunnel the visitor saw nothing for up to
`VIDTHEQUE_ASK_TIMEOUT_S` (90s at the time) and then the whole log and the
answer at once. Not a hang (Cloudflare's Proxy Read Timeout is 125s, so a 90s
budget stayed inside), but "ninety seconds of work made visible" is exactly the
thing that stopped working. **The budget is 180s since 2026-08-15**, so it no
longer fits inside 125s on its own; the stream stays alive on its activity
events, and the case to watch is a silent final completion — see
`demo-site.md` §3.5.

`X-Accel-Buffering: no` does not help and never did: it is an **nginx**
directive, cloudflared does not honour it, and no `originRequest` option
controls buffering. It is still sent, for the nginx that might one day be in
front of this, and it is not a lever to pull here.

**The fix that shipped (decided 2026-08-09 evening):** `/api/ask` now serves
the *same* event stream in a second framing, negotiated by `Accept` —
`text/event-stream` gets SSE frames, `application/x-ndjson` gets the NDJSON
lines unchanged, anything else gets the plain JSON body. The page asks for SSE
first. `demo-site.md` §3.5 carries the negotiation matrix; the events, the
payloads and the budget accounting are identical across framings by
construction, because there is one generator and the framing is a parameter.

Verify through the tunnel — the timestamps are the assertion:

```bash
curl -N -sS -X POST https://vidtheque.example.com/api/ask \
  -H 'Accept: text/event-stream' -H 'Content-Type: application/json' \
  -d '{"q":"what did people say about evals?"}' \
  | while IFS= read -r line; do printf '%s  %s\n' "$(date +%T)" "${line:0:80}"; done
```

Spread across the run: the tunnel is streaming, as intended. All sharing one
timestamp at the end: something in front of the origin is still buffering, and
the next thing to check is what *that* keys on — not another buffering-hint
header. Confirm the content type came back as `text/event-stream` before
concluding anything (`curl -sSD- -o/dev/null` on the same request).

**Do not run this against a quick tunnel** — §6.5: `trycloudflare.com` buffers
`text/event-stream` itself, so it will report the fix as broken. A named tunnel
or nothing.

If the stream is somehow still buffered and ask ships anyway, the fallback is
unchanged and honest: the page already has a non-streaming path, and a visitor
who waits 90s with a spinner is a worse demo than a visitor who was never
offered ask mode at all (§1.1's option (c)).

### 7.4 The rest

```bash
curl -sS https://vidtheque.example.com/healthz
curl -sS -o /dev/null -w '%{http_code}\n' https://vidtheque.example.com/dashboard
curl -sS -o /dev/null -w '%{http_code}\n' https://vidtheque.example.com/dashboard/jobs

# rate limiting is live and keyed per visitor, not per tunnel
for i in $(seq 1 35); do
  curl -s -o /dev/null -w '%{http_code} ' "https://vidtheque.example.com/api/search?q=test&limit=1"
done; echo
# expect 200s then 429s — and a 429 body carrying E_RATE_LIMIT and retry_after_s
```

Then, from a **second** device on a different network, confirm the second
visitor gets a fresh bucket rather than inheriting the first one's 429. That is
the end-to-end proof that `CF-Connecting-IP` is arriving and being used (§4) —
and it is the single test that covers the whole client-IP story.

Finally re-run §2.5's redaction greps against the public hostname. The flag
reaching the process locally does not prove it reached the container the tunnel
is pointed at.

### 7.5 Sharing checklist

- [ ] §1 audit complete, written up, no blocked items
- [ ] `/api/meta` reports `auth: "none"`, `clamps.policy: "public"`, the intended `ask_enabled`
- [ ] `tools/list` through the tunnel: seven read tools, no write tools
- [ ] every thumbnail on the demo page loads in a real browser
- [ ] `mcp_url` is the public hostname, and adding it to a client works
- [ ] `/mcp` does not 421
- [ ] jobs view shows no source URLs, no error text, no event messages
- [ ] rate limits fire, and two devices get two buckets
- [ ] the ask-stream result (§7.3) recorded, and the ask budget decision applied
- [ ] `cloudflared` running as a service, surviving a reboot
- [ ] rollback rehearsed (§8) — **before** the URL is shared, not after

**Added by the 2026-08-10 audit.** Everything above this line the repo can help
with. Everything below it is a fact about the box or an account elsewhere, and
no test in this repository can check any of it:

- [ ] the merged compose model publishes **loopback only**, checked with
      `docker compose … config --format json | jq '[.services[].ports[]?] | all(.host_ip == "127.0.0.1")'`
- [ ] `sudo ss -ltnpH | awk '$4 ~ /:(8080|8081|8100)$/'` shows no wildcard
      listener — the merged model is a claim, this is the fact (B-1)
- [ ] the **worker** is unreachable from a second machine on the LAN *and* from
      off-network. It has no authentication and no request-size limits; the
      compose overlay no longer publishes it at all, and that is worth
      confirming rather than assuming (F-12)
- [ ] Proxmox and LXC firewall, router port forwards, UPnP and IPv6 checked by
      hand — "bound to loopback" and "nothing forwards to it" are two claims
- [ ] `job-status` through the **public `/mcp`** shows no source URL and no
      error text. §2.5's greps only read dashboard HTML and never covered this
      path, which is how it stayed open for two phases (F-4)
- [ ] the OpenRouter key is **dedicated to this deployment and has a hard spend
      cap set in the console**. The in-app budget counts asks; only this counts
      money, and only this survives a bug in the counter
- [ ] **Bot Fight Mode is OFF.** It is zone-wide, cannot be scoped to a path,
      and cannot be skipped by WAF rules — it challenges non-browser clients,
      which is exactly what `/mcp` serves. Enabling it breaks the product for
      every agent
- [ ] the single free-plan WAF rate-limiting rule, if used, points at
      `/api/ask` — the only path that spends money
- [ ] `MODEL_REVISION` pinned, or the decision to leave it floating recorded.
      Nothing else stands between a third-party artifact and code execution in
      the worker container (F-26, F-27)

---

## 8. Rollback

**The tunnel is the whole exposure. Stopping it is the rollback.** There is no
DNS TTL to wait out and no cache to purge: the `CNAME` points at
`<UUID>.cfargotunnel.com`, and with no connector running Cloudflare has nothing
to route to. Visitors get a Cloudflare error page; the box goes back to being
invisible.

```bash
sudo systemctl stop cloudflared          # local/service install
sudo systemctl disable cloudflared       # …and do not come back after a reboot

docker compose -f deploy/docker-compose.yml \
               -f deploy/compose.public.example.yml stop cloudflared   # compose
```

Escalating, in order — each step is more permanent and less reversible:

1. **Stop the connector** (above). Seconds. Reversible with `start`.
2. **Turn off the demo, keep the box up.** `VIDTHEQUE_PUBLIC_READONLY=0` and
   restart. `/` stops being the page, `/api/*` disappears, the rate limiter
   drops to the `dashboard` bucket only — and the write tools come **back**, so
   only do this behind a stopped tunnel.
3. **Delete the DNS record** in the Cloudflare dashboard. Now the hostname does
   not resolve at all.
4. **Delete the tunnel**: `cloudflared tunnel delete vidtheque`. This
   invalidates the credentials/token; recreating means redoing §6.3.

**Rehearse step 1 before sharing the URL.** A rollback you have not run is a
plan, not a rollback.

**Rotate on exposure.** If the tunnel token or credentials JSON ever leaks:
rotate the token, restart the connector, and force-disconnect existing
connections via the API — Cloudflare documents both. If `OPENROUTER_API_KEY`
leaks, revoke it at OpenRouter first and change `.env` second; the running
process holds it in memory until restart.

---

## 9. Operating notes

- **Restarting the server does *not* reset the ask budget.** It used to, and
  this note used to say so. Migration 0005 writes the daily `ask_global` counter
  to SQLite (`ask_budget`, one row per bucket/client/UTC day), so a redeploy
  resumes the day where it left off — which was the point: on a launch day you
  redeploy several times an hour, and an in-memory cap handed the money back
  each time. What is still in memory is every *per-minute* bucket, and those do
  reset on a restart; that is deliberate, they guard against hammering and
  nobody hammers across a restart they did not know happened.
  - **The reset is the UTC date changing**, not local midnight and not a
    restart. `Retry-After` on a `429 ask_global` is the seconds until UTC
    midnight, which can be hours, and that is the honest number for a
    day-keyed budget.
  - **Reads happen once, at boot**; after that the in-memory counter is the
    gate and SQLite is only the record, written behind the request. An orderly
    stop drains the queue; a `kill -9` can lose whatever was in flight —
    **corrected 2026-08-11: that is the whole queued burst, not "at most one
    ask"**, and a persistently failing writer loses every delta it is handed,
    because a failed write is logged and dropped rather than retried. Each
    failed-write-then-restart cycle hands back up to a full day. The
    provider-side cap is what this cannot undo.
  - **Rows older than 30 days are pruned at boot**, so the table answers "what
    did the demo cost last month". **Corrected 2026-08-11:** "never grows" was
    wrong — pruning happens only at boot, so a long-lived process adds a row per
    UTC day until its next restart. That is a row a day, which is nothing; the
    sentence was still false.
  - To see it: `sqlite3 <data>/vidtheque.db 'SELECT * FROM ask_budget ORDER BY
    day DESC LIMIT 10;'`. To hand back a day by hand, `UPDATE ask_budget SET
    spent = 0 WHERE day = '<YYYY-MM-DD>'` **and restart** — the live counter is
    only re-read at boot.
  - A failed ask that bought nothing upstream is refunded (`-1`) and a paid one
    is not, so the row is a record of spend rather than of attempts
    (`demo-site.md` §4.4). **Sharpened 2026-08-11:** "bought nothing" now means
    *the provider never answered* — a failure to connect, or any status line it
    returned instead of generating. A read timeout and a cancelled request stay
    charged, because either can sit on top of a real generation. Before B-3 the
    row recorded observed successes, and work that was billed and then abandoned
    was invisible to it.
  - If the budget is money (§1.1), the cap that ultimately matters still lives
    at OpenRouter, not here.
- **Indexing while public.** Adding videos needs the write tools, which needs
  `VIDTHEQUE_PUBLIC_READONLY=0`, which un-masks them for everyone. Stop the
  tunnel, flip the flag, index, flip it back, verify with §2.1, restart the
  tunnel. Keep the yt-dlp politeness settings while doing it.
- **Documented limits worth knowing** (all from Cloudflare's docs, 2026-08-09):
  Proxy Read Timeout 125s → HTTP 524 past it; Proxy Write Timeout 30s; proxy
  idle timeout 900s; max request body 100 MB on Free and Pro → HTTP 413; URLs
  16 KB, request and response headers 128 KB each; WebSockets supported through
  tunnels with no extra configuration. **No documented bandwidth cap** for
  tunnels on the free plan — "not documented" is the honest phrasing, not
  "unlimited"; Cloudflare's general Terms §2.8 on non-HTML content is a separate
  constraint that lives outside the developer docs.
- **Watching it.** `cloudflared` can expose Prometheus-style metrics on
  loopback (`--metrics 127.0.0.1:20241`); `journalctl -u cloudflared -f` is the
  connector's own view. The application's view is `/dashboard` — which, in this
  deployment, the public can also see.
- **A creator asks to be removed.** `docs/takedown.md` is the procedure, and it
  is a standing obligation rather than an incident: the positioning contract
  promises publicly to *"take a channel out on request"*
  (`research/positioning-2026-08-10.md` §9.1), and the demo page's attribution
  line links to it. Three things in there that are not obvious and that a
  hurried removal gets wrong — a plain `sqlite3` connection **cannot** perform
  the delete (the cascade runs through `vec0` triggers and needs `sqlite-vec`
  loaded, and `PRAGMA foreign_keys` defaults off outside the app); the deleted
  transcript stays recoverable from free pages until a `VACUUM`; and stopping
  the origin does not un-publish the keyframes, because `/frames/*.jpg` sits in
  Cloudflare's edge cache for up to a day (§8's rollback card).

---

## 10. Environment variables

This runbook introduces **no new environment variables**. Everything it names
already has an entry in `deploy/.env.example`, which stays the document of
record:

| variable | §  | note |
|---|---|---|
| `PUBLIC_URL` / `VIDTHEQUE_PUBLIC_URL` | 3.1 | the https public origin |
| `VIDTHEQUE_PUBLIC_HOSTNAME` | 3.2 | bare host; without it `/mcp` 421s |
| `VIDTHEQUE_PUBLIC_READONLY` | 2.1 | `1` for the demo |
| `VIDTHEQUE_AUTH` | 2.2 | `none` |
| `VIDTHEQUE_DASHBOARD` | 2.1 | the read-only projection |
| `VIDTHEQUE_TRUSTED_IP_HEADER` | 4 | keep `CF-Connecting-IP` |
| `VIDTHEQUE_RATE_*` | 2.3 | five buckets |
| `VIDTHEQUE_DERIVED_CACHE_MB`, `VIDTHEQUE_FRAME_*`, `VIDTHEQUE_INLINE_FRAME_*` | 2.6 | frame byte caps |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `VIDTHEQUE_ASK_*` | 1.1, 2.3 | the spend surface |
| `TUNNEL_TOKEN` | 6.4 | remotely-managed tunnels only |
| `VIDTHEQUE_HOST`, `MCP_PORT`, `WORKER_PORT` | 4 | bind loopback |

If a future change to this runbook needs a variable that is not in that list,
add the `.env.example` entry in the same commit. CLAUDE.md: an env var without
one is a bug.
