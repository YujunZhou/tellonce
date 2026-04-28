#!/usr/bin/env bash
# memory-deterministic-block.sh — Stop hook (Phase B5 Tier A item 1)


# 3 deterministic regex hard-block rules: lang-pit-130 / oth-pref-001 / lang-pref-001 relaxed.
# Exit 2 + JSON decision='block' on violation; else exit 0.
# Set B5_DETERMINISTIC_DISABLED=1 to bypass entirely (logs as 'disabled').
# Defensive: any internal error → exit 0 (don't block legit work).

exec python3 "${HOME}/.claude/skills/preference-tracker/lib/deterministic_block.py"
