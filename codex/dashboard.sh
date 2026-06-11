#!/usr/bin/env bash
set -euo pipefail
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SKILL_DIR}/../.." && pwd)"
if [[ -d "${SKILL_DIR}/tellonce_codex" ]]; then
  REPO_ROOT="${SKILL_DIR}"
fi
PYTHON="${PYTHON:-python3}"
PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" "${PYTHON}" -m tellonce_codex dashboard "$@"
