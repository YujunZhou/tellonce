#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PT_LIB="${SCRIPT_DIR}/../lib"
set -uo pipefail

exec env PT_LIB="${PT_LIB}" PYTHONIOENCODING=utf-8 python3 "${PT_LIB}/session_start_inject.py"
