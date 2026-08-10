# Security audit — 2026-08-10, pre-exposure

The audit `docs/deploy-public.md` §1 gates the first public request on. Run the
night before ship day by three independent Codex (gpt-5.6-sol) reviewers on
fenced scopes, plus the orchestrating session's own verification. Tom answered
the four policy questions; everything else here is code.

**This document is append-only.** Later sessions add clearly-headed sections
rather than rewriting this one.

## Verdict

**Blocked.** Three findings must be fixed before the tunnel connects; all three
are in the deployment's own safety story rather than in application logic, which
is why the runbook's checks do not catch them.

The application surface itself is in good shape: no SQL injection, no path
traversal out of `DATA_DIR`, no XSS sink, autoescape unconditional, write tools
genuinely unregistered, clamps correctly credential-keyed, no permissive CORS.
The failures are in what the box *exposes* and what the demo *spends*.

## The four policy questions §1 reserved for Tom

| Question | Answer | Note |
|---|---|---|
| How is OpenRouter spend bounded? | Dedicated key **and** provider-side hard cap, **plus** the in-app 50/day budget | Belt and braces; the provider cap is the only one that survives F-1 |
| Which model? | `deepseek/deepseek-v4-flash-0731`, as documented | Not "v4 pro"; see D-3 |
| Is the dashboard public? | Yes, read-only projection | Widens the surface F-4 leaks through |
| Edge defenses? | WAF rate-limiting rule + Bot Fight Mode; Turnstile deferred | **Bot Fight Mode is now contested — see D-1** |

## Blockers

### B-1 — The public compose overlay does not restrict the ports it claims to

**Severity: critical.** `deploy/compose.public.example.yml:40,53` re-declare
`ports:` intending to replace the base file's publications. Compose **appends**
sequence fields; it does not replace them. Verified on this box:

```
$ docker compose -f docker-compose.yml -f compose.public.example.yml config
mcp     [{target: 8080, published: "8080"},                      # 0.0.0.0
         {host_ip: 127.0.0.1, target: 8080, published: "8080"}]
worker  [{target: 8081, published: "8081"},                      # 0.0.0.0
         {host_ip: 127.0.0.1, target: 8081, published: "8081"}]
```

Both services keep a wildcard binding. Consequences, in order of severity:

1. **The worker's unauthenticated OpenAI-compatible API is reachable from any
   network that can reach the LXC**, and it spends the 3090. `docs/deploy-public.md`
   §4 says "keep the worker off-box entirely"; the artifact that is supposed to
   implement that does the opposite.
2. **`CF-Connecting-IP` becomes forgeable**, because the origin is reachable
   without traversing the tunnel. Every per-IP bucket — `search`, `ask`,
   `frames`, `dashboard` — is void. §4's entire trust argument rests on the
   premise this breaks.
3. **§8's rollback claim is false.** Stopping the connector does not make the
   box invisible if the origin is independently reachable.

**Calibration.** The merge defect is certain — `docker compose config` was run
and both publications are in the model. Whether the wildcard bind is *live* is
not established by source: Docker may reject the overlapping wildcard/loopback
pair and fail loudly instead. So the honest statement is that the artifact does
not do what it claims, and the exposure must be settled on the box with
`ss -ltnp` rather than assumed in either direction. Either outcome is a blocker:
one is a public GPU, the other is a deployment that does not start.

**Fix.** Use `ports: !override` in the overlay, or drop host publication
entirely and let `cloudflared` reach `http://mcp:8080` on the compose network.
Then verify the merged model rather than the YAML, and verify actual listeners
rather than the merged model:

```bash
docker compose -f deploy/docker-compose.yml -f deploy/compose.public.example.yml \
  config --format json | jq '[.services[].ports[]?] | all(.host_ip == "127.0.0.1")'
sudo ss -ltnpH | awk '$4 ~ /:(8080|8081|8100)$/'
```

Every listener must be `127.0.0.1` or `[::1]`. Then prove it from a second LAN
machine and from off-network, because "bound to loopback" and "nothing forwards
to it" are two different claims and only the first is in this repo.

### B-2 — Public read-only mode fails open

**Severity: high — raised by F-15/F-16.** A fail-open flag does not just publish
an indexing service; it publishes an unrestricted SSRF probe into the home
network (F-15) and a file-deletion primitive (F-16). The base `mcp` service passes exactly four variables
(`deploy/docker-compose.yml:23-27`). Without the overlay,
`VIDTHEQUE_PUBLIC_READONLY` is absent, defaults false, and `hidden_tools(False)`
registers `index-video` and `tag-video` with no credential in front of them —
a public, anonymous indexing service. The runbook §6.1 documents this, which is
good, but documentation is not a control: the safe posture is opt-in, and the
dangerous one is the default.

It is worse than a missing overlay, because `_bool_env` (`config.py:75`) coerces
every unrecognised value to false rather than rejecting it. It normalises with
`.strip().lower()` and accepts `{1, true, yes, on}`, so `True` and a trailing
space are fine — but `Y`, `t`, `enabled` or `2` all silently mean false, and so
does a **misspelled key name**, because an absent variable takes the default.
There is no boot-time complaint in any of those cases.

*(Corrected after the verification pass: an earlier draft of this section named
`True` and a trailing space as failing examples. They do not — `_bool_env`
normalises both. The finding stands on the absent-key and unrecognised-spelling
paths.)*

**Fix.** Two changes, both small:

1. `_bool_env` rejects unrecognised non-empty values instead of coercing to
   false. A typo becomes a boot failure, not a public write surface.
2. A boot-time invariant in `app.py`: refuse to start when `AUTH=none`,
   `PUBLIC_READONLY` is false, and `PUBLIC_HOSTNAME` names a non-loopback host.
   That combination has no legitimate use.

`scripts/dev_stack.sh` does not have the env-plumbing problem (it uses
`set -a`), but it does have the typo problem, and it is what runs today.

### B-3 — The daily ask budget can be refunded after OpenRouter has billed

**Severity: high.** `Billing.paid` is set at `public/ask.py:289`, *after* the
upstream `await` returns. A visitor disconnect cancels the generator mid-await;
`CancelledError` derives from `BaseException`, so the `except Exception` at
`:285` does not catch it, `paid` is never set, and the `finally` at `:924`
refunds `ask_global` — while the provider has already generated and billed the
completion.

