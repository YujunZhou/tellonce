#!/usr/bin/env bash
# memory-verify-compliance.sh — Stop hook (log-only, Phase B3 lite)

# ──────────────────────────────────────────────────────────────────────────
# Short-circuit (per wf-pref-320, 2026-04-27, w/ C1 stale-tail guard):
# skip when THIS turn's obs entry has detected=false.
# Guards: (a) tail entry's session_id matches current, (b) obs_log mtime <60s.
# Both required — otherwise fall through to full hook (safe degrade).
# ──────────────────────────────────────────────────────────────────────────
_PT_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"
_INPUT_SC=$(cat)
_CUR_SID_SC=$(echo "${_INPUT_SC}" | jq -r '.session_id // empty' 2>/dev/null)
_OBS_LOG_FOR_SC=$(env PT_LIB="${_PT_LIB}" python3 -c 'import sys, os; sys.path.insert(0, os.environ["PT_LIB"]); import path_config; print(path_config.get_observations_log_path())' 2>/dev/null)
if [ -n "${_OBS_LOG_FOR_SC}" ] && [ -f "${_OBS_LOG_FOR_SC}" ] && [ -n "${_CUR_SID_SC}" ]; then
  # BSD stat (macOS) doesn't accept -c %Y. Try GNU first, fall back to BSD -f %m.
  _MTIME_SC=$(stat -c %Y "${_OBS_LOG_FOR_SC}" 2>/dev/null \
              || stat -f %m "${_OBS_LOG_FOR_SC}" 2>/dev/null \
              || echo 0)
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

# Appends a compliance record per turn. Never blocks.
# Re-feed captured stdin (drained by `cat` above). See C1 fix in
# memory-deterministic-block.sh for full context.
printf '%s' "${_INPUT_SC}" | exec python3 "${_PT_LIB}/verify_compliance.py"
