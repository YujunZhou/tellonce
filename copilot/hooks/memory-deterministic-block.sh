#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PT_LIB="${SCRIPT_DIR}/../lib"
set -uo pipefail

# memory-deterministic-block.sh — Stop hook (Phase B5 Tier A item 1)


# ──────────────────────────────────────────────────────────────────────────
# Short-circuit (per wf-pref-320, 2026-04-28, w/ C1 stale-tail guard):
# skip when THIS turn's obs entry has detected=false.
# Guards: (a) tail entry's session_id matches current, (b) obs_log mtime <60s.
# Both required — otherwise fall through to full hook (safe degrade).
# ──────────────────────────────────────────────────────────────────────────
_INPUT_SC=$(cat)
_CUR_SID_SC=$(echo "${_INPUT_SC}" | jq -r '.session_id // empty' 2>/dev/null)
_OBS_LOG_FOR_SC=$(env PT_LIB="${PT_LIB}" PYTHONIOENCODING=utf-8 python3 -c 'import sys, os; sys.path.insert(0, os.environ["PT_LIB"]); import path_config; print(path_config.get_observations_log_path())' 2>/dev/null)
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
        # User-prefer gate: only skip when user prefers URGENT (per v23 day-1 refinement)
        _TRANSCRIPT_SC=$(echo "${_INPUT_SC}" | jq -r '.transcript_path // empty' 2>/dev/null)
        _PREFER_SC="u"
        if [ -n "${_TRANSCRIPT_SC}" ] && [ -f "${_TRANSCRIPT_SC}" ]; then
          _PREFER_SC=$(timeout 5 python3 "${PT_LIB}/detect_user_prefer.py" "${_TRANSCRIPT_SC}" 2>/dev/null || echo u)
        fi
        if [ "${_PREFER_SC}" = "u" ]; then
          exit 0
        fi
      fi
    fi
  fi
fi
# End short-circuit — re-feed stdin to child below


# 3 deterministic regex hard-block rules: lang-pit-130 / oth-pref-001 / lang-pref-001 relaxed.
# Child emits JSON decision='block' on violation; wrapper normalizes exit 2 -> 0 for Copilot.
# Set B5_DETERMINISTIC_DISABLED=1 to bypass entirely (logs as 'disabled').
# Defensive: any internal error → exit 0 (don't block legit work).

# Re-feed captured stdin to the Python entry. `cat` above drained stdin; without
# this `printf` pipe, Python json.load(sys.stdin) would see EOF, hit the defensive
# `except Exception: sys.exit(0)`, and the hook would silently no-op on every
# fall-through (any time short-circuit doesn't trigger). See C1 fix.
printf '%s' "${_INPUT_SC}" | python3 "${PT_LIB}/deterministic_block.py"
_CHILD_RC=$?
if [ "${_CHILD_RC}" -eq 2 ]; then
  exit 0
fi
exit 0
