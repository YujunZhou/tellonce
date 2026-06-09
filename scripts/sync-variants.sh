#!/usr/bin/env bash
#
# sync-variants.sh — propagate shared core modules from lib/ (the single source
# of truth) to copilot/lib/.
#
# Mechanism C: the Claude-variant lib/ holds the canonical shared core. Each
# variant keeps its OWN pt_platform.py (platform-specific values) and its own
# variant-specific tooling/tests; everything else is identical and generated
# from lib/. Run this after editing any shared lib/ module, then commit both.
#
# Idempotent. Safe to re-run.
#
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${REPO}/lib"
DST="${REPO}/copilot/lib"

[ -d "${SRC}" ] || { echo "ERROR: ${SRC} not found" >&2; exit 1; }
[ -d "${DST}" ] || { echo "ERROR: ${DST} not found" >&2; exit 1; }

# Files that are NOT shared (stay per-variant — never copied):
#   pt_platform.py            platform-specific values (the whole point)
#   conftest.py / test_*.py   per-variant test harness
#   _install_merge_settings.py / _pt_hooks.txt   Claude-only install machinery
is_excluded() {
    case "$1" in
        pt_platform.py|conftest.py|test_*.py|_install_merge_settings.py|_pt_hooks.txt)
            return 0 ;;
        *) return 1 ;;
    esac
}

count=0
shopt -s nullglob
for f in "${SRC}"/*.py "${SRC}"/*.yaml; do
    base="$(basename "${f}")"
    is_excluded "${base}" && continue
    cp "${f}" "${DST}/${base}"
    count=$((count + 1))
done
shopt -u nullglob

echo "sync-variants: copied ${count} shared core file(s) lib/ -> copilot/lib/"
echo "Run scripts/parity-check.sh to verify, then commit both trees."
