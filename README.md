# mediaOS (working name: tvbox)

A small Arch Linux–based distribution for a dedicated media PC behind a TV,
fully usable from the couch with an Xbox controller or a phone.

- Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Decisions and things that didn't work: [docs/DECISIONS.md](docs/DECISIONS.md)
- Original brief: [media-distro-prompt.md](media-distro-prompt.md)

**Status:** Phase 0 (repo skeleton, build tooling, QEMU harness).

## Building

Requirements: `make`, docker or podman (or an Arch host), and for testing
`qemu-system-x86_64` + OVMF (`edk2-ovmf` on Arch, `ovmf` on Debian/Ubuntu).

```sh
make help         # list targets
make packages     # build pkgs/* into build/pkgs (Arch container)
make repo         # pacman repo in build/repo
make iso          # installer ISO in build/iso   (from Phase 1)
make qemu-iso     # boot the ISO in QEMU (UEFI, NVMe test disk)
make qemu-disk    # boot the installed test disk
make serve-repo   # let the VM pacman -Syu from your local build
make lint         # shellcheck + python checks
make qemu-smoke   # self-test of the QEMU harness, headless
```

Settings live in `config.mk` and can be overridden per call, e.g.
`make qemu-iso QEMU_MEM=8G QEMU_DISPLAY=vnc`.

Nothing in this repo ever writes to a real disk on the development machine;
installs are only tested on the QEMU disk image under `build/qemu/`.
