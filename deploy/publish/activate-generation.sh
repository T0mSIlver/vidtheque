#!/usr/bin/env bash
set -Eeuo pipefail

DATA_ROOT=${DATA_ROOT:-/srv/vidtheque-data}
DEPLOY_DIR=${DEPLOY_DIR:-/srv/vidtheque-deploy}
EDGE=${EDGE:-http://127.0.0.1:8080}
GENERATIONS="$DATA_ROOT/generations"
DRY_RUN=0
MODE=
REQUESTED_ID=
STEP=initialization
SWITCHED=0
OUTGOING_ID=
PREVIOUS_BEFORE=

log() {
    printf '%s %s\n' "$(date --iso-8601=seconds)" "$*"
}

print_command() {
    printf '%q ' "$@"
    printf '\n'
}

mutate() {
    if ((DRY_RUN)); then
        printf 'DRY-RUN '
        print_command "$@"
    else
        "$@"
    fi
}

usage() {
    echo "usage: $0 [--dry-run] [<id> | --ready | --rollback]" >&2
    exit 2
}

while (($#)); do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --ready|--rollback)
            [[ -z "$MODE" && -z "$REQUESTED_ID" ]] || usage
            MODE=$1
            ;;
        --*) usage ;;
        *)
            [[ -z "$MODE" && -z "$REQUESTED_ID" ]] || usage
            REQUESTED_ID=$1
            ;;
    esac
    shift
done
[[ -n "$MODE" || -n "$REQUESTED_ID" ]] || usage

current_id() {
    local target
    [[ -L "$DATA_ROOT/current" ]] || return 1
    target=$(readlink "$DATA_ROOT/current")
    [[ "$target" == generations/* && "$target" != */*/* ]] || return 1
    basename "$target"
}

reject_ready() {
    local reason=$1
    if [[ -f "$GENERATION_DIR/READY" ]]; then
        if ((DRY_RUN)); then
            printf 'DRY-RUN consume READY as REJECTED: %s\n' "$reason"
        else
            mv "$GENERATION_DIR/READY" "$GENERATION_DIR/REJECTED"
            printf '%s\n' "$reason" >"$GENERATION_DIR/REJECTED"
        fi
    fi
}

fail_before_switch() {
    local reason=$1
    log "rejected generation $GENERATION_ID: $reason"
    reject_ready "$reason"
    exit 1
}

rollback_after_failure() {
    local status=$?
    local message=${FAILURE_MESSAGE:-"command failed: $BASH_COMMAND (exit $status)"}
    trap - ERR
    if ((SWITCHED)); then
        log "failure during $STEP: $message; restoring ${OUTGOING_ID:-no current generation}"
        if ((DRY_RUN)); then
            log "dry run would restore the outgoing generation"
        elif [[ -n "$OUTGOING_ID" ]]; then
            ln -sfn "generations/$OUTGOING_ID" "$DATA_ROOT/.current.rollback"
            mv -Tf "$DATA_ROOT/.current.rollback" "$DATA_ROOT/current"
            (cd "$DEPLOY_DIR" && docker compose restart mcp) || true
        else
            rm -f "$DATA_ROOT/current"
            (cd "$DEPLOY_DIR" && docker compose restart mcp) || true
        fi
        if [[ -n "$PREVIOUS_BEFORE" ]]; then
            printf '%s\n' "$PREVIOUS_BEFORE" >"$GENERATIONS/.previous"
        else
            rm -f "$GENERATIONS/.previous"
        fi
        rm -f "$GENERATION_DIR/READY"
        printf 'step: %s\nmessage: %s\n' "$STEP" "$message" >"$GENERATION_DIR/FAILED"
    elif [[ -n "${GENERATION_DIR:-}" && -f "$GENERATION_DIR/READY" ]]; then
        log "failure during $STEP: $message"
        reject_ready "activation failed during $STEP: $message"
    fi
    exit "$status"
}
trap rollback_after_failure ERR

if [[ ! -d "$GENERATIONS" ]]; then
    if ((DRY_RUN)); then
        log "dry run cannot select a generation because $GENERATIONS does not exist"
        exit 1
    fi
    mkdir -p "$GENERATIONS"
fi
case "$MODE" in
    --ready)
        GENERATION_ID=$(
            find "$GENERATIONS" -mindepth 2 -maxdepth 2 -type f -name READY -printf '%h\n' \
                | while IFS= read -r directory; do basename "$directory"; done \
                | LC_ALL=C sort | tail -n 1
        )
        [[ -n "$GENERATION_ID" ]] || { log "no ready generation"; exit 0; }
        ;;
    --rollback)
        [[ -f "$GENERATIONS/.previous" ]] || { log "rollback refused: .previous is missing"; exit 1; }
        IFS= read -r GENERATION_ID <"$GENERATIONS/.previous"
        ;;
    *) GENERATION_ID=$REQUESTED_ID ;;
esac

GENERATION_DIR="$GENERATIONS/$GENERATION_ID"
if [[ ! "$GENERATION_ID" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+$ ]]; then
    log "refused invalid generation id: $GENERATION_ID"
    [[ "$MODE" != --ready || ! -d "$GENERATION_DIR" ]] || reject_ready "invalid generation id"
    exit 1
fi
[[ -d "$GENERATION_DIR" && ! -L "$GENERATION_DIR" ]] || {
    log "refused missing or linked generation: $GENERATION_ID"
    exit 1
}
[[ -f "$GENERATION_DIR/MANIFEST.json" ]] || fail_before_switch "MANIFEST.json is missing"
OUTGOING_ID=$(current_id || true)
if [[ -f "$GENERATIONS/.previous" ]]; then
    IFS= read -r PREVIOUS_BEFORE <"$GENERATIONS/.previous"
fi
[[ "$GENERATION_ID" != "$OUTGOING_ID" ]] || fail_before_switch "generation is already current"

STEP=verification
log "verifying generation $GENERATION_ID"
if ((DRY_RUN)); then
    printf 'DRY-RUN '
    print_command docker compose run --rm --no-deps -T mcp python -m \
        vidtheque_mcp.corpus_snapshot --verify "/data/generations/$GENERATION_ID"
elif ! VERIFY_OUTPUT=$(cd "$DEPLOY_DIR" && docker compose run --rm --no-deps -T mcp \
    python -m vidtheque_mcp.corpus_snapshot --verify "/data/generations/$GENERATION_ID" 2>&1); then
    fail_before_switch "verification failed: $VERIFY_OUTPUT"
else
    log "$VERIFY_OUTPUT"
fi

STEP=alignment
log "alignment change for $GENERATION_ID"
if [[ -n "$OUTGOING_ID" && -f "$GENERATIONS/$OUTGOING_ID/MANIFEST.json" ]]; then
    diff -u \
        <(jq --sort-keys '.alignment // []' "$GENERATIONS/$OUTGOING_ID/MANIFEST.json") \
        <(jq --sort-keys '.alignment // []' "$GENERATION_DIR/MANIFEST.json") || true
else
    jq --sort-keys '.alignment // []' "$GENERATION_DIR/MANIFEST.json"
fi

STEP=secret-copy
log "checking secret.key carry-forward for $GENERATION_ID"
if [[ -n "$OUTGOING_ID" && -f "$GENERATIONS/$OUTGOING_ID/secret.key" ]]; then
    log "copying secret.key from $OUTGOING_ID"
    mutate cp -p "$GENERATIONS/$OUTGOING_ID/secret.key" "$GENERATION_DIR/secret.key"
fi

STEP=switch
log "switching current to $GENERATION_ID"
if ((DRY_RUN)); then
    mutate ln -sfn "generations/$GENERATION_ID" "$DATA_ROOT/.current.new"
    mutate mv -Tf "$DATA_ROOT/.current.new" "$DATA_ROOT/current"
    [[ -z "$OUTGOING_ID" ]] || printf 'DRY-RUN write %q to %q\n' "$OUTGOING_ID" "$GENERATIONS/.previous"
else
    ln -sfn "generations/$GENERATION_ID" "$DATA_ROOT/.current.new"
    mv -Tf "$DATA_ROOT/.current.new" "$DATA_ROOT/current"
    [[ -z "$OUTGOING_ID" ]] || printf '%s\n' "$OUTGOING_ID" >"$GENERATIONS/.previous"
fi
SWITCHED=1

STEP=restart
log "restarting mcp"
if ((DRY_RUN)); then
    printf 'DRY-RUN '
    print_command docker compose restart mcp
else
    (cd "$DEPLOY_DIR" && docker compose restart mcp)
fi

STEP=health-check
log "checking edge health and corpus video count"
EXPECTED_VIDEOS=$(jq -er '.videos.total | numbers' "$GENERATION_DIR/MANIFEST.json")
if ((DRY_RUN)); then
    printf 'DRY-RUN poll %q and %q for 60 seconds; require videos=%q\n' \
        "$EDGE/healthz" "$EDGE/api/meta" "$EXPECTED_VIDEOS"
else
    deadline=$((SECONDS + 60))
    healthy=0
    while ((SECONDS < deadline)); do
        if curl -fsS "$EDGE/healthz" >/dev/null; then
            META=$(curl -fsS "$EDGE/api/meta" || true)
            ACTUAL_VIDEOS=$(jq -er '.videos | numbers' <<<"$META" 2>/dev/null || true)
            if [[ "$ACTUAL_VIDEOS" == "$EXPECTED_VIDEOS" ]]; then
                healthy=1
                break
            fi
        fi
        sleep 1
    done
    if [[ "$healthy" != 1 ]]; then
        FAILURE_MESSAGE="health check timed out after 60 seconds"
        false
    fi
fi

STEP=finalize
log "finalizing activation marker for $GENERATION_ID"
if [[ -f "$GENERATION_DIR/READY" ]]; then
    log "marking $GENERATION_ID activated"
    mutate mv "$GENERATION_DIR/READY" "$GENERATION_DIR/ACTIVATED"
fi

# The switch is good from here: a failed prune must not roll it back.
SWITCHED=0
STEP=prune
log "pruning generations older than current and previous"
# Only a generation whose READY was consumed: one without a marker may still be arriving.
mapfile -t GENERATION_DIRS < <(
    find "$GENERATIONS" -mindepth 1 -maxdepth 1 -type d -print \
        | while IFS= read -r directory; do
            [[ -f "$directory/MANIFEST.json" ]] || continue
            [[ -f "$directory/ACTIVATED" || -f "$directory/REJECTED" || -f "$directory/FAILED" ]] || continue
            printf '%s\n' "$directory"
        done
)
# Without a previous generation to keep, nothing is pruned: current must never stand alone.
if [[ -n "$OUTGOING_ID" && -f "$GENERATIONS/$OUTGOING_ID/MANIFEST.json" ]]; then
    for directory in "${GENERATION_DIRS[@]}"; do
        candidate=$(basename "$directory")
        if [[ "$candidate" != "$GENERATION_ID" && "$candidate" != "$OUTGOING_ID" ]]; then
            log "pruning generation $candidate"
            mutate rm -rf -- "$directory"
        fi
    done
fi

log "activated generation $GENERATION_ID"
