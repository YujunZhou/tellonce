#!/usr/bin/env bash
# memory-pending-promote.sh — Stop hook (Phase A.1, Session A 2026-04-25).

# ──────────────────────────────────────────────────────────────────────────
# Short-circuit (per wf-pref-320, 2026-04-27, w/ C1 stale-tail guard):
# skip when THIS turn's obs entry has detected=false.
# Guards: (a) tail entry's session_id matches current, (b) obs_log mtime <60s.
# Both required — otherwise fall through to full hook (safe degrade).
# ──────────────────────────────────────────────────────────────────────────
_INPUT_SC=$(cat)
_CUR_SID_SC=$(echo "${_INPUT_SC}" | jq -r '.session_id // empty' 2>/dev/null)
_OBS_LOG_FOR_SC=$(python3 -c 'import sys, os; sys.path.insert(0, os.path.expanduser("~/.claude/skills/preference-tracker/lib")); import path_config; print(path_config.get_observations_log_path())' 2>/dev/null)
if [ -n "${_OBS_LOG_FOR_SC}" ] && [ -f "${_OBS_LOG_FOR_SC}" ] && [ -n "${_CUR_SID_SC}" ]; then
  _MTIME_SC=$(stat -c %Y "${_OBS_LOG_FOR_SC}" 2>/dev/null || echo 0)
  _AGE_SC=$(( $(date +%s) - _MTIME_SC ))
  if [ "${_AGE_SC}" -lt 60 ]; then
    _LAST_LINE_SC=$(tail -1 "${_OBS_LOG_FOR_SC}" 2>/dev/null)
    _LAST_SID_SC=$(echo "${_LAST_LINE_SC}" | jq -r '.session_id // empty' 2>/dev/null)
    if [ "${_LAST_SID_SC}" = "${_CUR_SID_SC}" ]; then
      _LAST_DETECTED_SC=$(echo "${_LAST_LINE_SC}" | jq -r '.detection.detected // empty' 2>/dev/null)
      if [ "${_LAST_DETECTED_SC}" = "false" ]; then
        exit 0
      fi
    fi
  fi
fi
# End short-circuit — re-feed stdin to child below

# Promotes pending observations to pending_queue.jsonl; raises advisory alert if
# queue length crosses threshold. Never blocks — exit 0 always.
python3 "${HOME}/.claude/skills/preference-tracker/lib/pending_queue_manager.py" promote >/dev/null 2>&1 || true
exit 0
