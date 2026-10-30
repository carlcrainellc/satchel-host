#!/bin/sh
# Usage: scripts/make_release.sh <ssh-ed25519-private-key> [outdir]
set -e
cd "$(dirname "$0")/.."
key="$1"; out="${2:-dist}"
[ -n "$key" ] || { echo "usage: $0 <signing-key> [outdir]" >&2; exit 2; }
v=$(python3 bin/satchel_host.py --version)
mkdir -p "$out"; n="satchel-host-$v"
git archive --format=tar.gz --prefix="$n/" -o "$out/$n.tar.gz" HEAD
( cd "$out" && sha256sum "$n.tar.gz" > SHA256SUMS )
rm -f "$out/SHA256SUMS.sig"
ssh-keygen -Y sign -f "$key" -n satchel-release "$out/SHA256SUMS"
echo "release@satchel $(cat "$key.pub")" > "$out/allowed_signers"
( cd "$out" && sha256sum -c SHA256SUMS && \
  ssh-keygen -Y verify -f allowed_signers -I release@satchel -n satchel-release -s SHA256SUMS.sig < SHA256SUMS )
ls -l "$out"
