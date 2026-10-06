#!/bin/bash
# Lay out build/repo as a static site for GitHub Pages: $BUILD_DIR/pages/x86_64/,
# and the sources of the third-party packages in it: $BUILD_DIR/pages/sources/.
# Symlinks (tvbox.db -> tvbox.db.tar.gz) are copied as real files because
# static hosting does not serve symlinks.
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

src="$BUILD_DIR/repo"
dst="$BUILD_DIR/pages"
[[ -f $src/$REPO_NAME.db.tar.gz ]] || die "no repo; run 'make repo' first"

rm -rf "$dst"; mkdir -p "$dst/x86_64" "$dst/sources"
cp -L "$src"/* "$dst/x86_64/"
# Every third-party package published needs its sources next to it (GPL).
for f in "$dst"/x86_64/*.pkg.tar.zst; do
    name=${f##*/}; name=${name%-*-*-*.pkg.tar.zst}     # <name>-<ver>-<rel>-<arch>.pkg.tar.zst
    [[ $name == "$NAME"-* ]] && continue
    compgen -G "$BUILD_DIR/sources/$name-[0-9]*" >/dev/null || die "no sources for $name in $BUILD_DIR/sources"
    cp "$BUILD_DIR/sources/$name"-[0-9]* "$dst/sources/"
done
touch "$dst/.nojekyll"
{
    echo "<!doctype html><meta charset=utf-8><title>[$REPO_NAME] package repository</title>"
    echo "<h1>[$REPO_NAME] pacman repository</h1><p>Packages for x86_64:</p><ul>"
    for f in "$dst"/x86_64/*.pkg.tar.zst; do
        echo "<li><a href=\"x86_64/${f##*/}\">${f##*/}</a></li>"
    done
    echo "</ul><h2>Sources</h2>"
    echo "<p>The tvbox-* packages are built from <a href=\"https://github.com/Andri1411/TvBox-MediaOS\">the"
    echo "project's repository</a> (GPL-3.0-or-later). Third-party packages, with their recipes:</p><ul>"
    for f in "$dst"/sources/*; do
        [[ -e $f ]] && echo "<li><a href=\"sources/${f##*/}\">${f##*/}</a></li>"
    done
    echo "</ul>"
} > "$dst/index.html"
log "pages staged in $dst"
