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
BRANCH="main"

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
if command -v git >/dev/null 2>&1; then
    echo "Downloading (git)..."
    git clone --depth 1 --branch "${BRANCH}" "${REPO}.git" "${WORK}/repo" >/dev/null 2>&1 || fail "git clone failed"
    SRC_COPILOT="${WORK}/repo/copilot"
else
    echo "Downloading (tarball)..."
    curl -fsSL "${REPO}/archive/refs/heads/${BRANCH}.tar.gz" -o "${WORK}/src.tgz" || fail "download failed"
    tar -xzf "${WORK}/src.tgz" -C "${WORK}"
    SRC_COPILOT="$(find "${WORK}" -maxdepth 2 -type d -name copilot | head -1)"
fi
[ -d "${SRC_COPILOT}" ] || fail "Download succeeded but copilot/ folder missing — repo layout changed?"

# 4. Copy into installed-plugins.
DEST="${COPILOT_HOME}/installed-plugins/preference-tracker/preference-tracker"
mkdir -p "${DEST}"
# Copy contents, excluding caches.
( cd "${SRC_COPILOT}" && tar --exclude='__pycache__' --exclude='.pytest_cache' --exclude='PORT_NOTES' --exclude='*.pyc' -cf - . ) | ( cd "${DEST}" && tar -xf - )
echo "[OK] Plugin files installed to ${DEST}"

# 5. Optional dependency (best-effort).
if "${PY}" -m pip install --quiet --disable-pip-version-check pyyaml >/dev/null 2>&1; then
    echo "[OK] PyYAML ready"
else
    echo "[i] PyYAML not installed (fingerprint retrieval will degrade; core blocking still works)"
fi

# 6. Run post-install from installed copy.
echo "Running post-install..."
bash "${DEST}/install.sh" --mode observe

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
echo "================================================================"
