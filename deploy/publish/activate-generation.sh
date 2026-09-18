#!/usr/bin/env bash
# Activate a corpus generation on the public box (docs/design/publishing.md §2.3). Runs as root.
#
# Everything under generations/ is writable by uid 10001: the rsync sender and the public-facing
# mcp container. So nothing this script decides on, and nothing it writes, lives there. Its state
# is in STATE_DIR, and inside generations/ it only reads MANIFEST.json, unlinks READY and prunes.
set -Eeuo pipefail

DATA_ROOT=${DATA_ROOT:-/srv/vidtheque-data}
DEPLOY_DIR=${DEPLOY_DIR:-/srv/vidtheque-deploy}
STATE_DIR=${STATE_DIR:-/var/lib/vidtheque-activate}
EDGE=${EDGE:-http://127.0.0.1:8080}
GENERATIONS="$DATA_ROOT/generations"
MARKERS="$STATE_DIR/markers"
ID_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+$'
DRY_RUN=0
MODE=
REQUESTED_ID=
STEP=initialization
SWITCHED=0
OUTGOING_ID=
PREVIOUS_BEFORE=
GENERATION_ID=

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

has_marker() { # id, kind
    [[ -f "$MARKERS/$1.$2" ]]
}

# One marker per id, the latest outcome. READY is unlinked, never renamed or written through.
mark() { # id, kind, text
    local id=$1 kind=$2 text=$3
    if ((DRY_RUN)); then
        printf 'DRY-RUN mark %s %s: %s\n' "$id" "$kind" "$text"
        return
    fi
    rm -f -- "$MARKERS/$id.ACTIVATED" "$MARKERS/$id.REJECTED" "$MARKERS/$id.FAILED"
    printf '%s\n' "$text" >"$MARKERS/$id.$kind"
    rm -f -- "$GENERATIONS/$id/READY"
}

fail_before_switch() {
    local reason=$1
    log "rejected generation $GENERATION_ID: $reason"
    mark "$GENERATION_ID" REJECTED "$reason"
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
        else
            if [[ -n "$OUTGOING_ID" ]]; then
                ln -sfn "generations/$OUTGOING_ID" "$DATA_ROOT/.current.rollback"
                mv -Tf "$DATA_ROOT/.current.rollback" "$DATA_ROOT/current"
            else
                rm -f "$DATA_ROOT/current"
            fi
            (cd "$DEPLOY_DIR" && docker compose restart mcp) || true
            if [[ -n "$PREVIOUS_BEFORE" ]]; then
                printf '%s\n' "$PREVIOUS_BEFORE" >"$STATE_DIR/previous"
            else
                rm -f "$STATE_DIR/previous"
            fi
            mark "$GENERATION_ID" FAILED "step: $STEP; $message"
        fi
    elif [[ -n "$GENERATION_ID" ]]; then
        log "failure during $STEP: $message"
        mark "$GENERATION_ID" REJECTED "activation failed during $STEP: $message"
    fi
    exit "$status"
}
trap rollback_after_failure ERR

# `current` is renamed into DATA_ROOT, so DATA_ROOT must belong to whoever runs this and to
# nobody else: a directory uid 10001 could write is one where it could swap the symlink itself.
[[ -d "$DATA_ROOT" && ! -L "$DATA_ROOT" ]] || { log "refused: $DATA_ROOT is not a directory"; exit 1; }
ROOT_OWNER=$(stat -c '%u' "$DATA_ROOT")
ROOT_MODE=$(stat -c '%a' "$DATA_ROOT")
if [[ "$ROOT_OWNER" != "$(id -u)" ]] || (((8#$ROOT_MODE & 8#022) != 0)); then
    log "refused: $DATA_ROOT must be owned by uid $(id -u) and not group- or world-writable (uid $ROOT_OWNER, mode $ROOT_MODE)"
    exit 1
fi
[[ -d "$GENERATIONS" && ! -L "$GENERATIONS" ]] || { log "refused: $GENERATIONS is missing"; exit 1; }

if ((!DRY_RUN)); then
    install -d -m 0700 "$STATE_DIR" "$MARKERS"
    # A manual run and the path unit must not interleave two switches.
    exec 9>"$STATE_DIR/lock"
    flock 9
fi

case "$MODE" in
    --ready)
        mapfile -t READY_IDS < <(
            find "$GENERATIONS" -mindepth 2 -maxdepth 2 -name READY -printf '%h\n' \
                | while IFS= read -r directory; do basename "$directory"; done \
                | LC_ALL=C sort
        )
        ((${#READY_IDS[@]})) || { log "no ready generation"; exit 0; }
        GENERATION_ID=${READY_IDS[-1]}
        # The newest wins; an older READY left beside it would otherwise re-trigger the unit
        # and end with `current` on the older generation.
        for superseded in "${READY_IDS[@]::${#READY_IDS[@]}-1}"; do
            if [[ "$superseded" =~ $ID_RE ]]; then
                log "generation $superseded superseded by $GENERATION_ID"
                mark "$superseded" REJECTED "superseded by $GENERATION_ID"
            else
                mutate rm -f -- "$GENERATIONS/$superseded/READY"
            fi
        done
        ;;
    --rollback)
        [[ -f "$STATE_DIR/previous" ]] || { log "rollback refused: no previous generation recorded"; exit 1; }
        IFS= read -r GENERATION_ID <"$STATE_DIR/previous"
        ;;
    *) GENERATION_ID=$REQUESTED_ID ;;
esac

GENERATION_DIR="$GENERATIONS/$GENERATION_ID"
if [[ ! "$GENERATION_ID" =~ $ID_RE ]]; then
    log "refused invalid generation id: $GENERATION_ID"
    mutate rm -f -- "$GENERATION_DIR/READY"
    GENERATION_ID=
    exit 1
fi
[[ -d "$GENERATION_DIR" && ! -L "$GENERATION_DIR" ]] || fail_before_switch "generation directory is missing or a link"
[[ -f "$GENERATION_DIR/MANIFEST.json" && ! -L "$GENERATION_DIR/MANIFEST.json" ]] \
    || fail_before_switch "MANIFEST.json is missing or a link"
OUTGOING_ID=$(current_id || true)
if [[ -f "$STATE_DIR/previous" ]]; then
    IFS= read -r PREVIOUS_BEFORE <"$STATE_DIR/previous"
fi
[[ "$GENERATION_ID" != "$OUTGOING_ID" ]] || fail_before_switch "generation is already current"

STEP=verification
# A generation served before was opened for writing by the server; its file no longer hashes
# to the manifest, so a return to it checks everything but the size and hash. Whether it was
# served is this script's own record, not a file the generation's owner could plant.
VERIFY_ARGS=(--verify "/data/generations/$GENERATION_ID")
! has_marker "$GENERATION_ID" ACTIVATED || VERIFY_ARGS+=(--served)
log "verifying generation $GENERATION_ID"
if ((DRY_RUN)); then
    printf 'DRY-RUN '
    print_command docker compose run --rm --no-deps -T mcp python -m \
        vidtheque_mcp.corpus_snapshot "${VERIFY_ARGS[@]}"
elif ! VERIFY_OUTPUT=$(cd "$DEPLOY_DIR" && docker compose run --rm --no-deps -T mcp \
    python -m vidtheque_mcp.corpus_snapshot "${VERIFY_ARGS[@]}" 2>&1); then
    fail_before_switch "verification failed: $VERIFY_OUTPUT"
else
    log "$VERIFY_OUTPUT"
fi
EXPECTED_VIDEOS=$(jq -er '.videos.total | numbers' "$GENERATION_DIR/MANIFEST.json") \
    || fail_before_switch "MANIFEST.json has no videos.total"

STEP=alignment
log "alignment change for $GENERATION_ID"
if [[ -n "$OUTGOING_ID" && -f "$GENERATIONS/$OUTGOING_ID/MANIFEST.json" && ! -L "$GENERATIONS/$OUTGOING_ID/MANIFEST.json" ]]; then
    diff -u \
        <(jq --sort-keys '.alignment // []' "$GENERATIONS/$OUTGOING_ID/MANIFEST.json") \
        <(jq --sort-keys '.alignment // []' "$GENERATION_DIR/MANIFEST.json") || true
else
    jq --sort-keys '.alignment // []' "$GENERATION_DIR/MANIFEST.json"
fi

STEP=switch
log "switching current to $GENERATION_ID"
if ((DRY_RUN)); then
    mutate ln -sfn "generations/$GENERATION_ID" "$DATA_ROOT/.current.new"
    mutate mv -Tf "$DATA_ROOT/.current.new" "$DATA_ROOT/current"
else
    ln -sfn "generations/$GENERATION_ID" "$DATA_ROOT/.current.new"
    mv -Tf "$DATA_ROOT/.current.new" "$DATA_ROOT/current"
    [[ -z "$OUTGOING_ID" ]] || printf '%s\n' "$OUTGOING_ID" >"$STATE_DIR/previous"
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
mark "$GENERATION_ID" ACTIVATED "activated $(date --iso-8601=seconds)"

# The switch is good from here: a failed prune must not roll it back.
SWITCHED=0
STEP=prune
log "pruning generations older than current and previous"
# Without a previous generation to keep, nothing is pruned: current must never stand alone.
if [[ -n "$OUTGOING_ID" ]]; then
    while IFS= read -r directory; do
        candidate=$(basename "$directory")
        [[ "$candidate" =~ $ID_RE ]] || continue
        [[ "$candidate" != "$GENERATION_ID" && "$candidate" != "$OUTGOING_ID" ]] || continue
        # Only one this script has an outcome for: a directory without one may still be arriving.
        has_marker "$candidate" ACTIVATED || has_marker "$candidate" REJECTED \
            || has_marker "$candidate" FAILED || continue
        log "pruning generation $candidate"
        mutate rm -rf -- "$directory"
        mutate rm -f -- "$MARKERS/$candidate.ACTIVATED" "$MARKERS/$candidate.REJECTED" "$MARKERS/$candidate.FAILED"
    done < <(find "$GENERATIONS" -mindepth 1 -maxdepth 1 -type d -print)
fi

log "activated generation $GENERATION_ID"
