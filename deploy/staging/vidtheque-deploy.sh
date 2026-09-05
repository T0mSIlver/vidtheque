#!/bin/bash
# ===========================================================================
# BOX:      the PUBLIC container (CT 9001)
# GOES TO:  /usr/local/sbin/vidtheque-deploy   (root-owned, mode 0755)
# RUN BY:   vidtheque-deploy.timer, or manually: `vidtheque-deploy`
# TRIGGER:  a GitHub Deployment object — from any machine with gh auth:
#             gh api repos/T0mSIlver/vidtheque/deployments \
#               -f ref=main -F auto_merge=false -f 'required_contexts[]'
#           (see request-deploy.sh for the wrapped version)
#
# Pull-based on purpose: the repo's no-self-hosted-runners rule (CLAUDE.md)
# means nothing pushes into this box — it polls GitHub's Deployments API and
# acts only on a deployment object someone deliberately created. Deploy
# history and green/red live in GitHub's own Deployments panel.
#
# OPTIONAL: /etc/vidtheque-deploy.env with GITHUB_TOKEN=<fine-grained PAT,
# this repo only, Deployments: Read+Write>. With it, the box posts
# in_progress/success/failure statuses back (visible in the UI) and polls
# comfortably inside rate limits. Without it, everything still works —
# statuses are skipped and the unauthenticated rate limit applies.
# ===========================================================================
set -euo pipefail

REPO_DIR=/home/vidtheque/vidtheque
GH_REPO=T0mSIlver/vidtheque
STATE=/var/lib/vidtheque/.last-deployment-id
RUN="runuser -u vidtheque --"
# runuser does not load the user's login PATH; uv lives in ~/.local/bin
# (field failure, inaugural deploy 2026-08-11: "uv sync" = command not found).
UV=/home/vidtheque/.local/bin/uv
# Node and pnpm for the front end, same reason and same shape. Node is
# installed from the official tarball into /usr/local at the version
# ci-web.yml pins, and pnpm is corepack's shim beside it (install.md §5a).
PNPM=/usr/local/bin/pnpm

[ -f /etc/vidtheque-deploy.env ] && . /etc/vidtheque-deploy.env
AUTH=()
[ -n "${GITHUB_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN")

api() { curl -fsS -m 15 "${AUTH[@]}" -H "Accept: application/vnd.github+json" "$@"; }

LATEST=$(api "https://api.github.com/repos/$GH_REPO/deployments?per_page=1" |
  python3 -c 'import json,sys; d=json.load(sys.stdin); print("%s %s" % (d[0]["id"], d[0]["sha"]) if d else "")')
