#!/bin/bash
# Drive the installed test VM (the disk `make qemu-install` produced) for
# development and for the per-phase tests.
#
#   tests/qemu/vm.sh up              boot headless, wait for SSH and the session
#   tests/qemu/vm.sh push            install the packages from build/repo (pacman -U)
#   tests/qemu/vm.sh reboot          reboot and wait until the session is back
#   tests/qemu/vm.sh ssh [cmd...]    run a command as root (no cmd: shell)
#   tests/qemu/vm.sh tv <cmd...>     run a command as the tv user inside its session
#   tests/qemu/vm.sh put <file> <remote-path>
#   tests/qemu/vm.sh shot <file.png> screenshot
#   tests/qemu/vm.sh down            shut down
# shellcheck source=../../scripts/lib.sh
. "$(dirname "$0")/../../scripts/lib.sh"

state="$BUILD_DIR/qemu-test"
ssh_port=${QEMU_SSH_PORT:-2222}
mirror_port=${MIRROR_PORT:-8801}
qmp=(python3 "$ROOT/scripts/qmp.py" "$state/qmp.sock")
ssh_opts=(-q -i "$state/id_ed25519" -o StrictHostKeyChecking=no
          -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5)

ssh_vm() { ssh "${ssh_opts[@]}" -p "$ssh_port" root@127.0.0.1 "$@"; }
running() { [[ -S $state/qmp.sock ]] && "${qmp[@]}" status >/dev/null 2>&1; }

cmd=${1:-}
shift || true
case $cmd in
    up)
        [[ -f $state/disk.qcow2 ]] || die "no installed test disk; run 'make qemu-install' first"
        if running; then
            log "VM already running"
        else
            # A sound card with no host backend, so the guest has an audio sink.
            QEMU_STATE_DIR=$state QEMU_DISPLAY=${QEMU_DISPLAY:-none} \
                QEMU_AUDIO=${QEMU_AUDIO:-1} QEMU_AUDIODEV=${QEMU_AUDIODEV:-none} \
                setsid "$ROOT/scripts/qemu.sh" disk >"$state/qemu-boot.out" 2>&1 &
        fi
        # The test install points the VM's Arch mirrorlist at this cache.
        if ! pgrep -f "mirror-cache.py --port $mirror_port" >/dev/null; then
            setsid python3 "$ROOT/scripts/mirror-cache.py" --port "$mirror_port" \
                --cache "$BUILD_DIR/mirror-cache" >/dev/null 2>>"$BUILD_DIR/mirror-cache.log" &
        fi
        deadline=$((SECONDS + ${BOOT_TIMEOUT:-600}))
        until ssh_vm true 2>/dev/null; do
            ((SECONDS < deadline)) || die "no SSH after ${BOOT_TIMEOUT:-600}s (see $state/serial.log)"
            sleep 3
        done
        for _ in $(seq 60); do ssh_vm 'pgrep -u tv -x sway' >/dev/null 2>&1 && break; sleep 2; done
        log "VM up (ssh port $ssh_port)"
        ;;
    push)
        shopt -s nullglob
        pkgs=("$BUILD_DIR"/repo/*.pkg.tar.zst)
        ((${#pkgs[@]})) || die "no packages in $BUILD_DIR/repo; run 'make repo'"
        # the installer belongs on the ISO, not on the installed system
        for i in "${!pkgs[@]}"; do [[ ${pkgs[i]##*/} == "$NAME"-installer-* ]] && unset "pkgs[i]"; done
        ssh_vm 'rm -rf /tmp/push && mkdir /tmp/push'
        scp "${ssh_opts[@]}" -P "$ssh_port" "${pkgs[@]}" root@127.0.0.1:/tmp/push/
        # Dependencies come from the Arch mirrors configured at install time
        # (the test install points them at the host's caching mirror), not
        # from [tvbox], whose signed database a dev build can't provide.
        # shellcheck disable=SC2016 # expands in the VM
        ssh_vm 'sed "/^\[tvbox\]/,/^\$/d" /etc/pacman.conf > /tmp/pacman-notvbox.conf &&
                pacman --config /tmp/pacman-notvbox.conf -Sy >/dev/null &&
                pacman --config /tmp/pacman-notvbox.conf -U --noconfirm /tmp/push/*.pkg.tar.zst' \
            | tail -n 15 >&2
        ;;
    reboot)
        before=$(ssh_vm 'cat /proc/sys/kernel/random/boot_id')
        ssh_vm 'systemctl reboot' || true
        deadline=$((SECONDS + ${BOOT_TIMEOUT:-600}))
        until after=$(ssh_vm 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null) && [[ $after != "$before" ]]; do
            ((SECONDS < deadline)) || die "VM did not come back after reboot"
            sleep 2
        done
        for _ in $(seq 60); do ssh_vm 'pgrep -u tv -x sway' >/dev/null 2>&1 && break; sleep 2; done
        log "VM rebooted"
        ;;
    ssh)  ssh_vm "$@" ;;
    tv)
        # Same environment as the tv user's session services. ssh joins its
        # arguments into one string for the remote shell, hence the quoting.
        ssh_vm "uid=\$(id -u tv); exec sudo -u tv env XDG_RUNTIME_DIR=/run/user/\$uid" \
               "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/\$uid/bus $(printf '%q ' "$@")"
        ;;
    put)  scp "${ssh_opts[@]}" -P "$ssh_port" "$1" "root@127.0.0.1:$2" ;;
    shot) "${qmp[@]}" screendump "$1" && log "screenshot: $1" ;;
    down)
        if running; then
            ssh_vm 'systemctl poweroff' 2>/dev/null || true
            for _ in $(seq 30); do running || break; sleep 1; done
            running && "${qmp[@]}" quit
        fi
        pkill -f "mirror-cache.py --port $mirror_port" || true
        log "VM down"
        ;;
    *) die "usage: $0 up | push | reboot | ssh [cmd] | tv <cmd> | put <file> <path> | shot <png> | down" ;;
esac
