#!/usr/bin/env bash
# memory-retrieve-inject.sh — UserPromptSubmit hook
# Part of preference-tracker skill upgrade Phase B1 (2026-04-22).
# Reads stdin JSON, scans fingerprints.yaml for deterministic keyword triggers,
# emits additionalContext listing matched atomic_ids.
# Non-destructive: any failure → exit 0 silently.

exec python3 "${HOME}/.claude/skills/preference-tracker/lib/retrieve_inject.py"
