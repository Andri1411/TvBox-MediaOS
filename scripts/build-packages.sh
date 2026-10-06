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

# Source downloads (AUR packages' tarballs from GitHub) retry on any error:
# GitHub sometimes answers CI runners with a 403 for a moment, which curl's
# default --retry doesn't cover.
{
    cat /etc/makepkg.conf
    echo "DLAGENTS=('https::/usr/bin/curl -qgb \"\" -fLC - --retry 5 --retry-delay 10 --retry-all-errors -o %o %u'"
    echo "          'http::/usr/bin/curl -qgb \"\" -fLC - --retry 5 --retry-delay 10 --retry-all-errors -o %o %u')"
} > "$work/makepkg.conf"
makepkg_args=(--config "$work/makepkg.conf" --noconfirm --cleanbuild --force)
[[ -n ${SIGN_KEY:-} ]] && makepkg_args+=(--sign --key "$SIGN_KEY")

local_db="$out/_buildlocal.db.tar.gz"
setup_local_repo() {
    grep -q '^\[_buildlocal\]' /etc/pacman.conf && return 0
    [[ -f $local_db ]] || repo-add -q "$local_db"
    printf '\n[_buildlocal]\nSigLevel = Optional TrustAll\nServer = file://%s\n' "$out" \
        | sudo tee -a /etc/pacman.conf >/dev/null
    # pacman refuses to install build dependencies while a configured repo
    # has no synced database.
    sudo pacman -Sy --noconfirm >/dev/null
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
    # Split debug packages (AUR builds) have no place on the box.
    rm -f "$out"/*-debug-*.pkg.tar.*
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
    # Already built from this pin (local rebuilds; CI starts empty): keep it.
    local stamp="$out/.aur-$name"
    if [[ $(cat "$stamp" 2>/dev/null) == "$commit" ]] && compgen -G "$out/$name-[0-9]*.pkg.tar.zst" >/dev/null; then
        log "AUR $name @ ${commit:0:10} (already built)"
        compgen -G "$BUILD_DIR/sources/$name-[0-9]*" >/dev/null || publish_source "$name" "$dir"
        return 0
    fi
    git -C "$dir" fetch -q origin
    git -C "$dir" checkout -q --detach "$commit"
    log "AUR $name @ ${commit:0:10}"
    rm -f "$out/$name"-[0-9]*.pkg.tar.zst*
    # --nocheck: check() suites pull in heavy dependencies (xpadneo's wants
    # kernel headers to test-build the module; DKMS builds it on the box).
    build_dir "$dir" --syncdeps --needed --nocheck
    echo "$commit" > "$stamp"
    publish_source "$name" "$dir"
}

# The GPL wants the source of every binary we publish next to it: for third-
# party packages, their recipe and sources go to $BUILD_DIR/sources (the update
# site serves them). Our own packages' source is this repository.
srcout="$BUILD_DIR/sources"
publish_source() {  # publish_source <name> <dir-with-PKGBUILD>
    local name=$1 dir=$2 pkg ver tree
    mkdir -p "$srcout"
    rm -f "$srcout/$name"-[0-9]*
    if grep -q '^source=.*git+' "$dir/PKGBUILD"; then
        # A git checkout (its mirror can be ~100 MB): the recipe, plus the
        # exact tree that was compiled, submodules included.
        (cd "$dir" && SRCPKGDEST="$srcout" makepkg --config "$work/makepkg.conf" --source --force)
        pkg=$(compgen -G "$out/$name-[0-9]*.pkg.tar.zst" | head -1)
        ver=${pkg##*/"$name"-}; ver=${ver%-*.pkg.tar.zst}
        for tree in "$BUILDDIR/$name"/src/*/; do
            [[ -e $tree/.git ]] || continue
            (cd "$tree" && git ls-files --recurse-submodules -z \
                | tar --null -T - --transform "s,^,${name}-${ver}/," -czf "$srcout/$name-$ver-tree.tar.gz")
        done
        compgen -G "$srcout/$name-*-tree.tar.gz" >/dev/null || die "no source tree of $name (build it again)"
    else
        (cd "$dir" && SRCPKGDEST="$srcout" makepkg --config "$work/makepkg.conf" --allsource --force)
    fi
    log "sources of $name in $srcout"
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
