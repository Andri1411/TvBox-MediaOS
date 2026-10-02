#!/bin/bash
# Build the Arch builder image (tools/builder.Dockerfile).
# EXTRA_CA_CERT=/path/to/ca.crt bakes an additional trusted CA into the image,
# for networks with a TLS-intercepting proxy.
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

runtime=$("$ROOT/scripts/run-in-builder.sh" --print-runtime)
[[ $runtime != none ]] || die "no container runtime (docker/podman) found"

ctx=$(mktemp -d)
trap 'rm -rf "$ctx"' EXIT
cp "$ROOT/tools/builder.Dockerfile" "$ctx/Dockerfile"
cp "$ROOT/tools/builder-entrypoint.sh" "$ctx/"
if [[ -n ${EXTRA_CA_CERT:-} ]]; then
    cp "$EXTRA_CA_CERT" "$ctx/extra-ca.crt"
else
    : > "$ctx/extra-ca.crt"
fi

args=(--network host -t "${BUILDER_IMAGE:-$NAME-builder:latest}")
for v in HTTPS_PROXY HTTP_PROXY NO_PROXY https_proxy http_proxy no_proxy; do
    [[ -n ${!v:-} ]] && args+=(--build-arg "$v=${!v}")
done

log "building builder image with $runtime"
"$runtime" build "${args[@]}" "$ctx"