The docstring at `:150-172` shows this class of abuse was found in the
2026-08-09 review and fixed by moving the flag into `complete()`. That fix
closes the *post*-completion disconnect (wait for the first `activity` line,
then drop) but not the *mid*-completion one, which is the window where the
provider is doing the billable work. The docstring's stated caveat — "nobody can
force [a read timeout] on demand, which is what separates it from the
disconnect" — is the exact assumption that does not hold: a disconnect forces
cancellation at the same point.

Loop this and the 50/day ceiling never decrements. Per-IP `ask` (5/min) is then
the only remaining limit, and B-1 makes that forgeable too.

**Fix.** Reserve the budget *before* dispatching the first upstream request and
refund only requests that provably never reached the provider. Independently:
the provider-side hard cap on a dedicated key, which Tom has already chosen, is
the control that holds regardless of what the code does.

*Confidence note:* the code path is verified; the assumption that OpenRouter
bills a generation whose response was never read is not verified against the
provider. The fix is the same either way, and the provider cap makes the
question academic.

## High findings

### F-1 — Anonymous `/mcp` drives unbounded GPU work

`/mcp` is deliberately never rate-limited (`public/__init__.py:136`), and MCP
`search` computes text and frame query embeddings *before* the DB search
semaphore is acquired (`tools/search.py:333-381`). The worker queue is unbounded
(`worker/lifecycle.py:194`), and a cancelled caller does not dequeue its job. N
concurrent anonymous searches become up to 2N GPU forward passes with nothing
bounding N. Prompt injection in the corpus can amplify this through ask as well:
24 internal tool calls per ask, up to 48 query embeddings.

**Fix.** A public `/mcp` bucket; move search admission ahead of `embed_query`;
give the worker queue a `maxsize` with 503 backpressure.

### F-2 — MCP sessions are created without limit and never expire

`app.py:159` calls `streamable_http_app()` without `stateless_http=True`. The
SDK defaults to stateful sessions, retains a transport and server task per
session in `_server_instances`, and applies no idle timeout. Unauthenticated
`initialize` calls in a loop — on the one route with no rate limit — accumulate
until the LXC dies.

**Fix.** `stateless_http=True`. The app has global dependencies and no
per-session vidtheque state, so nothing is lost.

### F-3 — Ask sends no output-token cap

Neither payload sets `max_tokens` (`public/ask.py:366-372`, `:413-427`). "Under
150 words" is prompt text, not a control. Input is well bounded (question 400
chars, 6 results × 300 chars, context 1200), so the exposure is output-side and
the model's own default is the only ceiling.

Compounding it: `ASK_MAX_ROUNDS=4` yields **five** upstream completions, not
four — four tool-enabled rounds plus a forced final answer at `:412`. Both the
runbook §1.1 and `.env.example:771` state four.

**Fix.** Send an explicit `max_tokens`; truncate carried assistant history;
correct the docs to say five.

### F-4 — Demo-mode redaction is enforced on one of two read paths

The dashboard's `_redacted()` drops `source_url`, `error_message` and event
`message` from the jobs view. Three other anonymous paths publish the same
class of data:

| Path | Leaks | Where |
|---|---|---|
| MCP `job-status` | `source_url`, job/item error messages, degraded-stage errors | `tools/indexing.py:444,469,479,491` |
| MCP `corpus-summary` | stored `video_stages.error` strings | `tools/library.py:399` |
| `/dashboard/videos/{id}` | every stage's `model_key` and raw `error` | `dashboard/views.py:678` |

The chain is complete and needs no guessing: `/dashboard/jobs` renders job IDs
as visible links (`templates/jobs.html:87`) — the public projection keeps IDs
deliberately — and `job-status` is `readOnlyHint=True`
(`tools/descriptions.py:204`) so `readonly.py` does not mask it. A visitor reads
an ID off the dashboard, calls `job-status` through the public `/mcp`, and
recovers exactly what the dashboard redacted. The tool *knows* about read-only
mode — `deps.hint()` prints different copy for it — just not for redaction.

Runbook §2.5's greps cannot catch this: they only ever read dashboard HTML. The
strings they hunt for (`cookiefile`, `player_client`, `/home/`) are precisely
what yt-dlp error text carries.

Also on this surface: `tools/base.py:121` publishes raw embedding-worker
exception text in a search `note:`, and `:184` returns arbitrary exception text
as `E_INTERNAL`. Both can carry the internal worker URL or operator paths.

**Fix.** Gate the source/error prose in `tools/indexing.py` and
`tools/library.py` on `deps.offers("index-video")`; pass `_redacted(request)`
into `_stage_rows`; log exception detail server-side and return fixed wording.

## Medium and low findings

- **F-5 — Frame variants are a CPU oracle.** `variant_key`
  (`http/derived.py:65`) includes both `w` and `q`, so the clamps admit
  1,217 × 76 = **92,492 distinct cache keys per frame** — ~320M across the
  corpus, not the five widths §2.6 reasons about. Each miss is a full decode
  plus re-encode with `optimize=True` on the request path, against a 256 MiB
  LRU. One IP at 120/min sustains ~2 encodes/sec indefinitely. *Fix:* quantize
  public requests to the five real widths and a small quality allowlist.
- **F-6 — The budget's persistence queue can lose many charges.** Writes are
  queued and failures are logged and discarded (`ratelimit.py:206-210,266-279`).
  §9's "at most one ask" is wrong; the loss is the whole queued burst, and each
  failed-write-then-restart cycle restores up to 50 reservations.
- **F-7 — Secrets are distributed wider than needed.** `env_file: .env` hands
  `OPENROUTER_API_KEY` *and* `TUNNEL_TOKEN` to both `mcp` and the worker
  (`compose.public.example.yml:34,47`); `dev_stack.sh:29` does the same via
  `set -a`. Both are then visible in `docker inspect` and `/proc/*/environ`.
  Also: `deploy/cloudflared-credentials*.json` is not covered by `.gitignore`.
  *Verified clean:* no committed `.env`, no `.env` in history, no tracked
  key-shaped value.
- **F-8 — An upstream error body is logged raw.** `ask.py:305` logs
  `response.text[:200]`. A misconfigured or hostile upstream that echoes the
  request can copy the prepaid key into `mcp.log`.
- **F-9 — The default compose tunnel ignores the audited ingress file.** The
  shipped service runs a *remotely*-managed tunnel from `TUNNEL_TOKEN`
  (`docker-compose.yml:91`); `cloudflared.example.yml` — which is fail-closed,
  single-hostname, no wildcard, no TCP/bastion, terminal `http_status:404` — is
  not read on that path. An operator can review the safe file in the repo and
  deploy dashboard-managed ingress that says something else.
