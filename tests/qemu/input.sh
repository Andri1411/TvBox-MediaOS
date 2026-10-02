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
    "$vm" reboot
fi
"$vm" put "$ROOT/tests/qemu/input_test.py" /root/input_test.py
"$vm" put "$ROOT/tests/qemu/menu_test.py" /root/menu_test.py
failed=0
log "input checks (fake Xbox pad)"
"$vm" ssh 'cd /root && python input_test.py' || failed=1
log "system menu checks"
"$vm" ssh 'cd /root && python menu_test.py' || failed=1

# A real (QEMU USB) keyboard: its keys are not handled by inputd, sway's
# bindings open and drive the menu. Also checks that the menu is drawn.
log "keyboard and screen checks"
qmp=(python3 "$ROOT/scripts/qmp.py" "$BUILD_DIR/qemu-test/qmp.sock")
overlay() { "$vm" ssh 'curl -s 127.0.0.1:8080/api/state' | python3 -c 'import json,sys; print(json.load(sys.stdin)["overlay"])'; }
check() {  # check <description> <command...>
    local desc=$1; shift
    if "$@" >/dev/null; then printf '  PASS %s\n' "$desc"; else printf '  FAIL %s\n' "$desc"; failed=1; fi
}
shot="$BUILD_DIR/qemu-test"
"${qmp[@]}" screendump "$shot/menu-closed.ppm"
"${qmp[@]}" send-key ctrl-alt-m; sleep 2
check "keyboard: Ctrl+Alt+M opens the menu" test "$(overlay)" = menu
"${qmp[@]}" screendump "$shot/menu-open.ppm"
"${qmp[@]}" screendump "$shot/menu-open.png"
check "the menu is visible on screen" python3 "$ROOT/tests/qemu/screendiff.py" "$shot/menu-closed.ppm" "$shot/menu-open.ppm" --more 0.5
"${qmp[@]}" send-key down down down ret; sleep 2      # Mute
muted() { "$vm" ssh 'curl -s 127.0.0.1:8080/api/state' | grep -q '"muted": true'; }
check "keyboard: arrows and Enter drive the menu" muted
"${qmp[@]}" send-key ret esc; sleep 2
check "keyboard: Escape closes it" test "$(overlay)" = None
"${qmp[@]}" screendump "$shot/menu-after.ppm"
# (not an exact comparison: the placeholder screen's uptime line may change)
check "nothing is left on screen afterwards" python3 "$ROOT/tests/qemu/screendiff.py" "$shot/menu-closed.ppm" "$shot/menu-after.ppm" --less 0.05

((failed == 0)) || die "some checks failed"
log "all checks passed"
