#!/usr/bin/env bash
#
# parity-check.sh — verify the shared core is in sync across variants.
#
# Mechanism C invariant: every shared module is byte-identical between lib/ and
# copilot/lib/, and the two pt_platform.py files expose the SAME interface (same
# public names) so the shared core can rely on them. This replaces superpowers'
# copy-diff parity (we have one source, not rsynced copies) with a contract check.
#
# Exit 0 if in sync, non-zero (and prints what is off) otherwise. Intended for
# local use and CI.
#
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${REPO}/lib"
DST="${REPO}/copilot/lib"
PY="${PYTHON:-python3}"
fail=0

is_excluded() {
    case "$1" in
        pt_platform.py|conftest.py|test_*.py|_install_merge_settings.py|_pt_hooks.txt)
            return 0 ;;
        *) return 1 ;;
    esac
}

# 1. Shared modules must be byte-identical across variants.
shopt -s nullglob
for f in "${SRC}"/*.py "${SRC}"/*.yaml; do
    base="$(basename "${f}")"
    is_excluded "${base}" && continue
    if [ ! -f "${DST}/${base}" ]; then
        echo "FAIL: ${base} missing in copilot/lib/"
        fail=1
        continue
    fi
    if ! diff -q "${f}" "${DST}/${base}" >/dev/null 2>&1; then
        echo "FAIL: ${base} differs between lib/ and copilot/lib/ (run scripts/sync-variants.sh)"
        fail=1
    fi
done
shopt -u nullglob

# 2. pt_platform.py must exist in both and expose the same public interface.
iface() {
    ( cd "$1" && "${PY}" -c "import pt_platform as p; print(','.join(sorted(n for n in dir(p) if not n.startswith('_'))))" )
}
if [ -f "${SRC}/pt_platform.py" ] && [ -f "${DST}/pt_platform.py" ]; then
    A="$(iface "${SRC}")"
    B="$(iface "${DST}")"
    if [ "${A}" != "${B}" ]; then
        echo "FAIL: pt_platform interface mismatch"
        echo "  lib/:        ${A}"
        echo "  copilot/lib/: ${B}"
        fail=1
    fi
else
    echo "FAIL: pt_platform.py missing in one variant"
    fail=1
fi

# 3. codex/shared_lib must be a byte-identical copy of lib/ (minus tests).
CODEX_SHARED="${REPO}/codex/shared_lib"
if [ -d "${CODEX_SHARED}" ]; then
    shopt -s nullglob
    for f in "${SRC}"/*.py "${SRC}"/*.yaml "${SRC}"/*.txt; do
        base="$(basename "${f}")"
        case "${base}" in conftest.py|test_*.py) continue ;; esac
        if [ ! -f "${CODEX_SHARED}/${base}" ]; then
            echo "FAIL: ${base} missing in codex/shared_lib/ (run scripts/sync-variants.sh)"
            fail=1
        elif ! diff -q "${f}" "${CODEX_SHARED}/${base}" >/dev/null 2>&1; then
            echo "FAIL: ${base} differs between lib/ and codex/shared_lib/ (run scripts/sync-variants.sh)"
            fail=1
        fi
    done
    # No stale extras in codex/shared_lib.
    for f in "${CODEX_SHARED}"/*.py "${CODEX_SHARED}"/*.yaml "${CODEX_SHARED}"/*.txt; do
        [ -e "${f}" ] || continue
        base="$(basename "${f}")"
        [ -f "${SRC}/${base}" ] || { echo "FAIL: stale ${base} in codex/shared_lib/ (not in lib/)"; fail=1; }
    done
    shopt -u nullglob
fi

if [ "${fail}" -eq 0 ]; then
    echo "parity-check: OK — shared core identical, pt_platform interfaces match"
fi
exit "${fail}"
