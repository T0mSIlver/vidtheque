#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "usage: scripts/image_inputs.sh <worker|mcp> [--tag]" >&2
    exit 2
}

[[ $# -ge 1 && $# -le 2 ]] || usage
name=$1
mode=${2:-}
[[ -z "$mode" || "$mode" == "--tag" ]] || usage

case "$name" in
    worker)
        package=vidtheque-worker
        extras=(--extra gpu --extra nvml)
        ;;
    mcp)
        package=vidtheque-mcp
        extras=()
        ;;
    *) usage ;;
esac

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

export_requirements() {
    cd "$repo"
    uv export --frozen --no-emit-workspace --package "$package" "${extras[@]}"
}

if [[ "$mode" != "--tag" ]]; then
    export_requirements
    exit
fi

requirements=$(mktemp)
trap 'rm -f "$requirements"' EXIT
export_requirements > "$requirements"
cat "$requirements" "$repo/$name/Dockerfile.base" "$repo/.python-version" \
    | sha256sum \
    | cut -c1-16
