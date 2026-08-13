#!/bin/bash
# ===========================================================================
# BOX:      the PRIVATE docker box (CT 9002, vidtheque-rw)
# GOES TO:  /usr/local/sbin/vidtheque-update   (root-owned, mode 0755)
# RUN BY:   a human, manually:  `vidtheque-update 0.0.2`
#           (or `vidtheque-update v0.0.2` — the leading v is stripped, because
#           the git tag has one and the image tags do not)
#
# Pull-based and REPO-FREE. This box never builds — the worker image is a
# ~28 GB CUDA build and a serving box has no business making it — and it does
# not need the git checkout either. The two compose files it runs are fetched
# from raw.githubusercontent.com PINNED TO THE RELEASE TAG, so the compose file
# and the image it names always come from the same commit; there is no "the
# checkout drifted from the running images" state to reason about.
#
# What lives on the box, and what this script NEVER touches:
#   $DEPLOY_DIR/.env               your configuration (only IMAGE_TAG is edited)
#   $DEPLOY_DIR/compose.local.yml  your bind mount and env_file overlay
# Both are box-local by design (deploy/compose.local.example.yml is the
# template). Everything else in $DEPLOY_DIR is disposable and overwritten here.
#
# The earliest tag this can deploy is the first release that carries
# deploy/compose.release.example.yml — before that the fetch 404s, correctly.
# ===========================================================================
set -euo pipefail

DEPLOY_DIR=${VIDTHEQUE_DEPLOY_DIR:-/srv/vidtheque-deploy}
RAW=https://raw.githubusercontent.com/T0mSIlver/vidtheque

fail() { echo "update: FAILED — $1" >&2; exit 1; }

TAG=${1:-}
[ -n "$TAG" ] || fail "usage: vidtheque-update <version>   e.g. vidtheque-update 0.0.2"
TAG=${TAG#v}
# This string is spliced into a URL and into .env, so it is validated rather
# than trusted: exactly three dotted numbers, nothing else.
printf '%s' "$TAG" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' \
  || fail "not a version: '$TAG' (expected 1.2.3, or v1.2.3)"

[ -d "$DEPLOY_DIR" ] || fail "$DEPLOY_DIR does not exist (set VIDTHEQUE_DEPLOY_DIR?)"
# Absolute, because the rollback trap below fires after a `cd` into it.
DEPLOY_DIR=$(cd "$DEPLOY_DIR" && pwd)
[ -f "$DEPLOY_DIR/.env" ] || fail "$DEPLOY_DIR/.env is missing — this box's configuration, never fetched"
[ -f "$DEPLOY_DIR/compose.local.yml" ] || \
  fail "$DEPLOY_DIR/compose.local.yml is missing — copy deploy/compose.local.example.yml and edit the path"

PREV=$(sed -n 's/^IMAGE_TAG=//p' "$DEPLOY_DIR/.env" | tail -1)
echo "update: $DEPLOY_DIR — ${PREV:-<unset>} -> $TAG"

# Fetch BOTH before installing EITHER: a half-fetched pair leaves a compose
# file from one release beside one from another, which merges into nonsense.
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
for f in docker-compose.yml compose.release.example.yml; do
  curl -fsS -m 30 "$RAW/v$TAG/deploy/$f" -o "$TMP/$f" || fail "fetch $RAW/v$TAG/deploy/$f"
done
install -m 644 "$TMP/docker-compose.yml"           "$DEPLOY_DIR/docker-compose.yml"
install -m 644 "$TMP/compose.release.example.yml"  "$DEPLOY_DIR/compose.release.yml"

# .env.prev is the rollback, so it is written before the edit and restored on
# any failure below — a half-run leaves the box exactly as re-runnable as it
# was, pointing at the release that was live when this started.
cp -p "$DEPLOY_DIR/.env" "$DEPLOY_DIR/.env.prev"
trap 'cp -p "$DEPLOY_DIR/.env.prev" "$DEPLOY_DIR/.env"; rm -rf "$TMP"' EXIT
if grep -q '^IMAGE_TAG=' "$DEPLOY_DIR/.env"; then
  sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$TAG/" "$DEPLOY_DIR/.env"
else
  printf '\n# Set by vidtheque-update. The GHCR tag, no leading v.\nIMAGE_TAG=%s\n' "$TAG" >> "$DEPLOY_DIR/.env"
fi

cd "$DEPLOY_DIR"
FILES=(-f docker-compose.yml -f compose.release.yml -f compose.local.yml)
# --project-name adopts the running stack rather than starting a second one
# beside it. The base file carries `name: vidtheque` too; saying it here means a
# release that moved or renamed that key cannot orphan what is already up.
DC=(docker compose --project-name vidtheque "${FILES[@]}")

"${DC[@]}" pull  || fail "pull $TAG — is the tag published? tags carry no leading v"
"${DC[@]}" up -d || fail "up -d $TAG"

# The .env is now the live one: past this point a failure is a bad release, not
# a bad run, and the operator rolls back with the line printed below.
trap 'rm -rf "$TMP"' EXIT

# Ports from the box's own .env, defaulting exactly as the compose file does.
# If your compose.local.yml unpublishes the worker (`ports: !reset null`, the
# public overlay's stance), drop the second URL — mcp reaches it as
# http://worker:8081 and there is nothing on the host to curl.
MCP_PORT=$(sed -n 's/^MCP_PORT=//p' .env | tail -1)
WORKER_PORT=$(sed -n 's/^WORKER_PORT=//p' .env | tail -1)
sleep 8
for url in "http://127.0.0.1:${MCP_PORT:-8080}/healthz" "http://127.0.0.1:${WORKER_PORT:-8081}/healthz"; do
  curl -fsS -m 10 "$url" >/dev/null || {
    echo "update: healthz FAILED at $url — $TAG is up but not answering" >&2
    echo "update: roll back with:  vidtheque-update ${PREV:-<the previous tag>}" >&2
    echo "update: logs:            docker compose --project-name vidtheque logs --tail 50" >&2
    exit 1
  }
done

echo "update: OK — $TAG is live"
echo "        mcp     ghcr.io/t0msilver/vidtheque-mcp:$TAG    127.0.0.1:${MCP_PORT:-8080}"
echo "        worker  ghcr.io/t0msilver/vidtheque-worker:$TAG 127.0.0.1:${WORKER_PORT:-8081}"
echo "        rollback: vidtheque-update ${PREV:-<previous tag>}"
echo
echo "HINT: the previous images are still on disk — that is what makes the"
echo "      rollback above a restart instead of a ~28 GB pull. Reclaim the"
echo "      space, and give that up, only when you are done watching this"
echo "      release:  docker image prune -a --filter 'until=168h'"
echo "      (plain \`docker image prune\` removes only dangling layers and will"
echo "      not free the old worker image, which still has its tag.)"
