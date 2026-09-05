# The dashboard's HTTP contract for the Next.js front end

**Status: the first read slice is implemented (2026-09-05).** This file records
the decisions Tom has settled and the three endpoints that exist because of
them. It is not a plan — everything still open is in `docs/ROADMAP.md`, and
nothing is described here that is not in the tree. (The earlier speculative
draft of this file is gone; endpoints it sketched were never contracts.)

## 1. Settled decisions

From `DECISIONS.md` ("Frontend replacement", 2026-09-05) and Tom's review of
this slice:

1. **All three surfaces move to Next.js and React** — `/`, `/demo`,
   `/dashboard`. **Cutover happens only when all three are at parity**,
   dashboard reads and writes included; the Jinja pages keep serving until it.
2. **Browser API requests go directly to Python.** No relay through Next, no
   generic proxy. Next may also read Python server-side for rendering.
3. **Python owns state, authorization, sessions and resource limits. Next owns
   the UI.** No new database layer, no second auth system, and the HTTP-only
   `mcp/` ↔ `worker/` boundary is untouched.
4. **Production is one origin**, so the existing routes and the existing
   `vidtheque_session` cookie keep working unchanged. Cross-origin support and
   the CORS policy it needs come later; nothing here adds them.
5. **Typed values on the wire, formatting in React.** What stays Python's is
   *policy text*: refusal codes, messages and their `next:` line, and the
   redaction itself.
6. **The public read-only projection is unchanged**, and it redacts by
   *omission*: the reads behind the operator's box are not taken, so there is
   no field for a client to un-hide.

## 2. What landed

Three additive `GET` endpoints and the shared read assembly behind them. No
writes, no CORS, no new env var, no dependency or lockfile change, no auth
policy change, and no import from `worker/`.

In `mcp/src/vidtheque_mcp/dashboard/`: `api.py` (new) is the three handlers,
`read_models.py` (new) is the shared assembly, `__init__.py` registers the
routes, and `views.py` now calls the assemblers instead of holding them.
`mcp/tests/test_dashboard_api.py` (new) covers the slice.

`read_models.py` holds what the pages and the JSON must not answer twice:
`overview_reads`, `ledger_reads`, `pipeline_readiness`, `redacted`,
`declared_models`, `file_size`, `tool_error`, `thumb`, and the caps below.
`views.py` imports them back under the names it always used, so the Jinja pages
run the same code and the same number of database reads as before.

## 3. Common behaviour

- **Auth.** `/api/overview` and `/api/ledger` sit behind the route group's
  existing read gate (`dashboard/__init__.py:guarded`): a bearer token, a valid
  `vidtheque_session` cookie, a socket peer in
  `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS`, or `VIDTHEQUE_AUTH=none` (open by
  design). Refusal is `401` with `{"error": "E_AUTH_REQUIRED", "message",
  "next"}`. `/api/session` is outside the gate — see §6.
- **Caching.** Every response carries `Cache-Control: no-store`.
- **Parameters.** `overview` and `ledger` read no query string at all, so there
  is nothing to clamp; their bounds are the constants in §4.
- **Rate limit.** The existing per-IP `/dashboard/*` bucket
  (`VIDTHEQUE_RATE_DASHBOARD_PER_MIN`, default 120) covers all three.
- **Errors.** A tool refusal passes through as `{"error", "message", "next"}`
  at the status `errors.HTTP_STATUS` maps the code to.
- **Timestamps.** Epoch seconds, every one of them, `readiness.checked_at`
  included. The pages format that observation with `iso_z` because a
  `<time datetime=…>` attribute wants ISO-8601; `read_models._stamped` takes one
  reading of the clock and carries both shapes, so the page and the payload can
  never name different seconds. `api.py:_readiness` copies the block out field
  by field rather than forwarding the page's dict, so a value added for the
  templates does not join this contract by default.
- **No display roundings either.** `corpus_rollup` carries `hours` beside
  `duration_s` and its own SQL comment calls it "a display rounding"; the JSON
  sends the seconds only.

## 4. `GET /dashboard/api/overview`

The corpus overview page's reads (`views.overview`), typed.

```jsonc
{
  "counted_at": 1757030400,          // int, epoch seconds
  "redacted": false,                 // bool: is this the public projection
  "corpus": {
    "videos": 4, "queryable_videos": 3,
    "videos_by_index_state": {"ready": 3, "indexing": 1},  // present states only
    "data_status": "ok",             // verbatim from corpus-summary
    "cues": 0, "keyframes": 0, "ocr_lines": 0,
    "duration_s": 13500.0,
    "published": {"oldest": 1740000000, "newest": 1740000000},  // int|null
    "last_indexed": 1740000000       // int|null
  },
  "channels": [{"channel": "…", "videos": 3, "seconds": 8100.0}],  // ≤ 12
  "tags":     [{"tag": "topic:attention", "videos": 2}],           // ≤ 24
  "gaps":     {"transcript_no_ocr": 0, "indexing": 1, "failed": 0},
  "embed_backlog": {"text": 0, "frame": 0},
  "jobs": {"active": 2, "running": 1, "deferred": 1,
           "failed_recent": 1, "failed_window_s": 86400},
  "recent": [{"video_id": "…", "title": "…", "channel": "…",
              "duration_s": 5400.0, "indexed_at": 1740000000,
              "thumb": "/frames/….jpg?w=192&q=70"}],               // ≤ 8
  "readiness": {"mcp": "ready", "database": "ready",
                "vectors": {"enabled": true, "reason": null},
                "worker": {"state": "ready|unavailable|unconfigured",
                           "detail": "…",
                           "models": [{"task": "stt", "model": "…",
                                       "loaded": true}]},           // ≤ 12
                "checked_at": 1757030400},                          // int, epoch
  "declared_models": [{"label": "…", "key": "…", "value": "…", "dim": "…"}],
  "storage": {"keyframe_bytes": 0, "database_bytes": 0}
}
```

