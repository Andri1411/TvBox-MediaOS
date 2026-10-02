#!/bin/bash
# Build packages into $BUILD_DIR/pkgs.
#   - our own packages: pkgs/<name>/PKGBUILD
#   - third-party (AUR) packages pinned to a commit: pkgs/aur.list
# With arguments, only the named packages are built.
#
# Built packages are added to a throwaway local repo that pacman inside the
# builder can see, so later packages may depend on earlier ones.
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
require_arch

out="$BUILD_DIR/pkgs"
work="$BUILD_DIR/work/pkgs"
mkdir -p "$out" "$work"

export PKGDEST="$out" SRCDEST="$BUILD_DIR/work/sources" BUILDDIR="$work/makepkg"
export TVBOX_PKGVER; TVBOX_PKGVER=$(pkg_version)
# PKGBUILDs are built from a copy; this is where they find src/.
export TVBOX_SRC="$ROOT/src"
mkdir -p "$SRCDEST" "$BUILDDIR"

makepkg_args=(--noconfirm --cleanbuild --force)
[[ -n ${SIGN_KEY:-} ]] && makepkg_args+=(--sign --key "$SIGN_KEY")

local_db="$out/_buildlocal.db.tar.gz"
setup_local_repo() {
    grep -q '^\[_buildlocal\]' /etc/pacman.conf && return 0
    [[ -f $local_db ]] || repo-add -q "$local_db"
    printf '\n[_buildlocal]\nSigLevel = Optional TrustAll\nServer = file://%s\n' "$out" \
        | sudo tee -a /etc/pacman.conf >/dev/null
}
register_built() {
    local f
    for f in "$@"; do repo-add -q -R "$local_db" "$f"; done
    sudo pacman -Sy --noconfirm >/dev/null
}

build_dir() {  # build_dir <dir-with-PKGBUILD> [extra makepkg args]
    local dir=$1 before after new
    shift
    before=$(ls "$out")
    (cd "$dir" && makepkg "${makepkg_args[@]}" "$@")
    after=$(ls "$out")
    mapfile -t new < <(comm -13 <(echo "$before") <(echo "$after") | grep -E '\.pkg\.tar\.[a-z]+$' || true)
    ((${#new[@]})) && register_built "${new[@]/#/$out/}"
    return 0
}

build_aur() {  # build_aur <name> <commit>
    local name=$1 commit=$2 dir="$work/aur/$1"
    if [[ ! -d $dir/.git ]]; then
        git clone -q "https://aur.archlinux.org/$name.git" "$dir"
    fi
    git -C "$dir" fetch -q origin
    git -C "$dir" checkout -q --detach "$commit"
    log "AUR $name @ ${commit:0:10}"
    # --nocheck: check() suites pull in heavy dependencies (xpadneo's wants
    # kernel headers to test-build the module; DKMS builds it on the box).
    build_dir "$dir" --syncdeps --needed --nocheck
}

wanted() {  # wanted <name>: true if no filter was given or name is in it
    ((${#FILTER[@]} == 0)) && return 0
    local n; for n in "${FILTER[@]}"; do [[ $n == "$1" ]] && return 0; done
    return 1
}

FILTER=("$@")
setup_local_repo

# 1. pinned AUR packages, in file order
if [[ -f $ROOT/pkgs/aur.list ]]; then
    while read -r name commit _; do
        [[ -z $name || $name == \#* ]] && continue
        if wanted "$name"; then
            build_aur "$name" "$commit"
        fi
    done < "$ROOT/pkgs/aur.list"
fi

# 2. our packages, in build-order (falls back to alphabetical)
if [[ -f $ROOT/pkgs/build-order ]]; then
    mapfile -t order < <(grep -vE '^\s*(#|$)' "$ROOT/pkgs/build-order")
else
    order=()
    for d in "$ROOT"/pkgs/*/; do d=${d%/}; order+=("${d##*/}"); done
fi
for name in "${order[@]}"; do
    [[ -f $ROOT/pkgs/$name/PKGBUILD ]] || continue
    if wanted "$name"; then
        log "building $name $TVBOX_PKGVER"
        # Copy so makepkg never writes into the source tree.
        rm -rf "${work:?}/$name"
        cp -a "$ROOT/pkgs/$name" "$work/$name"
        # Our packages are arch=any with no build steps: runtime dependencies
        # need not be installed in the builder.
        build_dir "$work/$name" --nodeps
    fi
done

shopt -s nullglob
built=("$out"/*.pkg.tar.zst)
((${#built[@]})) || warn "no packages built"
log "packages in $out:"
printf '  %s\n' "${built[@]##*/}" >&2
