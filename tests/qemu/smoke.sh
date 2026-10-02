#!/bin/bash
# Harness self-test: boot the UEFI machine with an empty disk, headless, and
# check that firmware output reaches the serial log and QMP answers
# (screendump + quit). Uses a throwaway state dir; never touches build/qemu.
# shellcheck source=../../scripts/lib.sh
. "$(dirname "$0")/../../scripts/lib.sh"

timeout_s=${SMOKE_TIMEOUT:-180}
state=$(mktemp -d)
trap 'kill "$qpid" 2>/dev/null || true; rm -rf "$state"' EXIT

QEMU_STATE_DIR=$state QEMU_DISPLAY=none QEMU_DISK_SIZE=1G QEMU_MEM=1G QEMU_CPUS=1 \
QEMU_SSH_PORT=0 QEMU_HUB_PORT=0 \
    "$ROOT/scripts/qemu.sh" disk >"$state/qemu.out" 2>&1 &
qpid=$!

# OVMF prints BdsDxe messages, then drops into the UEFI shell or boot manager.
deadline=$((SECONDS + timeout_s))
until grep -qaE 'BdsDxe|Shell>|UEFI Interactive Shell' "$state/serial.log" 2>/dev/null; do
    kill -0 "$qpid" 2>/dev/null || { cat "$state/qemu.out" >&2; die "QEMU exited early"; }
    ((SECONDS < deadline)) || { cat "$state/qemu.out" >&2; die "no firmware output after ${timeout_s}s"; }
    sleep 2
done
log "firmware reached serial console"

python3 "$ROOT/scripts/qmp.py" "$state/qmp.sock" screendump "$state/screen.ppm"
[[ -s $state/screen.ppm ]] || die "QMP screendump produced nothing"
log "QMP screendump OK ($(head -c 15 "$state/screen.ppm" | tr '\n' ' '))"

python3 "$ROOT/scripts/qmp.py" "$state/qmp.sock" send-key esc ctrl-alt-f2
python3 "$ROOT/scripts/qmp.py" "$state/qmp.sock" quit
wait "$qpid" || true
log "smoke test passed"
