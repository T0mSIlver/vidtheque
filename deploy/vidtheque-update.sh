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
# not need the git checkout either. The two compose files it runs and the
# edge's Caddyfile are fetched from raw.githubusercontent.com PINNED TO THE
# RELEASE TAG, so the compose file, the routing rule and the images they name
# always come from the same commit; there is no "the checkout drifted from the
# running images" state to reason about. THE CADDYFILE IS PART OF THAT: it
# decides which of two processes answers a path, so a release that moves a
# route moves the rule with it.
#
# What lives on the box, and what this script NEVER touches:
#   $DEPLOY_DIR/.env               your configuration (only IMAGE_TAG is edited)
#   $DEPLOY_DIR/compose.local.yml  your bind mount and env_file overlay
# Both are box-local by design (deploy/compose.local.example.yml is the
# template). Everything else in $DEPLOY_DIR is disposable and overwritten here.
#
# Before it switches tags it takes an online backup of vidtheque.db and auth.db
# into $VIDTHEQUE_DATA_DIR/backups/ (the box's data mount), checks it, and
# keeps the last $KEEP. A failed backup stops the update with nothing changed;
# `--no-backup` skips it, for a rollback away from a damaged database.
#
# The earliest tag this can deploy is the first release that carries
# deploy/compose.release.example.yml AND deploy/Caddyfile — before either, the
# fetch 404s, correctly. Deploying a pre-edge release is therefore a manual
# job: that stack published mcp's own port and had no front end.
# ===========================================================================
set -euo pipefail

DEPLOY_DIR=${VIDTHEQUE_DEPLOY_DIR:-/srv/vidtheque-deploy}
RAW=https://raw.githubusercontent.com/T0mSIlver/vidtheque
# Backups kept on the data mount. vidtheque.db was 1.2 GB on 2026-10-03.
KEEP=3

fail() { echo "update: FAILED — $1" >&2; exit 1; }

BACKUP=1
if [ "${1:-}" = --no-backup ]; then BACKUP=0; shift; fi
TAG=${1:-}
[ -n "$TAG" ] || fail "usage: vidtheque-update [--no-backup] <version>   e.g. vidtheque-update 0.0.2"
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

# A box running the captions-only preset (docs/self-host.md) has no worker; its
# overlay is fetched and applied like the release one.
CAPTIONS=0
[ -f "$DEPLOY_DIR/compose.captions.yml" ] && CAPTIONS=1
PREV=$(sed -n 's/^IMAGE_TAG=//p' "$DEPLOY_DIR/.env" | tail -1)
echo "update: $DEPLOY_DIR — ${PREV:-<unset>} -> $TAG"

# Fetch ALL THREE before installing ANY: a half-fetched set leaves a compose
# file from one release beside one from another, which merges into nonsense —
# and since the edge exists, beside a Caddyfile that may route a path the
# release it names does not serve.
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
FETCH=(docker-compose.yml compose.release.example.yml Caddyfile)
[ "$CAPTIONS" = 1 ] && FETCH+=(compose.captions.example.yml)
for f in "${FETCH[@]}"; do
  curl -fsS -m 30 "$RAW/v$TAG/deploy/$f" -o "$TMP/$f" || fail "fetch $RAW/v$TAG/deploy/$f"
done

cd "$DEPLOY_DIR"
FILES=(-f docker-compose.yml -f compose.release.yml)
[ "$CAPTIONS" = 1 ] && FILES+=(-f compose.captions.yml)
FILES+=(-f compose.local.yml)
# --project-name adopts the running stack rather than starting a second one
# beside it. The base file carries `name: vidtheque` too; saying it here means a
# release that moved or renamed that key cannot orphan what is already up.
DC=(docker compose --project-name vidtheque "${FILES[@]}")

# The backup runs before anything is installed, so it goes through the release
# that is live now: its compose files, its .env, its mcp image. The sqlite3
# backup API copies a consistent snapshot while mcp keeps writing.
SNAP=
if [ "$BACKUP" = 1 ]; then
  { [ -f docker-compose.yml ] && [ -f compose.release.yml ]; } || \
    fail "no live compose files to back up through — first deploy? rerun with --no-backup"
  if [ -n "$("${DC[@]}" ps --status running --quiet mcp 2>/dev/null)" ]; then
    RUN=("${DC[@]}" exec -T mcp python -)
  else
    RUN=("${DC[@]}" run --rm --no-deps -T --entrypoint python mcp -)
  fi
  SNAP=$("${RUN[@]}" "${PREV:-unset}" "$KEEP" <<'PY'
import os, shutil, sqlite3, sys, time
from pathlib import Path

data = Path(os.environ["VIDTHEQUE_DATA_DIR"])
label, keep = sys.argv[1], int(sys.argv[2])
names = [n for n in ("vidtheque.db", "auth.db") if (data / n).is_file()]
if "vidtheque.db" not in names:
    sys.exit(f"no vidtheque.db in {data}")
# Room for the copies plus 1 GiB, so the backup cannot fill the live database's disk.
need = sum((data / n).stat().st_size for n in names) + 2**30
free = shutil.disk_usage(data).free
if free < need:
    sys.exit(f"{free >> 20} MiB free in {data}, need {need >> 20} MiB")

root = data / "backups"
root.mkdir(exist_ok=True)
for stale in root.glob("*.partial"):
    shutil.rmtree(stale)
dest = root / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{label}"
work = dest.with_name(dest.name + ".partial")
work.mkdir()
try:
    for n in names:
        src = sqlite3.connect(data / n, timeout=60)
        dst = sqlite3.connect(work / n)
        src.backup(dst)
        dst.close()
        src.close()
        check = sqlite3.connect(work / n)
        result = check.execute("pragma quick_check").fetchall()
        check.close()
        if result != [("ok",)]:
            raise RuntimeError(f"quick_check on the copy of {n}: {result[:3]}")
except Exception as exc:
    shutil.rmtree(work)
    sys.exit(f"{type(exc).__name__}: {exc}")
work.rename(dest)
done = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.endswith(".partial"))
for old in done[:-keep]:
    shutil.rmtree(old)