Caps, from `read_models`: `CHANNEL_CAP=12`, `TAG_CAP=24`, `RECENT_CAP=8`,
`FAILED_WINDOW_S=86_400`, `WORKER_BACKEND_CAP=12`; the worker probe is bounded
by `WORKER_STATUS_TIMEOUT_S=1.0` wall-clock and `WORKER_STATUS_MAX_BYTES=64 kB`
and runs concurrently with the database reads. `gaps.failed` is a **count** —
the rows behind it carry `video_stages.error`, the pipeline's prose about the
operator's box, and reach no surface from here.

## 5. `GET /dashboard/api/ledger`

The ledger page's reads: a fixed number of whole-table and index counts, no
per-video work.

```jsonc
{
  "counted_at": 1757030400, "redacted": false,
  "corpus": {"videos": 4, "duration_s": 13500.0,
             "cues": 0, "keyframes": 0, "ocr_lines": 0,
             "chunks": 0, "tags": 0, "channels": 2, "last_indexed": 1740000000},
  "videos_by_state": {"ready": 3, "pending": 0, "indexing": 1,
                      "failed": 0, "stale": 0},   // sums to corpus.videos
  "jobs_by_state": {"queued": 1, "running": 1, "done": 0,
                    "failed": 1, "cancelled": 0},
  "queue": {"active": 2, "running": 1, "deferred": 1,
            "failed_recent": 1, "failed_window_s": 86400},
  "embed_backlog": {"text": 0, "frame": 0},
  "gaps": {"transcript_no_ocr": 0},
  "readiness": { … as above … },
  "storage": {"keyframe_bytes": 0, "database_bytes": 0}
}
```

## 6. `GET /dashboard/api/session`

**Readable signed out, deliberately** — a React shell that cannot ask this can
only guess whether to render a dashboard or a sign-in link, or probe a data
endpoint and read the 401. It is no new disclosure: `GET /dashboard` has
answered an anonymous browser with the auth mode and this exact sign-in hint
since phase 1, and the endpoint is on the same rate-limit bucket.

```jsonc
{
  "version": "0.0.6",
  "auth_mode": "token",        // none | token | oauth
  "readonly": false,           // VIDTHEQUE_PUBLIC_READONLY
  "write_side": true,          // does this deployment register writes at all
  "writes_allowed": true,      // db.writes_allowed
  "authenticated": false,      // may this caller read the data endpoints
  "is_owner": false,           // did they *prove* it ("open" is not a credential)
  "signed_in": false,          // a validated session row, never cookie presence
  "policy": "public",          // public | owner — the clamp policy they earn
  "login_url": "/dashboard/login",   // null when there is no write side
  "sign_in_hint": "Sign in at /dashboard/login, or send Authorization: …",
  "accepts_password": true, "accepts_token": true
}
```

`signed_in` is `auth/credential.py:credential()` returning `"session"` — the
cookie looked up in `login_sessions` and found unexpired. A cookie the browser
still holds after its row is gone reads `false`, which is the whole point.

Three fields whose reading is easy to get wrong, and each one is a page's
existing behaviour rather than a new rule:

- `signed_in` here is **not** `base.html`'s `signed_in`, which is the cookie's
  mere presence — deliberately, so a stale cookie still gets a **Sign out**
  button to clear it. A React shell wanting that affordance reads the cookie it
  can see, not this field; this one answers "will the next request be served".
- `sign_in_hint` is `null` in `VIDTHEQUE_AUTH=none`. The string
  `access.sign_in_hint` builds names a bearer unconditionally, which is correct
  where it is used — a 401 page, in a mode that takes one — and untrue as a
  standing description of a deployment that refuses nobody and registers no
  login page.
- `writes_allowed` is `db.writes_allowed`, the database's own flag, exactly as
  the rail reads it. It is not the write gate: a deployment can have a writable
  database and no write side (`readonly`, or `AUTH=none`), and a client renders
  a control only when `write_side` is true.

**Never in this payload:** the token, the password, which of the two matched,
`PUBLIC_URL`, the worker URL, the database path, the trusted CIDRs, the
declared model ids, the drift reason.

## 7. The projection, per field

In `VIDTHEQUE_PUBLIC_READONLY=1` (`read_models.redacted`), on both corpus
endpoints:

| Field | Public projection |
| --- | --- |
| `declared_models` | `null` — not read |
| `storage` | `null` — the byte totals are not read |
| `readiness.worker` | `null` — the probe is not made at all |
| `readiness.vectors.reason` | `null`; `enabled` stays, because search answers differently without the vector legs |
| everything else | unchanged — counts, channels, tags, gaps, queue, arrivals are corpus, not deployment |

## 8. Tests and what is not here

`mcp/tests/test_dashboard_api.py`, over `test_dashboard.py`'s fixture corpus and
client builders: the gate (anonymous, bearer, live and stale session cookie),
`no-store`, GET-only registration and disappearance with `VIDTHEQUE_DASHBOARD=0`,
typed values against the fixture's exact tallies, a scan of both payloads for a
rendered clock (the readiness stamp is the one field where the split nearly
leaked back), the caps and the absence of
parameters, the worker probe dropping `/status`'s operator-only fields, and the
projection both ways — the demo losing the box and never probing the worker,
against the owner's instance still seeing both. The Jinja pages are unchanged
and `test_dashboard.py` plus `test_dashboard_following.py` still cover them.

Writes, CORS and cross-origin sessions, the remaining read endpoints, the React
pages and the cutover are `docs/ROADMAP.md`'s. No schema for them is stated here
until it exists.
