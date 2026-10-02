# shellcheck shell=bash
# Shared helpers for build scripts. Source, don't execute.

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
NAME=${NAME:-tvbox}
VERSION=${VERSION:-0.0.1}
REPO_NAME=${REPO_NAME:-$NAME}
BUILD_DIR=${BUILD_DIR:-build}
[[ $BUILD_DIR = /* ]] || BUILD_DIR="$ROOT/$BUILD_DIR"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m==> WARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m==> ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# Package version for our own packages: VERSION plus commit count, so every
# commit produces a strictly newer pkgver and `pacman -Syu` picks it up.
pkg_version() {
    local n
    n=$(git -C "$ROOT" rev-list --count HEAD 2>/dev/null || echo 0)
    printf '%s.r%s\n' "$VERSION" "$n"
}

require_arch() {
    [[ -f /etc/arch-release ]] || die "this step must run on Arch (use the builder container: CONTAINER=auto)"
}

# newest_file <glob...>: print the most recently modified existing file.
newest_file() {
    local f best=""
    for f in "$@"; do
        [[ -f $f ]] || continue
        [[ -z $best || $f -nt $best ]] && best=$f
    done
    [[ -n $best ]] && printf '%s\n' "$best"
}
