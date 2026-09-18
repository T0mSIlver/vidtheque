<!--
BOX:      none — this is the map. Each file below says which box it belongs on.
INSTALLS: the public box's units, env and tunnel config.
STATUS:   EXECUTED 2026-08-11 — vidtheque.dev live. Kept as the cutover
          reference for the next box, and §12 is current: the corpus-refresh
          runbook `deploy/staging/vidtheque-deploy.sh` cites by section.
          Reasoning: research/release-staging-2026-08-11.md (incl. §10).
NOTE:     this file describes CT 9001, the PUBLIC box (git clone + systemd,
          pull-based deploy). The private read-write box, CT 9002, runs
          release images and is updated with `vidtheque-update <version>`
          (deploy/vidtheque-update.sh) — a different machine and a different
          mechanism, not described here.
-->

# deploy/staging — install order

Ten staged files and the repo's own `deploy/Caddyfile`, two boxes, one purpose:
**when Tom's container exists, cutover is copy-paste rather than authorship.** Every decision from Phase 1 is already
baked in; every value that could not be known before the container exists is a
`<PLACEHOLDER>` and every one of those is in §0's table.

`docs/deploy-public.md` stays the authority for the go-public *checks*.
`docs/LESSONS.md` carries the cutover rules that outlived that morning. This file only says
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

