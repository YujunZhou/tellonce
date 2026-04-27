#!/usr/bin/env bash
# memory-pending-promote.sh — Stop hook (Phase A.1, Session A 2026-04-25).
# Promotes pending observations to pending_queue.jsonl; raises advisory alert if
# queue length crosses threshold. Never blocks — exit 0 always.
python3 "${HOME}/.claude/skills/preference-tracker/lib/pending_queue_manager.py" promote >/dev/null 2>&1 || true
exit 0
