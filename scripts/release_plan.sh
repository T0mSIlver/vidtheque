#!/usr/bin/env bash
# Decide whether a release tag must build an image or may retag the previous
# release's (publishing.md §4). Prints `build=`, `prev=` and `reason=` lines for
# $GITHUB_OUTPUT. Anything it cannot prove unchanged is a build.
set -euo pipefail

usage() {
    echo "usage: scripts/release_plan.sh <worker|mcp|web> <git-tag>" >&2
    exit 2
}

[[ $# -eq 2 ]] || usage
name=$1
tag=$2

case "$name" in
    # uv.lock and the root pyproject.toml reach an image only through its base,
    # which the base tag already hashes.
    worker | mcp) paths=("$name/" .python-version scripts/image_inputs.sh ".github/workflows/build-$name.yml") ;;
    web) paths=(web/ .github/workflows/build-web.yml) ;;
    *) usage ;;
esac

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"

out() {
    echo "build=$1"
    echo "prev=${2:-}"
    echo "reason=$3"
    exit 0
}

semver='^v[0-9]+\.[0-9]+\.[0-9]+$'
[[ "$tag" =~ $semver ]] || out true "" "$tag is not a release tag"
prev=$(git describe --tags --abbrev=0 --match 'v[0-9]*.[0-9]*.[0-9]*' "$tag^" 2>/dev/null) \
    || out true "" "no release tag before $tag"
[[ "$prev" =~ $semver ]] || out true "" "previous tag $prev is not a release tag"

# The version bump touches every release; a file whose only change is the old
# version string becoming the new one does not count. Quoted, so a stray
# number elsewhere in a file still reads as a change.
old=${prev#v}
new=${tag#v}
while IFS= read -r file; do
    a=$(git show "$prev:$file" 2>/dev/null | sed "s/\"${old//./\\.}\"/\"@VERSION@\"/g") \
        || out true "$old" "$file is new since $prev"
    b=$(git show "$tag:$file" 2>/dev/null | sed "s/\"${new//./\\.}\"/\"@VERSION@\"/g") \
        || out true "$old" "$file was removed since $prev"
    [[ "$a" == "$b" ]] || out true "$old" "$file changed since $prev"
done < <(git diff --name-only "$prev" "$tag" -- "${paths[@]}")

if [[ "$name" != web ]]; then
    # The dependency set, as the base image tag sees it, at both releases.
    tmp=$(mktemp -d)
    trap 'git worktree remove --force "$tmp/prev" >/dev/null 2>&1 || true
          git worktree remove --force "$tmp/now" >/dev/null 2>&1 || true
          rm -rf "$tmp"' EXIT
    git worktree add --quiet --detach "$tmp/prev" "$prev"
    git worktree add --quiet --detach "$tmp/now" "$tag"
    [[ -x "$tmp/prev/scripts/image_inputs.sh" ]] || out true "$old" "$prev has no scripts/image_inputs.sh"
    was=$("$tmp/prev/scripts/image_inputs.sh" "$name" --tag)
    now=$("$tmp/now/scripts/image_inputs.sh" "$name" --tag)
    [[ "$was" == "$now" ]] || out true "$old" "base inputs changed since $prev ($was -> $now)"
fi

out false "$old" "inputs unchanged since $prev apart from the version string"