| file | box | destination | cutover step |
|---|---|---|---|
| `stack.env.public` | public | `/var/lib/vidtheque/stack.env` | 4.1 |
| `stack.env.web` | public | `/var/lib/vidtheque/web.env` | 4.3 |
| `stack.env.sandbox` | sandbox | `/home/dev/vidtheque-data/stack.env` (**replaces** the live file) | 4.1 + 4.2 |
| `vidtheque-worker.service` | sandbox | `/etc/systemd/system/vidtheque-worker.service` | 4.2 (and Phase 8's worker-liveness item) |
| `vidtheque-mcp.service` | public | `/etc/systemd/system/vidtheque-mcp.service` | 2.4 |
| `vidtheque-web.service` | public | `/etc/systemd/system/vidtheque-web.service` | 5a |
| `vidtheque-caddy.service` | public | `/etc/systemd/system/vidtheque-caddy.service` | 5b |
| `../Caddyfile` — the repo's, not a staged copy | public | `/etc/caddy/Caddyfile` | 5b, and again on every deployment |
| `cloudflared.service` | public | `/etc/systemd/system/cloudflared.service` | 2.4 + 5 |
| `cloudflared-config.yml` | public | `/etc/cloudflared/config.yml` | 5 |
| `cloudflare-dashboard-checklist.md` | neither — a browser | — | 5, the "before Phase 6" block |

**FOUR PROCESSES ON THE PUBLIC BOX SINCE THE FRONT-END CUTOVER, NOT TWO.**
Python renders no page any more (`docs/design/frontend-migration.md` §1a, §1d;
`dashboard.md` §23), so the box runs `vidtheque-mcp` on 127.0.0.1:8100,
`vidtheque-web` on 127.0.0.1:3000, `vidtheque-caddy` on 127.0.0.1:8080 in front
of both, and the worker on 127.0.0.1:8081. **The tunnel points at the edge**,
which is the only listener it can reach — the same exposure argument as before,
one hop further out and with one process fewer able to answer a forged
`CF-Connecting-IP`. Which of the two servers answers a path is
`deploy/Caddyfile`, and it is the repo's file rather than a staged copy on
purpose: two route tables drift, and a drifted one serves a path from the wrong
process and says nothing.

---

## 2. Order, and the one thing it is easy to get wrong

The sandbox comes **first**. The public box's search quality is a hard
dependency on the worker, and Phase 3.2's "stop the old stack" takes the worker
down with it (`dev_stack.sh stop` stops both services and has no worker-only
verb — codex blocker #2). So the worker must be brought back **bridge-bound,
under its own unit**, before the public box is asked to talk to it.

```
Phase 2.1  freeze the corpus, verify the queue empty        (LESSONS.md)
Phase 2.2  Tom creates the container
Phase 2.3  install + clone on the public box
Phase 3.1  copy keyframes/ while the old stack is UP
Phase 3.2  stop the old stack
   -> §3   SANDBOX: stack.env + worker unit + firewall      (this file)
Phase 3.3-3.9  snapshot, copy, verify
   -> §4   PUBLIC: stack.env, and §4.3 web.env
   -> §5   PUBLIC: vidtheque-mcp.service
   -> §5a  PUBLIC: node + pnpm + vidtheque-web.service
   -> §5b  PUBLIC: caddy + the Caddyfile + vidtheque-caddy.service
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

## 4.3 Public — `web.env`, the front end's two variables

```bash
sudo install -m 644 -o vidtheque -g vidtheque \
  /home/vidtheque/vidtheque/deploy/staging/stack.env.web \
  /var/lib/vidtheque/web.env
```

A second, two-line env file rather than more lines in `stack.env`, so the
front-end process does not hold `OPENROUTER_API_KEY` — the file's own header
carries the reasoning. Both keys are in `deploy/.env.example`'s `web/` section,
so §4's key-set diff on `stack.env` is unaffected by them.

**Verify — the one that is silent in both directions:**

```bash
# The forwarded-address header must be the same string in both files. If they
# disagree, every visitor this server reads for shares one 30/min bucket, and
# the symptom is "the demo got popular" right up until it stops answering.
diff <(sed -n 's/^VIDTHEQUE_TRUSTED_IP_HEADER=//p' /var/lib/vidtheque/stack.env) \
     <(sed -n 's/^VIDTHEQUE_CLIENT_IP_HEADER=//p'  /var/lib/vidtheque/web.env)
# expect no output. vidtheque-web.service guards its side at every start too.
```

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

## 5a. Public — Node, pnpm, and `vidtheque-web.service`

The front end is the only thing that serves a page on this box. Node is pinned
to the version `.github/workflows/ci-web.yml` installs, from the official
tarball rather than a distribution package, for the reason the CUDA base is
digest-pinned: the build that runs here should be the build that was checked.

```bash
# 5a.1 Node 24.18.0 into /usr/local, and pnpm from corepack beside it.
NODE=node-v24.18.0-linux-x64
curl -fsSLO "https://nodejs.org/dist/v24.18.0/$NODE.tar.xz"
curl -fsSL https://nodejs.org/dist/v24.18.0/SHASUMS256.txt | grep "$NODE.tar.xz" | sha256sum -c -
sudo tar -xJf "$NODE.tar.xz" -C /usr/local --strip-components=1 \
  --exclude CHANGELOG.md --exclude LICENSE --exclude README.md
node --version && sudo corepack enable   # writes /usr/local/bin/pnpm
# The pnpm VERSION is web/package.json's `packageManager`; corepack fetches it
# on first use, exactly as pnpm/action-setup does in CI.

# 5a.2 the first build, as the service user (the deploy script does this on
#      every deployment afterwards).
sudo -u vidtheque /usr/local/bin/pnpm --dir /home/vidtheque/vidtheque/web install --frozen-lockfile
sudo -u vidtheque /usr/local/bin/pnpm --dir /home/vidtheque/vidtheque/web build

# 5a.3 the unit
sudo cp /home/vidtheque/vidtheque/deploy/staging/vidtheque-web.service \
        /etc/systemd/system/vidtheque-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now vidtheque-web
```

**Verify:**

```bash
systemctl status vidtheque-web --no-pager
ss -tlnp | grep 3000        # expect 127.0.0.1:3000 and NOTHING on 0.0.0.0

# A page, and its four document headers — the CSP with a nonce, and the three
# beside it. They left Python with the pages (demo-site.md §7 item 0) and no
# test in mcp/ can see them, so this is where they are checked on the box.
curl -sSD- -o /dev/null http://127.0.0.1:3000/ | grep -iE \
  'content-security-policy|x-frame-options|x-content-type-options|referrer-policy'
# expect all four, and a fresh `nonce-…` in the CSP on every request:
curl -s -D- -o /dev/null http://127.0.0.1:3000/ | grep -o "nonce-[^']*"
curl -s -D- -o /dev/null http://127.0.0.1:3000/ | grep -o "nonce-[^']*"
# the two must DIFFER. A repeated nonce means a page was prerendered, which is
# a CSP that protects nothing (frontend-migration.md §1b).
```

---

## 5b. Public — caddy and the edge

```bash
# 5b.1 install caddy (caddyserver.com/docs/install, verified 2026-09-06)
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy

# 5b.2 THE PACKAGE STARTS ITS OWN CADDY ON :80. Stop it before anything else:
#      it serves the package's welcome page, and two caddies on one box makes
#      "which one answered" a question you do not want on launch morning.
sudo systemctl disable --now caddy
sudo systemctl mask caddy

# 5b.3 the routing rule, from the checkout. vidtheque-deploy.sh re-installs it
#      on every deployment so it can never lag the code it routes to.
sudo install -m 644 /home/vidtheque/vidtheque/deploy/Caddyfile /etc/caddy/Caddyfile

# 5b.4 the unit
sudo cp /home/vidtheque/vidtheque/deploy/staging/vidtheque-caddy.service \
        /etc/systemd/system/vidtheque-caddy.service
sudo systemctl daemon-reload
sudo systemctl enable --now vidtheque-caddy
```

**Verify — and this is the step the whole cutover turns on:**

```bash
# The bind. This is the box's exposure argument, the same one stack.env's
# VIDTHEQUE_HOST=127.0.0.1 makes for mcp — and now the only one that matters,
# because mcp and web are behind it.
ss -tlnp | grep 8080        # expect 127.0.0.1:8080 and NOTHING on 0.0.0.0

# THE ROUTE TABLE, through the edge. Each line is a row of
# frontend-migration.md §1a/§1d, and the METHOD SPLIT is the half no other
# check covers: a proxy that routes the three collision paths on path alone
# answers a form POST with a document and nothing says so.
E=http://127.0.0.1:8080
curl -s -o /dev/null -w '%{http_code} %{content_type}  GET /\n'      $E/
curl -s -o /dev/null -w '%{http_code} %{content_type}  GET /demo\n'  $E/demo
curl -s -o /dev/null -w '%{http_code} %{content_type}  GET /healthz\n' $E/healthz
curl -s -o /dev/null -w '%{http_code} %{content_type}  GET /dashboard\n' $E/dashboard
curl -s -o /dev/null -w '%{http_code} %{content_type}  GET /dashboard/login\n' $E/dashboard/login
curl -s -o /dev/null -w '%{http_code} %{content_type}  GET /dashboard/api/session\n' $E/dashboard/api/session
# The first five must be text/html (Next); /healthz and the api one must be
# application/json (Python).

for p in login index following; do
  curl -s -o /dev/null -w "%{http_code} %{content_type}  POST /dashboard/$p\n" \
    -X POST -H 'Content-Type: application/x-www-form-urlencoded' \
    -H 'Accept: application/json' --data '' $E/dashboard/$p
done
# NONE OF THE THREE MAY BE text/html. On this box all three are 404s, because
# a read-only deployment registers no write side at all (dashboard.md §21), and
# an unrouted path under the MCP mount answers `text/plain` — so `404
# text/plain` is the pass here. `200 text/html` is the failure that matters:
# that is the front end answering a write with the page beside it, and on a box
# WITH a write side it would be a form that silently never posted.
```

---

## 6. Public — mode verification, before the tunnel exists

**Mode verification, in full.** Run all of it against
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

# redactions (deploy-public.md §2.5) — AGAINST THE JSON, not against HTML.
# Python renders no page since 2026-09-06, so the greps that read
# /dashboard and /dashboard/jobs now read the payloads the React pages read.
# The projection is the same one, and it redacts by OMISSION: the operator's
# reads are not taken, so there is no field to un-hide (frontend-migration §7).
curl -s 127.0.0.1:8100/dashboard/api/jobs | grep -ciE 'youtube\.com|youtu\.be/|cookiefile|player_client|/home/'  # 0
curl -s 127.0.0.1:8100/dashboard/api/overview | grep -ciE 'Qwen/|declared_models|auth_mode'                      # 0
curl -s 127.0.0.1:8100/dashboard/api/session | jq '{readonly, write_side, policy}'
# expect readonly true, write_side false, policy "public" — the three facts the
# rail's "read-only demo" sentence used to be the only witness to.

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
# all three must resolve to http://127.0.0.1:8080 — THE EDGE, one rule for
# every path, the dashboard one included because the dashboard is public
# (Phase 1 decision 4). Which of the two servers behind it answers each of
# these is the Caddyfile's business and §5b's checks, not the tunnel's.

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
systemctl is-enabled vidtheque-web        # on the public box  -> enabled
systemctl is-enabled vidtheque-caddy      # on the public box  -> enabled
systemctl is-enabled vidtheque-worker     # on the SANDBOX     -> enabled
systemctl is-enabled caddy                # on the public box  -> MASKED (§5b.2)
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

The escalation, each step more permanent. Reasoning:
`research/release-staging-2026-08-11.md` §8.5.

1. **Stop the connector** (above). Seconds, reversible with `start`. It **does
   not un-publish the keyframes**: `/frames/*.jpg` is
   `Cache-Control: public, max-age=86400` and `.jpg` is default-cached by
   Cloudflare, so edge copies survive the origin for up to a day. If the
   rollback is about *content* rather than an outage, also do **Caching →
   Purge Everything**, and accept that already-downloaded copies are gone for
   good.
2. **Roll the corpus back.** §12's poller keeps one previous generation at
   `/var/lib/vidtheque/corpus-previous`; point `corpus-manifest.json` at the
   older release and request a deployment.
3. **Roll the code back.** Request a deployment of an earlier ref — this box is
   a git clone, so there is no image tag to pin. (The *private* box, CT 9002,
   rolls back differently: `vidtheque-update <previous tag>`.) Since the
   front-end cutover that ref carries three things rather than one: the Python
   source, the `web/` build the deploy script re-runs from it, and
   `deploy/Caddyfile`, which the script re-installs and the edge re-validates.
   A ref from before the cutover has no front end and no Caddyfile, so rolling
   back past it is `systemctl stop vidtheque-caddy vidtheque-web` plus pointing
   the tunnel at 127.0.0.1:8100 again — a hand-run rollback, not a deployment.
4. **`pct rollback <id> pre-launch`.** Undoes the container.
5. **Delete the DNS record**, then `cloudflared tunnel delete vidtheque` —
   this invalidates the credentials, so recreating means redoing §9.

**Rehearse step 1 before the URL is shared.** A rollback you have not run is a
plan.

**Rotate on exposure.** Tunnel credentials leaked → rotate, restart the
connector, force-disconnect existing connections via the API.
`OPENROUTER_API_KEY` leaked → revoke at OpenRouter **first**, change the config
second; the running process holds it in memory until restart.

## 12. Corpus generations (2026-09-18)

### One-time setup

On the public box, create the receiving account and directory:

```bash
groupadd --gid 10001 corpus
useradd --uid 10001 --gid 10001 --create-home --shell /bin/sh corpus
# The data root stays root's: `current` is renamed into it, and the activation script
# refuses a root that anyone else can write. Only generations/ is the corpus user's.
install -d -o root -g root -m 0755 /srv/vidtheque-data
install -d -o corpus -g corpus /srv/vidtheque-data/generations
```

Generate the transfer key on the private box. Put its public half in
`/home/corpus/.ssh/authorized_keys` on the public box with this prefix:

```bash
ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519_corpus -N ''
```

```text
restrict,command="rrsync /srv/vidtheque-data/generations" ssh-ed25519 …
```

Configure the private box to use that key for `corpus@192.168.1.42`.

The public box's `compose.local.yml` must contain:

```yaml
services:
  mcp:
    environment:
      VIDTHEQUE_DATA_DIR: /data/current
    volumes: !override
      - /srv/vidtheque-data:/data
```

Set `VIDTHEQUE_SECRET` in the public box's `deploy/.env` (any long random
string, or the old `secret.key`'s content to keep existing frame URLs valid).
Without it the server writes a fresh `secret.key` into each generation it
serves and every publish rotates the signing key.

Install and start the activation watcher:

```bash
install -m 0644 deploy/publish/vidtheque-corpus-activate.{path,service} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now vidtheque-corpus-activate.path
```

### Publish and rollback

Run the publish command from the dev sandbox:

```bash
scripts/publish_corpus.sh --generation 2026-09-18-public \
  --keep-channel "AI Engineer" --keep-tag series:aie-paris-2026
scripts/publish_corpus.sh --rollback
```

Use `--dry-run` on the publish command to inspect every remote command without
running it.
