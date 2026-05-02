#!/usr/bin/env bash
# memory-retrieve-inject.sh — UserPromptSubmit hook (CC)
# Part of preference-tracker skill upgrade Phase B1 (2026-04-22).
# Reads stdin JSON, dispatches to keyword or cli-backed retrieve_inject.
# Round-10 (2026-05-02): default backend cli + claude haiku.
# Non-destructive: any failure → exit 0 silently.

# Recursion guard: when retrieve_inject spawns `claude -p` to do semantic
# retrieval, that nested claude session also fires UserPromptSubmit hooks.
# This flag ensures the nested call exits immediately so we don't loop.
if [ "${B5_RETRIEVE_RECURSION_GUARD}" = "1" ]; then
    exit 0
fi

exec python3 "${HOME}/.claude/skills/preference-tracker/lib/retrieve_inject.py"
