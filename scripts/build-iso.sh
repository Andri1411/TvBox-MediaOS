#!/bin/bash
# Build the installer ISO from the archiso profile in iso/.
# The freshly built [$REPO_NAME] repo is injected into the profile's
# pacman.conf (placeholder @LOCAL_REPO_DIR@) so the ISO, and the installer
# running on it, install our packages from the copy embedded in the ISO.
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
require_arch
[[ $EUID == 0 ]] || die "mkarchiso must run as root (use: make iso)"
[[ -f $ROOT/iso/profiledef.sh ]] || die "no archiso profile in iso/ yet (Phase 1)"
[[ -f $BUILD_DIR/repo/$REPO_NAME.db.tar.gz ]] || die "no repo; run 'make repo' first"

work="$BUILD_DIR/work/iso"
profile="$work/profile"
out="$BUILD_DIR/iso"

rm -rf "$work"; mkdir -p "$profile" "$out"
cp -a "$ROOT/iso/." "$profile/"
sed -i "s|@LOCAL_REPO_DIR@|$BUILD_DIR/repo|g; s|@REPO_NAME@|$REPO_NAME|g" "$profile/pacman.conf"

# Ship the repo inside the ISO so installation works offline-first.
mkdir -p "$profile/airootfs/opt/$NAME/repo"
cp -a "$BUILD_DIR/repo/." "$profile/airootfs/opt/$NAME/repo/"

mkarchiso -v -w "$work/mkarchiso" -o "$out" "$profile"
chown -R "${HOST_UID:-0}:${HOST_GID:-0}" "$out"
log "ISO: $(newest_file "$out"/*.iso)"
