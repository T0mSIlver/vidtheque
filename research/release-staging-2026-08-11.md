# Release staging — the fresh LXC, the tunnel, the move (2026-08-11)

Written overnight 2026-08-10→11 by the release-staging agent, on Tom's order
"**infra staged, launch together**": everything below is research and
preparation. **Nothing here was executed. Nothing is public.** The companion
deliverable is `BEFORE-SHIP.md` at the repo root — the ordered morning list;
this document is the reasoning behind it.

**What this is not.** It is not a replacement for `docs/deploy-public.md`,
which is the go-public *checklist* (auth surfaces, clamps, rate limits,
hostname mapping, tunnel commands, smoke tests, rollback) and stays the
contract for the day itself. This document answers the questions that runbook
assumes are already settled: **which box does it run on, how does the corpus
get there, and what has to be true before the first public request.** Where
the two overlap, `docs/deploy-public.md` wins and this file points at it by
section number.

Ground truth consulted: `research/HANDOFF-2026-08-08.md` (box, architecture),
`docs/design/demo-site.md` §1 (the intended production combination),
`docs/design/index-schema.md` §0/§5/§6 (what state is, how to back it up),
`deploy/.env.example` (document of record), `scripts/dev_stack.sh` (the
migration story as designed), `HANDOFF-2026-08-10.md` / `HANDOFF-2026-08-11.md`
(the deferred security items).

---

## 0. The recommendation in one page

**Topology: split (B).** Build **one** fresh unprivileged LXC that runs the
**mcp** service only — CPU, no CUDA, no GPU device nodes, no HuggingFace cache,
no agent tooling — and leave the **worker** where it already works, reached
over the host bridge on a private address. The `mcp/ ↔ worker/ is HTTP only`
invariant is not a workaround here, it is the thing that makes this legal, and
the public box then contains exactly what the public touches: one SQLite file,
one directory of JPEGs, one uvicorn process.

**Why not the obvious "one fresh LXC with everything":** the single
highest-variance step on launch morning is GPU passthrough into a *new*
container (driver/userspace version match, device-node gids under an
unprivileged container, the nodes disappearing across a host reboot). It is a
2–4 hour problem when it goes wrong and a 20-minute one when it goes right, and
you cannot know which until you try. Splitting removes it from launch day
entirely, at the cost of one firewall rule.

**The finding that shapes everything else** (§2): a public search **calls the
GPU worker at query time**, twice, on a 20-second timeout, through a single
FIFO GPU queue with no priority. So:

1. **Indexing must be stopped before cutover** — the rolling-tranche standing
   order has to end. A whisperX stage in flight makes every visitor's search
   wait up to 20 s and then silently degrade to FTS-only.
2. The worker is a **hard dependency of public search quality**, which is an
   argument for a firewalled private link, not for co-locating it with the
   public surface.
3. `EMBED_RESIDENT` / `IDLE_UNLOAD_SECONDS` become a **user-facing latency
   decision** on launch day, not a VRAM housekeeping one.

**Three things found while writing this, that nobody had written down:**

- **The job runner starts in public read-only mode too** (§5.2b). Read-only
  unregisters the write *tools*, not the *runner*, and a stale claim is
  re-claimable after 300 s — so a database migrated with live queue rows has
  the **public** box fetching from YouTube behind the tunnel. There is one job
  `running` right now.
- **Browser Integrity Check is ON by default on the Cloudflare free plan**
  (§6.5) and challenges *"visitors … with a non-standard user agent"* — which is
  every MCP client and half the smoke tests. It is scopable, and it must be
  scoped **before** the smoke tests or they will fail for an edge reason.
- **`/static/lab/*` is publicly reachable** (§9): 13 MB of unreleased landing
  prototypes answer at `https://<host>/static/lab/versions/v5.html` the moment
  the tunnel opens.

**Domain: a subdomain of `tomvaucourt.com`.** Verified tonight by DNS: that
zone is already served by Cloudflare nameservers (`diva`/`thaddeus.ns.
cloudflare.com`) and already proxied, so the zone is Active and
`cloudflared tunnel route dns` will work immediately. `vidtheque.dev` is
**still unregistered** (no SOA, no NS) — registering it on launch morning adds
a registrar step and a nameserver-propagation wait (minutes to hours) to the
critical path, for a name nobody has seen yet. Ship on the subdomain; buy the
`.dev` at leisure and add it as a second hostname later.

**Six things gate the first public request** — the full ordered list with owners
and durations is `BEFORE-SHIP.md`; the short version:

1. Tom's security branch merged (`G1`).
2. The trusted-CIDR/tunnel question **answered**, not just warned about (`G2`,
   §6.4).
3. The organiser-consent email sent (`G3`) — the positioning contract makes it
   load-bearing, and 100% of the public corpus is one channel.
4. `/static/lab/*` off the public surface (`G4a`, §9 finding 1).
5. The removal path we promised in public actually existing, at least as a
   documented procedure (`G4b`, §9 finding 2).
6. The rolling indexing order stopped **and the queue verified empty** (§2.2,
   §5.2b).

---

## 1. Measured starting state — 2026-08-11, ~01:10 Paris

Everything in this table was measured tonight on the box, not estimated,
unless the row says otherwise. It is here so the morning can diff against it.

### 1.1 The corpus

| | value | how |
|---|---|---|
| videos `ready` | **191** | `select index_state, count(*) from videos group by 1` |
| videos `indexing` / `pending` | 1 / 1 | same query — tranche 7 in flight |
| distinct channels | **1** (AI Engineer) | `count(distinct channel_name)` |
| duration indexed | **67.0 h** | `sum(duration_s)` over `ready` |
| transcript cues | **43,549** | `count(*) from cues` |
| chunks | **7,034** | `count(*) from chunks` |
| keyframes | **11,781** | `count(*) from keyframes` |
| OCR lines | **184,301** | `count(*) from ocr_lines` |
| ask budget spent today | 5 / 50 | `ask_budget` row for `2026-08-10` |

One channel is a positioning fact, not just a number: the contract says the
demo is "the first shelf, not the library"
(`research/positioning-2026-08-10.md` §5 open question 4). It is also the
reason the organiser-consent email is a gate rather than a courtesy (§9.1 of
the same doc) — 100% of the public corpus belongs to one organiser.

### 1.2 The disk

| path | size | verdict for the move |
|---|---:|---|
| `/home/dev/vidtheque-data/vidtheque.db` | **195 MB** | **must move**, and never as a plain file copy (§5.3) |
| `…/vidtheque.db-wal` | 1.1 MB | do not copy; checkpoint instead |
| `…/keyframes/` (191 dirs) | **1.4 GB** | **must move** — this is `get-frames`, the evidence |
| `…/audio/` | **1.4 GB** | optional; insurance against takedowns + re-STT |
| `…/media/` | 76 MB | **do not move** — scaffolding, deleted after indexing |
| `…/derived/` | 7.8 MB | **do not move** — disposable LRU cache, rebuilds |
| `…/run/` | 11 MB | **do not move** — pidfiles + 10 MB of `mcp.log` |
| `…/stack.env`, `secret.key`, `openrouter.env` | 12 KB | **move by hand, reviewed** (§5.4) |
| **essential total** | **~1.6 GB** (db + keyframes) | |
| **with `audio/`** | **~3.0 GB** | |
| `~/.cache/huggingface` | **13 GB** | only if the worker moves |
| `/home/dev/work/vidtheque/.venv` | **7.8 GB** | GPU venv — only if the worker moves |

`docs/design/index-schema.md` §6.1's projection for 500 videos is 420 MB DB +
~3.9 GB keyframes; at 191 videos we are tracking it almost exactly (195 MB,
1.4 GB). Nothing about the move gets cheaper by waiting.

### 1.3 The current box

- `systemd-detect-virt` → **`lxc`**. The "fresh LXC" is therefore a **sibling
  container on the same Proxmox host**, not a new class of thing. The GPU
  already reaches this container by bind-mounted device nodes
  (`/dev/nvidia0`, `/dev/nvidiactl`, `/dev/nvidia-uvm`,
  `/dev/nvidia-uvm-tools`, all mode `crw-rw-rw-`), exactly as
  `research/HANDOFF-2026-08-08.md` describes.
- Debian **13 (trixie)**. This matters for cloudflared: `pkg.cloudflare.com`
  carries a public-key rollover notice that names Trixie
  (`docs/deploy-public.md` §6.2). Whatever template the new LXC uses, the
  install path has to be checked against that page, not copied from memory.
- 10 cores, 16 GB RAM (5.2 GB used, 3.6 GB of 8 GB swap in use), 134 GB disk
  at **72% used, 36 GB free**. Load average 10.12 — this box is a working
  sandbox, and that is the argument for §3.0.
