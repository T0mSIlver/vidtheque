#!/usr/bin/env bash
set -euo pipefail

PRIVATE_SSH=${PRIVATE_SSH:-root@192.168.1.43}
PUBLIC_SSH=${PUBLIC_SSH:-root@vidtheque}
PUBLIC_RSYNC=${PUBLIC_RSYNC:-corpus@192.168.1.42}
PRIVATE_DEPLOY_DIR=${PRIVATE_DEPLOY_DIR:-/srv/vidtheque-deploy}
PRIVATE_DATA_ROOT=${PRIVATE_DATA_ROOT:-/srv/vidtheque-data}
PUBLIC_ACTIVATE=${PUBLIC_ACTIVATE:-/srv/vidtheque-deploy/deploy/publish/activate-generation.sh}
PRIVATE_COMPOSE_FILES=${PRIVATE_COMPOSE_FILES:--f docker-compose.yml -f compose.release.yml -f compose.local.yml}

DRY_RUN=0
ROLLBACK=0
GENERATION=
KEEP_RULES=()

usage() {
    echo "usage: $0 --generation <id> [--keep-channel <name> | --keep-tag <tag>]... [--dry-run]" >&2
    echo "       $0 --rollback [--dry-run]" >&2
    exit 2
}

quote_command() {
    printf '%q ' "$@"
}

run() {
    if ((DRY_RUN)); then
        printf 'DRY-RUN '
        quote_command "$@"
        printf '\n'
    else
        "$@"
    fi
}

remote_command() {
    local rendered
    printf -v rendered '%q ' "$@"
    printf '%s' "${rendered% }"
}

step() {
    printf '==> %s\n' "$*"
}

while (($#)); do
    case "$1" in
        --generation)
            (($# >= 2)) || usage
            GENERATION=$2
            shift
            ;;
        --keep-channel|--keep-tag)
            (($# >= 2)) || usage
            KEEP_RULES+=("$1" "$2")
            shift
            ;;
        --rollback) ROLLBACK=1 ;;
        --dry-run) DRY_RUN=1 ;;
        *) usage ;;
    esac
    shift
done

print_public_status() {
    step "activation journal"
    run ssh "$PUBLIC_SSH" "$(remote_command journalctl -u vidtheque-corpus-activate -n 40 --no-pager)"
    step "public corpus video count"
    run ssh "$PUBLIC_SSH" "$(remote_command sh -c 'curl -fsS http://127.0.0.1:8080/api/meta | jq .videos')"
}

if ((ROLLBACK)); then
    [[ -z "$GENERATION" && ${#KEEP_RULES[@]} -eq 0 ]] || usage
    step "roll back the public corpus"
    run ssh "$PUBLIC_SSH" "$(remote_command "$PUBLIC_ACTIVATE" --rollback)"
    print_public_status
    exit 0
fi

[[ "$GENERATION" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]+$ ]] || usage
((${#KEEP_RULES[@]} > 0)) || usage
read -r -a COMPOSE_ARGS <<<"$PRIVATE_COMPOSE_FILES"

BUILD=(docker compose "${COMPOSE_ARGS[@]}" exec -T mcp python -m
    vidtheque_mcp.corpus_snapshot --data-dir /data --out-dir /data/generations
    --generation "$GENERATION" "${KEEP_RULES[@]}")
step "build generation $GENERATION on the private box"
printf -v PRIVATE_DEPLOY_Q '%q' "$PRIVATE_DEPLOY_DIR"
BUILD_Q=$(remote_command "${BUILD[@]}")
run ssh "$PRIVATE_SSH" "cd $PRIVATE_DEPLOY_Q && $BUILD_Q"

step "print the generation manifest"
run ssh "$PRIVATE_SSH" "$(remote_command sh -c "cat '$PRIVATE_DATA_ROOT/generations/$GENERATION/MANIFEST.json'")"

step "find the public current generation"
CURRENT_QUERY=$(remote_command sh -c \
    'target=$(readlink /srv/vidtheque-data/current 2>/dev/null || true); basename "$target"')
if ((DRY_RUN)); then
    run ssh "$PUBLIC_SSH" "$CURRENT_QUERY"
    PREVIOUS=
else
    PREVIOUS=$(ssh "$PUBLIC_SSH" "$CURRENT_QUERY")
fi

# An id that already exists on the public box would be rsynced over in place, under a
# generation that may be the one being served.
if ((!DRY_RUN)) && ssh "$PUBLIC_SSH" "$(remote_command test -e "/srv/vidtheque-data/generations/$GENERATION/ACTIVATED")"; then
    echo "generation $GENERATION was already activated on the public box; pick a new id" >&2
    exit 1
fi

# A rerun finds READY already in the source; it must still arrive last.
RSYNC_ARGS=(rsync -a --exclude=/READY)
[[ -z "$PREVIOUS" ]] || RSYNC_ARGS+=("--link-dest=../$PREVIOUS")
RSYNC_ARGS+=("$PRIVATE_DATA_ROOT/generations/$GENERATION/" "$PUBLIC_RSYNC:$GENERATION/")
step "transfer generation $GENERATION from the private box"
run ssh "$PRIVATE_SSH" "$(remote_command "${RSYNC_ARGS[@]}")"

READY_PATH="$PRIVATE_DATA_ROOT/generations/$GENERATION/READY"
step "transfer READY last"
TOUCH_Q=$(remote_command touch "$READY_PATH")
READY_RSYNC_Q=$(remote_command rsync -a "$READY_PATH" "$PUBLIC_RSYNC:$GENERATION/")
run ssh "$PRIVATE_SSH" "$TOUCH_Q && $READY_RSYNC_Q"

step "wait for public activation"
if ((DRY_RUN)); then
    run ssh "$PUBLIC_SSH" "$(remote_command sh -c \
        "test -f '/srv/vidtheque-data/generations/$GENERATION/ACTIVATED' -o -f '/srv/vidtheque-data/generations/$GENERATION/REJECTED' -o -f '/srv/vidtheque-data/generations/$GENERATION/FAILED'")"
else
    deadline=$((SECONDS + 600))
    marker=
    while ((SECONDS < deadline)); do
        for candidate in ACTIVATED REJECTED FAILED; do
            if ssh "$PUBLIC_SSH" test -f \
                "/srv/vidtheque-data/generations/$GENERATION/$candidate"; then
                marker=$candidate
                break 2
            fi
        done
        sleep 5
    done
    [[ -n "$marker" ]] || { echo "activation timed out after 10 minutes" >&2; exit 1; }
    echo "activation result: $marker"
fi

print_public_status
if [[ -n "${marker:-}" && "$marker" != ACTIVATED ]]; then
    exit 1
fi