[ -z "$LATEST" ] && exit 0
DEP_ID=${LATEST%% *}
SHA=${LATEST##* }

LAST=$(cat "$STATE" 2>/dev/null || echo 0)
[ "$DEP_ID" -le "$LAST" ] && exit 0

echo "deploy: deployment $DEP_ID requests $SHA"

status() {  # state, description
  [ -n "${GITHUB_TOKEN:-}" ] || return 0
  api -X POST "https://api.github.com/repos/$GH_REPO/deployments/$DEP_ID/statuses" \
    -d "{\"state\":\"$1\",\"description\":\"$2\",\"environment_url\":\"https://vidtheque.dev\"}" \
    >/dev/null || true
}

fail() { status failure "$1"; echo "deploy: FAILED — $1" >&2; echo "$DEP_ID" > "$STATE"; exit 1; }

status in_progress "pull, sync, restart"

# CI gate: a red check on the SHA refuses; absent checks do not block.
CONCLUSIONS=$(api "https://api.github.com/repos/$GH_REPO/commits/$SHA/check-runs" 2>/dev/null |
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(" ".join(c["conclusion"] or "" for c in d.get("check_runs",[])))' \
  2>/dev/null || echo "")
case " $CONCLUSIONS " in
  *" failure "*|*" timed_out "*) fail "checks red on $SHA: $CONCLUSIONS" ;;
esac

cd "$REPO_DIR"
$RUN git fetch origin --quiet                     || fail "git fetch"
$RUN git checkout --quiet "$SHA"                  || fail "checkout $SHA"

# Self-update FIRST, then re-exec, so a fix to this script deploys itself in
# the same run that ships it (inaugural-deploy lesson: updating last means a
# broken step earlier can never be healed by the mechanism it broke).
if ! cmp -s "$REPO_DIR/deploy/staging/vidtheque-deploy.sh" /usr/local/sbin/vidtheque-deploy; then
  install -m 755 "$REPO_DIR/deploy/staging/vidtheque-deploy.sh" /usr/local/sbin/vidtheque-deploy
  if [ -z "${VIDTHEQUE_DEPLOY_REEXEC:-}" ]; then
    echo "deploy: self-updated — re-executing the new script"
    VIDTHEQUE_DEPLOY_REEXEC=1 exec /usr/local/sbin/vidtheque-deploy
  fi
fi
$RUN "$UV" sync --frozen --group gpu --quiet      || fail "uv sync"

# ---------------------------------------------------------------------------
# The front end. Every page on this deployment is served by web/ since the
# 2026-09-06 cutover, so a deploy that syncs Python and skips this ships new
# routes with the old pages behind them. Both steps are the ones CI runs:
# --frozen-lockfile, then a production build. `pnpm build` needs no API and no
# environment — every page reads its data at request time (ci-web.yml says so
# where it runs the same command).
#
# It is BEFORE the restart on purpose: a build failure leaves the running
# process serving the previous build, which is the same discipline `uv sync`
# has above.
# ---------------------------------------------------------------------------
if [ -f "$REPO_DIR/web/package.json" ]; then
  status in_progress "web: install and build"
  $RUN "$PNPM" --dir "$REPO_DIR/web" install --frozen-lockfile --silent \
    || fail "pnpm install"
  $RUN "$PNPM" --dir "$REPO_DIR/web" build || fail "pnpm build"
fi

# The edge's rule, from this same commit. It is the file that decides WHICH of
# the two servers answers a path, so it moves with the code it routes to and
# never lags it (deploy/Caddyfile; the route table is frontend-migration.md
# §1a and §1d). The unit validates it before it opens a listener.
if [ -f "$REPO_DIR/deploy/Caddyfile" ] && ! cmp -s "$REPO_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile; then
  install -m 644 "$REPO_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile || fail "install Caddyfile"
  echo "deploy: edge rule updated"
fi

# ---------------------------------------------------------------------------
# Corpus refresh — the corpus deploys the way the code does: pulled, never
# pushed. deploy/staging/corpus-manifest.json names a generation and the
# release assets that hold its tarball (split under GitHub's 2 GB/asset cap;
# the parts are literal byte-ranges of one tar.gz, so `cat` restores it).
# A generation change swaps vidtheque.db + keyframes/ and keeps the outgoing
# generation in corpus-previous/, so a rollback is `git revert` of the
# manifest plus one more deployment — not an ssh session this box does not
# offer. No manifest, or an already-current generation, means this whole
# block is a no-op. Runbook: deploy/staging/install.md §12.
# ---------------------------------------------------------------------------
MANIFEST="$REPO_DIR/deploy/staging/corpus-manifest.json"
DATA_DIR=/var/lib/vidtheque
if [ -f "$MANIFEST" ]; then
  read -r GEN PACKED UNPACKED <<<"$(python3 -c '
import json,sys
m = json.load(open(sys.argv[1]))
print(m["generation"], m["bytes_packed"], m["bytes_unpacked"])' "$MANIFEST")" \
    || fail "corpus manifest unreadable"
  CUR=$(cat "$DATA_DIR/.corpus-generation" 2>/dev/null || echo bootstrap)
  if [ "$GEN" != "$CUR" ]; then
    status in_progress "corpus refresh: $CUR -> $GEN"
    STAGE="$DATA_DIR/corpus-incoming"
    PREV="$DATA_DIR/corpus-previous"
    rm -rf "$STAGE" && mkdir -p "$STAGE"
    if [ -d "$PREV/$GEN" ]; then
      # The requested generation is the one we swapped out last time: this
      # deployment is a rollback, and the bytes are already on the box.
      echo "deploy: corpus $GEN staged from corpus-previous (rollback)"
      mv "$PREV/$GEN/vidtheque.db" "$PREV/$GEN/keyframes" "$STAGE"/ \
        || fail "corpus rollback staging"
    else
      rm -rf "$PREV"   # one rollback generation is the budget; free it first
      AVAIL=$(df --output=avail -B1 "$DATA_DIR" | tail -1)
      [ "$AVAIL" -gt $((PACKED + UNPACKED + 1024*1024*1024)) ] \
        || fail "corpus refresh needs $((PACKED + UNPACKED)) B + 1 GiB slack, have $AVAIL B"
      python3 -c '
import json,sys
for p in json.load(open(sys.argv[1]))["parts"]:
    print(p["sha256"] + "  " + p["url"])' "$MANIFEST" > "$STAGE/parts.lst" \
        || fail "corpus manifest parts"
      while read -r SUM URL; do
        F="$STAGE/$(basename "$URL")"
        echo "deploy: fetching $(basename "$URL")"
        curl -fsSL --retry 3 -m 3600 -o "$F" "$URL"  || fail "corpus fetch $URL"
        echo "$SUM  $F" | sha256sum -c - >/dev/null  || fail "corpus sha256 $F"
      done < "$STAGE/parts.lst"
      # Stream-extract the concatenation (the glob sorts, and part-aa < ab);
      # each part is already integrity-checked, and gunzip+tar reject a torn
      # stream — so the tarball never needs to exist reassembled on disk.
      cat "$STAGE"/*.part-* | tar -xzf - -C "$STAGE" || fail "corpus unpack"
      rm -f "$STAGE"/*.part-* "$STAGE/parts.lst"
      [ -f "$STAGE/vidtheque.db" ] && [ -d "$STAGE/keyframes" ] \
        || fail "corpus tarball missing vidtheque.db or keyframes/"
    fi
    # The front end goes down with them, deliberately: with mcp stopped it
    # would render every page as "the API is unreachable", and a clean 502 from
    # the edge is a better answer than a page that looks broken. The restart at
    # the end starts all four again.
    systemctl stop vidtheque-mcp vidtheque-worker vidtheque-web \
      || fail "corpus stop"
    rm -rf "$PREV" && mkdir -p "$PREV/$CUR"
    [ -f "$DATA_DIR/vidtheque.db" ] && mv "$DATA_DIR/vidtheque.db" "$PREV/$CUR/"
    rm -f "$DATA_DIR"/vidtheque.db-wal "$DATA_DIR"/vidtheque.db-shm
    [ -d "$DATA_DIR/keyframes" ] && mv "$DATA_DIR/keyframes" "$PREV/$CUR/keyframes"
    mv "$STAGE/vidtheque.db" "$DATA_DIR/vidtheque.db" || fail "corpus swap db"
    mv "$STAGE/keyframes"    "$DATA_DIR/keyframes"    || fail "corpus swap keyframes"
    rm -rf "$DATA_DIR/derived" "$STAGE"   # resize cache of frames that just changed
    # The front end's data cache, for the same reason and it is easy to miss:
    # `web/src/lib/library.ts` keeps the two library reads in `unstable_cache`
    # with a 60 s and a 3600 s lifetime, and that store is ON DISK under
    # .next/cache, so it survives the restart below. A corpus swap with it
    # intact serves the OLD corpus's videos from a page for up to an hour while
    # /healthz says everything is fine. The whole directory goes rather than the
    # one file inside it: the cost is a slower next build, and the cost of
    # naming an internal path that later changes is stale data nobody sees.
    rm -rf "$REPO_DIR/web/.next/cache"
    chown -R vidtheque:vidtheque "$DATA_DIR/vidtheque.db" "$DATA_DIR/keyframes"
    echo "$GEN" > "$DATA_DIR/.corpus-generation"
    echo "deploy: corpus $GEN in place ($CUR kept in corpus-previous)"
  fi
fi

systemctl restart vidtheque-worker vidtheque-mcp vidtheque-web vidtheque-caddy \
  || fail "restart"
sleep 5
# The worker and mcp on their own ports, then the same health check THROUGH THE
# EDGE, which is the path a visitor's request takes and the only listener the
# tunnel can reach.
for url in http://127.0.0.1:8081/healthz http://127.0.0.1:8100/healthz \
           http://127.0.0.1:8080/healthz; do
  curl -fsS -m 10 "$url" >/dev/null || fail "healthz $url"
done
# And one page, because /healthz is Python's and would answer exactly the same
# with the front end dead and the edge routing every page into a 502.
curl -fsS -m 15 -o /dev/null http://127.0.0.1:8080/ || fail "the landing page did not render"

echo "$DEP_ID" > "$STATE"
status success "live at $SHA"
echo "deploy: OK — $SHA is live"
