#!/bin/bash
# Assemble the publishable pacman repo in $BUILD_DIR/repo from $BUILD_DIR/pkgs.
# Only the newest version of each package is kept. With SIGN_KEY set, the
# database is signed too (packages are signed at build time).
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
require_arch

src="$BUILD_DIR/pkgs"
dst="$BUILD_DIR/repo"
[[ -d $src ]] || die "no packages; run 'make packages' first"

rm -rf "$dst"; mkdir -p "$dst"
shopt -s nullglob
pkgs=("$src"/*.pkg.tar.zst)
((${#pkgs[@]})) || die "no packages in $src"

cp -a "${pkgs[@]}" "$dst/"
for f in "${pkgs[@]}"; do [[ -f $f.sig ]] && cp -a "$f.sig" "$dst/"; done

add_args=(--new --remove --prevent-downgrade)
[[ -n ${SIGN_KEY:-} ]] && add_args+=(--sign --verify --key "$SIGN_KEY")
repo-add "${add_args[@]}" "$dst/$REPO_NAME.db.tar.gz" "$dst"/*.pkg.tar.zst

# repo-add keeps superseded files around; drop everything not in the db.
paccache -r -k1 -c "$dst" >/dev/null 2>&1 || true

log "repo [$REPO_NAME] ready in $dst"
