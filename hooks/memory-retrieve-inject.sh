#!/usr/bin/env bash
# memory-retrieve-inject.sh — UserPromptSubmit hook (CC)
# Part of the tellonce memory retrieve + inject flow.
# Reads stdin JSON, dispatches to keyword or cli-backed retrieve_inject.
# Default backend: cli + claude haiku.
# Non-destructive: any failure → exit 0 silently.

_PT_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"

# Recursion guard: when retrieve_inject spawns `claude -p` to do semantic
# retrieval, that nested claude session also fires UserPromptSubmit hooks.
# This flag ensures the nested call exits immediately so we don't loop.
if [ "${B5_RETRIEVE_RECURSION_GUARD}" = "1" ]; then
    exit 0
fi

exec python3 "${_PT_LIB}/retrieve_inject.py"
