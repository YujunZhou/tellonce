#!/usr/bin/env bash
# memory-shadow-judge.sh — Stop hook (Phase B5 Tier A item 2)
# LLM judge runs on every Stop, but ALWAYS exits 0 (shadow mode, never blocks).
# Side effects: write violations to b5_shadow_log.jsonl + B5_SHADOW_ALERT.md (rolling cap N=3).
# Set B5_SHADOW_DISABLED=1 to skip judge entirely.
# Set ANTHROPIC_CREDIT_OK=1 to enable judge (default off until credit verified).
# Defensive: any internal error → exit 0 (don't block legit work).

exec python3 "${HOME}/.claude/skills/preference-tracker/lib/verify_retry_shadow.py"
