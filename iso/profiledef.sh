#!/usr/bin/env bash
# shellcheck disable=SC2034
# tvbox installer ISO, derived from archiso's releng profile (UEFI only).

iso_name="tvbox-mediaos"
iso_label="TVBOX_$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y%m)"
iso_publisher="TvBox MediaOS <https://github.com/Andri1411/TvBox-MediaOS>"
iso_application="TvBox MediaOS installer"
iso_version="$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y.%m.%d)"
install_dir="arch"
buildmodes=('iso')
bootmodes=('uefi.systemd-boot')
arch="x86_64"
pacman_conf="pacman.conf"
airootfs_image_type="squashfs"
airootfs_image_tool_options=('-comp' 'zstd' '-Xcompression-level' '15' '-b' '1M')
file_permissions=(
  ["/etc/shadow"]="0:0:400"
  ["/root"]="0:0:750"
  ["/root/.gnupg"]="0:0:700"
)
