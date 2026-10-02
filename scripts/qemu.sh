#!/bin/bash
# QEMU option values are comma-separated by design.
# shellcheck disable=SC2054
# QEMU test harness: UEFI (OVMF) machine resembling the target box.
#
#   scripts/qemu.sh iso   [path.iso]  boot the installer ISO (newest in build/iso by default)
#   scripts/qemu.sh disk              boot the installed test disk
#
# The virtual disk is NVMe (like the real mainboard) so installer device naming
# (/dev/nvme0n1p1) is exercised. Never touches host block devices.
#
# Environment knobs:
#   QEMU_DISPLAY   gtk | sdl | vnc | none   (default: gtk if $DISPLAY/$WAYLAND_DISPLAY, else vnc)
#   QEMU_GL=1      virtio-vga-gl + GL display (closer to real GPU path; needs host GL)
#   QEMU_USB_HOST  vid:pid of a host USB device to pass through, e.g. 045e:0b12
#                  (Xbox Series controller) to test real controller input
#   QEMU_AUDIO=1   add an Intel HDA sound card (HDMI-like PCM sink in the guest)
#   QEMU_STATE_DIR where disk, UEFI vars, serial log and QMP socket live (default build/qemu)
#   QEMU_AUTOINSTALL  file with installer answers (key=value), passed via fw_cfg
#                  as opt/tvbox/autoinstall: the ISO installs unattended and powers off
#   QEMU_EXTRA     extra arguments appended verbatim
#
# Side channels (for scripted tests, see scripts/qmp.py):
#   $QEMU_STATE_DIR/serial.log   guest serial console
#   $QEMU_STATE_DIR/qmp.sock     QMP control socket (send-key, screendump, quit)
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

mode=${1:-}
[[ $mode == iso || $mode == disk ]] || die "usage: $0 iso [file.iso] | disk"

command -v qemu-system-x86_64 >/dev/null || die "qemu-system-x86_64 not installed"

state=${QEMU_STATE_DIR:-$BUILD_DIR/qemu}
mkdir -p "$state"
disk="$state/disk.qcow2"

find_ovmf() {
    local code vars pair
    # Pairs of CODE:VARS for Arch, Debian/Ubuntu, Fedora, NixOS-ish layouts.
    for pair in \
        /usr/share/edk2/x64/OVMF_CODE.4m.fd:/usr/share/edk2/x64/OVMF_VARS.4m.fd \
        /usr/share/OVMF/OVMF_CODE_4M.fd:/usr/share/OVMF/OVMF_VARS_4M.fd \
        /usr/share/edk2/ovmf/OVMF_CODE.fd:/usr/share/edk2/ovmf/OVMF_VARS.fd \
        /usr/share/OVMF/OVMF_CODE.fd:/usr/share/OVMF/OVMF_VARS.fd \
        /usr/share/qemu/edk2-x86_64-code.fd:/usr/share/qemu/edk2-i386-vars.fd
    do
        code=${pair%%:*}; vars=${pair##*:}
        if [[ -f $code && -f $vars ]]; then echo "$code:$vars"; return 0; fi
    done
    return 1
}
ovmf=$(find_ovmf) || die "OVMF not found (Arch: pacman -S edk2-ovmf; Debian/Ubuntu: apt install ovmf)"
ovmf_code=${ovmf%%:*}
[[ -f $state/OVMF_VARS.fd ]] || cp "${ovmf##*:}" "$state/OVMF_VARS.fd"

[[ -f $disk ]] || qemu-img create -q -f qcow2 "$disk" "${QEMU_DISK_SIZE:-32G}"

args=(
    -name "$NAME-test"
    -machine q35,smm=on
    -m "${QEMU_MEM:-4G}" -smp "${QEMU_CPUS:-4}"
    -drive "if=pflash,format=raw,readonly=on,file=$ovmf_code"
    -drive "if=pflash,format=raw,file=$state/OVMF_VARS.fd"
    -drive "if=none,id=disk0,format=qcow2,file=$disk"
    -device "nvme,serial=${NAME}0,drive=disk0,bootindex=1"
    -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:${QEMU_SSH_PORT:-2222}-:22,hostfwd=tcp:127.0.0.1:${QEMU_HUB_PORT:-8080}-:8080"
    -device virtio-net-pci,netdev=net0
    -device qemu-xhci,id=xhci
    -device usb-kbd -device usb-tablet
    -chardev "file,id=serial0,path=$state/serial.log"
    -serial chardev:serial0
    -qmp "unix:$state/qmp.sock,server=on,wait=off"
    -device virtio-rng-pci
)

if [[ -w /dev/kvm ]]; then
    args+=(-accel kvm -cpu host)
else
    warn "no /dev/kvm: falling back to TCG emulation (slow)"
    args+=(-accel tcg -cpu max)
fi

display=${QEMU_DISPLAY:-}
if [[ -z $display ]]; then
    if [[ -n ${DISPLAY:-}${WAYLAND_DISPLAY:-} ]]; then display=gtk; else display=vnc; fi
fi
if [[ ${QEMU_GL:-0} == 1 ]]; then
    args+=(-device virtio-vga-gl)
    case $display in
        gtk|sdl) args+=(-display "$display,gl=on") ;;
        *) die "QEMU_GL=1 needs QEMU_DISPLAY=gtk or sdl" ;;
    esac
else
    args+=(-device virtio-vga)
    case $display in
        gtk|sdl) args+=(-display "$display") ;;
        vnc)  args+=(-display none -vnc 127.0.0.1:0); log "VNC on 127.0.0.1:5900" ;;
        none) args+=(-display none) ;;
        *) die "unknown QEMU_DISPLAY=$display" ;;
    esac
fi

if [[ -n ${QEMU_USB_HOST:-} ]]; then
    args+=(-device "usb-host,bus=xhci.0,vendorid=0x${QEMU_USB_HOST%%:*},productid=0x${QEMU_USB_HOST##*:}")
fi
if [[ ${QEMU_AUDIO:-0} == 1 ]]; then
    args+=(-audiodev "${QEMU_AUDIODEV:-pa},id=snd0" -device ich9-intel-hda -device hda-output,audiodev=snd0)
fi

if [[ $mode == iso ]]; then
    iso=${2:-}
    if [[ -z $iso ]]; then
        iso=$(newest_file "$BUILD_DIR"/iso/*.iso || true)
        [[ -n $iso ]] || die "no ISO in $BUILD_DIR/iso; run 'make iso' or pass a path"
    fi
    [[ -f $iso ]] || die "ISO not found: $iso"
    args+=(-drive "if=none,id=cd0,media=cdrom,readonly=on,file=$iso"
           -device "ide-cd,drive=cd0,bootindex=0")
    log "booting ISO $iso"
else
    log "booting disk $disk"
fi

if [[ -n ${QEMU_AUTOINSTALL:-} ]]; then
    [[ -f $QEMU_AUTOINSTALL ]] || die "QEMU_AUTOINSTALL file not found: $QEMU_AUTOINSTALL"
    args+=(-fw_cfg "name=opt/tvbox/autoinstall,file=$QEMU_AUTOINSTALL")
fi

# shellcheck disable=SC2206 # QEMU_EXTRA is intentionally word-split
[[ -n ${QEMU_EXTRA:-} ]] && args+=($QEMU_EXTRA)

: > "$state/serial.log"
log "serial log: $state/serial.log   QMP: $state/qmp.sock"
exec qemu-system-x86_64 "${args[@]}"