- **F-10 — Containers run as root on floating tags.** Neither Dockerfile
  declares `USER`; `cloudflared` uses `:latest`; base images are tag-pinned, not
  digest-pinned, against CLAUDE.md's stated rule. The worker downloads model
  weights at runtime by name with no revision hash. *Python deps are exemplary*
  — `uv.lock` with hashes, `uv sync --locked`. CI is clean: GitHub-hosted
  runners, ordinary `pull_request`, no `pull_request_target`, no self-hosted
  runner.
- **F-11 — One unvalidated URL scheme.** `templates/video.html:57` puts stored
  extractor `video.url` straight into `href`. Autoescape prevents attribute
  breaking but not `javascript:`. Low exploitability on a fixed YouTube corpus,
  but it is the one data-built link that skips the `http(s)`-only rule the
  browser code follows.

## Second pass — the surfaces the first three reviewers left unaudited

A fourth reviewer at extra-high reasoning took the six surfaces tracks A/B/C
each declared out of scope. Two are serious; three are clean, which is worth
recording so nobody re-audits them.

### F-12 — The worker is an unauthenticated resource-exhaustion service

**Severity: critical, and it escalates B-1.** B-1 established the worker's port
is published on `0.0.0.0`. This is what a stranger who reaches it can do:

- `api.py:157` spools an arbitrarily large transcription upload to a tempfile
  with **no byte or duration limit**.
- `api.py:263` and `:387` read as many as 64 unbounded uploads **fully into RAM
  before admission control runs** — image embedding and OCR.
- `api.py:205` and `:338` cap item *count* on the text endpoints but not
  characters or tokens.
- `app.py:89` installs **no authentication and no request-size middleware** at
  all.
- `api.py:420` publishes model IDs, VRAM and queue state on `/status`.

Huge multipart uploads, decompression-bomb images, long audio, or 512 enormous
strings will fill the LXC's temporary storage, exhaust host RAM before GPU
serialization can help, monopolize the 3090, and thrash model loading. No path
was found from here to the prepaid API key or to corpus mutation — availability
and the box itself are the damage.

**Fix tonight:** remove the worker's host `ports:` publication entirely, or bind
it to `127.0.0.1`. Do not expose 8081 until it has authentication and
byte/pixel/duration/token caps. This is the same fix as B-1 and it is why B-1 is
critical rather than merely wrong.

### F-13 — Inline frames are a memory and uplink amplifier

**Severity: high.** `get-frames return="image"` ships with
`inline_frame_max=4` and `inline_frame_bytes=6 MiB`
(`config.py:142,253-254`). Base64 turns 6 MiB of raw JPEG into roughly an 8 MiB
MCP response, with further raw, encoded, string and JSON copies alive in memory
at once. The cumulative byte check runs **after** `path.read_bytes()`
(`tools/frames.py:137-141`), so the file is fully resident before the cap is
consulted, and `w`/`q` do not resize inline images.

`/mcp` has no rate limit, so a hostile client loops `return="image"` with four
known IDs and turns a home uplink and the LXC's heap into the bottleneck.

**Fix tonight, one env var:** `VIDTHEQUE_INLINE_FRAME_MAX=0`, which forces the
URL fallback. That is the documented default posture for this deployment anyway
— the frames-by-URL rule in CLAUDE.md exists because Claude Code mangles MCP
`ImageContent`. Inline base64 is for an owner's agent, not anonymous traffic.

### F-14 — Unbounded strings are parsed, copied, and echoed back whole

**Severity: medium.** `parse_corpus_time` (`timeparse.py:57`) runs an arbitrary
string through `strip`, `lower`, regex and ISO parsing, and a malformed value is
then **echoed back in full** in both the text and structured error content.
`split_csv`/`validate_tag` (`text.py:194`) accept an unbounded tag string the
same way. `published_after="A"*10_000_000` is several multi-megabyte copies plus
a multi-megabyte error response, on an unlimited route.

Separately, `parse_offset("NaN")` succeeds — the negative-only check does not
reject non-finite floats (`timeparse.py:101`) — and surfaces later as
`E_INTERNAL`.

The regexes themselves are sound: no nested ambiguous quantifiers, no
catastrophic backtracking found. `middle_truncate` slices code points safely,
though it can split a grapheme or emoji — cosmetic, not corruption.

**Fix:** a short pre-parse length limit, `math.isfinite`, and never echo more
than a short prefix of a rejected value.

### Clean, and checked specifically

- **The three MCP resource URIs** (`tools/resources.py:193,242`,
  `tools/__init__.py:384`) are three literal, parameterless URIs. No URI
  component reaches a filesystem path or a SQL parameter, so traversal,
  templating and invented `vidtheque://video/...` reads cannot expose anything
  else. They publish up to 200 newest corpus rows, operational counts, feature
  flags and version — all intended.
- **`get-segment-context`** (`tools/segment.py:23,42`) clamps the window to
  5–300 s, transcript text to 20,000 chars, OCR to eight frames / 1,200 chars,
  frame refs to 12, and never reads image files. The documented
  `max_text_chars=0` opt-out stays inside a 600 s window.
- **`corpus-summary`'s state path** (`tools/corpus_state.py:52,181`) does
  aggregate reads over at most 25 active job rows and publishes counts, status
  and the next deferred time — **not** job IDs, errors, paths or URLs. (The
  separate `include_gaps` leak in F-4 is a different code path.)

## What the front end got right, and must keep

Track C found **no executable rendering sink**. Jinja autoescape is
unconditional with no `|safe`, no `Markup`, no disabled block
(`dashboard/render.py:129`). Demo results, OCR text, snippets, answers, activity
lines and `<mark>` highlights are built with text nodes and DOM elements, not
HTML strings (`public/static/app.js:32,39,170,780`). Frames are always
`image/jpeg`. No permissive CORS middleware.

**The V5 rebuild must preserve these mechanisms and their regression tests.**
Every value on those pages is attacker-influenceable in principle — titles,
channel names, transcript text, OCR text — and the current safety is entirely a
property of *how* they are rendered. A rewrite that swaps DOM construction for
template-string HTML reintroduces XSS everywhere at once.

## The ingest pipeline — and what B-2 actually costs

Audited because the demo is one flag from exposing `index-video`, and because
everything ingested is later fed to an LLM and rendered publicly. Findings are
tagged **[EXPOSED]** (needs `index-video` reachable) or **[CONTENT]** (true even
when Tom indexes hostile media himself).

