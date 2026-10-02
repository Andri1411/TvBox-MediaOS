# Arch Linux build environment for packages, the repo and the ISO.
# Used on non-Arch dev machines and in CI so every build sees the same toolchain.
FROM archlinux:latest

# Optional extra CA (corporate / sandbox TLS proxies). Empty file = no-op.
COPY extra-ca.crt /etc/ca-certificates/trust-source/anchors/extra-ca.crt
RUN update-ca-trust \
 && pacman -Syu --noconfirm --needed \
      base-devel git archiso pacman-contrib sudo python python-pytest shellcheck \
 && pacman -Scc --noconfirm

# makepkg refuses to run as root; the entrypoint remaps this user to the host UID.
RUN useradd -m builder \
 && echo 'builder ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/builder \
 && git config --system safe.directory '*'   # repo is bind-mounted, owned by the host user

COPY builder-entrypoint.sh /usr/local/bin/builder-entrypoint
ENTRYPOINT ["/usr/local/bin/builder-entrypoint"]
