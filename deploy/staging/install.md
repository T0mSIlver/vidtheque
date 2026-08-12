<!--
BOX:      none — this is the map. Each file below says which box it belongs on.
INSTALLS: docs/history/BEFORE-SHIP.md Phases 2.4, 4.1, 4.2 and 5.
STATUS:   EXECUTED 2026-08-11 — vidtheque.dev live. Kept as the cutover
          reference for the next box. Written 2026-08-11 against
          docs/history/BEFORE-SHIP.md (Phase 1 ANSWERED) and
          research/release-staging-2026-08-11.md (the runbook, incl. §10).
-->

# deploy/staging — install order

Seven files, two boxes, one purpose: **when Tom's container exists, cutover is
copy-paste rather than authorship.** Every decision from Phase 1 is already
baked in; every value that could not be known before the container exists is a
`<PLACEHOLDER>` and every one of those is in §0's table.

`docs/deploy-public.md` stays the authority for the go-public *checks*.
`docs/history/BEFORE-SHIP.md` stays the ordered list for the morning. This file only says
**which artifact goes where, in what order, and how you know it worked**.

---

## 0. Fill these in

Nothing in this directory is installable until every row is filled. Grep for
what is left:

```bash
grep -rn '<[A-Z_]*>\|<paste' deploy/staging/
```

| placeholder | appears in | what it is | how to get it |
|---|---|---|---|
| `192.168.1.98` | `stack.env.public` (`WORKER_URL`), `stack.env.sandbox` (`VIDTHEQUE_HOST`, and the commented rollback block) | the **sandbox** container's address on the host bridge — the address the public box calls for embeddings | on the sandbox: `ip -4 -br addr show` (the `vmbr0`-facing interface, usually `eth0`) |
| `192.168.1.42` | no file — only the firewall command in §3 | the **public** container's address on the host bridge — the only source allowed to reach `:8081` | on the public box: `ip -4 -br addr show` |
| `<TUNNEL_ID>` | `cloudflared-config.yml` (`tunnel:`), and the DNS CNAME target you eyeball in the dashboard checklist §2.4 | the tunnel's UUID | printed by `cloudflared tunnel create vidtheque`; `cloudflared tunnel list` prints it again |
| `<CREDENTIALS_PATH>` | `cloudflared-config.yml` (`credentials-file:`) | the credentials JSON written by `tunnel create` | **copy the literal path out of that command's output** — `sudo` resolves `$HOME` to `/root`, so it is often not where you expect |
| `<paste the capped key>` | `stack.env.public` (`OPENROUTER_API_KEY`) | Tom's **existing spend-capped** OpenRouter key (Phase 1 decision 2) | Tom, on the box, once. Never into the repo, never into a chat, never into a commit |
| `<CTID>` | no file — only the reboot-policy check in §9 | the public container's Proxmox id | `pct list` on the host |
| `<some-id>` | dashboard checklist §4.2 | any keyframe id, for the cache-status curl | any `thumb` URL from `/api/search` |

**Not placeholders, but assumptions — change them together or not at all.**
Three paths are written out concretely in four files, because a half-filled
template is worse than a wrong-but-consistent one:

| assumption | value | appears in |
|---|---|---|
| public service user | `vidtheque` | `vidtheque-mcp.service` |
| public repo clone | `/home/vidtheque/vidtheque` | `vidtheque-mcp.service` |
| public data dir | `/var/lib/vidtheque` | `stack.env.public`, `vidtheque-mcp.service` |
| sandbox user / repo / data | `dev`, `/home/dev/work/vidtheque`, `/home/dev/vidtheque-data` | `vidtheque-worker.service`, `stack.env.sandbox` — these three are **measured from the live box**, not assumed |

Keeping the data directory named `vidtheque-data` is deliberate (§5.1): the
path is free to change — `keyframes.jpeg_path` is stored relative to
`$VIDTHEQUE_DATA` — but every runbook, handoff and muscle memory names it, and
launch morning is not when to introduce a second true path.

---

## 1. The manifest