**This section changes B-2's severity.** B-2 said a fail-open flag yields "a
public anonymous indexing service". It yields more than that: an SSRF probe into
the home network and a file-deletion primitive.

### F-15 — Unrestricted SSRF through the submitted URL [EXPOSED]

**Severity: critical if `index-video` is ever reachable.** The complete
validation is:

```python
_URL = re.compile(r"^https?://", re.I)      # tools/indexing.py:36
```

`normalize_url` (`:47`) accepts a bare 11-char YouTube id, and otherwise returns
**any** `http(s)` URL unchanged. Its own error text advertises "supported:
youtube.com / youtu.be video, playlist and channel URLs" — a host allowlist the
code does not implement. The YouTube host list in `pipeline/sources.py:335`
exists only for local identity lookup; unknown hosts are deliberately handed to
yt-dlp (`runner.py:336,397`).

So `http://169.254.169.254/latest/meta-data/`, `http://127.0.0.1:8100/…`, or any
attacker URL that redirects to one, is fetched by the box. There are no DNS, IP
or redirect-hop checks. Playlist children are appended without revalidation
(`runner.py:278`, `store.py:536`) and extracted subtitle URLs are fetched
directly (`sources.py:612`). `file://` is rejected; internal HTTP is not.

**Fix:** enforce exact HTTPS YouTube forms before queueing, revalidate every
expanded child, require a YouTube extractor result, and block private,
link-local and loopback addresses at every DNS resolution and redirect.
Network-level egress denial is the non-bypassable version.

### F-16 — A remote extractor id becomes a glob, and retention deletes what it matches [EXPOSED]

**Severity: high.** `parse_info` accepts any non-empty extractor id with no
grammar check (`sources.py:398`). It is interpolated straight into filesystem
paths and **glob patterns** (`paths.py:39,80`, `sources.py:703`), and retention
unlinks every glob match (`runner.py:1201`).

A video served from `https://attacker.example/*.mp4` makes yt-dlp's generic
extractor derive the id `*`. `media_candidates("*")` then matches every file in
`media/`, and the retention pass deletes all of them. Percent-encoded slashes
produce traversal-shaped ids the same way. Ids have no length or unicode bound.

**Fix:** reject ids outside a source-specific grammar — YouTube is
`[A-Za-z0-9_-]{11}` — and reject separators, glob metacharacters and dots
universally. Replace glob lookup with the exact filename yt-dlp returns, and
prove every write and delete stays beneath `DATA_DIR`.

### F-17 — No download, duration, pixel or stage deadline [CONTENT + EXPOSED]

**Severity: high, and live today.** Downloads set no byte or duration cap
(`sources.py:632,654`) and the height selector falls back to unrestricted
`bv*/b` (`:691`). Keyframe detection **decodes the entire video** before the
600-frame budget applies (`keyframes.py:218,375`), and candidate frames are
width-capped only after decoding (`:443,568`). yt-dlp and decode run via
`to_thread` with no hard deadline (`runner.py:522,871`); cancellation happens
only between stages. The STT timeout grows without ceiling from the remote
`duration_s` (`worker_client.py:363`).

This one needs no attacker — a multi-hour, high-bitrate upload pins download,
full-video decode, disk and CPU on a box Tom is indexing with tonight.

### F-18 — Hostile text is persisted verbatim [CONTENT]

Titles, descriptions, channel fields, chapters, caption text, chunks and OCR
text are stored with no content or length limits (`sources.py:406`,
`store.py:49,227,262,368`). Megabytes in a single cue, bidi and control
characters, and "ignore prior instructions" in an OCR line all land in the DB —
and from there into embeddings, the ask prompt, and the public page. Writes are
correctly parameterised; this is a taint finding, not SQL injection.

### F-19 — Job fan-out exceeds its advertised cap [EXPOSED]

`max_items` clamps to 200 (`tools/indexing.py:88`) but each of ten roots
independently appends up to 200 children (`:83`), so one request can create
~2,010 items. Make the cap transactional and job-wide.

### Clean — and this one is genuinely well built

**Subprocess and argument construction is sound.** No scoped file imports
`subprocess`, uses `shell=True`, or builds a yt-dlp CLI argv. The URL is passed
as a Python argument to `extract_info(url, download=…)` (`sources.py:551`), so a
leading-dash URL cannot become an option; `--exec`, `--config-location` and a
caller-supplied `-o` are never configured. Audio args are fixed lists, the video
format is generated from an integer setting, and the output template is a fixed
`%(id)s.%(ext)s`. Titles never reach filenames or arguments. The reviewer also
read the pinned yt-dlp's own ffmpeg wrapper and confirmed `shell=False` with
`file:` prefixes that neutralise leading-dash and protocol-looking filenames.

Also clean: no XML or entity parser and no archive parser is reachable, so there
is no entity-expansion path; PIL only reopens JPEGs the pipeline itself wrote.

## The model supply chain — the one risk that does not need an open port

Every other finding in this audit is gated on reachability. These are not: they
fire on any **cold model load**, including one triggered by an ordinary
anonymous search, and they are true with port 8081 firewalled.

### F-26 — Whisper alignment downloads an unhashed pickle and loads it unrestricted

**Severity: critical, and it is the shipping path.** `align=True` is the default
(`worker/api.py`), and `whisperx_stt.py:214` calls
`whisperx.load_align_model(language_code=…, device=…)` with no revision and no
integrity argument. For en/fr/de/es/it, WhisperX selects torchaudio bundles;
torchaudio downloads from `download.pytorch.org` **without requesting hash
checking or weights-only loading**, and PyTorch's `hub` defaults are
`check_hash=False` and `weights_only=False` — which then calls `torch.load`, i.e.
unrestricted unpickling of a freshly downloaded file.

The main Whisper model is comparatively clean: faster-whisper fetches JSON plus
a native CTranslate2 `model.bin` and never unpickles or executes hub Python.
It is the *alignment* path — the reason this worker exists, since word
timestamps are the product — that carries the risk.

**What it takes to exploit:** a compromised upstream artifact, CDN or CA path.
Ordinary network MITM is stopped by TLS. So this is not "a stranger runs code
tonight"; it is "there is no independent integrity check standing between a
third-party artifact and container-root code execution with the GPU attached".

### F-27 — SentenceTransformer repositories can turn safe defaults off

