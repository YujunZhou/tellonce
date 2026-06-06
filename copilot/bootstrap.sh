#!/usr/bin/env bash
# bootstrap.sh — ONE-COMMAND installer for preference-tracker (GitHub Copilot CLI, macOS/Linux).
#
# Users run a single copy-paste line (no environment fiddling required):
#
#   curl -fsSL https://raw.githubusercontent.com/YujunZhou/preference-tracker/main/copilot/bootstrap.sh | bash
#
# Downloads the plugin, drops it into Copilot's plugin folder, installs the
# optional PyYAML dep, runs post-install (state, seed, observe mode, register,
# python path), and tells you to restart Copilot. Safe to re-run.
set -euo pipefail

REPO="https://github.com/YujunZhou/preference-tracker"
# Pinned to a release tag (immutable) for integrity.
REF="v1.0.0"
REFKIND="tags"

fail() { printf '\033[31m[X] %s\033[0m\n' "$1" >&2; exit 1; }

echo "================================================================"
echo "  preference-tracker — one-command installer (Copilot CLI)"
echo "================================================================"

# 1. Copilot home.
COPILOT_HOME="${HOME}/.copilot"
[ -d "${COPILOT_HOME}" ] || fail "Copilot CLI home (~/.copilot) not found. Install GitHub Copilot CLI first, run it once, then re-run this."

# 2. Find python 3.7+.
PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
        if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,7) else 1)' 2>/dev/null; then PY="$c"; break; fi
    fi
done
[ -n "${PY}" ] || fail "Python 3.7+ not found. Install it, then re-run."
echo "[OK] Python: $(command -v "${PY}")"

# 3. Download repo (git if available, else tarball).
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
SRC_COPILOT=""
got_src=0
if command -v git >/dev/null 2>&1; then
    echo "Downloading (git)..."
    if git clone --depth 1 --branch "${REF}" "${REPO}.git" "${WORK}/repo" >/dev/null 2>&1; then
        SRC_COPILOT="${WORK}/repo/copilot"; got_src=1
    else
        echo "[i] git clone failed, falling back to tarball..."
    fi
fi
if [ "${got_src}" -ne 1 ]; then
    echo "Downloading (tarball)..."
    curl -fsSL "${REPO}/archive/refs/${REFKIND}/${REF}.tar.gz" -o "${WORK}/src.tgz" \
        || fail "Download failed (no internet / proxy / private repo?). Check your connection and retry."
    tar -xzf "${WORK}/src.tgz" -C "${WORK}" || fail "Extract failed."
    SRC_COPILOT="$(find "${WORK}" -maxdepth 2 -type d -name copilot | head -1)"
fi
[ -d "${SRC_COPILOT}" ] || fail "Download succeeded but copilot/ folder missing — repo layout changed?"

# 4. Copy into installed-plugins.
DEST="${COPILOT_HOME}/installed-plugins/preference-tracker/preference-tracker"
mkdir -p "${DEST}"
# Copy contents, excluding caches.
( cd "${SRC_COPILOT}" && tar --exclude='__pycache__' --exclude='.pytest_cache' --exclude='PORT_NOTES' --exclude='*.pyc' -cf - . ) | ( cd "${DEST}" && tar -xf - )
echo "[OK] Plugin files installed to ${DEST}"

# 5. Optional dependency (best-effort; deterministic blocking works without it,
# but session-start rule injection needs PyYAML).
if "${PY}" -m pip install --quiet --disable-pip-version-check "pyyaml>=6.0" >/dev/null 2>&1; then
    echo "[OK] PyYAML ready"
else
    echo "[i] PyYAML not installed — session-start rule injection will be OFF (deterministic blocking still works). Install later: ${PY} -m pip install pyyaml"
fi

# 6. Run post-install from installed copy. Pass the resolved python through.
echo "Running post-install..."
bash "${DEST}/install.sh" --mode observe --python "${PY}"

echo ""
echo "================================================================"
echo "[OK] preference-tracker installed."
echo "  >> RESTART Copilot for the hooks to load. <<"
echo ""
echo "  Default mode = observe (records your preferences, never blocks)."
echo "  Turn on hard blocking later with:"
echo "    ${PY} \"${DEST}/lib/pt_mode.py\" enforce"
echo "  Check status anytime:"
echo "    ${PY} \"${DEST}/lib/doctor.py\""
echo "  Uninstall:"
echo "    ${PY} \"${DEST}/lib/uninstall.py\" --all"
echo "================================================================"
