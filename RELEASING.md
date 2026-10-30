# Releasing

    ./scripts/make_release.sh <signing-key> [outdir]      # default outdir: dist/

This builds `satchel-host-<ver>.tar.gz` from committed files (`git archive`), then writes
`SHA256SUMS`, a detached SSH signature `SHA256SUMS.sig` (namespace `satchel-release`,
identity `release@satchel`) and `allowed_signers`. Upload all four files to the release.

Sign real releases only with the project's release key, and keep that key off any shared or
online build machine. A key made for testing is a throwaway: never publish signatures made
with it. Publish the release public key in the README and next to each release, so hosts can
check `allowed_signers` against it.