print(dest)
PY
  ) || fail "backup of $DEPLOY_DIR's databases — nothing was changed"
  echo "update: backup $SNAP (mcp's data dir), quick_check ok, last $KEEP kept"
fi
install -m 644 "$TMP/docker-compose.yml"           "$DEPLOY_DIR/docker-compose.yml"
install -m 644 "$TMP/compose.release.example.yml"  "$DEPLOY_DIR/compose.release.yml"
# The edge's rule, pinned to the same tag as the images it routes to. The
# compose file mounts it read-only from beside itself.
install -m 644 "$TMP/Caddyfile"                    "$DEPLOY_DIR/Caddyfile"
if [ "$CAPTIONS" = 1 ]; then
  install -m 644 "$TMP/compose.captions.example.yml" "$DEPLOY_DIR/compose.captions.yml"
fi

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

# Code first is wrong when the release migrated the schema: the previous release
# refuses a newer database, so the data goes back before the tag does. The -wal
# and -shm files go too, or SQLite replays the newer log onto the old copy.
rollback_hint() {
  echo "rollback: vidtheque-update ${PREV:-<previous tag>}"
  [ -n "$SNAP" ] || return 0
  local dc="docker compose --project-name vidtheque ${FILES[*]}"
  echo "  if that release will not start on this database, restore the backup first"
  echo "  (anything indexed since the backup is lost):"
  echo "    cd $DEPLOY_DIR"
  echo "    $dc stop mcp"
  echo "    $dc run --rm --no-deps --entrypoint sh mcp -c 'cd \$VIDTHEQUE_DATA_DIR && rm -f *.db-wal *.db-shm && cp -p backups/${SNAP##*/}/*.db .'"
  echo "    vidtheque-update ${PREV:-<previous tag>}"
}

"${DC[@]}" pull  || fail "pull $TAG — is the tag published? tags carry no leading v"
"${DC[@]}" up -d || fail "up -d $TAG"

# The .env is now the live one: past this point a failure is a bad release, not
# a bad run, and the operator rolls back with the line printed below.
trap 'rm -rf "$TMP"' EXIT

# Ports from the box's own .env, defaulting exactly as the compose file does.
# The first URL goes THROUGH THE EDGE, because that is the only listener the
# stack publishes now: mcp has no host port, so this checks caddy's routing and
# Python's health in one request — which is also the path a visitor takes.
# If your compose.local.yml unpublishes the worker (`ports: !reset null`, the
# public overlay's stance), drop the second URL — mcp reaches it as
# http://worker:8081 and there is nothing on the host to curl.
EDGE_PORT=$(sed -n 's/^EDGE_PORT=//p' .env | tail -1)
WORKER_PORT=$(sed -n 's/^WORKER_PORT=//p' .env | tail -1)
URLS=("http://127.0.0.1:${EDGE_PORT:-8080}/healthz")
[ "$CAPTIONS" = 1 ] || URLS+=("http://127.0.0.1:${WORKER_PORT:-8081}/healthz")
sleep 8
for url in "${URLS[@]}"; do
  curl -fsS -m 10 "$url" >/dev/null || {
    echo "update: healthz FAILED at $url — $TAG is up but not answering" >&2
    rollback_hint >&2
    echo "update: logs:            docker compose --project-name vidtheque logs --tail 50" >&2
    exit 1
  }
done

echo "update: OK — $TAG is live"
echo "        edge    caddy (deploy/Caddyfile)                127.0.0.1:${EDGE_PORT:-8080}"
echo "        web     ghcr.io/t0msilver/vidtheque-web:$TAG    (behind the edge)"
echo "        mcp     ghcr.io/t0msilver/vidtheque-mcp:$TAG    (behind the edge)"
if [ "$CAPTIONS" = 1 ]; then
  echo "        worker  none (captions-only)"
else
  echo "        worker  ghcr.io/t0msilver/vidtheque-worker:$TAG 127.0.0.1:${WORKER_PORT:-8081}"
fi
echo
rollback_hint
echo
echo "HINT: the previous images are still on disk — that is what makes the"
echo "      rollback above a restart instead of a ~28 GB pull. Reclaim the"
echo "      space, and give that up, only when you are done watching this"
echo "      release:  docker image prune -a --filter 'until=168h'"
echo "      (plain \`docker image prune\` removes only dangling layers and will"
echo "      not free the old worker image, which still has its tag.)"
