#!/bin/bash
# Static checks that run on any Linux host (no Arch needed).
#   PYTHON=/path/to/venv/bin/python scripts/lint.sh   use an interpreter that
#   has pytest + evdev + aiohttp when the system python3 doesn't
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"
cd "$ROOT" || exit 1
py=${PYTHON:-python3}

mapfile -t shells < <(
    git ls-files -co --exclude-standard -- '*.sh' 'pkgs/*/PKGBUILD' 'pkgs/*/*.install'
    # extensionless scripts with a bash shebang under pkgs/ and iso/
    git ls-files -co --exclude-standard -- 'pkgs/*' 'iso/*' | while read -r f; do
        [[ $f == *.* || ! -f $f ]] && continue
        head -c 40 "$f" | grep -qE '^#!(/usr/bin/env |/bin/)?(ba)?sh' && echo "$f"
    done
)
if command -v shellcheck >/dev/null; then
    log "shellcheck (${#shells[@]} files)"
    # PKGBUILDs set variables consumed by makepkg (SC2034) and use its globals (SC2154).
    for f in "${shells[@]}"; do
        if [[ $f == *PKGBUILD || $f == *.install ]]; then
            shellcheck -s bash -e SC2034,SC2154,SC2164 "$f"
        else
            shellcheck -x "$f"
        fi
    done
else
    warn "shellcheck not installed, skipping"
fi

mapfile -t pys < <(git ls-files -co --exclude-standard -- '*.py')
if ((${#pys[@]})); then
    log "python compile (${#pys[@]} files)"
    "$py" -m py_compile "${pys[@]}"
fi

if [[ -d tests/unit ]]; then
    if "$py" -c 'import pytest, evdev, aiohttp' 2>/dev/null; then
        log "unit tests"
        "$py" -m pytest -q tests/unit
    else
        warn "unit tests skipped: $py lacks pytest, evdev or aiohttp"
    fi
fi
log "lint OK"
