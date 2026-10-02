#!/bin/bash
# Run a repo script inside the Arch builder container (or directly on an Arch
# host when CONTAINER=none). The repo is bind-mounted at the same path inside
# the container so paths in logs match the host.
#
#   scripts/run-in-builder.sh scripts/build-packages.sh [args...]
#   RUN_PRIVILEGED=1 scripts/run-in-builder.sh scripts/build-iso.sh
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

detect_runtime() {
    case ${CONTAINER:-auto} in
        none|docker|podman) echo "${CONTAINER}" ;;
        auto)
            if [[ -f /etc/arch-release ]] && command -v makepkg >/dev/null; then echo none
            elif command -v podman >/dev/null; then echo podman
            elif command -v docker >/dev/null; then echo docker
            else echo none
            fi ;;
        *) die "CONTAINER must be auto|docker|podman|none" ;;
    esac
}

runtime=$(detect_runtime)
if [[ ${1:-} == --print-runtime ]]; then echo "$runtime"; exit 0; fi
[[ $# -gt 0 ]] || die "usage: $0 <script> [args...]"

if [[ $runtime == none ]]; then
    require_arch
    if [[ ${RUN_PRIVILEGED:-0} == 1 && $EUID != 0 ]]; then
        exec sudo -E "$@"
    fi
    exec "$@"
fi

image=${BUILDER_IMAGE:-$NAME-builder:latest}
if ! "$runtime" image inspect "$image" >/dev/null 2>&1; then
    "$ROOT/scripts/builder-image.sh"
fi

args=(run --rm --network host
      -v "$ROOT:$ROOT" -w "$ROOT"
      -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)")
for v in NAME VERSION REPO_NAME SIGN_KEY BUILD_DIR PKGS \
         HTTPS_PROXY HTTP_PROXY NO_PROXY https_proxy http_proxy no_proxy; do
    [[ -n ${!v:-} ]] && args+=(-e "$v=${!v}")
done
if [[ ${RUN_PRIVILEGED:-0} == 1 ]]; then
    # mkarchiso needs mount(2) and loop devices.
    args+=(--privileged -e AS_ROOT=1)
fi
if [[ -n ${SIGN_KEY:-} && -d ${GNUPGHOME:-$HOME/.gnupg} ]]; then
    args+=(-v "${GNUPGHOME:-$HOME/.gnupg}:/home/builder/.gnupg")
fi
[[ -t 0 ]] && args+=(-it)

exec "$runtime" "${args[@]}" "$image" "$@"
