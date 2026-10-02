#!/bin/bash
# Lay out build/repo as a static site for GitHub Pages: $BUILD_DIR/pages/x86_64/.
# Symlinks (tvbox.db -> tvbox.db.tar.gz) are copied as real files because
# static hosting does not serve symlinks.
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

src="$BUILD_DIR/repo"
dst="$BUILD_DIR/pages"
[[ -f $src/$REPO_NAME.db.tar.gz ]] || die "no repo; run 'make repo' first"

rm -rf "$dst"; mkdir -p "$dst/x86_64"
cp -L "$src"/* "$dst/x86_64/"
touch "$dst/.nojekyll"
{
    echo "<!doctype html><meta charset=utf-8><title>[$REPO_NAME] package repository</title>"
    echo "<h1>[$REPO_NAME] pacman repository</h1><p>Packages for x86_64:</p><ul>"
    for f in "$dst"/x86_64/*.pkg.tar.zst; do
        echo "<li><a href=\"x86_64/${f##*/}\">${f##*/}</a></li>"
    done
    echo "</ul>"
} > "$dst/index.html"
log "pages staged in $dst"
