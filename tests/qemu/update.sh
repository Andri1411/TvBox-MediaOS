#!/bin/bash
# Update flow end to end, with reboots, on the installed test VM:
#   update through the hub (as the TV and the phone do) → fallback recorded →
#   reboot into the new version → two boots that never get confirmed → GRUB
#   starts the fallback snapshot → "keep this backup" → rolled back for good.
#
#   make repo VERSION=<higher than installed>   # e.g. VERSION=0.0.9
#   tests/qemu/update.sh                        # (make qemu-update)
#
# Test-only change in the VM: [tvbox] points at this machine's build/repo
# (make serve-repo) and accepts unsigned packages.
# Remote commands are single-quoted on purpose (they expand in the VM).
# shellcheck disable=SC2016
# shellcheck source=../../scripts/lib.sh
. "$(dirname "$0")/../../scripts/lib.sh"
vm="$ROOT/tests/qemu/vm.sh"
failed=0
check() {  # check <description> <condition...>
    local desc=$1; shift
    if "$@"; then printf '  PASS %s\n' "$desc"; else printf '  FAIL %s\n' "$desc"; failed=1; fi
}
api() { "$vm" ssh "curl -s -X POST -d '$1' 127.0.0.1:8080/api/cmd" >/dev/null; }
update() {  # update <python expression on u = state["update"]>
    "$vm" ssh "curl -s 127.0.0.1:8080/api/state" | python3 -c "import json,sys; u=json.load(sys.stdin)['update']; print($1)"
}
wait_hub() { "$vm" ssh 'for i in $(seq 60); do curl -sf 127.0.0.1:8080/api/state | grep -q "\"overlay\"\]" && exit 0; sleep 1; done; exit 1'; }
version() { "$vm" ssh 'pacman -Q tvbox-core' | awk '{print $2}'; }
booted() { "$vm" ssh 'grep -o "rootflags=subvol=[^ ]*" /proc/cmdline' | sed 's/rootflags=subvol=//'; }

"$vm" up
[[ ${KEEP_VM:-0} == 1 ]] || trap '"$vm" down; kill $server 2>/dev/null' EXIT
REPO_HTTP_PORT=${REPO_HTTP_PORT:-8800} "$ROOT/scripts/serve-repo.sh" >"$BUILD_DIR/serve-repo.log" 2>&1 &
server=$!
sleep 1
kill -0 "$server" 2>/dev/null || die "could not serve the repo (port ${REPO_HTTP_PORT:-8800} in use?), see $BUILD_DIR/serve-repo.log"
"$vm" ssh 'sed -i "/^\[tvbox\]/,/^\$/c\[tvbox]\nSigLevel = Optional TrustAll\nServer = http://10.0.2.2:'"${REPO_HTTP_PORT:-8800}"'/repo\n" /etc/pacman.conf'
wait_hub
old=$(version)

log "update through the hub"
api '{"cmd":"update_check"}'
for _ in $(seq 60); do [[ $(update 'u["status"]') != checking ]] && break; sleep 2; done
check "update found (tvbox-core $old → newer)" test "$(update '[p["new"] for p in u["updates"] if p["name"]=="tvbox-core"]')" != "[]"
check "the summary says a restart is needed" test "$(update '"tvbox-core" in u["reboot_for"]')" = True
api '{"cmd":"update_apply"}'
sleep 3
for _ in $(seq 300); do [[ $(update 'u["status"]') != applying ]] && break; sleep 2; done
check "update installed" test "$(update 'u["status"]')" = "done" || update 'u["error"], u["log"][-5:]' >&2
new=$(version)
fallback=$(update 'u["fallback"]')
check "tvbox-core $old → $new" test "$new" != "$old"
check "the snapshot before the update is the boot fallback ($fallback)" test -n "$fallback" -a "$fallback" != None

log "reboot into the new version"
"$vm" reboot >/dev/null
check "boots the updated system" test "$(booted)" = @

log "two boots that never become healthy"
"$vm" ssh 'systemctl stop tvbox-boot-ok.timer'          # this boot is not confirmed
"$vm" reboot >/dev/null
"$vm" ssh 'systemctl stop tvbox-boot-ok.timer'
"$vm" reboot >/dev/null
check "GRUB started the fallback snapshot by itself" test "$(booted)" = "@snapshots/$fallback/snapshot"
wait_hub
check "the TV knows it runs from the backup" test "$(update 'u["booted_snapshot"]')" = "$fallback"
check "it runs the version from before the update" test "$(version)" = "$old"

log "keep the backup"
before=$("$vm" ssh 'cat /proc/sys/kernel/random/boot_id')
api "{\"cmd\":\"snapshot_rollback\",\"number\":$fallback}"
for _ in $(seq 120); do
    now=$("$vm" ssh 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null) && [[ $now != "$before" ]] && break
    sleep 5
done
"$vm" up >/dev/null
check "rolled back for good: normal boot from @" test "$(booted)" = @
check "with the version from before the update" test "$(version)" = "$old"
check "and a writable root" "$vm" ssh 'touch /root/.rollback-test && rm /root/.rollback-test'
check "pacman works after the rollback (no lock caught in the snapshot)" "$vm" ssh 'pacman -Sy >/dev/null 2>&1'
check "the old system is kept as @rollback-*" "$vm" ssh 'mount -o subvolid=5 $(findmnt -no SOURCE /home | cut -d[ -f1) /mnt && btrfs subvolume list /mnt | grep -q " path @rollback-"; r=$?; umount /mnt; exit $r'

((failed == 0)) || die "some checks failed"
log "all update checks passed"
