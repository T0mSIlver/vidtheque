# Security fixes — overnight, 2026-08-11

Branch `security-fixes`, 14 commits, **rebased onto `origin/main@93720c9`** and
green: `make test` → **1150 passed**. Every finding in
`research/security-audit-2026-08-10.md` is either fixed here or listed under
"Not done" below.

Merge it; nothing in it is a work in progress.

---

## The three blockers

**B-1 — the origin was not tunnel-only.** `compose.public.example.yml` restated
`ports:` meaning to replace the base file's wildcard publication, and Compose
appends sequence fields. Both publications existed, so the bind the overlay
promised to fix was open for as long as the overlay had existed. `!override` on
`mcp`, `!reset` on `worker` — which needs no host publication at all, since
`mcp` reaches it over the compose network. The worker also loses `env_file`, so
it no longer holds the OpenRouter key or the tunnel token while running
untrusted weights on a GPU.

**B-2 — read-only failed open.** `_bool_env` coerced anything unrecognised to
false, so `VIDTHEQUE_PUBLIC_READONLY=Y` meant read-write on a public hostname
with no complaint. It now refuses. And `AUTH=none` + writes + a non-loopback
hostname refuses to boot, with `VIDTHEQUE_ALLOW_PUBLIC_WRITES=1` as the
deliberate exception for the §9 indexing workflow. Your dev stack is untouched:
loopback hostnames never trip it.

**B-3 — the ask budget refunded work that was already billed.** `Billing.paid`
was set after the upstream await returned, so a disconnect mid-completion
cancelled the task, skipped the assignment (`CancelledError` is a
`BaseException`) and refunded the day. The flag goes up before dispatch now and
comes back down only where nothing can have been generated. `max_tokens` is sent
on every completion; it was not sent at all.

## Everything else

| | |
|---|---|
| F-1, F-2, F-13 | `/mcp` has a rate bucket, sessions are stateless, query embedding moved inside search admission, inline frames default off in public mode |
| F-4, F-8 | `job-status`, `corpus-summary` and `/dashboard/videos/{id}` redact what the jobs view already did; `E_INTERNAL` returns a trace id instead of `str(exc)` |
| F-5, F-14 | `w`/`q` snap to the five widths and two qualities that are real; parsed strings are bounded and no longer echoed whole; `NaN` offsets refused |
| F-15…F-19 | Ingest is YouTube-only and revalidates playlist children; extractor ids must match YouTube's grammar; job-wide fan-out cap; 4h duration ceiling |
| F-20…F-25 | Cancelled reads no longer repool a live connection; `COMMIT` is inside the rollback handler; a missing DB refuses under readonly; newer schemas refused; heartbeat failures no longer strand a job; job events pruned |
| F-26…F-32 | `weights_only`/`use_safetensors` forced so a repo config cannot weaken them, `MODEL_REVISION` to pin; bounded worker queue; cancelled jobs skipped before load; `/docs` off; hook errors name the label not the command, and kill the process group; both images run non-root; `cloudflared` off `:latest` |
| auth | frame-ancestors, X-Frame-Options, an origin check on both POSTs, login backoff, non-ASCII compare fixed, CIMD streams to its byte cap and covers CGNAT, `auth.db` 0600, plaintext client secrets dropped |

Nine false claims in `docs/deploy-public.md` are corrected in place and dated.
`tool-surface.md` carries the frame-variant amendment. Every new env var has an
`.env.example` entry — there is a test that enforces that, and it caught me.

## Not done, deliberately

**The OAuth scope model.** `credential.py` discards the token claims and treats
any valid bearer as the owner, so `vidtheque:read` buys `index-video`. That is
the confused deputy and it is a design decision about what the scopes mean, not
a bug fix — your call, and inert until you enable `token` or `oauth`. Everything
mechanical around it is fixed.

**The V5 front end.** It did not exist when the audit ran. The audit found **no
executable rendering sink** in the current pages — autoescape unconditional,
results and OCR text built with text nodes rather than HTML strings — and those
mechanisms are what the safety consists of. Re-check them against the finished
pages before sharing the URL.

**The structural change** (splitting the anonymous data plane from the owner
control plane) is recorded in the audit as where the next one starts. Not a
ship-day action.

## Before you share the URL

The audit's checks that no test can make are now in `deploy-public.md` §7.5.
The four that matter most:

1. `sudo ss -ltnpH | awk '$4 ~ /:(8080|8081|8100)$/'` — loopback only. The
   merged compose model is a claim; this is the fact.
2. The worker unreachable from a second LAN machine **and** from off-network.
3. A **hard spend cap on a dedicated OpenRouter key**, set in their console.
   The in-app budget counts asks; only that counts money.
4. **Bot Fight Mode off.** It is zone-wide, cannot be scoped to a path, cannot
   be skipped by WAF rules, and challenges non-browser clients — which is what
   `/mcp` serves. Spend the free plan's single rate-limiting rule on
   `/api/ask`, the only path that costs money.

## Merge notes

Rebased onto `93720c9`, so it applies cleanly. Two conflicts came up and both
are resolved in favour of keeping *both* intentions:

- `tools/base.py` — you made the unreachable-worker note name its cause; I had
  removed the cause because it carries the worker URL. It now prints the
  exception's **class name**, which says whether the failure is transient
  without naming a host, and logs the message. Your test now pins the property
  rather than the exact string, and has a sibling asserting the URL never
  appears.
- `tool-surface.md` — your demo-geometry update kept verbatim; the frame-variant
  amendment appended. `w=320` is in the snapped set, so nothing changes for the
  demo.
