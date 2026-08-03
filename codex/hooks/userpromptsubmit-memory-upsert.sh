#!/usr/bin/env bash
# Fast, non-blocking UserPromptSubmit hook: enqueue only; semantic work is detached.
set +e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED_LIB="${SCRIPT_DIR}/../shared_lib"
PYTHONIOENCODING=utf-8 PYTHONPATH="${SHARED_LIB}" \
  python3 "${SHARED_LIB}/memory_upsert_hook.py" prompt
exit 0
