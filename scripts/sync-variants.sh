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

# Codex plugin bundle: codex/ ships as a self-contained plugin (the codex hooks
# hardcode ${SKILL_DIR}/shared_lib), so codex/shared_lib must be a committed
# copy of lib/. Unlike copilot/lib it keeps the CC pt_platform + install
# machinery (codex's runtime uses them); only tests are dropped. This mirrors
# what codex/install.sh's rsync generates at install time.
CODEX_SHARED="${REPO}/codex/shared_lib"
if [ -d "${CODEX_SHARED}" ]; then
    ccount=0
    shopt -s nullglob
    for f in "${SRC}"/*.py "${SRC}"/*.yaml "${SRC}"/*.txt; do
        base="$(basename "${f}")"
        case "${base}" in conftest.py|test_*.py) continue ;; esac
        cp "${f}" "${CODEX_SHARED}/${base}"
        ccount=$((ccount + 1))
    done
    shopt -u nullglob
    # Drop any stale files in codex/shared_lib that no longer exist in lib/.
    for f in "${CODEX_SHARED}"/*.py "${CODEX_SHARED}"/*.yaml "${CODEX_SHARED}"/*.txt; do
        [ -e "${f}" ] || continue
        base="$(basename "${f}")"
        [ -f "${SRC}/${base}" ] || rm -f "${f}"
    done
    echo "sync-variants: copied ${ccount} shared core file(s) lib/ -> codex/shared_lib/"
fi

echo "Run scripts/parity-check.sh to verify, then commit both trees."