**Severity: critical if the repository is ever compromised.** The Qwen3,
Qwen3-VL and BGE backends load **mutable repository heads** with no revision, no
`use_safetensors` and no `weights_only` (`qwen3_embed.py:75`,
`qwen3_vl_embed.py:268`, `bge_m3_embed.py:56`). SentenceTransformers 5.7 reads a
repository-controlled `sentence_bert_config.json` and, while it strips
`trust_remote_code`, it **preserves `weights_only` and `use_safetensors`** and
passes them to `AutoModel.from_pretrained`. Transformers defaults to the safe
`weights_only=True` but accepts `False` and then calls unrestricted
`torch.load`.

So a compromised or typosquatted repo adds
`model_kwargs: {use_safetensors: false, weights_only: false}`, ships a malicious
`pytorch_model.bin`, and the next cold load executes code as container root.

**Calibrated honestly:** the reviewer checked the live HF trees — Qwen's are
safetensors and BGE's `.bin` currently loads weights-only. This is a
future-update and repository-compromise path, **not** evidence that today's
artifacts are malicious. A remote caller also cannot choose the repository
(`worker/api.py:217` ignores the API `model` field); it can only trigger a load.

**Fix for both:** pin exact commit revisions, force caller-priority
`weights_only=True` and `use_safetensors=True`, verify artifact digests, and
load pre-fetched local snapshots offline.

### F-28 — What an RCE there would actually reach

No `USER` is set and the CUDA base is tag-pinned, not digest-pinned
(`worker/Dockerfile:15`); the service has no read-only root, no `cap_drop`, no
`no-new-privileges` and no resource limits, and mounts a writable persistent
`/hf-cache` (`docker-compose.yml:78`). So model RCE gets container root,
writable application files and `/tmp`, **persistent HF-cache poisoning**,
environment and hook credentials, container-network and outbound access, and the
GPU device surface.

It does **not** get host root or host files automatically, and the `/data`
volume is mounted only into MCP, not the worker — no Docker socket anywhere.
That is a meaningful limit and worth recording.

### Also in the worker, and reachable only via 8081

- **F-29 (high)** — the queue is unbounded (`lifecycle.py:194`) with no
  admission deadline, and **cancellation does not remove queued work**
  (`:432`): the consumer never checks the caller's future before loading and
  running. For STT, the handler deletes the tempfile in `finally`, so a stale
  queued job loads the model and only then fails on the missing file. There is
  no inference timeout, and `to_thread` cannot terminate a hung native call.
- **F-30 (high)** — request-controlled work bypasses VRAM accounting: admission
  estimates the configured 256-patch budget while a request may select up to
  4096 and inference honours it (`api.py:257`,
  `siglip2_image_embed.py:112,178`). Images are also `Image.open().convert()`ed
  before any pixel cap (`qwen3_vl_embed.py:514`, `siglip2_image_embed.py:329`),
  so 64 compressed uploads allocate far more than their upload size suggests.
  Failed loads never `_unload` (`base.py:421`).
- **F-31 (medium)** — the GPU lease hooks run via `create_subprocess_shell`
  (`gpu.py:98`), but **nothing request-derived is interpolated**, so this is not
  HTTP command injection. The real issues are that acquire failure embeds the
  **complete command** in a returned 503 (`gpu.py:100`, `app.py:134`), leaking
  any inline token; the timeout kills only the shell PID, not its process group;
  and admission probes VRAM *before* running the acquire hook (`:543`), so if
  llama.cpp holds the memory the hook exists to free, admission rejects forever
  without ever invoking it.
- **F-32 (low)** — `/docs`, `/redoc` and `/openapi.json` are enabled
  (`app.py:89`) and publish every route, schema and advertised cap; `/status`
  adds exact model IDs, VRAM totals, queue depth and lease flags.

**Clean:** tempfile handling — `mkstemp` gives an exclusive 0600 file, the
untrusted filename contributes only a suffix, cleanup is in `finally`, and no
traversal path exists. RapidOCR's model ID is cosmetic; it constructs packaged
ONNX and never passes the ID. **No worker caller sets `trust_remote_code=True`.**

## The auth package — nothing here ships tonight, and none of it is ready

**Scope note, read this first.** The box ships `AUTH=none`, so **every finding
below is inert on day one**. They are gated on `token` or `oauth`, which is one
env var away and which `deploy-public.md` §1.1 says to re-audit before enabling.
This is that re-audit, done early. Nothing here changes the ship decision; all
of it changes the "should we add a private surface later" decision, and the
answer today is **not without work**.

### The structural flaw: the scope model is decorative

`credential.py:45` **discards the `AccessToken` claims**, and `:73` equates any
valid bearer with the owner. `modes.py:96` requires only `READ_SCOPE` for the
entire MCP endpoint, and `frames.py:201` accepts any valid JWT without checking
scope at all.

So a client that obtained consent for **`vidtheque:read` only** can call
`index-video` and `tag-video`, or bearer-authenticate a dashboard write. That is
a textbook confused deputy, and it is the same category error the threat-model
pass identified elsewhere: `readOnlyHint` is a database word doing an
authorization job, and `vidtheque:read` is a scope word doing no job at all.

**Fix:** preserve the token claims and enforce `WRITE_SCOPE` per write tool and
route, `READ_SCOPE` on frames and reads.

### The rest, ranked

- **HIGH — the authorization UI is clickjackable and consent is CSRF-able.**
  `login.py:52` sets neither `frame-ancestors` nor `X-Frame-Options`; the login
  and consent POSTs (`:85`, `:128`) have no Origin or CSRF check. `SameSite=Lax`
  does not stop a hostile same-site sibling. An attacker who obtains an `rq` can
  frame `/auth/login?rq=…` or auto-submit `decision=allow`, and receives a code
  whose PKCE verifier they already know.
- **HIGH — unlimited password guessing.** `login.py:92` compares the password
  and mints a session even with an empty or expired `rq`, and the root limiter
  passes `/auth/*` through. `config.py:269` only requires the password to be
  non-empty.
- **HIGH — unauthenticated permanent disk and memory sinks.** Dynamic client
  registration is public (`modes.py:140`) and every registration persists
  forever; `/authorize` creates pending rows (`provider.py:109`) that are only
  cleaned at startup (`store.py:222`); the CIMD cache never evicts
  (`cimd.py:164`).
