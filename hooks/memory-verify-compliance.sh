#!/usr/bin/env bash
# memory-verify-compliance.sh — Stop hook (log-only, Phase B3 lite)
# Appends a compliance record per turn. Never blocks.
exec python3 "${HOME}/.claude/skills/preference-tracker/lib/verify_compliance.py"
