#!/bin/bash
# Runs inside the builder container. Remaps the "builder" user to the host
# UID/GID so files written to the bind-mounted repo stay owned by the host user,
# then runs the requested command either as builder (default) or as root
# (AS_ROOT=1, needed by mkarchiso).
set -euo pipefail

uid=${HOST_UID:-1000}
gid=${HOST_GID:-1000}

if [[ $uid != 0 ]]; then
    groupmod -o -g "$gid" builder
    usermod -o -u "$uid" -g "$gid" builder
    chown builder: /home/builder
fi

# Refresh package databases once per container run; builds install deps.
pacman -Sy --noconfirm >/dev/null

if [[ ${AS_ROOT:-0} == 1 || $uid == 0 ]]; then
    exec "$@"
fi
exec sudo -E -u builder env HOME=/home/builder "$@"
