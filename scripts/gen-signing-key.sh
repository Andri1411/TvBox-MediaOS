#!/bin/bash
# One-time: create the package signing key.
#
#   scripts/gen-signing-key.sh [private-key-output-file]
#
# - The PRIVATE key is written OUTSIDE the repo (default ~/tvbox-signing-key.asc).
#   Paste it into the GitHub secret TVBOX_SIGNING_KEY, keep a backup (e.g. in a
#   password manager), then delete the file.
# - The PUBLIC key goes into pkgs/tvbox-keyring/, which you commit. Boxes
#   trust packages signed by this key and nothing else.
#
# The key has no passphrase because CI must sign unattended; its protection is
# the GitHub secret store. Needs only gpg (Linux, macOS via Homebrew, or the
# gpg that ships with Git for Windows' Git Bash).
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

command -v gpg >/dev/null || die "gpg not installed"
out=${1:-$HOME/$NAME-signing-key.asc}
case $(realpath -m "$out") in
    "$ROOT"/*) die "refusing to write the private key inside the repository" ;;
esac
[[ ! -e $out ]] || die "$out already exists"
keyring_dir="$ROOT/pkgs/$NAME-keyring"
[[ ! -f $keyring_dir/$NAME.gpg ]] || die "a public key already exists in $keyring_dir (delete it first to rotate)"

tmp=$(mktemp -d)
trap 'gpgconf --homedir "$tmp" --kill all 2>/dev/null; rm -rf "$tmp"' EXIT
export GNUPGHOME=$tmp

gpg --batch --quiet --passphrase '' \
    --quick-generate-key "$NAME package signing <$NAME@localhost>" ed25519 sign never
fpr=$(gpg --batch --with-colons --list-secret-keys | awk -F: '/^fpr:/ {print $10; exit}')

umask 077
gpg --batch --armor --export-secret-keys "$fpr" > "$out"
umask 022
mkdir -p "$keyring_dir"
gpg --batch --export "$fpr" > "$keyring_dir/$NAME.gpg"
echo "$fpr:4:" > "$keyring_dir/$NAME-trusted"
: > "$keyring_dir/$NAME-revoked"
grep -qx "$NAME-keyring" "$ROOT/pkgs/build-order" \
    || sed -i "0,/^$NAME-release$/s//$NAME-release\n$NAME-keyring/" "$ROOT/pkgs/build-order"

cat >&2 <<END

Key fingerprint: $fpr

Next steps:
 1. GitHub → repository → Settings → Secrets and variables → Actions →
    New repository secret:   name  TVBOX_SIGNING_KEY
                             value the entire content of $out
 2. Back up $out somewhere safe (password manager), then delete it.
 3. Commit the public key:   git add pkgs/$NAME-keyring pkgs/build-order && git commit
END
