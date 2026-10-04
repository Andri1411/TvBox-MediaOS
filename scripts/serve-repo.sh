#!/bin/bash
# Serve $BUILD_DIR over HTTP for QEMU guests. Inside a guest using QEMU user
# networking the host is 10.0.2.2, so the repo is at
#   http://10.0.2.2:${REPO_HTTP_PORT:-8800}/repo
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
port=${REPO_HTTP_PORT:-8800}
[[ -d $BUILD_DIR/repo ]] || die "no repo; run 'make repo' first"
log "serving $BUILD_DIR/repo on :$port (guest URL: http://10.0.2.2:$port/repo)"
exec python3 -m http.server --directory "$BUILD_DIR" --bind 127.0.0.1 "$port"   # QEMU guests reach it as 10.0.2.2
