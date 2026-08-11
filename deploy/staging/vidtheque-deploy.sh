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
$RUN uv sync --frozen --group gpu --quiet         || fail "uv sync"

# Self-update: keep the installed copy current with the tree just checked out,
# so a fix to this script ships like any other commit (field lesson 2026-08-11:
# a hand-patched installed copy + a re-run install command = silently dead
# mechanism). Applies from the NEXT run — this run continues as loaded.
cmp -s "$REPO_DIR/deploy/staging/vidtheque-deploy.sh" /usr/local/sbin/vidtheque-deploy || {
  install -m 755 "$REPO_DIR/deploy/staging/vidtheque-deploy.sh" /usr/local/sbin/vidtheque-deploy
  echo "deploy: self-updated /usr/local/sbin/vidtheque-deploy from $SHA"
}

systemctl restart vidtheque-worker vidtheque-mcp  || fail "restart"
sleep 5
for url in http://127.0.0.1:8081/healthz http://127.0.0.1:8100/healthz; do
  curl -fsS -m 10 "$url" >/dev/null || fail "healthz $url"
done

echo "$DEP_ID" > "$STATE"
status success "live at $SHA"
echo "deploy: OK — $SHA is live"
