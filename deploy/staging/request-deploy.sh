#!/bin/bash
# The deploy button. Run from ANY machine with `gh` authed as T0mSIlver:
#   ./deploy/staging/request-deploy.sh            # deploys main's HEAD
#   ./deploy/staging/request-deploy.sh <sha|ref>  # deploys that (rollback = old sha)
# The box (CT 9001) polls the Deployments API once a minute and does the rest;
# watch it land at https://github.com/T0mSIlver/vidtheque/deployments
set -euo pipefail
REF=${1:-main}
gh api "repos/T0mSIlver/vidtheque/deployments" \
  -f ref="$REF" -F auto_merge=false -f 'required_contexts[]' \
  -f description="requested via request-deploy.sh" \
  --jq '"deployment \(.id) created for \(.ref) — the box picks it up within a minute"'
