#!/usr/bin/env bash
# memory-shadow-alert-inject.sh — UserPromptSubmit hook (Phase B5 Tier A item 3)
# Read B5_SHADOW_ALERT.md (set by shadow judge), inject "last turn you violated X" notice
# into next-turn additionalContext.
# Set B5_INJECT_DISABLED=1 to opt out.
# 24h TTL on alerts.
# Defensive: any error → exit 0 silently.

exec python3 "${HOME}/.claude/skills/preference-tracker/lib/shadow_alert_inject.py"