| file | box | destination | BEFORE-SHIP step |
|---|---|---|---|
| `stack.env.public` | public | `/var/lib/vidtheque/stack.env` | 4.1 |
| `stack.env.sandbox` | sandbox | `/home/dev/vidtheque-data/stack.env` (**replaces** the live file) | 4.1 + 4.2 |
| `vidtheque-worker.service` | sandbox | `/etc/systemd/system/vidtheque-worker.service` | 4.2 (and Phase 8's worker-liveness item) |
| `vidtheque-mcp.service` | public | `/etc/systemd/system/vidtheque-mcp.service` | 2.4 |
| `cloudflared.service` | public | `/etc/systemd/system/cloudflared.service` | 2.4 + 5 |
| `cloudflared-config.yml` | public | `/etc/cloudflared/config.yml` | 5 |
| `cloudflare-dashboard-checklist.md` | neither — a browser | — | 5, the "before Phase 6" block |

---

## 2. Order, and the one thing it is easy to get wrong

The sandbox comes **first**. The public box's search quality is a hard
dependency on the worker, and Phase 3.2's "stop the old stack" takes the worker
down with it (`dev_stack.sh stop` stops both services and has no worker-only
verb — codex blocker #2). So the worker must be brought back **bridge-bound,
under its own unit**, before the public box is asked to talk to it.

```
Phase 2.1  freeze the corpus, verify the queue empty        (BEFORE-SHIP)
Phase 2.2  Tom creates the container                        (BEFORE-SHIP)
Phase 2.3  install + clone on the public box                (BEFORE-SHIP)
Phase 3.1  copy keyframes/ while the old stack is UP        (BEFORE-SHIP)
Phase 3.2  stop the old stack
   -> §3   SANDBOX: stack.env + worker unit + firewall      (this file)
Phase 3.3-3.9  snapshot, copy, verify                       (BEFORE-SHIP)
   -> §4   PUBLIC: stack.env
   -> §5   PUBLIC: vidtheque-mcp.service
   -> §6   PUBLIC: Phase 4.3 mode verification
   -> §7   PUBLIC: cloudflared config + unit
   -> §8   BROWSER: cloudflare-dashboard-checklist.md
Phase 6    smoke through the tunnel, from a device that is not the box
   -> §9   reboot policy, both claims
Phase 7    share
```

---

## 3. RETIRED (2026-08-11, Topology A) — the worker moved to CT 9001

Tom's field decision: GPU passthrough into CT 9001 (copied verbatim from CT
9000's bind-mount mechanism, driver 550.163.01) — everything runs on the
public box, the worker on LOOPBACK, and no firewall is needed anywhere: the
inference API never touches a network interface. The worker unit now installs
on CT 9001 (§5, alongside the mcp unit); the sandbox keeps only its rollback
demo role. What Topology B would have been — the bridge-bound worker, its
iptables story, and the dev_stack.sh double-start trap — is in git history
(this section pre-cleanup) and in `research/release-staging-2026-08-11.md`.

---

## 4. Public — `stack.env`

After Phase 3's copy and Phase 3.9's verification.

```bash
# /var/lib/vidtheque is a root-owned LXC mount point — give it to the
# service user once, before anything writes there:
sudo chown vidtheque:vidtheque /var/lib/vidtheque
sudo install -m 600 -o vidtheque -g vidtheque \
  /home/vidtheque/vidtheque/deploy/staging/stack.env.public \
  /var/lib/vidtheque/stack.env
$EDITOR /var/lib/vidtheque/stack.env   # ONLY the OpenRouter key remains to fill
```

**Verify — the four checks Phase 4.1 and gate G2a ask for, none of which is
"read the file and feel good":**

```bash
DATA=/var/lib/vidtheque

# 1. G2a: the trusted-CIDR line must be empty. This must print NOTHING.
grep -E '^VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS=.+' $DATA/stack.env

# 2. codex blocker #1: both lines exist, exactly.
grep -E '^VIDTHEQUE_(HOST|PORT)=' $DATA/stack.env
# expect exactly: VIDTHEQUE_HOST=127.0.0.1 and VIDTHEQUE_PORT=8100

# 3. G1: strict booleans. Every flag must read 1/true/yes/on or 0/false/no/off.
#    Anything else — Y, 2, enabled — is now a BOOT FAILURE by design.
grep -E '^VIDTHEQUE_(PUBLIC_READONLY|ALLOW_PUBLIC_WRITES|DASHBOARD)=' $DATA/stack.env

# 4. the key-set diff against the document of record (deploy-public.md §2.4).
#    A key here that is NOT in .env.example is a bug by CLAUDE.md's rule.
diff <(grep -oE '^[A-Z_]+=' /home/vidtheque/vidtheque/deploy/.env.example | sort -u) \
     <(grep -oE '^[A-Z_]+=' $DATA/stack.env | sort -u)
# expect: only "<" lines (keys in .env.example that this file leaves at their
# default). Any ">" line is a finding. As staged there are none.

# 5. no secret was committed, anywhere.
git -C /home/vidtheque/vidtheque log --oneline -- deploy/.env      # must be empty
git -C /home/vidtheque/vidtheque grep -n 'sk-or-' -- . || echo "no key in the tree"
```

> The same `>`-lines check run on the **sandbox** will show
> `VIDTHEQUE_WORKER_PORT` and `VIDTHEQUE_MCP_PORT`. Those are read by
> `scripts/dev_stack.sh` and have **no `deploy/.env.example` entry** — a
> pre-existing document-of-record gap, reported 2026-08-11, not something this
> staging introduced. Leave them: `dev_stack.sh` still starts the sandbox mcp
> for the rollback lever.

---

## 5. Public — `vidtheque-mcp.service`

```bash
sudo cp /home/vidtheque/vidtheque/deploy/staging/vidtheque-mcp.service \
        /etc/systemd/system/vidtheque-mcp.service
sudo systemctl daemon-reload
sudo systemctl enable --now vidtheque-mcp
```

**Verify:**

```bash
systemctl status vidtheque-mcp --no-pager
journalctl -u vidtheque-mcp -n 50 --no-pager

# The bind address — this is codex blocker #1, proven rather than configured.
ss -tlnp | grep 8100      # expect 127.0.0.1:8100 and NOTHING on 0.0.0.0

# G2a's second half: the boot log must carry NO "treated as the owner" warning.
journalctl -u vidtheque-mcp | grep -i 'treated as the owner' || echo "clean"

# The hardening actually applied (informational, but it is the cheap proof
# that ProtectSystem/PrivateTmp did not silently fail in an unprivileged LXC).
systemd-analyze security vidtheque-mcp.service
```

**If it refuses to start**, read the failure before changing anything:

| symptom | cause | fix |
|---|---|---|
| `ExecStartPre` exited 1, unit never ran | one of the three guards fired: `VIDTHEQUE_HOST` is not `127.0.0.1`, `VIDTHEQUE_PORT` is not `8100`, or the trusted-CIDR line is non-empty | fix `stack.env` — the guard is right |
| `status=226/NAMESPACE` | a sandboxing option could not be set up in this container | bisect by commenting the hardening block; the whole set was verified in an unprivileged Debian 13 LXC (systemd 257) against this repo's real import path, so this is unexpected and worth understanding rather than papering over |
| `ConfigError` in the journal naming `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS` | the application's own G2 refusal (commit `2f29bdd`) | empty the allowlist. This is the code doing exactly what Tom asked it to |
| exit 2 with `configuration error:` | `Settings.from_env()` rejected something | the message names the variable |

---

## 6. Public — mode verification, before the tunnel exists

**docs/history/BEFORE-SHIP.md Phase 4.3, verbatim.** Run all of it against
`http://127.0.0.1:8100`. A thing that is wrong here is wrong through the tunnel
too, and cheaper to find.

```bash
# modes, clamps, and the public hostname already in mcp_url
curl -s 127.0.0.1:8100/api/meta | jq '{auth, ask_enabled, mcp_url, clamps, limits}'
# expect auth "none", clamps.policy "public", mcp_url https://vidtheque.dev/mcp,
# and ask_enabled TRUE (Phase 1 decision 2 — if it is false, the key did not
# reach the process)

# the one-line proof the read-only flag reached the process
curl -s 127.0.0.1:8100/healthz | jq .writes_allowed        # expect false

# seven read tools; index-video and tag-video ABSENT, not present-and-refusing
uv run --no-sync scripts/mcp_call.py --url http://127.0.0.1:8100/mcp list-tools

# write routes 404, not 403
for p in login logout index; do
  curl -s -o /dev/null -w "%{http_code} /$p\n" -X POST 127.0.0.1:8100/dashboard/$p
done

# redactions (deploy-public.md §2.5)
curl -s 127.0.0.1:8100/dashboard/jobs | grep -ciE 'youtube\.com|youtu\.be/|cookiefile|player_client|/home/'   # 0
curl -s 127.0.0.1:8100/dashboard | grep -ciE 'Qwen/|Declared models|keyframe JPEGs|auth='                     # 0
curl -s 127.0.0.1:8100/dashboard | grep -c 'read-only demo'                                                   # 1

# THE LEG CHECK — the one thing topology B adds, and the one /api/search
# cannot answer, because the facade does not carry leg_counts.
uv run --no-sync scripts/mcp_call.py --url http://127.0.0.1:8100/mcp \
  call search '{"q":"what did people say about evals?","limit":3}' | grep -i '^Legs:'
# THE GATE IS A NON-ZERO `vec` COUNT ON BOTH THE TRANSCRIPT AND FRAME LEGS.
# `Legs:` prints the leg NAMES even at vec 0, so "it named the vector leg" is a
# check that FTS-only search passes.

# the negative check on the facade
curl -s 'http://127.0.0.1:8100/api/search?q=evals&limit=3' | jq -r '.notes[]?'
# expect nothing about an unreachable embedding worker

# and finally
make test        # green on the box that will serve
```

---

## 7. Public — cloudflared

`docs/deploy-public.md` §6.2 has the apt commands and they are correct. **Two
trixie traps, both verified 2026-08-10:** the apt suite is **`any`** (there is
no `trixie` suite — `dists/trixie/Release` 404s), and the signing key rolled
2025-10-30 with the old keys removed 2026-04-30, so fetch `cloudflare-main.gpg`
**fresh** and never copy a keyring over from the sandbox (Debian 13's `sqv`
verifier rejects the old SHA-1-bound signatures).

```bash
# 7a. the tunnel — copy the UUID and the literal credentials path out of the
#     output; do not assume the directory.
cloudflared tunnel login          # pick the vidtheque.dev zone (must read Active)
cloudflared tunnel create vidtheque

# 7b. config, and its own user
#     (Field note 2026-08-11: the .deb does NOT create this user — the hardened
#      unit fails at start without this useradd. Confirmed in production.)
sudo useradd --system --no-create-home --shell /usr/sbin/nologin cloudflared
sudo mkdir -p /etc/cloudflared
sudo cp /home/vidtheque/vidtheque/deploy/staging/cloudflared-config.yml \
        /etc/cloudflared/config.yml
sudo $EDITOR /etc/cloudflared/config.yml       # <TUNNEL_ID>, <CREDENTIALS_PATH>
sudo cp <CREDENTIALS_PATH> /etc/cloudflared/
sudo chown -R cloudflared:cloudflared /etc/cloudflared
sudo chmod 600 /etc/cloudflared/*.json

# 7c. validate BEFORE running anything
cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
cloudflared tunnel ingress rule --config /etc/cloudflared/config.yml https://vidtheque.dev/mcp
cloudflared tunnel ingress rule --config /etc/cloudflared/config.yml https://vidtheque.dev/dashboard
cloudflared tunnel ingress rule --config /etc/cloudflared/config.yml https://vidtheque.dev/frames/x-00000.jpg
# all three must resolve to http://127.0.0.1:8100 — the dashboard one included,
# because the dashboard is public (Phase 1 decision 4)

# 7d. DNS. Do NOT hand-create the record.
cloudflared tunnel route dns vidtheque vidtheque.dev

# 7e. run it in the FOREGROUND once and watch the log. Do the dashboard
#     checklist (§8) and Phase 6 while it is in the foreground.
sudo -u cloudflared cloudflared --config /etc/cloudflared/config.yml tunnel run
```

**Only after Phase 6 passes**, install the unit:

```bash
sudo cp /home/vidtheque/vidtheque/deploy/staging/cloudflared.service \
        /etc/systemd/system/cloudflared.service
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared
```

**Verify — and this is the step where a green dot lies:**

```bash
# READ THE ACTUAL ExecStart. The known failure writes a DNS-proxy invocation
# and the unit reports active (running) with no tunnel behind it.
systemctl cat cloudflared | grep ExecStart
# must contain: tunnel run

journalctl -u cloudflared -n 20 --no-pager | grep -i 'Registered tunnel connection'
curl -s 127.0.0.1:20241/metrics | head -5      # the connector's own counters
```

Expect `cannot create ICMPv4 proxy: Group ID … is not between ping group 1 to 0`
in the log and **ignore it** — cosmetic for an HTTP-only tunnel
(cloudflared #1334, #1109). Do not spend launch morning on it.

> **Do not test with a quick tunnel.** `trycloudflare.com` does not support SSE
> and `/mcp` *is* an SSE transport, so it will report the product as broken.

---

## 8. Browser — the dashboard settings

`deploy/staging/cloudflare-dashboard-checklist.md`, all of it, **before Phase
6**. Its §8 done-check is the gate. The one that most nearly costs a launch is
§1.1: Browser Integrity Check is **on by default on Free** and challenges every
client without a browser user agent — which is every MCP client and half the
smoke tests.

---

## 9. Reboot policy — two claims, and having one of each is the failure

Phase 1 decision 6 is **both**:

```bash
systemctl is-enabled cloudflared          # on the public box  -> enabled
systemctl is-enabled vidtheque-mcp        # on the public box  -> enabled
systemctl is-enabled vidtheque-worker     # on the SANDBOX     -> enabled
pct config <CTID> | grep onboot           # on the HOST        -> onboot: 1
```

Then **exercise it, do not assert it** (Phase 8): reboot the public container
once *before* the URL is shared and confirm the site comes back — including the
worker on the sandbox, which nothing restarted before this staging work.

---

## 10. What this staging deliberately does not do

- **It does not fix `scripts/dev_stack.sh`.** Tom's call, 2026-08-11. The
  script's two blocking limitations (a loopback bind pinned inline, and a
  `stop` with no worker-only verb) are routed around with standalone units
  rather than patched. The consequence is written into
  `vidtheque-worker.service`'s header and repeated in §3: do not run
  `dev_stack.sh start` on the sandbox once the unit is installed.
- **It does not arm rollback lever 2** ("re-point the tunnel at the sandbox").
  Four preconditions have to be true and none is true by default; they are
  spelled out in `stack.env.sandbox`'s closing block, with the two hostname
  variables staged as comments. Either arrange and **test** all four on launch
  morning, or strike the lever and rely on `pct rollback` — an untested
  fallback is not a fallback.
- **It does not raise `VIDTHEQUE_DERIVED_CACHE_MB`.** Left at 256 MB. The
  remeasured §2.6 arithmetic says the product's five widths are ~6.4× that, so
  the LRU is permanently hot under any crawl and the 192px thumbnails are what
  survives — which is the right way round. Raise it on day one only if
  `du -sh $DATA/derived` and the disk say so; it is a CPU/latency decision, not
  a correctness one, and the Cloudflare edge absorbs most of it.
- **It does not add monitoring.** Phase 8's three items are a `df`, a
  `/healthz` curl from another box, and — separately, because `/healthz`
  reports the *database's* vector state and stays green with the worker dead —
  a curl of the worker's own `:8081/healthz`. Do not stand up a metrics stack
  on launch day.

---

## 11. Rollback

**The tunnel is the whole exposure. Stopping the connector is the rollback.**
Seconds.

```bash
sudo systemctl stop cloudflared
sudo systemctl disable cloudflared
```

The full escalation — five steps, each more permanent, with the cache-purge
caveat that "no cache to purge" was wrong — is **docs/history/BEFORE-SHIP.md's rollback
card**, and the reasoning is `research/release-staging-2026-08-11.md` §8.5.
Two things from there that belong next to these commands:

1. Stopping the connector **does not un-publish the keyframes**. `/frames/*.jpg`
   is `Cache-Control: public, max-age=86400` and `.jpg` is default-cached by
   Cloudflare, so edge copies survive the origin for up to a day. If the
   rollback is about *content*, also do **Caching → Purge Everything**.
2. **Rehearse step 1 before the URL is shared.** A rollback you have not run is
   a plan.

**Rotate on exposure.** Tunnel credentials leaked → rotate, restart the
connector, force-disconnect existing connections via the API.
`OPENROUTER_API_KEY` leaked → revoke at OpenRouter **first**, change the config
second; the running process holds it in memory until restart.
