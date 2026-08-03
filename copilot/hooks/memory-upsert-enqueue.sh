#!/usr/bin/env bash
# Fast, non-blocking Stop hook: write one inbox request and detach the worker.
set +e
PT_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
PYTHONIOENCODING=utf-8 PYTHONPATH="${PT_LIB}" \
  python3 "${PT_LIB}/memory_upsert_hook.py" stop
exit 0
