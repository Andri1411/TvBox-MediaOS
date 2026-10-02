#!/bin/bash
# Input layer test on the installed test VM: a fake Xbox controller is plugged
# in inside the guest and the daemon's output is checked (input_test.py).
#
#   tests/qemu/input.sh        needs the disk from `make qemu-install`
#   PUSH=1 tests/qemu/input.sh first install the packages from build/repo and reboot
#   KEEP_VM=1                  leave the VM running afterwards
# shellcheck source=../../scripts/lib.sh
. "$(dirname "$0")/../../scripts/lib.sh"
vm="$ROOT/tests/qemu/vm.sh"

"$vm" up
[[ ${KEEP_VM:-0} == 1 ]] || trap '"$vm" down' EXIT
if [[ ${PUSH:-0} == 1 ]]; then
    "$vm" push
    "$vm" ssh 'systemctl reboot' || true
    sleep 10
    "$vm" up
fi
"$vm" put "$ROOT/tests/qemu/input_test.py" /root/input_test.py
log "input checks"
"$vm" ssh 'python /root/input_test.py'