- **HIGH/MEDIUM — the SSRF guard is still incomplete even with HTTPS.** Beyond
  the known `allow_insecure` early return (`cimd.py:74`): DNS is checked and then
  independently re-resolved at fetch (`:47`), leaving a rebinding TOCTOU;
  `100.64.0.0/10` and other RFC 6890 special-use ranges are uncovered; and the
  64 KiB limit is applied only **after** `response.content` has buffered and
  decompressed the whole body (`:174`), so a compression bomb lands first.
  `follow_redirects=False` genuinely blocks redirect pivots — only those.
- **MEDIUM — refresh rotation does not detect replay families.** A replayed
  token returns `invalid_grant` but the active descendant survives
  (`store.py:153`), rotation is not transactional, every authorization gets a
  30-day refresh token even without `offline_access` (`provider.py:227`), and a
  revoked access token stays usable for up to an hour (`:272`).
- **MEDIUM — code and pending-request "take" operations are not atomic**
  (`store.py:109,209`): separate SELECT and DELETE, safe only because one event
  loop serializes them today.
- **LOW** — mix-up protection is incomplete on error responses (`iss` omitted,
  `state` concatenated unencoded, `login.py:136`); metadata advertises client
  auth methods that do not match what is implemented (`metadata.py:45`,
  `cimd.py:126`); non-ASCII credentials reach string-form
  `hmac.compare_digest` and raise `TypeError` → 500 (`login.py:95`,
  `modes.py:45`, `tokens.py:143`); `auth.db` mode is left to umask and stores
  plaintext session IDs and client secrets (`store.py:73`, `provider.py:84`).

### Clean, and checked

PKCE is **mandatory, S256-only and verifier-bound**; redirect-URI validation is
exact with no substring or open-redirect path; code-to-client and token-endpoint
redirect bindings are present. JWT verification fixes HS256 and checks
signature, exact issuer, audience and expiry — no algorithm or key confusion.
The frame signer's input is unambiguously newline-delimited, HMAC is
144-bit-truncated SHA-256, constant-time compared, and covers the clamped tuple.
Default secret generation is strong, persistent and mode 0600, with domain
separation between access, frame and refresh keys. Login rotates the session ID,
so no fixation. With `allow_insecure=False`, IPv4-mapped IPv6 and
decimal/octal/hex IPv4 encodings all canonicalise and are blocked.

## The data layer — and the disconnect primitive

### F-20 — A cancelled request returns a SQLite connection that is still in use

**Severity: high, and anonymously reachable.** `ReadPool.run`
(`db/connection.py:175`) returns the connection to the pool in `finally` right
after `interrupt()`, without waiting for the executor thread to exit — and
Python cannot kill a thread, so `asyncio.to_thread` being cancelled does not
stop `fn` running on that connection. The next request pulls the same connection
out of the pool and uses it concurrently. `Writer.run` (`:225`) has the same
shape: `async with self._lock` releases on cancellation while `to_thread` may
still be inside the transaction, so a second writer can meet an already-active
transaction.

The trigger is **a client disconnect during a search** — free, anonymous, and
repeatable.

**This is the audit's most productive attacker primitive, and it is now hitting
two subsystems.** The same disconnect that refunds the day's ask budget (B-3)
also races the connection pool here. Anything that reasons about "the visitor
went away" needs to assume the visitor went away *on purpose, at a chosen
moment*.

**Fix:** run each executor call as a retained, shielded task; interrupt reads;
and await real thread completion before repooling or releasing the writer lock.

### F-21 — A failed COMMIT poisons the only writer until restart

**Severity: high.** `Writer._run_sync` (`db/connection.py:230`) wraps `fn` in
try/except with a ROLLBACK, but `conn.execute("COMMIT")` is **outside** that
block. A disk-full or I/O error at commit propagates with no rollback; if SQLite
leaves the transaction open, every later `BEGIN IMMEDIATE` fails and jobs and
paid-budget accounting stop. Finding F-22 makes disk-full a realistic trigger
rather than a hypothetical one.

**Fix:** one handler across begin/fn/commit, rollback whenever
`conn.in_transaction`, and reopen the connection after fatal I/O errors.

### F-22 — A missing database is silently recreated, and the budget resets to zero

**Severity: high.** `sqlite3.connect` (`:92`) creates a missing file, and
`database.py:71` migrates it as a valid empty deployment. A deleted DB, a failed
volume mount, or a wrong data path boots an **empty corpus with `ask_budget` at
zero** instead of failing closed — so the money guard silently disappears at
exactly the moment an operator is most likely to be improvising. There is no
backup, integrity-check or restore workflow anywhere in the lifecycle.

**Fix:** require an explicit init command or deployment sentinel; refuse to boot
a missing established DB in production. Add snapshots with restore tests.

### F-23 — Older code accepts a newer schema

`migrations.py:90` checks only `user_version == max(known version)`, and `:100`
validates only migration files this binary knows. Rolling back from schema 0006
to a 0005 binary starts cleanly and may read and write incompatible structures.
There is no downgrade path, so refusing is the only safe answer: reject
`user_version > latest_discovered` and require contiguous versions with checksum
audit rows.

### F-24 — One heartbeat exception strands a job until restart

`jobs/runner.py:245` awaits the heartbeat suppressing only `CancelledError`, and
`_active.discard()` happens afterwards; `:212` excludes every `_active` job from
stale recovery. A heartbeat that dies with `OperationalError` — which F-20 and
F-21 both make possible — leaves the id in `_active`, so the batch and its video
claims are stuck until the process restarts. Discard and settle in an
unconditional nested `finally`.

### F-25 — Job history grows without retention

Every event is appended (`jobs/store.py:441`) and nothing prunes `jobs`,
`job_items` or `job_events`. Disk exhaustion then feeds directly into F-21.
Anonymous reads do not drive this growth; repeated reindexing does.

### Clean at this layer

- **Dynamic SQL**: the only input-facing order interpolation selects from a
  complete constant map (`queries.py:1200`) with unknown values falling back
  before execution (`:1271`); LIMIT/OFFSET are bound.
- **Migration atomicity**: one `BEGIN IMMEDIATE` per migration with FK checks,
  version and audit insert before commit (`migrations.py:121`); process death
  rolls back and rerun skips checksummed versions. Migration completes before
  the lifespan yield, so requests are never served mid-migration in one process.
- **Claims**: `UPDATE … RETURNING` claims atomically, and a partial unique index
  prevents two queued items for one video.
- **Anonymous reads**: read connections are `mode=ro` plus `query_only`, and all
  query-layer DML is isolated to the explicit tag-write function (`:1948`).