- RTX 3090, driver **550.163.01**, CUDA 12.4, **8,165 MiB / 24,576 MiB in
  use** at the time of writing (llama.cpp's co-tenancy is real).
- Listening sockets include **`0.0.0.0:8200`** (the landing-versions preview
  server) and `0.0.0.0:3773`, plus `*:22`. None of those belong on a public
  box; naming them is half the reason to build a new one.

### 1.4 The names

| check | result | consequence |
|---|---|---|
| `dig NS tomvaucourt.com` | `diva.ns.cloudflare.com`, `thaddeus.ns.cloudflare.com` | zone already on Cloudflare, already Active |
| `dig A tomvaucourt.com` | `188.114.96.2`, `188.114.97.2` | Cloudflare-proxied today |
| `dig A vidtheque.tomvaucourt.com` | *(empty)* | the hostname is free |
| `dig SOA vidtheque.dev` | *(empty)* | **still unregistered**, as on 2026-08-08 |
| `dig MX tomvaucourt.com` | *(empty)* | no mail on the zone — the organiser email will come from wherever Tom's mail already lives |

---

## 2. The constraint that decides the topology: a public search calls the GPU

This is the finding that was not written down anywhere before tonight, and it
reorders the whole plan.

### 2.1 Query-time embedding is on the request path

`mcp/src/vidtheque_mcp/embeddings.py` is the worker-facing embedding client.
`tools/base.py` calls it **twice** for a `content_type=all` search — once into
the text space (`POST /v1/embeddings`, `input_type=query`) and once into the
frame space (`POST /v1/embeddings/frame-query`) — because the unified
`Qwen3-VL-Embedding-2B` is instruction-aware and the two legs want different
instructions (`tools/base.py:73-87`).

The budget is **20 seconds, no retry**
(`HTTPEmbeddingClient.__init__(timeout_s=20.0)`, and `HTTPWorkerClient` keeps
that for `input_type != "document"` on purpose — `worker_client.py:365-390`:
"a search would still rather answer FTS-only in 20 seconds than wait two
minutes for a model to load"). On timeout the leg is skipped and the payload
carries a `note:` — which is the right behaviour and also a completely silent
one from a visitor's point of view.

**Correction, from the codex review (§10, finding 5): the ceiling is ~40 s, not
20.** The 20 s applies *per call*, and the two calls are sequential, so a
`content_type=all` search against a dead worker burns two full timeouts before
it starts the SQLite work — and `VIDTHEQUE_QUERY_TIMEOUT_S=30` does not cover
it, because that deadline starts afterwards. Forty seconds of spinner and then a
quietly worse answer is a materially different launch-day picture from twenty,
and it is the number to plan the smoke test around.

### 2.2 The GPU queue is a single FIFO with no priority

`worker/src/vidtheque_worker/lifecycle.py`: "**a single consumer task drains
one job queue**, so ten concurrent HTTP requests become ten sequential GPU
jobs instead of an OOM." There is one `asyncio.Queue`, unbounded, and no
priority class — a 15 ms query embedding queues behind whatever is in front of
it. In front of it, during indexing, is whisperX: `VIDTHEQUE_STT_TIMEOUT_S` is
**1800**, and the per-item budget scales with duration.

**Therefore: a public search issued while a talk is being transcribed waits up
to 20 s and then comes back FTS-only.** Not an error, not a 503 — a slower,
quieter, worse answer. This is the single worst launch-day failure mode
available, because it looks like the product being bad rather than the box
being busy.

And it is not enough to *decide* not to index: the job runner starts on the
public box as well, unconditionally — see §5.2b, which is the sharpest edge in
the migration and was found while writing it.

**And the queue is not only the indexer's.** Codex's review (§10, finding 7)
makes the point this section had understated: `worker/api.py` submits STT, text
embedding, image embedding, frame-query **and CPU OCR** to that same FIFO. So
"the SQLite job queue is empty" is not the same claim as "the GPU queue is
empty". The gate wants the worker's own view:

```bash
curl -s http://<worker>:8081/status | jq '{queue, loaded: [.slots[]?.loaded]}'
# depth 0, nothing in flight, the consumer alive
```

### 2.2b The queue is reachable by strangers, with no limiter in front of it

**The most serious thing the review found (§10, finding 3), and it is not a
staging problem — it is a shape problem that launch day exposes.**

Three shipped decisions compose badly:

- `/mcp` is **deliberately never rate limited** (`test_public.py:386`) — correct
  on its own terms, since bucketing a long-lived session breaks the transport;
- under `AUTH=none` **anyone** can open an MCP session and call tools;
- each `search` with `content_type=all` submits **two jobs** to an **unbounded**
  GPU queue (`lifecycle.py:342` — "Unbounded queue: `put` never awaits").

`VIDTHEQUE_MAX_CONCURRENT_SEARCHES=2` does not save this: the semaphore in
`tools/search.py` guards the *SQLite* work and the embedding happens **before**
it. So a stranger with a loop can keep the GPU queue arbitrarily deep, and the
victims are every other visitor's search *and* the shared 3090 — which under
topology B is the development box's GPU.

This is not a reason to change the topology (topology A would put the same
attacker on the same card, with the public surface co-resident). It is a reason
to (a) know it before the URL is shared, (b) consider a Cloudflare rate-limiting
rule matching `/mcp` as the only available brake, and (c) put "bound the worker
queue, or charge MCP tool calls against *something*" on the audit list. Related
and cheap: `lifecycle.submit` never removes an already-cancelled job from the
queue and `_execute` runs it before checking whether the caller is still there
(finding 6), so abandoned requests still spend the GPU afterwards — which is
exactly what turns a burst into a lasting outage.

**Operational rule, promoted to a gate:** the corpus is **frozen** before the
tunnel goes up, and the queue is *verified* empty rather than assumed empty. The rolling-tranche standing order (`HANDOFF-2026-08-10.md`,
16:55) ends; the queue drains; `job-status` shows nothing running.
`docs/deploy-public.md` §9 already says indexing while public needs the flag
flipped and the tunnel stopped — this adds the *reason it is not merely a
tidiness rule*.

### 2.3 The cold-load cliff is a visitor-facing number

Measured in `research/multimodal-embedding-2026-08-09.md` §"VRAM: it fits":

| | |
|---|---:|
| load, warm HF cache | **3.5 s** |
| first call after load | **916 ms** |
| steady state | **11–13 ms** |
| weights resident (nvidia-smi view) | **4,318 MB** |

Shipped configuration is `EMBED_RESIDENT=0` and `IDLE_UNLOAD_SECONDS=300`. On
a demo with sporadic traffic — which is exactly what a launch link produces —
**most visitors arrive more than five minutes after the last one**, so the
common case is that the *first search a human ever runs against vidtheque*
pays ~4.4 s of model load before its vector legs return.

Three options, all one line, and the choice is Tom's:

1. **`EMBED_RESIDENT=1`** — 4.3 GB standing on a 24 GB card. Cost is stated
   plainly in `.env.example`: with a resident embedder the GPU lease is
   released only at shutdown, so a llama.cpp co-tenant "is stopped at the first
   embedding request and never restarted". Since `GPU_ACQUIRE_CMD`/
   `GPU_RELEASE_CMD` are **currently unset** in `stack.env`, nothing is
   managing that lease today anyway — the practical cost is 4.3 GB of the
   8.2 GB currently in use by llama.cpp having to give way, once, by hand.
2. **`IDLE_UNLOAD_SECONDS=3600`** — keeps the model for an hour of quiet,
   still frees it overnight. Softer, and it does not touch the lease bracket.
3. **Ship as-is and accept a 4.4 s first search.** Defensible if ask mode is
   off and the page shows a working spinner; indefensible if it is the first
   thing on Hacker News.

Recommendation: **(2) for launch day, (1) if traffic is real by evening.**
(2) changes no invariant and is reversible without a VRAM argument.

### 2.3b The degradation is graceful — and it prints the worker's URL to visitors

Verified in code, because "graceful" is a claim and not a vibe. `tools/base.py`
catches `EmbeddingUnavailable`, appends

> `note: the embedding worker is unreachable ({exc}) — the {leg} leg was
> skipped for this search.`

and returns `None`; the search proceeds on FTS. Nothing 500s. So far so good.

But `{exc}` is `str(exc)` of whatever httpx raised
(`embeddings.py:107`, `:123`, `:134` — `raise EmbeddingUnavailable(str(exc))`),
and the demo page **renders notes to visitors**: `public/api.py:285` passes them
through `humanize.notes(..., demo=True)`, whose demo filter drops exactly one
clause (`AGENT_ONLY_NOTES = ("semantic (nearest-neighbour) legs were not
queried",)`), and `humanize.note()` then just strips the `note:` prefix and
capitalises. `app.js:782,797` renders the result in a muted status line.

A transport failure usually stringifies without a URL ("All connection attempts
failed"). **A non-2xx does not**: httpx's `HTTPStatusError` stringifies as
*"Server error '503 Service Unavailable' for url
'http://10.x.x.x:8081/v1/embeddings'"* — and 503 is exactly what the worker
returns on `InsufficientVRAM`, which is a *routine* condition on a card shared
with llama.cpp.

Today that URL is `http://127.0.0.1:8081` and the leak is cosmetic. **Under
topology B it becomes the sandbox container's private address**, printed on the
public demo page, on an ordinary busy-GPU day. That is a small finding with an
easy fix (sanitise the exception into a fixed sentence before it reaches a note)
and it belongs in the joint audit rather than in a launch-morning scramble — but
it should be *known* before the tunnel opens, not discovered from a screenshot.

### 2.4 What this does *not* threaten

Query-time GPU is only the **vector** legs. FTS5, the OCR text leg, browsing,
`/frames/*`, the dashboard and every MCP read tool are pure SQLite + JPEG and
survive a dead worker with a `note:`. `/healthz` reports `vector_legs` from the
database's own view (`http/health.py:21`), so it will keep saying `true` even
when the worker is unreachable — **do not use `/healthz` as the worker probe**;
use the worker's own `:8081/healthz` and `/status`.

---

## 3. The topologies

### 3.0 Option 0 — don't move at all

Keep serving from this LXC, point the tunnel at `127.0.0.1:8100`.

**Cost: 0. Rejected anyway**, and the reasons are specific rather than
hygienic. This container currently holds: five git worktrees with concurrent
agents writing to them, a 7.8 GB GPU venv per worktree, `gh` credentials, an
SSH server, a preview HTTP server bound to `0.0.0.0:8200` serving the
unreleased landing prototypes, a HuggingFace cache, `~/backups`, and ten logged-in
sessions. `docs/deploy-public.md` §4 requires "the origin must not be reachable
by any path except the tunnel" for the `CF-Connecting-IP` trust to be sound —
a claim that is genuinely hard to make about this box, and impossible to keep
true while agents keep opening ports on it.

Also: the rollback story for a compromised public box is "delete the container
and restore it", and that is only cheap when the container is *only* the public
service.

### 3.1 Topology A — one fresh LXC, mcp **and** worker, GPU passed through

```
Proxmox host
├── LXC 9000  "sandbox"     dev, agents, worktrees          (no public role)
└── LXC 91xx  "vidtheque"   mcp :8100 + worker :8081        /dev/nvidia* bound
                            cloudflared ──► Cloudflare edge
```

**For.** One box to reason about. The public service owns its own GPU access,
so nothing outside it can be restarted under it. Matches `HANDOFF-2026-08-08`'s
"run the worker beside llama-server in the same LXC" instinct — one container,
one lifecycle.

**Against, and this is the decisive part.**

- **GPU passthrough into a new unprivileged container is the highest-variance
  step of the whole project**, and it is not testable in advance without doing
  it. It is a *solved* problem — see the citations below, it works, unprivileged
  and without nesting — but it is a solved problem with three traps attached,
  and all three are of the "works until it doesn't" family:
  - **Driver userspace must match the host kernel module exactly.** The module
    lives on the host; the container carries only `libcuda`/`libnvidia-ml`/
    `nvidia-smi`. A mismatch gives `Failed to initialize NVML: Driver/library
    version mismatch`
    ([NVIDIA forums](https://forums.developer.nvidia.com/t/failed-to-initialize-nvml-driver-library-version-mismatch-nvml-library-version-535-113/268168)).
    The install is the same `.run` file on both sides —
    `--dkms` on the host, `--no-kernel-modules` inside the container
    ([ProxMenux, PVE 9 / trixie](https://proxmenux.com/en/guides/nvidia-manual/);
    [vLLM-on-PVE-9 writeup, 2026-01-19](https://medium.com/@jakeasmith/running-a-vllm-lxc-on-proxmox-9-f7fbb8a7db2f)).
    Note the flag is **plural** on modern drivers; older guides say
    `--no-kernel-module` and are wrong for 580.x. **This box is pinned to
    550.163.01**, so every GPU-bearing container is coupled to that version:
    Topology A turns a future host driver bump from a two-way reinstall into a
    three-way coordinated one.
  - **`/dev/nvidia-uvm` gets a dynamically allocated major number that changes
    across host reboots** (users report 508 → 505). This is the specific reason
    the old `lxc.cgroup2.devices.allow: c 508:* rwm` +
    `lxc.mount.entry` syntax rots, and the reason the modern `dev[n]` form —
    which addresses the device *by path* and resolves major/minor at container
    start — is the one to use
    ([forum thread, 2023-06 → 2025-03](https://forum.proxmox.com/threads/major-number-for-nvidia-uvm-changes-over-reboot.129238/);
    [`pct.conf(5)`](https://pve.proxmox.com/pve-docs/pct.conf.5.html)).
  - **The device nodes may not exist at all after a bare host reboot.** They are
    created lazily by `nvidia-modprobe` when the first client opens the driver,
    so a container with `onboot: 1` can start before they appear and come up
    GPU-less
    ([PVE 8.4.1 thread, 2025-05-18](https://forum.proxmox.com/threads/nvidia-a4000-lxc-passthrough-pve-8-4-1-intermittent-dev-nvidia-dev-dri-creation-udev-nvidia-modprobe-issues.166377/)).
    The durable fixes are `nvidia`/`nvidia_uvm` in
    `/etc/modules-load.d/` plus **`nvidia-persistenced` enabled on the host**,
    and a `startup: order=…,up=<delay>` on the container.

  Every one of those is a fine problem to have on a Tuesday and a terrible one
  at 10:00 on launch day.
- **Disk.** The worker's world is ~21 GB before any corpus: 13 GB HF cache +
  7.8 GB CUDA venv. Against 36 GB free on the host's current allocation for
  this container, provisioning that for a *second* container is a host-level
  storage question that has to be answered before the build starts, not during.
- **Blast radius.** The public container would hold the model cache and a live
  handle on the 3090. A public surface that can spend GPU is the thing
  `deploy/.env.example` and `docs/deploy-public.md` §4 both warn about ("keep
  the worker off-box entirely — it answers an unauthenticated
  OpenAI-compatible API that will happily spend the GPU").
- It does not remove the co-tenancy question. llama.cpp still lives in its own
  LXC; two containers sharing a card is genuinely fine — the driver runs once
  on the host and N containers bind the same nodes, no IOMMU, no vfio, no
  exclusive lock, documented working at seven services across two GPUs
  ([2026-04-12](https://www.joekarlsson.com/blog/proxmox-gpu-passthrough-multi-service/)) —
  but there is **no VRAM isolation or quota**, so the arithmetic is the same
  either way and the lease hooks stay the only enforcement.

### 3.2 Topology B — split: mcp in a fresh LXC, worker stays **(recommended)**

```
Proxmox host
├── LXC 9000  "sandbox"     dev + agents + WORKER :8081 on vmbr0 addr
│                                    ▲ 2 embed calls per search, ~13 ms
└── LXC 91xx  "vidtheque"   mcp :8100 (loopback) ──┘
                            cloudflared ──► Cloudflare edge
```

The public container holds: the repo, a CPU-only venv, `DATA_DIR`
(SQLite + keyframes), one uvicorn process, cloudflared. No torch, no CUDA, no
device nodes, no HF cache, no credentials beyond the OpenRouter key (if ask
mode ships).

**For.**

- **The riskiest step is deleted, not deferred.** No GPU work on launch day.
- **Small.** ~1.5 GB of venv + ~1.6–3.0 GB of data. A 16 GB disk, 4 GB RAM,
  2–4 cores container. Provisionable from host free space without a
  conversation.
- **The boundary is already the contract.** `CLAUDE.md`'s HTTP-only invariant
  means this is the *designed* deployment shape, not a compromise;
  `deploy/.env.example` already documents pointing `WORKER_URL` at a remote
  OpenAI-compatible endpoint for people with no GPU.
- **Blast radius is minimal and legible**: a compromised public box gets a
  read-only corpus it was already publishing, plus the ability to ask the
  worker for embeddings — which is rate-limitable at the firewall and cannot
  reach the corpus.
- Rebuild is `pct destroy` + 20 minutes.

**Against, honestly.**

- **Public search quality now depends on the sandbox LXC staying up** — the one
  with agents in it. Mitigations: (a) the degradation is graceful and
  documented (FTS-only + `note:`); (b) move the worker to its own LXC as the
  first post-launch chore (§3.3); (c) until then, treat "restart the worker"
  as a production action even though it lives on the dev box, and say so in
  `HANDOFF`.
- **One more hop.** Loopback → bridge. Sub-millisecond on the same host; the
  20 s budget is not remotely threatened.
- **A firewall rule becomes load-bearing.** The worker must listen on the
  bridge address (it currently binds `127.0.0.1` via `dev_stack.sh`) and must
  accept **only** the public container's address. That is a change to a
  hardened default and it needs to be written down where the next person finds
  it — see §4.5.

### 3.3 Topology B+ — the end state (not for launch day)

Same split, but the worker moves into its own fresh LXC too, and the sandbox
leaves the serving path entirely. It costs the GPU-passthrough problem from
§3.1 plus a 21 GB rsync — which is exactly why it is a **calm-afternoon**
task, not a launch-morning one. Doing B first costs nothing toward B+: the mcp
container never learns where the worker is beyond one env var.

### 3.4 Side by side

| | 0: stay | A: one fresh LXC | **B: split** | B+: two fresh |
|---|---|---|---|---|
| launch-day GPU work | none | **passthrough, unproven** | none | passthrough |
| new disk needed | 0 | ~25 GB | **~6 GB** | ~25 GB |
| build time (estimate) | 0 | 2–4 h, tail risk high | **45–75 min** | 3–5 h |
| public box holds GPU | yes | yes | **no** | no |
| public box holds agents/keys | **yes** | no | no | no |
| serving depends on dev box | yes | no | **yes (worker)** | no |
| "tunnel is the only way in" provable | **no** | yes | **yes** | yes |
| rollback | flip flags | destroy container | **destroy container** | destroy container |

### 3.5 Recommendation

**Ship B. Schedule B+.** Two sentences for the record: *the public box should
contain only what the public touches, and on launch morning the cheapest thing
to not debug is GPU passthrough into a container that has never had it.* The
HTTP-only invariant already made this the supported shape; taking it costs one
env var and one firewall rule.

An independent research pass over the current Proxmox/NVIDIA documentation
(dispatched tonight without being told which topology this document favours)
arrived at B for the same three reasons plus one this section had not weighed:
**the driver-version coupling multiplies with every GPU-bearing container.**
Under A, a future host driver bump becomes a coordinated three-way reinstall
(host + two containers) whose failure mode is `NVML: Driver/library version
mismatch` on the public box. Under B it stays the two-way operation this box
already survives.

---

## 4. Building the container

Everything in this section is **to be executed on the Proxmox host by Tom**,
in the morning, together. Agents have no host access and this document does not
pretend otherwise. Commands are written out so the morning is typing, not
deciding.

### 4.1 Create

Sizing, from §1.2's measurements plus headroom for a year of corpus growth:

| resource | value | basis |
|---|---|---|
| rootfs | **20 GB** | OS ~2 GB + CPU venv ~1.5 GB + repo + journald + headroom. **`pct resize` grows online but [cannot shrink](https://pve.proxmox.com/pve-docs/pct.1.html)** — size up, not down |
| `mp0` (the corpus) | **20 GB volume mount, `backup=1`** | 3 GB today; see the bind-mount warning below |
| RAM | **2 GB** | one uvicorn + SQLite page cache + cloudflared. 1 GB is comfortable; 2 GB buys page cache for the 195 MB DB and the hot JPEGs, which is where the latency actually is |
| swap | **0** | swapping a SQLite working set is worse than an OOM kill, and container swap is *host* swap regardless of in-container `vm.swappiness` ([forum](https://forum.proxmox.com/threads/no-swap-in-container.96631/)) |
| cores | **2–4** | search is SQLite-bound; no keyframe/OCR/decode work runs here in demo mode. 4 if the derived-cache resizer gets busy |
| unprivileged | **yes** | Proxmox's own default and position: privileged "should only be used in trusted environments" ([wiki](https://pve.proxmox.com/wiki/Linux_Container)). With no GPU nodes to map there is no counter-argument at all |
| `features` | **none** — explicitly turn `nesting` **off** | `nesting` has defaulted to **on** for GUI-created containers since roughly PVE 8.3, because modern systemd wants procfs/sysfs. Nothing in this stack needs it (§4.3), and it is a weakened-namespace flag on an internet-facing box. Turn it back on only if systemd misbehaves at boot |
| `onboot` | decide, §8.4 | the tunnel either survives a host reboot or it does not; having one of each is the failure |

The template should match the box that produced the data (**Debian 13
trixie**) so that `uv`'s resolved wheels and the system `ffmpeg` behave
identically. A mismatch is not fatal — the venv is rebuilt from `uv.lock`
either way — but launch morning is the wrong time to discover a different
`libc`.

**The bind-mount trap, and it is the one that ruins rollbacks.** Proxmox states
it twice: *"The contents of bind mount points are not backed up when using
vzdump"*, and *"Device and bind mounts are never backed up, as their content is
managed outside the Proxmox VE storage library"*
([wiki](https://pve.proxmox.com/wiki/Linux_Container),
[vzdump](https://pve.proxmox.com/pve-docs/chapter-vzdump.html)). If `DATA_DIR`
is a bind mount from the host, then:

- a container backup does **not** contain the corpus, and
- a `pct rollback` rolls the *application* back while leaving the data at its
  newest state — a silent split-brain.

So: put `DATA_DIR` on a **volume mount point** (`mp0: <storage>:20,
mp=/home/<user>/vidtheque-data`, `backup=1`), not a bind mount, and keep taking
the SQLite snapshot separately (§5.3) regardless — a `vzdump` of a container
with a live WAL is not a substitute for `VACUUM INTO`.

(The inverse is also useful, for Topology B+ later: a HuggingFace cache is
exactly what you *do* want on a bind mount or a `backup=0` volume — 13 GB of
re-downloadable weights that should never enter a nightly. Unprivileged
ownership lines up via the mount point's own `idmap=` option rather than global
`lxc.idmap` surgery; note that per-mount-point idmap is **new** — patches
applied 2026-05-07 — so confirm it exists on the installed PVE version before
designing around it.)

### 4.2 What goes in, and what must not

**In:** `git clone` of the public repo (a clean clone, not an rsync of a
worktree), `uv`, `ffmpeg`, `curl`, `ca-certificates`, `cloudflared`, one
unprivileged service user, `DATA_DIR`, `systemd` units (§4.4).

**Explicitly not in, and worth reading as a list:** no SSH keys that reach
anything else; no `gh`/GitHub token; no agent tooling, no `claude`/`codex`
binaries, no worktrees; no HuggingFace cache; no `/dev/nvidia*`; no
`~/backups`; no `.env` from anywhere (§5.4 builds a fresh one); no second HTTP
server on `0.0.0.0` for previewing anything.

### 4.3 Bare `uv`, not Docker

`deploy/docker-compose.yml` exists and is the reference deployment for other
people. For this box, run bare:

- The live stack has always been `scripts/dev_stack.sh` (bare `uv`), so bare is
  the configuration that has actually been exercised for 1,100+ tests and three
  days of indexing.
- **Proxmox does not support Docker-in-LXC and says so.** Its containers are
  *system* containers; the admin guide's recommendation for application
  containers is "nesting containers inside a Proxmox QEMU VM"
  ([wiki](https://pve.proxmox.com/wiki/Linux_Container)). Compose in an LXC
  wants `nesting=1` and usually `keyctl=1`; the most-repeated forum "fix" for
  the AppArmor fallout is `lxc.apparmor.profile: unconfined`, which removes the
  MAC layer entirely. On an internet-facing container that is the wrong trade,
  and it buys nothing here.
- The compose path carries a live, documented footgun that bare does not:
  `deploy/.env` is compose's *interpolation* source, not the container
  environment, so `VIDTHEQUE_PUBLIC_READONLY` and `VIDTHEQUE_AUTH` sit in
  `.env` **read by nobody** unless `deploy/compose.public.example.yml` is
  overlaid — and the failure is "full read-write mode on a public hostname"
  (`docs/deploy-public.md` §6.1). `dev_stack.sh` sources `stack.env` with
  `set -a`; everything in it reaches the process.

### 4.4 systemd, not `nohup`

`scripts/dev_stack.sh` is a development stack and says so: `setsid nohup`,
pidfiles under `$DATA_DIR/run/`, logs appended to files that nothing rotates
(`mcp.log` is **10 MB after ~one day** of indexing — measured tonight). For a
box that must survive a reboot and a crash unattended, the public container
should run a systemd unit instead:

- `Restart=on-failure` with a `RestartSec` — an unattended crash at 03:00
  currently means the demo is down until someone looks.
- `journald` gives rotation for free, which is the missing piece today (§8.3).
- `After=network-online.target`, `WantedBy=multi-user.target`.
- The env file is the same `stack.env`, via `EnvironmentFile=`.

**And it must set the bind address explicitly — this is the review's finding 1,
and it is a blocker for exactly this path.** `mcp/__main__.py:22-24` defaults to
`VIDTHEQUE_HOST=0.0.0.0` and `VIDTHEQUE_PORT=8080`. `dev_stack.sh` never relies
on those defaults — it passes `VIDTHEQUE_HOST=127.0.0.1` and the port *inline*
on the `env` line, so they never appear in `stack.env` and are easy to lose when
`stack.env` becomes the whole configuration. A unit that just does
`EnvironmentFile=stack.env` therefore produces **`0.0.0.0:8080`**: the tunnel
targets `127.0.0.1:8100` and 502s, and the origin is reachable from the LAN,
where a forged `CF-Connecting-IP` mints a fresh rate-limit bucket per request
and voids every per-IP limit (`docs/deploy-public.md` §4). Put both in
`stack.env`, explicitly:

```sh
VIDTHEQUE_HOST=127.0.0.1
VIDTHEQUE_PORT=8100
```

This is a **new file** (a unit, or two), and by CLAUDE.md's rule any new env
var it introduces needs a `deploy/.env.example` entry — it should introduce
none. Writing the unit is **agent-doable tonight** (it touches nothing any
sibling agent owns) if Tom wants it pre-baked; it is listed that way in
`BEFORE-SHIP.md`.

### 4.5 The private worker link (topology B's one new rule)

**`scripts/dev_stack.sh` cannot express this topology, and that is a blocker
until someone notices** (review findings 1–2). Two hard facts about that script:

- `start` passes `VIDTHEQUE_HOST=127.0.0.1` to the worker **inline**, hard-coded
  on the `env` line — `stack.env` cannot override it, because `stack.env` is
  sourced *before* the inline assignment wins.
- `stop` stops **both** services, and there is no worker-only verb. So "stop the
  old stack" in §5.2c's step 3 takes the worker down too — the worker the public
  box is about to depend on.

So the launch sequence has an unwritten step: after the cutover, the worker must
come back up **bound to the bridge address**, which today means either editing
`dev_stack.sh` (a one-word change, `VIDTHEQUE_HOST` sourced from the environment
instead of pinned) or starting the worker by hand / under its own systemd unit.
Decide which *before* the morning; discovering it between "stop the old stack"
and "the demo is live" is the worst possible time.

With that resolved, the rules are:

1. Worker binds the **sandbox container's bridge address**, not `0.0.0.0` and
   not loopback.
2. Host or container firewall accepts `:8081` **only** from the public
   container's address. The worker answers an unauthenticated
   OpenAI-compatible API; the network is the entire authorization story.
3. Public container's `stack.env` sets `WORKER_URL=http://<sandbox-ip>:8081`.
4. `mcp` still binds `127.0.0.1` — cloudflared is the only thing that talks to
   it, and that is the precondition that makes trusting `CF-Connecting-IP`
   sound (`docs/deploy-public.md` §4).

Verification, from the public container, before the tunnel exists:

```bash
curl -fsS http://<sandbox-ip>:8081/healthz     # {"status":"ok","version":"0.0.1"}
curl -fsS http://<sandbox-ip>:8081/status      # loaded models, VRAM, queue depth
```

and from anywhere else on the LAN, the same two must **fail**.

---

## 5. Moving the corpus

The migration story `scripts/dev_stack.sh` documents in its header — "rsync
`$DATA_DIR`, clone the repo, `make sync-gpu`, run" — is correct and this
section is the version with the sharp edges named.

### 5.1 It is safe to change the path

`keyframes.jpeg_path` is stored **relative to `$VIDTHEQUE_DATA`**
(`index-schema.md:483`), and `http/frames.py:_resolve` joins it to
`settings.data_dir` and refuses anything that escapes the root. So the new
container may mount the corpus anywhere. **Keep `/home/<user>/vidtheque-data`
anyway** — every runbook, every handoff and every muscle memory names it, and
a launch morning is not the moment to introduce a second true path.

`videos.media_path` / `audio_path` are the retention scaffolding, not the
product; `media/` is deleted after indexing by design (`KEEP_SOURCE=audio`).

### 5.2 What moves

```
MOVE      vidtheque.db          195 MB   via snapshot, never a file copy (§5.3)
MOVE      keyframes/            1.4 GB   11,781 files in 191 dirs
MOVE      secret.key            —        see §5.4
DECIDE    audio/                1.4 GB   insurance, not product (§5.5)
SKIP      media/                76 MB    scaffolding
SKIP      derived/              7.8 MB   disposable LRU cache
SKIP      run/                  11 MB    pidfiles + unrotated logs
REWRITE   stack.env             —        §5.4 — do not copy it
```

### 5.2b The job runner starts on the public box, and it will resume your queue

**Found while writing this section, and it is the sharpest edge in the whole
migration.** `app.py` constructs a `PipelineRunner` and starts it whenever
`run_pipeline` is true — which **defaults to `True` and is not tied to
`public.enabled`** (`app.py:82,191`). Read-only mode unregisters the write
*tools*; it does not stop the *runner*. And `VIDTHEQUE_STALE_CLAIM_S=300` means
a job the old process had claimed becomes re-claimable five minutes after that
process stops.

So a database migrated with live queue rows produces this, unattended, behind
the tunnel:

- the public box starts **fetching from YouTube** — the "remote yt-dlp as a
  service" behaviour the design warns about everywhere, now on the box whose
  whole point is that it only reads;
- it puts **whisperX on the GPU queue in front of every public search** (§2.2),
  with the 20-second timeout and the silent FTS-only degradation;
- and it does it from a **new IP** with no bot-check history, which is how a
  fresh address earns one.

Measured tonight, on the live database: **1 job `running`**, with **4 `queued`
items and 1 `running` item**. That is not a hypothetical.

**Before the snapshot** (§5.3), the queue must be genuinely empty, not merely
quiet. **Parking is not enough** — the review's finding 9 is right that pushing
`not_before` into the future leaves the rows *queued*, which contradicts the
empty-queue gate two paragraphs later and leaves a timer pointed at the public
box. Either let it drain, or **cancel terminally**; then verify:

```sql
SELECT state, COUNT(*) FROM jobs      GROUP BY 1;   -- nothing running/queued
SELECT state, COUNT(*) FROM job_items GROUP BY 1;   -- no queued, no running
SELECT index_state, COUNT(*) FROM videos GROUP BY 1;-- no 'indexing', no 'pending'
```

The two `videos` rows currently in `indexing`/`pending` are the same problem
wearing a different hat: a half-indexed video is a video whose `data_status`
will honestly report `degraded` to a visitor on launch day. Either finish it or
leave it out of the snapshot.

### 5.2c Order of operations, and the reason for it

1. **Stop indexing. Drain the queue — and verify it is empty, per §5.2b.**
   (§2.2; also gate 5 in `BEFORE-SHIP.md`.)
2. **Copy `keyframes/` while the stack is still up.** 1.4 GB with the service
   running costs nothing and takes the bulk transfer off the cutover clock.
   **Caveat, from the review (finding 16): "append-only" is an overstatement.**
   A *reindex* deletes and replaces a video's keyframe rows, and the runner
   publishes the new directory after the DB commit — so if the freeze in step 1
   missed an active reindex, a plain `rsync` can leave **stale files** the new
   database no longer references. Two cheap defences: require that step 1's
   verification shows no active stages at all, and make the second pass
   `rsync --delete` rather than additive.
3. **Stop the stack.**
4. **Snapshot the database** (§5.3) and copy the snapshot.
5. **Re-run the `keyframes/` rsync** to catch anything that landed in step 2's
   window. Second pass is seconds.
6. **Verify** (§5.6) before anything else happens.

Host-local transfer between two containers on the same node runs at LVM speed;
budget **~5 minutes** for 3 GB and verify by checksum rather than by clock.
`rsync -a --info=progress2` over the bridge, or a host-side copy if it is
easier — the path does not matter, the verification does.

### 5.3 Never copy `vidtheque.db` as a file

`index-schema.md` §5 is explicit: with WAL, recent transactions live in `-wal`
and a bare copy is a torn database. Two blessed paths:

```sql
VACUUM INTO '/path/vidtheque-2026-08-11.db';   -- 32 ms measured; compacted, consistent
```

or `Connection.backup(target, pages=200, sleep=0.1)` (the online backup API)
when the source is under write load. **`VACUUM INTO` after a clean stop** is
the right choice for a cutover: it is one statement, it compacts, and it is
the "scheduled snapshot" path the schema doc names.

The **existing rollback point already used the online-backup path**:
`/home/dev/backups/vidtheque-2026-08-11/` — 2.8 GB, DB taken with the backup
API, integrity checked, 181 videos ready at snapshot, `chmod a-w`. That is
*rogue-agent insurance from last night*, not a cutover snapshot: it is ten
videos stale. Take a fresh one at cutover; keep the old one until the new box
has served real traffic.

### 5.4 Config: rewrite it, do not copy it

`stack.env` on the sandbox is a **development** configuration and copying it is
the single easiest way to ship the wrong mode. Read tonight, it contains
`VIDTHEQUE_PUBLIC_READONLY` (currently `0`, for indexing), the yt-dlp politeness
and circuit-breaker knobs, `EMBED_RESIDENT`, `VIDTHEQUE_AUTH`, ports, and
`OPENROUTER_API_KEY` **inline**.

Build the new one from `deploy/.env.example` — the document of record — with
the intended production combination from `docs/design/demo-site.md` §1:

```sh
# --- the public container's stack.env ---
VIDTHEQUE_AUTH=none
VIDTHEQUE_PUBLIC_READONLY=1
PUBLIC_URL=https://vidtheque.tomvaucourt.com
VIDTHEQUE_PUBLIC_HOSTNAME=vidtheque.tomvaucourt.com
VIDTHEQUE_HOST=127.0.0.1                          # NOT the 0.0.0.0 default (§4.4)
VIDTHEQUE_PORT=8100                               # NOT the 8080 default (§4.4)
VIDTHEQUE_TRUSTED_IP_HEADER=CF-Connecting-IP      # the default; keep it (§6.4)
VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS=                # MUST be empty (§6.4)
VIDTHEQUE_DASHBOARD=1                             # or 0, per the audit
WORKER_URL=http://<sandbox-ip>:8081
VIDTHEQUE_DATA_DIR=/home/<user>/vidtheque-data
OPENROUTER_API_KEY=                               # day-one posture: empty (§8.2)
```

**The embedder-residency knob is a *worker* variable and belongs on the other
box** (review finding 11). `IDLE_UNLOAD_SECONDS` and `EMBED_RESIDENT` are read
by `worker/`, so setting them in the public container's `stack.env` changes
nothing at all — and the §2.3 latency decision would silently never take effect,
with the smoke test measuring the old behaviour and confirming it.

```sh
# --- the SANDBOX container's stack.env, where the worker actually runs ---
IDLE_UNLOAD_SECONDS=3600      # or EMBED_RESIDENT=1 — §2.3
```

Then run `docs/deploy-public.md` §2.4's key-set diff against `.env.example`,
which is the mechanical check that nothing was invented or dropped.

**Secrets.** `secret.key` (`config.py:299`) is the signing key for tokens and
sessions. Under `AUTH=none` nothing signs anything, so the honest options are
"carry it for continuity" or "let the new box mint a fresh one". **Let it mint
a fresh one** — fewer secrets crossing containers, and no live credential
depends on it. `openrouter.env` should not travel either: if ask mode ships, it
ships with a **new, dedicated, spend-capped key** (§8.2), which also gives the
old one a clean revocation.

### 5.5 `audio/` — take it

1.4 GB, and `index-schema.md` §5 calls it "the cheapest insurance against a
video disappearing: it is the input to STT, so a better model can be re-run
without a re-download". The public box will not re-run STT — but the public box
is about to become the **only** box with the current corpus, and a talk that
gets taken down after launch is unrecoverable without it. Take it. If disk is
tight, take it to the *backup*, not to the container.

### 5.6 Verify before you believe it

On the new box, before the tunnel exists (this is the schema doc's own restore
check, plus the counts from §1.1):

**The obvious version of this is broken in two ways, and the review caught both**
(finding 10): a `<<'PY'` heredoc is quoted, so `$DATA` reaches Python as four
literal characters; and `INSERT INTO cues_fts(cues_fts) VALUES('integrity-check')`
is a **write**, which a `mode=ro` connection refuses. A verification step that
always fails is a verification step that gets skipped under launch pressure.

```bash
DATA=/home/<user>/vidtheque-data          # exported, and the heredoc is UNQUOTED
python3 - <<PY
import sqlite3
ro = sqlite3.connect("file:$DATA/vidtheque.db?mode=ro", uri=True)
print("integrity", ro.execute("pragma integrity_check").fetchone())
print("videos   ", ro.execute("select index_state,count(*) from videos group by 1").fetchall())
print("cues     ", ro.execute("select count(*) from cues").fetchone())
print("chunks   ", ro.execute("select count(*) from chunks").fetchone())
print("frames   ", ro.execute("select count(*) from keyframes").fetchone())
print("ocr      ", ro.execute("select count(*) from ocr_lines").fetchone())
ro.close()
# the FTS integrity command WRITES — it needs a read-write handle, on the copy,
# before anything is serving from it
rw = sqlite3.connect("$DATA/vidtheque.db")
rw.execute("insert into cues_fts(cues_fts) values('integrity-check')")
print("fts ok")
rw.close()
PY

# every row's file exists, and no file is orphaned
find "$DATA/keyframes" -type f -name '*.jpg' | wc -l   # must match the keyframes count
```

Numbers must match §1.1 (adjusted for anything indexed between now and the
cutover). Then the §3.3 vector-drift query from `index-schema.md` — the one
that caught the random-init disaster on 08-10 — and a live spot check:

```bash
curl -s 'http://127.0.0.1:8100/api/search?q=evals&limit=3' | jq '.results[].thumb'
curl -s 'http://127.0.0.1:8100/api/meta' | jq '{auth, ask_enabled, mcp_url, clamps, limits}'
```

**Run the leg check through MCP, not through `/api/search`.** The `Legs:` line
and its structured twin `leg_counts` (`tools/search.py:519,1121`) live in the
*tool's* payload; `grep leg_counts` finds **nothing** in `public/api.py`,
`public/static/app.js` or `dashboard/views.py`, so the facade does not carry
them. A `/api/search` that returns hits proves the FTS leg and says nothing
about the vector leg — which makes it exactly the wrong instrument for the one
thing topology B adds.

So, two different checks:

```bash
# positive proof of the worker link: through MCP, read the Legs: line
uv run --no-sync scripts/mcp_call.py --url http://127.0.0.1:8100/mcp \
  call search '{"q":"what did people say about evals?","limit":3}' | grep -i '^Legs:'
# expect: transcript N (fts a · vec b) — a vec count of 0 everywhere is the tell

# negative check on the facade: the note that would appear if it were broken
curl -s 'http://127.0.0.1:8100/api/search?q=evals&limit=3' | jq -r '.notes[]?'
# expect: nothing about an unreachable embedding worker
```

### 5.7 The rollback point

Before cutover: a fresh `VACUUM INTO` snapshot plus `keyframes/` under
`/home/dev/backups/vidtheque-<date>-cutover/`, `chmod a-w`. Keep last night's
`/home/dev/backups/vidtheque-2026-08-11/` as the older generation.

**The sandbox stack is itself the rollback for the first hours**: it is not
deleted, its data directory is not deleted, and bringing it back is
`dev_stack.sh start`. The new box's failure mode does not have to be repaired
under time pressure — it has to be *abandoned*, which is §8.5.

---

## 6. The tunnel

`docs/deploy-public.md` §6 is the procedure and it is current as of 2026-08-09.
This section is what staging adds, plus the items that need a decision rather
than a command.

### 6.1 Named tunnel, locally managed

Confirmed choice, for the reason the runbook gives: ingress in the repo is
greppable and diffable. `deploy/cloudflared.example.yml` is the template; the
three placeholders are the tunnel UUID, the credentials path, and the hostname,
and the service URL for topology B is `http://127.0.0.1:8100` — cloudflared
runs **in the public container**, beside mcp.

**Do not test with a quick tunnel.** `trycloudflare.com` does not support SSE
(runbook §6.5) and `/mcp` *is* an SSE transport, so a quick tunnel would report
the product as broken.

### 6.2 The ingress file, staged

Copy `deploy/cloudflared.example.yml` → `~/.cloudflared/config.yml` in the
public container and fill it. Validate before running:

```bash
cloudflared tunnel ingress validate --config ~/.cloudflared/config.yml
cloudflared tunnel ingress rule --config ~/.cloudflared/config.yml https://<host>/mcp
cloudflared tunnel ingress rule --config ~/.cloudflared/config.yml https://<host>/dashboard
```

The optional `^/dashboard → http_status:404` rule stays commented **unless the
audit decides the dashboard is not public** — note that it is a *regex*, not a
prefix, and it must come **before** the catch-all rule to have any effect.

### 6.2b The apt install has two traps on trixie, and one of them is undocumented

`docs/deploy-public.md` §6.2 already prints the right commands. Two things
sharpen it, both verified 2026-08-10:

1. **There is no `trixie` suite in the cloudflared apt repository.**
   `https://pkg.cloudflare.com/cloudflared/dists/trixie/Release` **404s**;
   `dists/any/Release` and `dists/bookworm/Release` both return 200. Any guide
   (or model) that tells you to write `… /cloudflared trixie main` produces
   *"The repository '… trixie Release' does not have a Release file"*. The
   runbook's line already says **`any`** — keep it, and do not "fix" it to the
   codename.
2. **The signing key rolled on 2025-10-30 and the old keys were removed on
   2026-04-30.** Trixie is named in the notice on `pkg.cloudflare.com`
   specifically because Debian 13 replaced GnuPG with **`sqv` (Sequoia)** as
   apt's OpenPGP verifier, and sqv rejects SHA-1-bound signatures — which is
   what the old Cloudflare key produced. A **fresh** install is fine; a machine
   that configured the repo before the rollover must re-download the keyring.
   Since this container will be built this morning, this is a non-issue *as
   long as nobody copies a keyring across from the sandbox.*

### 6.3 Streaming: the thing that must not regress, and the thing that might still bite

**What the runbook says, and it is right about the outcome.** Cloudflare Tunnel
buffers a proxied response unless the origin sends
`Content-Type: text/event-stream`; that is why `/api/ask` grew an SSE framing on
2026-08-09 (`demo-site.md` §3.5, runbook §7.3). Nothing in `originRequest`
controls buffering, and `X-Accel-Buffering: no` is an nginx directive
cloudflared does not honour. The fix was the media type, and the verification is
the timestamp-spread test in runbook §7.3 through the *named* tunnel with the
response `Content-Type` confirmed first.

**What tonight's research adds — the documented sentence is narrower than the
code, and there is a second buffer nobody had named.**

`cloudflared`'s own `connection/connection.go` flushes on **any** of three
conditions, not one: **no `Content-Length`**, **`Transfer-Encoding: chunked`**,
or a content type prefixed by one of `text/event-stream`, `application/grpc`,
**`application/x-ndjson`**. So `application/x-ndjson` is *already* in
cloudflared's flushable list — which means the buffering the ask stream hit on
2026-08-09 was probably **not cloudflared's**. That does not make the fix wrong
(it is verified empirically and it is the shape the MCP transport uses anyway);
it means the *mechanism* recorded in the contract may be misattributed, and the
real one is still live:

- **Cloudflare added Request/Response Body Buffering as Configuration Rules
  settings on 2026-01-27.** `Response Body Buffering` defaults to `Standard`,
  which lets products *"inspect a prefix of the request body when necessary"*;
  `None` streams *"directly to the client without inspection"*, with an explicit
  warning that it *"may impact the effectiveness of the WAF"*. This is the best
  available explanation for the persistent community reports of SSE being held
  until roughly **100 KB** accumulates despite correct headers.
- **Consequence for launch day:** an ask answer that is short streams fine; the
  first one that pushes ~100 KB of activity events might not. Mitigations:
  **`/mcp` already has heartbeats** — the review checked the pinned
  `sse-starlette` and it emits a 15-second comment ping, so that lever is
  already pulled on the transport and only `/api/ask` would need its own — then
  try a Configuration Rule setting `Response Body Buffering: None` on
  `/api/ask` and `/mcp` —
  **plan availability on Free is undocumented, so this is an experiment, not a
  step.** Free gets 10 Configuration Rules.
- **Compression is a trap for JSON, not for SSE.** Cloudflare compresses
  `application/json` by default; `text/event-stream` and
  `application/x-ndjson` are not on the list. So the SSE path is safe by
  content type, and any future streaming endpoint labelled
  `application/json` would need `Cache-Control: no-transform` — the documented
  escape hatch, and it must come from the origin.
- **Do not set `disableChunkedEncoding`.** The runbook already says so, for the
  right reason (it is a WSGI option and we are ASGI); the sharper reason is that
  chunked encoding is **one of the three flush triggers**, so disabling it would
  force buffering to compute a `Content-Length`.
- **`--protocol` stays `auto`.** Over QUIC the flush logic is bypassed entirely
  (`quic_connection.go`'s `Flush()` is a documented no-op because writes go
  straight onto the stream). Pin `http2` only if QUIC handshakes prove unstable;
  note that after falling back to HTTP/2 cloudflared never retries QUIC, and
  that a Linux box may want `net.core.rmem_max` raised or the log fills with
  *"failed to sufficiently increase receive buffer size"*.

**The one open bug that should shape the MCP smoke test.**
[cloudflared #1449](https://github.com/cloudflare/cloudflared/issues/1449)
(open since 2025-04-12, unanswered) reports **SSE over GET buffered until the
connection closes, while POST streams fine** — reproduced with FastAPI +
`sse-starlette`, with correct `cache-control: no-store`, and the reporter
concluding the delay is *after* the origin. MCP Streamable HTTP uses **POST**
for request/response (the path that carries tool results — reported working) and
an optional **GET** for the server-initiated listening stream. So: **test the
GET listening stream explicitly** and be prepared to find that a client which
opens it sees nothing. It is not a blocker — tool calls ride the POST path — but
it is a real "works on my box, silent through the tunnel" candidate and it is
not in anybody's checklist today.

**Timeouts, current numbers** (Cloudflare's connection-limits table): Proxy Read
Timeout **125 s** (→ 524; Enterprise-only to raise), Proxy Write Timeout 30 s,
TCP connect 19 s (→ 522), proxy idle 900 s, URL 16 KB, request **and** response
headers 128 KB each (raised from 32/16 KB on 2025-10-16), request body 100 MB on
Free and Pro (→ 413). `VIDTHEQUE_ASK_TIMEOUT_S=90` sits inside 125 deliberately.
The widely-repeated "100 seconds" is the old figure.

### 6.4 The trusted-CIDR footgun — the correct production config

This is the deferred security item from `HANDOFF-2026-08-10.md` that "needs
Tom", and it is a **pre-launch gate**, not a note.

The mechanism: `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS` grants **owner** status on
the **socket peer address**. Behind a tunnel the socket peer is *cloudflared*,
not the visitor. So any CIDR that covers loopback or the container's own
network makes **every anonymous visitor an owner** — owner clamps on both API
prefixes, and with them the `max_text_chars=0` full-transcript hatch that
`demo-site.md` §2 reserves for an owner's agent.

**The correct production value is empty, which is the shipped default.** The
whole of the fix, for launch day, is:

```bash
grep -E '^VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS=.+' $DATA/stack.env   # must print NOTHING
```

plus the boot-log check and the from-outside behaviour check from
`docs/deploy-public.md` §1.1. The boot-time warning that shipped on 08-10
(`dashboard/settings.py:123`) fires when the allowlist overlaps loopback/RFC1918/
ULA **and** `VIDTHEQUE_TRUSTED_IP_HEADER` is set — with the variable empty it
stays quiet, which is how you know the config is right rather than merely
unwarned.

What still needs Tom's decision is the **deeper fix** deferred from the 08-10
review: whether the code should *refuse* proxy-origin CIDRs outright, or
require a credential when a trusted header is present. That is a design change
and it belongs in the joint audit; it is not required to launch, because
"empty" is both the default and the answer.

### 6.5 Rate limiting and WAF posture for a public MCP endpoint

The application already limits: search 30/min, ask 5/min, frames 120/min,
dashboard 120/min per IP, ask 50/UTC-day server-wide (persisted to SQLite by
migration 0005, so a redeploy no longer hands the money back). `/mcp` is
**deliberately never limited** (`test_public.py:386`) — an MCP session is a
long-lived stream, and bucketing it would break the product.

That last fact is the reason to be conservative at the edge. The list below is
"what breaks a programmatic client", and it is ordered by how likely it is to
ruin launch day.

**1. Browser Integrity Check is ON by default on Free, and it is aimed at us.**
This is the finding that most nearly cost the launch and was in nobody's
checklist. BIC *"challenges visitors without a user agent or with a
non-standard user agent"* — which is the exact description of an MCP client, a
`curl` smoke test, and most agent HTTP stacks. Unlike Bot Fight Mode it **is**
scopable: a **Configuration Rule** setting *Browser Integrity Check: Off* for
`/mcp`, `/api/*` and `/frames/*` is the fix, and Free gets 10 Configuration
Rules. Do this **before** the smoke tests, or half of §8.1 will fail for a
reason that has nothing to do with the application.

**2. Bot Fight Mode: leave OFF, and understand that it cannot be scoped.**
Cloudflare's own docs say *"You cannot bypass or skip Bot Fight Mode using WAF
custom rules or Page Rules"* — it runs outside the Ruleset Engine, so
Configuration Rules and Skip rules cannot reach it — and warn that these
products *"may challenge API or mobile app traffic"*. It is whole-domain only,
with no path scoping and no exceptions, and it force-enables JavaScript
Detections. A Python MCP client has no browser fingerprint and no clearance
token, so it gets a 403 or an HTML challenge body where JSON was expected. The
scopable version (Super Bot Fight Mode) is Pro+.

**3. Under Attack mode / a high Security Level: OFF for anything programmatic.**
*"Challenge Pages interrupt the request flow by returning a full HTML page… This
mechanism fails when the browser expects a non-HTML response"*, and the
recommended remedy (Turnstile pre-clearance) is impossible for a headless
client. If it is ever needed, scope it with a Configuration Rule to `/` and
`/dashboard` and never to `/mcp` or `/api/`. Related and useful: the legacy
threat score is now *"always 0"*, so every old "set Security Level to High"
guide is inert.

**4. Never add a request-header Transform Rule on this hostname — and know that
one of them ships as a one-click Managed Transform.** *"Remove visitor IP
headers"* removes `cf-connecting-ip`, `x-forwarded-for` and `true-client-ip`,
and it is **available on all plans**. Enabling it makes the limiter silently
fall back to the cloudflared socket address: the whole internet in one bucket,
with no error anywhere (runbook §4). Same class: **Pseudo IPv4 must stay off** —
in `Overwrite Headers` mode Cloudflare *"overwrites the existing Cf-Connecting-IP
and X-Forwarded-For headers with a pseudo IPv4 address"*, i.e. the rate-limit
key becomes a hash that collides across IPv6 visitors.

**5. The edge limiter is a backstop and a weak one.** On Free: **one** rate-
limiting rule, counting characteristic **IP only**, period **10 s**, mitigation
timeout **10 s**, no custom response body; the match expression can reference
the path but the counter cannot be *keyed* on it, and every counter is
implicitly per-datacenter. Also, a 90-second SSE request counts as **one**
request at open — edge rate limiting protects against connection floods, not
against long-hold concurrency. **The in-app limiter and the persisted ask budget
remain the real controls**, which is the right outcome anyway: the app's typed
`E_RATE_LIMIT` body with `retry_after_s` is a better thing for a visitor to
receive than an edge block page.

**6. Caching, and one consequence worth naming out loud.** Cloudflare caches by
**extension**, not content type: `.jpg` is cached by default with a 120-minute
edge TTL when the origin sets no cache headers, and HTML and JSON are **not**
cached by default. Our frames already send `Cache-Control: public,
max-age=86400`, and the default cache key includes the **full query string**, so
the `?w=…&q=…` variants cache separately and correctly.

That is mostly good news — the edge absorbs frame traffic and largely dissolves
the derived-cache thrash concern in `docs/deploy-public.md` §2.6 for *public*
traffic. But it has a flip side the audit should record: **an edge cache hit
never reaches the origin, so it is never charged to
`VIDTHEQUE_RATE_FRAMES_PER_MIN`.** `demo-site.md` §5's guard is "slow enough to
be pointless, not impossible"; once a crawler has warmed the edge, a second
crawler walks the corpus at edge speed with the per-IP limiter never seeing it.
The corpus is public keyframes of public talks, so this is very likely an
**accept**, not a fix — but it should be an accept that was written down rather
than one nobody noticed.

Do **not** add a zone-wide "Cache Everything" rule. Leave Rocket Loader,
Hotlink Protection and HTTP/3 off (HTTP/3 purely to keep one variable out of the
streaming debugging). Leave **0-RTT off** — only GET/HEAD/OPTIONS ride as early
data, and MCP opens its listening stream with a GET, exactly the replayable
class. Leave the Free Managed Ruleset and Email Obfuscation **on**: the first
cannot be disabled anyway, the second is HTML-only and never touches JSON.
Hold in reserve, deploy only on an observed false positive: a WAF custom rule
with the **Skip → all managed rules** action for `/mcp` (Free gets 5 custom
rules).

### 6.5b The terms-of-service clause, corrected

`docs/deploy-public.md` §9 cites *"Cloudflare's general Terms §2.8 on non-HTML
content"*. **Section 2.8 no longer exists** — it was deleted in May 2023 and
Cloudflare's own announcement says they *"got rid of the antiquated HTML vs.
non-HTML construct, which was far too broad."* The live Self-Serve Subscription
Agreement runs 2.1 → 2.7 and then Section 3.

The clause actually in force is in the **Service-Specific Terms**, under
*Content Delivery Network (Free, Pro, or Business)*, and reserves the right to
limit CDN access if it is used *"to serve video or a disproportionate percentage
of pictures, audio files, or other large files"*, with *"reasonable efforts to
provide you with notice"* first. No numeric threshold is published, and there is
no documented bandwidth cap for the Free plan (Cloudflare's 2024 commitment post
still says "unmetered bandwidth on the Free plan", with the carve-out that
"large assets (like videos) are not supported").

**Why this matters for a video product specifically:** serving keyframe JPEGs is
not the target of that clause; **proxying or re-serving source video bytes
would be.** vidtheque already deletes the source media after indexing and cites
`youtu.be/ID?t=…` rather than re-hosting — so the ToS-safe posture and the
product design are the same posture. Keep it that way, and update the runbook's
§9 citation, which is now three years stale.

### 6.6 cloudflared inside the container — three ways it looks fine and isn't

cloudflared itself is unremarkable in an LXC: a static Go binary, a systemd
unit, no privileges, no nesting, no device access. The known failures are all
operational, and two of them present as a healthy green `systemctl status`.

1. **`cloudflared service install` can write the wrong `ExecStart`.** Reported
   2026-02-03: the unit came up `active (running)` while actually running in
   DNS-over-HTTPS proxy mode (`cloudflared --config …/config.yml`) instead of
   `cloudflared tunnel run <id>`, so the tunnel was never established and
   nothing looked wrong
   ([community-scripts/ProxmoxVE #11501](https://github.com/community-scripts/ProxmoxVE/issues/11501)).
   **Read the actual `ExecStart` in `/etc/systemd/system/cloudflared.service`;
   do not trust the green dot.** Note this compounds with the runbook's §6.3
   warning that `sudo` resolves `$HOME` to `/root` — pass `--config` explicitly.
2. **It does not come back after a reboot.** Two independent causes, both
   silent: the container's `onboot` was never set, or the unit was never
   `systemctl enable`d inside it
   ([tteck/Proxmox #1817](https://github.com/tteck/Proxmox/discussions/1817)).
   Check `pct config <id> | grep onboot` **and** `systemctl is-enabled
   cloudflared` — they are two claims.
3. **`cannot create ICMPv4 proxy: Group ID … is not between ping group 1 to 0`
   at startup is cosmetic** for an HTTP-only tunnel
   ([cloudflared #1334](https://github.com/cloudflare/cloudflared/issues/1334),
   [#1109](https://github.com/cloudflare/cloudflared/issues/1109)). Do not spend
   launch morning on it. Set `net.ipv4.ping_group_range` later if the log noise
   annoys.

Two more worth doing while the unit is being written: run cloudflared as a
dedicated **non-root** user with a drop-in setting `NoNewPrivileges=yes` and
`ProtectSystem=strict` — it needs no capabilities for an HTTP tunnel and it is
the one process in the container that talks to the internet. And **do not mix
locally-managed ingress with dashboard-managed ingress**: having rules in both
`config.yml` and the Zero Trust dashboard is a recurring source of "routes to
the wrong thing / 502" ([tteck #1611](https://github.com/tteck/Proxmox/discussions/1611)).
We are choosing local (§6.1); the dashboard's ingress for this tunnel stays
empty.

---

## 7. Domain, DNS, TLS

### 7.1 The hostname decision, and why it is already made

Use **`vidtheque.tomvaucourt.com`**. Verified tonight (§1.4): the zone is
already on Cloudflare nameservers and already proxied, so `cloudflared tunnel
route dns` works the moment the tunnel exists, and **the registrar and
nameserver steps — the ones measured in hours — are already done.**

*Not* "no propagation window at all", which is how this paragraph read before
the review corrected it (finding 18): a proxied Cloudflare record still carries
a **300-second TTL**, and Cloudflare's own docs warn that local resolver caches
may take longer than that. Record *creation* is immediate; *external resolution*
is not. So the gate stays "an external device resolved it and got TLS", and the
plan budgets a few minutes of nothing-happening rather than treating the first
`NXDOMAIN` as a failure.

`vidtheque.dev` is still unregistered; buying it
on launch morning adds a registrar purchase, a nameserver change and a wait of
minutes-to-hours before `route dns` will accept the zone.

If Tom wants the `.dev`: buy it today, point its nameservers at Cloudflare,
and add it as a **second ingress hostname** on the same tunnel once it is
Active — a second `hostname:` block and a second `route dns`, no downtime. Then
decide separately whether `PUBLIC_URL` moves, because that is the variable with
teeth (§7.3).

### 7.2 The record

Do not hand-create it. `cloudflared tunnel route dns vidtheque
vidtheque.tomvaucourt.com` creates the proxied `CNAME` to
`<UUID>.cfargotunnel.com`. **Proxied (orange cloud) is not cosmetic** — it is
what puts `CF-Connecting-IP` on the request, which is what makes the per-IP
rate limiter work at all.

### 7.3 `PUBLIC_URL` and `VIDTHEQUE_PUBLIC_HOSTNAME` — two variables, two disasters

`docs/deploy-public.md` §3 is the full treatment; the compressed version,
because both failures are silent and neither is caught by "the page looks
fine":

| variable | shape | what breaks if wrong |
|---|---|---|
| `PUBLIC_URL` | `https://vidtheque.tomvaucourt.com` — full origin, scheme included | **every absolute URL the server emits.** `mcp_url` on `/api/meta` is `PUBLIC_URL + /mcp` — the string the demo page's copy button hands a visitor, so a wrong value means **every visitor copies a URL pointing at their own laptop**. And `thumb`/`thumb_large` on every search hit, every `/api/videos` row and every ask citation are absolute by default, so **every image on the demo page breaks** |
| `VIDTHEQUE_PUBLIC_HOSTNAME` | `vidtheque.tomvaucourt.com` — bare host, no scheme, no port | **`/mcp` answers `421 Misdirected Request` to everything** while `/`, `/api/*`, `/frames/*`, `/dashboard/*` and `/healthz` all work perfectly. Under `AUTH=none` nothing validates this at boot. The demo looks flawless and the actual product is dead |

Note the asymmetry that makes `PUBLIC_URL` easy to get wrong: the **dashboard**
builds thumbnails with `absolute=False` and therefore renders correctly on any
hostname, so *a dashboard that looks perfect proves nothing about the demo
page*. That asymmetry exists because of the 2026-08-09 SSH-tunnel incident, and
absolute stays the default because an agent receiving a frame URL over MCP has
no page to resolve it against.

Set `PUBLIC_URL` to the **https** origin even though cloudflared reaches the
origin over plain http on loopback. It describes what the world sees. It is
also what arms the CIMD SSRF guard and the `Secure` cookie flag if the box ever
leaves `AUTH=none`.

### 7.4 TLS

Cloudflare issues and terminates the edge certificate. The tunnel's hop to the
origin is plain http on loopback and needs no certificate. Leave the zone's
SSL/TLS mode alone — Flexible/Full/Strict is not about tunnel origins.

---

## 8. Launch day: smoke, monitoring, budget, rollback

### 8.1 Smoke tests

`docs/deploy-public.md` §7 is the list and it is good; run it verbatim, **from
a device that is not the box** (a phone on cellular is the honest test — no
shared DNS cache, no `/etc/hosts`, no local route). The order that matters:

1. `/mcp` **first** — the 421 check (§7.3). Nothing else on the site tells you.
2. `claude mcp add --transport http vidtheque https://<host>/mcp`, then
   `tools/list` → **seven read tools**, `index-video` and `tag-video` absent.
3. `/api/meta` → `auth: "none"`, `clamps.policy: "public"`, `mcp_url` on the
   public hostname, `limits` as configured.
4. One search in a real browser, network panel open, **zero failed image
   requests**.
5. One `get-frames` URL fetched from outside.
6. `/dashboard` and `/dashboard/jobs` → 200, and the redaction greps from §2.5
   re-run **through the tunnel** (a flag reaching the local process does not
   prove it reached the process the tunnel points at).
7. Rate limits: 35 rapid searches → 200s then 429s carrying `E_RATE_LIMIT` and
   `retry_after_s`; then a **second device on a different network** gets a
   fresh bucket. That single test is the end-to-end proof that
   `CF-Connecting-IP` is arriving and being used.
8. If ask mode ships: the SSE timestamp-spread test (§6.3).

**Staging-specific additions, from this document:**

9. `Legs:` on a search names the **vector** leg — proof the private worker link
   (§4.5) survived the cutover. **Through MCP, not `/api/search`**: the facade
   does not carry `leg_counts` (§5.6), so a demo page full of results is not
   evidence about the vector leg at all.
10. The worker's **own** `/healthz` and `/status` from the public container —
    and the same two **refused** from anywhere else on the LAN.
11. `curl -s http://127.0.0.1:8100/healthz` on the box → `writes_allowed:
    false` (it reports `true` today, because the sandbox is in indexing mode).
12. First-search latency measured twice: cold (after ≥ `IDLE_UNLOAD_SECONDS` of
    quiet) and warm. The cold number is the §2.3 decision, taken with data.
13. **The MCP GET listening stream, explicitly.** POST-carried tool results are
    the path reported to work through tunnels; the optional server-initiated
    GET stream is the one with an open, unanswered cloudflared bug (§6.3). Open
    it, wait, and see whether anything arrives before the connection closes. A
    failure here is a "note it and move on", not a rollback — but discovering it
    from a user is worse.
14. **A `curl` with no `User-Agent` at all**, against `/mcp` and `/api/search`.
    If Browser Integrity Check is still armed on those paths, this is the
    request that finds out (§6.5 item 1), and it is the closest cheap analogue
    of somebody's homegrown agent.

### 8.2 The ask-mode budget

`docs/deploy-public.md` §1.1 lands on three options and recommends (c). Nothing
found tonight changes that recommendation:

- The shipped model id **is not free** — `.env.example` records that there is
  no `deepseek/*:free` on OpenRouter any more and the whole family is paid.
- The daily cap is now **persisted** (migration 0005, `ask_budget`), so a
  redeploy no longer hands the day's money back — a real improvement over the
  in-memory version, and still not a spend cap.
- The one that ultimately matters lives at OpenRouter, not here.

**Day-one posture: `OPENROUTER_API_KEY=` empty.** `/api/meta` then reports
`ask_enabled: false` and the page hides the toggle rather than offering a
button that 503s. Ship search first; turn ask on when the traffic shape is
known, with a **dedicated, spend-capped key** created for this deployment so it
can be revoked without touching anything else.

If Tom wants ask live at launch anyway — and it is the better demo — then the
dedicated spend-capped key is not optional, and `VIDTHEQUE_RATE_ASK_PER_DAY`
wants a hard look at 50 × 4 rounds of a paid model.

### 8.3 Monitoring: what exists, and the one thing to add

**Exists.** `/dashboard` (which, in this deployment, the public can also see),
the jobs view, `/healthz` on both services, the worker's `/status` (loaded
models, VRAM, queue depth), `journalctl -u cloudflared -f`, and cloudflared's
Prometheus-style metrics on loopback (`--metrics 127.0.0.1:20241`).

**Missing, and it matters on day one:**

1. **Log rotation.** `mcp.log` reached **10 MB in about a day** of indexing
   with nothing rotating it. A public box takes more requests than an indexing
   box. Running under systemd (§4.4) solves this by moving to journald;
   staying on `dev_stack.sh` means a `logrotate` stanza, and "we will notice"
   is not a plan for a 16 GB container.
2. **Disk headroom alarm.** `derived/` is a byte-capped LRU (256 MB default)
   and everything else is static, so the container should never grow — which
   means any growth is a signal, and nothing is watching for it.
3. **A liveness ping.** Something that notices the origin is down before a
   visitor does. Cheapest honest version: a Cloudflare health check, or a cron
   on another box curling `/healthz` through the tunnel.

Do **not** add an application metrics stack on launch morning. The three items
above are a systemd unit, a `df` check and a curl.

### 8.4 Reboot behaviour — decide it, do not discover it

If `onboot` is set on the container and cloudflared is `enable`d, a host reboot
brings the site back automatically. If either is not, it does not. Both
answers are defensible; the failure is having one of each. Decide, write it in
`HANDOFF`, and test it once by rebooting the container **before** the URL is
shared.

### 8.5 Rollback

The runbook's §8 escalation is correct in shape — **the tunnel is the whole
exposure; stopping the connector is the rollback**, seconds, no DNS TTL to wait
out. Steps 2–4 (turn the demo off, delete the DNS record, delete the tunnel)
escalate in permanence.

**One sentence in it is wrong, and the review found it (finding 12): "no cache
to purge" is false.** `/frames/*.jpg` is served with `Cache-Control: public,
max-age=86400`, `.jpg` is in Cloudflare's default-cached extension list, and the
edge keeps a copy per PoP. Stopping the connector removes the *origin*, not the
edge's copies — so keyframes stay publicly retrievable for up to a day after a
rollback that was supposed to make the box invisible. If the rollback is a
content decision rather than an outage, add:

```
Cloudflare dashboard → Caching → Purge Everything
```

and accept honestly that copies already downloaded by a visitor or sitting in a
browser cache are unrecoverable, which is true of anything ever published.

**Rehearse step 1 before the URL is shared.** A rollback you have not run is a
plan.

Staging adds three more levers *below* the runbook's:

- **`pct snapshot` before the tunnel goes up.** `pct snapshot <id> pre-launch`
  is near-instant on snapshot-capable storage (ZFS / LVM-thin / Btrfs / Ceph),
  and `pct rollback <id> pre-launch` undoes the last twenty minutes of
  fiddling far more cheaply than a `vzdump` restore. Take a `vzdump` too, for
  off-box durability — but know the difference: vzdump's `snapshot` mode
  **suspends the container briefly** ("minimal downtime", not zero), and
  **`snapshot` mode requires every backed-up volume to be on snapshot-capable
  storage** ([vzdump docs](https://pve.proxmox.com/pve-docs/chapter-vzdump.html)).
  Under Topology B the state is split across two containers, so the rollback
  story has two moving parts: **snapshot both**, public one first.

- **Abandon the container.** The sandbox stack still has the corpus; if the new
  box is wrong in a way that is not a five-minute fix, re-point the tunnel's
  ingress at the sandbox (`http://<sandbox-ip>:8100`) and debug the new
  container off the critical path.

  **This is not one line, and calling it one line is how it fails at 11:00**
  (review finding 13). Three things have to be true that are not true by
  default: the sandbox's mcp must be **running** (§5.2c step 3 stopped it), it
  must be **bound to the bridge** rather than to loopback (`dev_stack.sh` pins
  `VIDTHEQUE_HOST=127.0.0.1` inline — the same fact as §4.5), and the firewall
  must **allow 8100** from the public container, where §4.2 opened only 8081.
  Plus the sandbox must be in demo mode rather than back in indexing. Either
  pre-arrange all four this morning and *test* the repoint before sharing the
  URL, or drop this lever from the plan and rely on `pct rollback` — an untested
  fallback is not a fallback.
- **Restore the corpus.** `/home/dev/backups/vidtheque-2026-08-11/` (and the
  fresh cutover snapshot, §5.7) are `chmod a-w`. Restore is: put the two
  directories back, `PRAGMA integrity_check`, the FTS integrity insert, a
  `cues` vs `cues_fts_docsize` count comparison, and the §3.3 vector-drift
  query.

---

## 9. Repo release-quality sweep

The repo is **already public** (`gh repo view` → `PUBLIC`, pushed 2026-08-10)
and CI is green on `main` (last 8 runs, all `success`). So "release day" is an
*announcement* day, not a publication day, and this sweep is about what a
stranger sees when the link lands.

Severity is about launch, not about correctness. Owner is who can do it.

| # | finding | where | severity | owner |
|---|---|---|---|---|
| 1 | **`/static/lab/*` is publicly reachable.** `Route("/static/{asset:path}")` serves any `.html` under `static/`, and `static/lab/` holds 13 MB of unreleased landing prototypes: `hero.html` and `versions/v1..v6.html` with their asset dirs. On launch these answer at `https://<host>/static/lab/versions/v5.html` — six competing versions of a page that has not shipped, with documented DESIGN.md divergences, invented UI (V1's "multiviewer source bar"), and the m41 quote whose profanity is still an open call | `mcp/src/vidtheque_mcp/public/__init__.py:102`, `…/public/static/lab/` | **gate** | code agent / Tom |
| 2 | **The removal path promised by the positioning contract does not exist.** `research/positioning-2026-08-10.md` §9.1 commits publicly to "take a channel out on request — one row, one command, and we say so publicly", and lists as a repo-side obligation that "an unfollow/remove path exists and is documented… it needs a runbook line in `docs/deploy-public.md`". There is no `delete_video`/`delete-video` implementation in `mcp/src` and no takedown section in the runbook | `docs/deploy-public.md`, `mcp/src/…/tools/` | **gate** (it is a public promise made in an answer to the hardest objection) | Tom (decide scope), then code |
| 3 | **Organiser-consent email not sent.** Same §9.1: "the organisers get asked **before** the demo is announced… now load-bearing rather than optional." 100% of the public corpus is one channel (§1.1) | positioning contract | **gate** | Tom |
| 3b | **A worker 503 prints the worker's URL on the public demo page** (§2.3b). Cosmetic today (`127.0.0.1`); an internal-address disclosure under topology B, on an ordinary busy-GPU day. Fix is to sanitise the exception into a fixed sentence before it becomes a `note:` | `mcp/src/vidtheque_mcp/embeddings.py:107,123,134`, `public/humanize.py:65`, `public/api.py:285` | medium | joint audit |
| 3c | **The job runner starts in public read-only mode** (§5.2b). Read-only unregisters the write *tools*, not the *runner*; with live queue rows the public box fetches from YouTube behind the tunnel. Mitigated for launch by draining the queue, but the *shape* is wrong — a read-only deployment arguably should not construct a runner at all | `mcp/src/vidtheque_mcp/app.py:82,191` | medium (high if the queue is not verified empty) | joint audit |
| 4 | `deploy/docker-compose.yml`'s `mcp` service says *"Placeholder: the MCP framework has not been chosen yet (see `mcp/NOTE.md`), so this builds a stub image"*. The framework was chosen (official SDK v2), `mcp/Dockerfile` is real and complete, and **`mcp/NOTE.md` does not exist**. The README's quickstart is `docker compose up -d`, so this comment is the first thing a stranger reads about the deployment they are running | `deploy/docker-compose.yml:14-16` | high | agent-doable |
| 4b | **The README's tunnel quickstart publishes the unsafe deployment.** It tells a stranger to run `TUNNEL_TOKEN=… docker compose --profile tunnel up -d` against the **base** compose file, without `deploy/compose.public.example.yml`. That is precisely the footgun `docs/deploy-public.md` §6.1 exists to warn about: the base file hands the container four variables and nothing else, so `VIDTHEQUE_PUBLIC_READONLY` and `VIDTHEQUE_AUTH` are read by nobody, the write tools are registered, and the port publishes on `0.0.0.0`. **The README's own advice is the failure the runbook calls "the worst one available"** | `README.md` quickstart | **high** — it is aimed at strangers | agent-doable |
| 4c | **`worker/README.md` describes a model topology the project no longer ships.** It presents *"1024 dims from `Qwen3-Embedding-0.6B` … 1152 dims from `SigLIP 2 NaFlex so400m"*, two separate spaces, and `IMAGE_EMBED_BACKEND=siglip2`; the root README, the compose defaults and the code all ship the **unified `Qwen3-VL-Embedding-2B` at 2048 dims in one shared slot**. A self-hoster reading the worker's own document picks the wrong model and the wrong dimensions on their first try | `worker/README.md:20,31,64` | high | agent-doable |
| 4d | **"Point `WORKER_URL` at any OpenAI-compatible provider" is not true as stated.** Full operation also needs `/v1/embeddings/frame-query`, `/v1/embeddings/image` and `/v1/ocr` at the same base URL — none of which a normal provider serves. The honest sentence is that a hosted provider covers the *transcript* leg and the *query* side of it, and that frame search and OCR need the worker (or a compatible shim). As written it promises a configuration that fails at indexing time | `README.md`, `deploy/docker-compose.yml` header | medium | agent-doable |
| 4e | `docs/deploy-public.md` §2.6's frame-crawl arithmetic is stale: it models **3,460** keyframes and "under half an hour" for one IP at 120/min. The corpus is now **11,781** — the walk is ~98 minutes, and the derived-cache estimate that follows is understated by roughly 3.4×. The *decision* (§2.6's "slow enough to be pointless") may still stand; the numbers under it no longer do — and §6.5 above adds that edge cache hits are not charged to that limiter at all | `docs/deploy-public.md` §2.6 | medium | agent-doable |
| 5 | Version is consistently `0.0.1` across root/`mcp`/`worker` `pyproject.toml` and reported by `/healthz`. **No git tags exist.** Tagging `v0.0.1` triggers both GHCR publish workflows (`build-mcp`, `build-worker`, on `v*`), which means the README's *"there are no releases and no published images yet"* becomes false the moment a tag is pushed | `README.md:19-22`, `.github/workflows/*` | medium — a **coupled decision**, not a bug | Tom |
| 6 | The same "no releases yet" line lives in the landing prototypes (`v1..v6`) and will graduate into the shipped page with V5. If a tag ships, both copies flip together | `…/lab/versions/*.html`, `README.md` | medium | with #5 |
| 7 | Box specifics in the public repo: `CLAUDE.md` §"Tom's box" (deliberate, contextual), `PRODUCT.md:105` (llama.cpp co-tenancy — deliberate, it is a product claim), `bench/` defaults hard-coding `/home/dev/vidtheque-data` (`embed_latency.py:387`, `frame_retrieval_spotcheck.py:53`), `bench/results/raw/*.json` containing full host paths, `HANDOFF-2026-08-09.md` naming the data dir. **None of it is a secret**; the bench defaults are the only ones that read as sloppy rather than as documentation | repo-wide | low | agent-doable, post-launch |
| 8 | `HANDOFF-*.md` files sit at the repo root and are internal operations logs — readable, honest, and they mention agents, incidents and backups by path. Keeping them is a defensible "work in the open" choice; it should be a **choice**, made once, not a leftover | repo root | low | Tom |
| 8b | **No `SECURITY.md`, no `CONTRIBUTING.md`, no issue templates** — `.github/` holds only `workflows/`. For a repo about to be announced with a *live public instance* attached, the missing one that matters is `SECURITY.md`: there is currently no stated way to report a vulnerability in a server people are being invited to point their agents at. One file, five lines, an email address | `.github/` | medium for `SECURITY.md`, low for the rest | agent-doable |
| 9 | `deploy/.env.example` is 48 KB and is the document of record — it is genuinely the best artifact in the repo for a self-hoster, and the README does not send anyone to it except inside the quickstart's comment. One line in the README would fix that | `README.md` | low | agent-doable |

Not findings, checked and clean: `LICENSE` (MIT, 2026, Tom Vaucourt) matches
every `pyproject.toml`'s `license` field and the README's footer; `.gitignore`
covers `.env`, weights, caches and playwright output; no `deploy/.env` is
tracked; CI does not use a self-hosted runner (CLAUDE.md's rule holds).

---

## 10. Codex's review of this plan — credited

An adversarial second opinion was run on the draft of this document and of
`BEFORE-SHIP.md`: **codex `gpt-5.6-sol`, high reasoning effort, web search on,
read-only over this repo, driven interactively in a herdr pane** (the
`drive-codex-in-herdr` path). It was asked for what is missing, what breaks
first, and what is wrong *for this stack* — explicitly not for a summary. It
worked for twelve minutes, read the code rather than the prose, and returned
**25 findings: 3 BLOCKER, 12 HIGH, 9 MED, 1 LOW**.

**Everything below has been folded into the sections named.** This list is the
ledger, not a to-do — the to-do is `BEFORE-SHIP.md`.

### 10.1 The three it called blockers, and it was right about all three

| # | finding | where it landed |
|---|---|---|
| 1 | **The public container never sets its bind address.** `mcp/__main__.py:22-24` defaults to `0.0.0.0:8080`; `dev_stack.sh` passes `VIDTHEQUE_HOST=127.0.0.1` and the port **inline**, so neither appears in `stack.env` — and the systemd unit this document recommends inherits the defaults. Result: the tunnel 502s against `127.0.0.1:8100`, and the origin is LAN-reachable, where a forged `CF-Connecting-IP` voids every per-IP limit | §4.4, §5.4, `BEFORE-SHIP` 4.1 |
| 2 | **The cutover command kills the production worker.** `dev_stack.sh stop` stops *both* services and has no worker-only verb, and its `start` hard-codes the worker to loopback — so "stop the old stack" takes down the thing the public box is about to depend on, and the script cannot bring it back bound to the bridge | §4.5, §5.2c, `BEFORE-SHIP` 4.2 |
| 3 | **Anonymous MCP calls can exhaust the GPU queue.** `/mcp` is deliberately unlimited, `AUTH=none` lets anyone open a session, `search` embeds *before* `MAX_CONCURRENT_SEARCHES` is taken, and the worker queue is unbounded. A stranger with a loop starves every visitor and the shared 3090 without touching a single `/api/` bucket | §2.2b (new), §11 |

Finding 3 is the one this document had genuinely missed, and it is the best
argument in the review: it is not a staging problem, it is a shape problem that
a public URL turns into an exploitable one.

### 10.2 The corrections it made to claims this document was making

| # | it said | what changed |
|---|---|---|
| 5 | search degradation is **~40 s**, not 20 — the timeout is *per call* and the two calls are sequential, with the 30 s query deadline starting afterwards | §2.1 |
| 7 | stopping *indexing* does not drain the *GPU* queue: STT, both embed legs, frame-query and CPU OCR all share the FIFO. The gate needs the worker's `/status`, not only SQLite job rows | §2.2, `BEFORE-SHIP` 2.1 |
| 9 | parking with `not_before` leaves rows **queued**, contradicting the empty-queue gate — cancel terminally instead | §5.2b |
| 10 | the §5.6 verification snippet **cannot succeed**: a quoted heredoc passes `$DATA` literally, and the FTS integrity command is a *write* against a `mode=ro` connection | §5.6, rewritten |
| 11 | `IDLE_UNLOAD_SECONDS` is a **worker** variable — putting it in the public box's `stack.env` means the §2.3 latency decision silently never happens, and the smoke test then confirms the old behaviour | §5.4 |
| 12 | "no cache to purge" in the rollback is **false**: `/frames/*.jpg` is `public, max-age=86400` and `.jpg` is default-cached, so the edge keeps copies for a day after the connector stops | §8.5 |
| 13 | the "re-point the tunnel at the sandbox" lever is **not one line** — it needs the sandbox mcp running, bound to the bridge, and port 8100 opened, none of which the plan arranges | §8.5 |
| 16 | `keyframes/` is **not** strictly append-only: a reindex deletes and republishes, so a plain rsync can leave stale files. Use `--delete` and verify no active stages | §5.2c |
| 18 | "no propagation window" overstates it: a proxied record still has a **300 s TTL** and local resolvers may lag. Creation is instant; external resolution is not | §7.1 |
| 25 | `docs/deploy-public.md` §2.6's crawl arithmetic is stale — 3,460 keyframes modelled, 11,781 actual | §9 finding 4e |

### 10.3 What it added that had no home in the draft at all

| # | finding | landed |
|---|---|---|
| 4 | **A worker outage stays green and never self-recovers.** `/healthz` reports the *database's* vector state, not worker reachability, and under topology B the worker keeps running under `dev_stack.sh`'s `nohup` while only the public services get systemd. A worker crash yields HTTP 200 and FTS-only search, indefinitely, with liveness green | §8.3, §11 |
| 6 | `lifecycle.submit` never removes a cancelled queued job and `_execute` runs it before checking whether the caller is still there — so abandoned requests spend the GPU *afterwards*, turning a burst into a lasting outage | §2.2b |
| 8 | the private link is described as "the embeddings link", but the worker also accepts **transcription uploads, image batches and OCR**. An L3 source allowlist bounds *who*, not *what* or *how expensive* — so compromising the public container exposes the whole inference service | §4.5, §11 |
| 15 | the worker exposes `instruction` on its responses and `mcp/embeddings.py` **discards it**, validating only model and dimension. Wrong query prompts therefore produce non-zero vector legs, valid images and passing health checks while recall silently collapses — compare `/status.backends[].instructions` against the DB `config` | §11 |
| 17 | the shortened leg gate in `BEFORE-SHIP` accepted FTS-only: `Legs:` prints the leg *names* even at `vec 0`. Require **non-zero** vector counts | `BEFORE-SHIP` 4.3 |
| 19 | **G2 is internally inconsistent**: it demands an external HTTPS request while declaring that every gate precedes all work — but DNS and the tunnel do not exist until Phase 5 | `BEFORE-SHIP` G2, split |
| 20 | **G3 is a notification gate wearing a consent gate's title.** It passes on *sending* an email and explicitly permits launching before an answer. If consent is load-bearing, receipt is the gate | `BEFORE-SHIP` G3 |
| 21 | reboot survival is *asserted* and never *exercised*; and nothing arranges automatic recovery of the sandbox worker, so the first reboot can make FTS-only permanent | §8.4, `BEFORE-SHIP` |
| 22 | snapshotting both containers is not *coordinated* rollback — the worker is stateless and its queue/GPU state is not atomically captured, so rolling the sandbox back discards unrelated dev state without restoring a coherent pair | §8.5 |
| 14, 23, 24 | three README-level release defects: the tunnel quickstart publishes the unsafe deployment, `worker/README.md` documents the retired two-model topology, and "any OpenAI-compatible provider" omits the three custom endpoints | §9 findings 4b–4d |

### 10.4 What it checked and confirmed

Worth recording, because a second opinion that only disagrees is not a second
opinion. It independently verified: the HTTP-only boundary is not violated by
splitting containers; a public search really does perform two separate query
embeddings through one FIFO; worker failures really are caught per leg and
degrade to FTS with a note rather than erroring; `PUBLIC_READONLY=1` really does
remove the write tools *and* routes, the pipeline runner excepted; and current
Cloudflare guidance really does support named tunnels, disabling BIC and Bot
Fight Mode for programmatic clients, and trusting `CF-Connecting-IP` only when
the tunnel is the only way in.

It also corrected a mitigation upward rather than downward: **`/mcp` already
emits heartbeats** — the pinned `sse-starlette` sends a 15-second comment ping —
so the "add keepalives" advice in §6.3 was already satisfied on the transport
and only `/api/ask` would ever need its own.

---

## 11. Open questions that need Tom, not an agent

1. **`EMBED_RESIDENT` / `IDLE_UNLOAD_SECONDS`** (§2.3) — is a 4.4 s first
   search acceptable, or does the embedder stay warm and llama.cpp give up
   4.3 GB?
2. **Ask mode at launch** (§8.2) — off (recommended), or on with a dedicated
   spend-capped key?
3. **Dashboard public** (`VIDTHEQUE_DASHBOARD=1` + the ingress 404 rule) — the
   runbook's §1 policy question, still unanswered.
4. **The deeper CIDR fix** (§6.4) — does the code refuse proxy-origin CIDRs, or
   is "ships empty, warns loudly" the permanent answer?
5. **The removal/takedown path** (§9 finding 2) — what does "one row, one
   command" actually mean, and does it exist before the promise is published?
6. **Tag `v0.0.1`?** (§9 findings 5–6) — it publishes images and flips the
   README's caveat.
7. **`vidtheque.dev`** — buy it today as a redirect, or ship on the subdomain
   and leave the name for later?
8. **Reboot policy** (§8.4) — does the site come back by itself?
9. **The two audit items this document added** (§9 findings 3b, 3c): should a
   worker error be sanitised before it becomes a visitor-visible `note:`, and
   should a read-only deployment construct a pipeline runner at all?
10. **The unmetered `/mcp` → unbounded GPU queue** (§2.2b, review finding 3).
    Launch anyway and watch, or put a Cloudflare rate-limiting rule on `/mcp`
    (the free plan's single rule), or bound the worker queue? This is the one
    genuinely new *design* question the night produced.
11. **Worker liveness** (review finding 4): `/healthz` goes green with the
    worker dead. Does the worker get a systemd unit and a probe this morning, or
    is "somebody will notice FTS-only results" the accepted answer for week one?
12. **Blast radius of the private link** (review finding 8): the allowlist bounds
    *who* reaches the worker, not *what they may ask it to do*. Accept for
    launch, or put a reverse proxy in front that exposes only the two embedding
    paths?


## §12 — Field correction, 2026-08-11 ~13:00: `nesting` is required on Debian 13

§4.1's "features empty, nesting explicitly off" recommendation did not survive
contact: CT 9001 (unprivileged, Debian 13/trixie, systemd 257) booted degraded
with **20 units failing `status=243/CREDENTIALS`, including journald** — no
logging at all. Mechanism: systemd ≥254 builds private mount namespaces for its
credentials machinery (and for PrivateTmp-class unit hardening, which our
staged units use); an unprivileged container without `nesting` cannot create
them. This is precisely why PVE defaults `nesting` on since 8.3.

Resolution (Tom + orchestrator): `pct set 9001 --features nesting=1`, reboot,
verified clean. Unprivileged + nesting is the supported configuration; a public
box without journald is a worse security posture than the marginal namespace
surface nesting adds. `keyctl` remains off. BEFORE-SHIP §2.2 corrected in the
same commit as this note.

---

## 2026-08-11 — where the companion docs went (repo cleanup)

The checklist this research fed, `BEFORE-SHIP.md`, was executed on launch
morning and now lives at `docs/history/BEFORE-SHIP.md`, beside the overnight
handoffs (`docs/history/HANDOFF-2026-08-09/-10/-11.md`). References in this
document to `BEFORE-SHIP.md` at the repo root read through to that path.