- **PRAGMAs**: WAL, `synchronous=NORMAL`, 10s busy timeout and foreign keys are
  requested on every connection — though `:85` ignores the returned values, so
  nothing verifies WAL is actually active. `synchronous=NORMAL` also permits
  losing recent budget charges on power loss; use FULL if that matters.
- **Job ids** are `secrets.token_hex(6)` — 48 bits. Fine today because ids are
  listable and therefore not secrets; move to 128 bits before they ever become
  capabilities.

## The security model itself

A pass that read the design docs rather than the code, and asked whether the
invariants are load-bearing or aspirational. Its findings are structural: most
of the individual findings above are instances of them.

### Where the model is incoherent

**`readOnlyHint` classifies database intent and is used as an authorization
classifier.** A read tool can spend GPU, money, CPU, memory, disk cache, uplink,
sessions and reputation. `demo-site.md` §1.1 makes that annotation decide what
public mode registers. F-1, F-5, F-12 and F-13 are not four accidents; they are
the same category error four times — every one is a "read" tool allocating a
scarce resource.

**"`AUTH=none` is a content decision, not a security posture" is false.**
`deploy-public.md` §2.2 argues it well and the conclusion is still wrong: the
mode removes caller identity, disables frame signing, decides abuse attribution,
and exposes every costly read capability anonymously. Those are security
properties, not content ones.

**The XSS model treats OCR as adversarial; the ask model treats the same OCR as
evidence.** Rendering is structurally safe — text nodes, no HTML strings. Prompt
injection is answered by "answer only from tool results", which is *precisely* a
prompt-only control, in a project whose CLAUDE.md invariant is "server-side
clamps, never prompt-only".

**Citation integrity is not claim integrity.** The server proves `[3]` names a
retrieved record. It does not prove the sentence beside `[3]` follows from it.
The demo's whole promise is receipts, and the receipt only covers the pointer.

**Redaction is presentation-specific rather than data-classification-driven.**
Each view carries its own denylist instead of receiving an already-public-safe
projection. F-4 is the predictable consequence, and it will recur at the next
new view.

**"Token discipline everywhere" is true per response and false across
concurrency, sessions, repeated pagination, worker inputs and provider output.**
Every resource finding in this audit sits in one of those omitted dimensions.

**The product requires good bots and has no identity with which to tell them
from bad ones.** Browser challenges break MCP; IP limits are weak identity; with
no credential there is no durable quota, no revocation and no attribution. This
is the general form of the Bot Fight Mode conflict recorded above.

**There is no private control plane.** To index, the runbook stops the tunnel
and flips the flag that restores write tools for everybody
(`deploy-public.md` §9). The operational workflow is itself the proof.

**One process serves public traffic, owner management, spending and worker
control**, chosen to preserve SQLite's single-writer simplicity and to avoid a
second auth story (`dashboard.md` §2.2). That choice collapsed the boundary that
now matters most.

### Trust boundaries that nothing actually enforces

- **mcp → worker**: "HTTP only" is an architecture rule, not a control. There is
  no peer authentication and no capability check. F-12 is what that costs.
- **corpus content → ask prompt**: nothing separates quoted evidence from
  instructions; the system prompt is the only semantic authority.
- **operator env → process**: there is no single validated "public deployment
  profile". Security emerges from orthogonal flags across two launch mechanisms
  plus manual verification. B-2 is what that costs.
- **MCP result → a stranger's agent**: nothing marks transcript and OCR text as
  hostile data. vidtheque can therefore become an indirect prompt-injection
  *source* for someone else's agent holding unrelated credentials — a liability
  that points outward, at users, not at this box.

### The structural change worth considering

Split the anonymous public data plane from the owner control plane: the public
deployment becomes a separate profile with a read-only database snapshot and a
read-only keyframe mount — no SQLite writer, no indexing pipeline, no owner
routes, no raw worker access, no ambient secrets — reaching embeddings and ask
through narrow brokers with size, concurrency and monetary quotas. The owner
dashboard stays LAN-only or behind Cloudflare Access.

It removes whole classes of reliance on correct flags: a typo cannot expose the
writer when the writer is physically absent, and compromising the public
renderer yields neither the tunnel credential nor the GPU API nor a mutable
index. The cost is real — a second deployable, snapshot freshness, explicit
internal APIs, more monitoring — and it does not solve prompt injection or model
truthfulness. What it does is convert those into bounded demo failures instead of
possible home-host, credential and corpus-integrity failures.

**Not a ship-day action.** Recorded because the day-one fix list above is a set
of patches on a model that has this shape, and the next audit should start here.

## Which green checkmarks actually mean something

Runbook §2.3 tells the operator to run `make test` plus `test_public.py` and
`test_dashboard.py` and treat green as evidence the public-mode machinery works.
A verification pass checked whether those tests would actually *fail* if the
property regressed.

**Real controls** — these would break loudly:

| Property | Test |
|---|---|
| Write tools unregistered in readonly | `test_public.py:124` — exercises a live `tools/list`, requires the exact read set |
| Dashboard write routes absent | `test_dashboard.py:1379` — credentialed requests must 404; a route-inventory test catches undeclared additions |
| Anonymous gets public clamps on both prefixes | `test_dashboard.py:2371`, backed by concrete 50/20 assertions at `:273-300` |
| The `max_text_chars=0` hatch is closed | `test_dashboard.py:2485` — a spy verifies both handlers pass 400, not 0 |
| Jobs-view redaction | `test_dashboard.py:795` — seeded secrets must vanish, with an unredacted-owner contrast |
| `/mcp` is never rate-limited | `test_public.py:393` — four real MCP searches under a one-request limit |

**Cosmetic** — green proves less than it looks:

- **Trusted-IP header** (`test_public.py:358`) tests `client_key()` directly. It
  does not test env resolution, nor that the middleware actually separates
  buckets for two different header values. The end-to-end wiring §4 depends on
  is unverified.
- **"Both prefixes share one handler"** (`test_dashboard.py:254`) compares
  payloads. Equal payloads cannot distinguish one shared handler from two copies
  that happen to agree — so a future divergence would not be caught here.

**Absent entirely** — a refactor breaks these in silence:

1. **Redaction of `source_url`/`error_message` through anonymous MCP
   `job-status`.** No test exists, and the code emits them
   (`tools/indexing.py:444,469`). This is F-4, found independently three times
   now, with nothing in CI standing between it and a regression.
2. **Loopback-only compose publication.** No test references the overlay or the
   rendered port model; `make test` is pytest only (`Makefile:21`). B-1 has no
   CI guard whatsoever.
3. **End-to-end `VIDTHEQUE_TRUSTED_IP_HEADER` → middleware → distinct buckets.**

**And the suspicious rewrite was fine.** §1.1 records that dashboard phase 5
rewrote two phase-1 assertions, which is normally a smell. Checked against
history: `2a40d04` → `bab24ee` moved the anonymous dashboard maxima from 100/50
with policy `owner` to 50/20 with policy `public`. That is a **strengthening**,
reinforced by the new two-prefix matrix and the hatch test — not a quiet
relaxation. Recorded so nobody has to re-derive it.

## Runbook corrections

`docs/deploy-public.md` is accurate on authz and mostly accurate elsewhere. What
is wrong:

| § | Claim | Reality |
|---|---|---|
| 1.1 | `ask_global` is in memory, reset by restart | Stale — migration 0005 persists it |
| 1.1 | Four rounds = four completions | Five; a forced final follows the loop |
| 1.1, 2.6 | `w`/`q` clamps bound the frame surface | They bound values, not cache-key cardinality (F-5) |
| 1.1, 2.5 | Redaction "fixed, not accepted" | True for the dashboard jobs view only (F-4) |
| 2.2 | "Nothing that can write exists" | An anonymous frame GET writes and evicts cache files |
| 9 | A hard kill loses at most one ask | The queue is unbounded (F-6) |
| 9 | The row records spend | It records *observed* successes; billed-then-refunded work is invisible (B-3) |
| 9 | The table never grows | Pruning is boot-only; a long-lived process adds a row per UTC day |
| 4 | Origin is tunnel-only | Not as the artifacts ship (B-1) |

Verified correct and worth keeping: the whole `AUTH=none` reasoning; the derived
read-only mask; either-flag-disables-writes; credential-keyed clamps with an
empty CIDR list; frame path containment; the 421 `PUBLIC_HOSTNAME` trap; SQL
binding and FTS lexing; per-minute bucket maths and refund flooring; search's
expensive legs bounded independently of `limit`.

Gaps in §1.1's own checklist: it enumerates `/dashboard` and `/dashboard/api/*`
but not `/dashboard/videos*`, `/dashboard/jobs*`, `/dashboard/api/jobs*`,
`/dashboard/static/*`; it names `/mcp` but not the three public resource URIs or
the session lifecycle; it never audits public *tool* error text.

## Edge defenses — a decision that changed

Tom chose a WAF rate-limiting rule plus Bot Fight Mode. Two verified constraints
change that picture:

**Bot Fight Mode should not be enabled.** It is zone-wide, cannot be scoped to a
path or hostname, and **cannot be skipped by WAF custom rules or Page Rules**
because it runs outside the Ruleset Engine. It issues CPU-intensive JS
challenges to non-browser clients. `/mcp` exists so strangers can point
non-browser agents at it — that is the product. Enabling it breaks every agent
client, and there is no free-plan exemption. Super Bot Fight Mode (Pro) is what
offers granularity.

**The free plan gives exactly one rate-limiting rule**, matching path and
verified-bot only, counting by IP only, with a fixed 10-second window and
10-second mitigation. That is burst protection, not a budget — and one rule
means one path. Spend it on `/api/ask`, the only path that costs money.

Turnstile on `/api/ask` remains the strongest available control against
automated spend, and it touches only the endpoint that spends. It needs
front-end work on a page that is mid-rebuild, which is the argument for
deferring it — but it is the right answer once V5 settles.

Sources: <https://developers.cloudflare.com/bots/get-started/bot-fight-mode/>,
<https://developers.cloudflare.com/waf/rate-limiting-rules/>

## Fix order for ship day

Nothing below §1 of the runbook should start until the first three land.

1. **B-1 + F-12** — drop the worker's host publication entirely and
   `ports: !override` the rest, then verify listeners from a second machine.
   These are one fix and it is the most urgent thing on the list.
2. **B-2** — strict `_bool_env` + the boot-time invariant.
3. **B-3** — reserve the budget before dispatch; set the OpenRouter hard cap on
   a dedicated key regardless.
4. **F-13** — `VIDTHEQUE_INLINE_FRAME_MAX=0`. One env var, do it tonight.
5. **F-2** — `stateless_http=True`. One line.
6. **F-3** — `max_tokens`; fix the four-vs-five docs.
7. **F-4** — gate tool-side error prose on the read-only predicate.
8. **F-1** — `/mcp` bucket and worker queue bound.
9. **F-5** — quantize frame variants.
10. **F-14** — pre-parse length limits and `math.isfinite`.
11. F-7 through F-11 as time allows; none gate the URL.

Additions to the §7.5 sharing checklist:

- [ ] merged compose model shows loopback-only ports (B-1)
- [ ] `ss -ltnp` shows no wildcard listener for 8080/8081/8100
- [ ] worker unreachable from a second LAN host *and* from off-network
- [ ] Proxmox/LXC firewall, router forwards, UPnP and IPv6 checked by hand
- [ ] `job-status` through the public `/mcp` shows no source URL or error text
- [ ] OpenRouter dedicated key has a hard spend cap set, verified in the console
- [ ] Bot Fight Mode **off**; the single WAF rate-limit rule points at `/api/ask`

## Method

Three read-only Codex (gpt-5.6-sol, high effort) reviewers on fenced scopes:
exposed surface and authorization; abuse, spend and the OpenRouter key;
deployment, secrets and the browser surface. Each was briefed on this specific
deployment — home LXC, Cloudflare Tunnel, `AUTH=none`, a prepaid key, a public
`/mcp` — and asked to judge severity for it rather than by generic instinct.
The orchestrating session independently verified B-1 (`docker compose config`),
B-3, F-2, F-3 and F-4 rather than relaying them, and researched the Cloudflare
constraints.

**Second round (later the same night).** Five more reviewers at extra-high
reasoning, each in its own pane, on areas the first round declared out of scope:
the `auth/` package as if token/oauth were live; the ingest pipeline; the worker
and its model supply chain; the data and job layers; and the security model
itself. Findings F-15 through F-32 and the two model sections come from that
round. The orchestrating session independently verified F-15, F-16, F-20, F-21
and F-26 against source rather than relaying them.

Everything is now covered at least once, with one deliberate exception: no
reviewer has audited the **front-end V5 rebuild**, because it did not exist when
this ran. The XSS mechanisms it must preserve are recorded above; re-check them
against the finished pages before the URL is shared.
