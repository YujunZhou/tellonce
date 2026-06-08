#!/bin/bash
# Hook: Verify preference-tracker Gate Function was FULLY executed
# Fires on Stop event — blocks response if any step is missing
#
# Gate Function has 4 steps when signal detected:
#   1. SCAN      — did you analyze for preference/pitfall/friction signals?
#   2. RECORD    — did you write to the observation log?
#   3. CONFIRM   — did you state the scan result to the user?
#   4. ROOT CAUSE — if signal detected, did you analyze the root cause?
#                   (only required when CONFIRM contains friction/pitfall/preference)
#
# Enforcement:
#   HARD: observation log file must be updated within 120s
#   SOFT: response text must contain markers for all steps

set -uo pipefail   # NB: no -e — this hook does plenty of optional jq parses
                   # whose failure is acceptable. With -e set, any of those
                   # falling through pipeline-status non-zero would kill the
                   # whole hook (H8 fix).

# H8 fix: jq is required for parsing the stdin JSON payload. Without it the
# rest of this script would silently degrade — `jq -r ... 2>/dev/null` returns
# empty + `set -e` (above) would exit 127. Skip the hook if jq is missing
# rather than reporting a hook-error that confuses users.
if ! command -v jq > /dev/null 2>&1; then
    exit 0
fi

_PT_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")/../lib" && pwd)"

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || echo "")

# Detect OBS_LOG via path_config (single source of truth). Use env-channel argv
# rather than string-interpolated `sys.path.insert(0, '${HOME}...')` to avoid
# breaking when HOME contains a single quote. (H14 fix.)
OBS_LOG=$(env PT_LIB="${_PT_LIB}" \
              PYTHONIOENCODING=utf-8 \
              python3 -c '
import os, sys
sys.path.insert(0, os.environ["PT_LIB"])
import path_config
print(path_config.get_observations_log_path())
' 2>/dev/null || echo "")

# Fallback if path_config unavailable: silent skip (no Python / lib not installed).
if [[ -z "${OBS_LOG}" ]]; then
  exit 0
fi

# Trace log: opt-in via PT_TRACE=1 (default OFF; legacy B5_TRACE still honored).
# When opt-in, write to
# state_dir (per project) instead of /tmp — /tmp is world-readable on shared
# hosts and INPUT contains transcript_path / cwd / session_id (privacy fix per
# H11 review). Set via env PT_TRACE_LOG (legacy B5_TRACE_LOG) to override the
# path explicitly.
B5_TRACE="${B5_TRACE:-${PT_TRACE:-0}}"
B5_TRACE_LOG="${B5_TRACE_LOG:-${PT_TRACE_LOG:-}}"
if [[ "${B5_TRACE:-0}" == "1" ]]; then
  if [[ -n "${B5_TRACE_LOG:-}" ]]; then
    TRACE_LOG="${B5_TRACE_LOG}"
  else
    # Default to state_dir (path_config), private to current user / project
    _PT_STATE_DIR_FOR_TRACE=$(env PT_LIB="${_PT_LIB}" python3 -c '
import os, sys
sys.path.insert(0, os.environ["PT_LIB"])
try:
    import path_config
    print(path_config.get_state_dir())
except Exception:
    print("")
' 2>/dev/null)
    if [[ -n "${_PT_STATE_DIR_FOR_TRACE}" ]]; then
      TRACE_LOG="${_PT_STATE_DIR_FOR_TRACE}/hook-trace.log"
      mkdir -p "${_PT_STATE_DIR_FOR_TRACE}" 2>/dev/null || true
    else
      TRACE_LOG="${TMPDIR:-/tmp}/preference-tracker-hook-trace-$$.log"
    fi
  fi
  TRACE_ID="inv-$(date +%s.%N)"
  echo "═══════════ $TRACE_ID ═══════════" >> "$TRACE_LOG" 2>/dev/null || true
  echo "[timestamp] $(date +%Y-%m-%d\ %H:%M:%S.%N)" >> "$TRACE_LOG" 2>/dev/null || true
  echo "[input] $INPUT" >> "$TRACE_LOG" 2>/dev/null || true
  # Restrict trace log to user-only (best-effort; ignore failure on shared FS)
  chmod 600 "$TRACE_LOG" 2>/dev/null || true
else
  # Tracing disabled by default. Provide a no-op TRACE_LOG so later `>> "$TRACE_LOG"`
  # stays harmless (writes go to /dev/null).
  TRACE_LOG="/dev/null"
  TRACE_ID="inv-$(date +%s.%N)"
fi

WARNINGS=""

# ══════════════════════════════════════════════════════════════════════════
# CHECK 1 (HARD): Observation log has entry for THIS turn
# Rules:
#   a) File mtime within 30s (tight — a just-completed turn must have logged)
#   b) Last entry's session_id matches current session_id (if entries include it)
#   c) Last entry's timestamp within 30s of now
# Any miss → warn
# ══════════════════════════════════════════════════════════════════════════
CURRENT_SESSION=$(echo "$INPUT" | jq -r '.session_id // empty')

if [ -f "$OBS_LOG" ]; then
  # BSD stat (macOS) doesn't accept -c %Y. Try GNU first, fall back to BSD -f %m.
  LAST_MOD=$(stat -c "%Y" "$OBS_LOG" 2>/dev/null \
             || stat -f %m "$OBS_LOG" 2>/dev/null \
             || echo "0")
  NOW=$(date +%s)
  AGE=$((NOW - LAST_MOD))

  # Threshold: env-tunable. Default raised 600 → 1800 (Sprint v23 day-1, 2026-04-27)
  # to accommodate long write-heavy autonomous blocks where mid-turn obs append is missed.
  # Tune via env OBSERVATION_LOG_AGE_THRESHOLD_SEC (e.g. 600 for short-turn projects).
  OBS_AGE_THRESHOLD="${OBSERVATION_LOG_AGE_THRESHOLD_SEC:-1800}"

  # Auto-fallback: when age exceeds threshold and OBSERVATION_LOG_AUTO_FALLBACK=1
  # (default), invoke verify_compliance.py --auto-light-fallback to append a
  # synthetic detected=False entry rather than emit a friction warning. This keeps
  # the gate's structured-log validation happy without nagging the user. Set env
  # OBSERVATION_LOG_AUTO_FALLBACK=0 to revert to warning-mode (legacy behavior).
  AUTO_FALLBACK="${OBSERVATION_LOG_AUTO_FALLBACK:-1}"

  if [ "$AGE" -gt "$OBS_AGE_THRESHOLD" ]; then
    if [ "$AUTO_FALLBACK" = "1" ]; then
      VERIFY_PY="${_PT_LIB}/verify_compliance.py"
      if [ -f "$VERIFY_PY" ]; then
        # Best-effort invocation; defensive (never block hook on its failure).
        FB_OUT=$(python3 "$VERIFY_PY" --auto-light-fallback \
                   --session-id "${CURRENT_SESSION:-unknown}" \
                   --age-sec "$AGE" \
                   --threshold-sec "$OBS_AGE_THRESHOLD" \
                   --obs-log-path "$OBS_LOG" \
                   --cwd "${CWD:-$(pwd)}" \
                   --quiet 2>&1)
        FB_RC=$?
        echo "[auto-fallback] age=${AGE}s threshold=${OBS_AGE_THRESHOLD}s rc=${FB_RC} out=${FB_OUT}" >> "$TRACE_LOG"
        # On success, suppress the warning — the synthetic entry now satisfies CHECK 1.
        # On failure, fall through to the warning so user knows something's wrong.
        if [ "$FB_RC" -ne 0 ]; then
          WARNINGS="${WARNINGS}⚠️ OBSERVATION LOG last modified ${AGE}s ago (threshold ${OBS_AGE_THRESHOLD}s) AND auto-fallback failed (rc=${FB_RC}). The log likely wasn't appended this turn.\n"
        fi
      else
        WARNINGS="${WARNINGS}⚠️ OBSERVATION LOG last modified ${AGE}s ago (threshold ${OBS_AGE_THRESHOLD}s) AND verify_compliance.py not found at ${VERIFY_PY} for auto-fallback.\n"
      fi
    else
      WARNINGS="${WARNINGS}⚠️ OBSERVATION LOG last modified ${AGE}s ago (threshold ${OBS_AGE_THRESHOLD}s; tune via env OBSERVATION_LOG_AGE_THRESHOLD_SEC; auto-fallback off via OBSERVATION_LOG_AUTO_FALLBACK=0). The log likely wasn't appended this turn.\n"
    fi
  fi

  # Parse last entry for session_id match (if present)
  LAST_LINE=$(tail -1 "$OBS_LOG" 2>/dev/null)
  if [ -n "$LAST_LINE" ] && [ -n "$CURRENT_SESSION" ]; then
    LAST_SESSION=$(echo "$LAST_LINE" | jq -r '.session_id // empty' 2>/dev/null)
    if [ -n "$LAST_SESSION" ] && [ "$LAST_SESSION" != "$CURRENT_SESSION" ]; then
      WARNINGS="${WARNINGS}⚠️ Last observation entry is from a DIFFERENT session (${LAST_SESSION} vs current ${CURRENT_SESSION}). This turn was not logged.\n"
    fi
  fi
else
  WARNINGS="${WARNINGS}⚠️ Observation log file not found at ${OBS_LOG}.\n"
fi

# ══════════════════════════════════════════════════════════════════════════
# ROBUSTNESS REWRITE (2026-04-19, final):
# Replaced response-text regex scan (unstable) with STRUCTURED LOG VALIDATION
# (stable, zero false positive). Checks the LAST observation entry has:
#   1. detection.detected ∈ {true, false} (not null / "unknown")
#   2. trigger.user_message_excerpt non-empty
#   3. self_observations.uncertainty_notes non-empty
#   4. If detected=true: signal_type ∈ {preference,pitfall,friction}
#      AND content field length >= 30 chars (no "ok" sloppy entries)
#      AND action.confirmation_text non-empty
# ══════════════════════════════════════════════════════════════════════════
RUN_SOFT_CHECK=false  # legacy text-scan permanently disabled (per 2026-04-19 robustness rewrite).
# The SOFT check section at lines 153-227 below is kept for legacy reference (per `wf-pref-027`,
# never overwrite). To use the grep-based text-marker check: (a) set RUN_SOFT_CHECK=true; or (b)
# delete that lines 153-227 section. The current version **never executes** that section.

QUALITY_WARNINGS=""
if [ -f "$OBS_LOG" ]; then
  LAST_LINE=$(tail -1 "$OBS_LOG" 2>/dev/null)
  if [ -n "$LAST_LINE" ]; then
    # Validate structure via jq
    DETECTED=$(echo "$LAST_LINE" | jq -r '.detection.detected' 2>/dev/null)
    EXCERPT=$(echo "$LAST_LINE" | jq -r '.trigger.user_message_excerpt // ""' 2>/dev/null)
    UNCERTAINTY=$(echo "$LAST_LINE" | jq -r '.self_observations.uncertainty_notes // ""' 2>/dev/null)

    if [ "$DETECTED" != "true" ] && [ "$DETECTED" != "false" ]; then
      QUALITY_WARNINGS="${QUALITY_WARNINGS}⚠️ LOG QUALITY: detection.detected must be boolean (got: ${DETECTED}).\n"
    fi
    if [ -z "$EXCERPT" ] || [ "$EXCERPT" = "null" ]; then
      QUALITY_WARNINGS="${QUALITY_WARNINGS}⚠️ LOG QUALITY: trigger.user_message_excerpt is empty.\n"
    fi
    if [ -z "$UNCERTAINTY" ] || [ "$UNCERTAINTY" = "null" ]; then
      QUALITY_WARNINGS="${QUALITY_WARNINGS}⚠️ LOG QUALITY: self_observations.uncertainty_notes is empty — show your reasoning.\n"
    fi

    # If a signal was detected, enforce stricter content checks
    if [ "$DETECTED" = "true" ]; then
      SIG_TYPE=$(echo "$LAST_LINE" | jq -r '.detection.signal_type // ""' 2>/dev/null)
      CONTENT=$(echo "$LAST_LINE" | jq -r '.detection.content // ""' 2>/dev/null)
      CONF_TEXT=$(echo "$LAST_LINE" | jq -r '.action.confirmation_text // ""' 2>/dev/null)
      case "$SIG_TYPE" in
        preference|pitfall|friction) : ;;
        *) QUALITY_WARNINGS="${QUALITY_WARNINGS}⚠️ LOG QUALITY: signal_type must be preference/pitfall/friction when detected=true (got: ${SIG_TYPE}).\n" ;;
      esac
      CONTENT_LEN=${#CONTENT}
      if [ "$CONTENT_LEN" -lt 30 ]; then
        QUALITY_WARNINGS="${QUALITY_WARNINGS}⚠️ LOG QUALITY: detection.content too short (${CONTENT_LEN} chars, need >=30) — describe the signal substantively.\n"
      fi
      if [ -z "$CONF_TEXT" ] || [ "$CONF_TEXT" = "null" ]; then
        QUALITY_WARNINGS="${QUALITY_WARNINGS}⚠️ LOG QUALITY: action.confirmation_text empty when signal detected — must tell user what was confirmed.\n"
      fi
    fi

    # Merge quality warnings into main WARNINGS pile
    if [ -n "$QUALITY_WARNINGS" ]; then
      WARNINGS="${WARNINGS}${QUALITY_WARNINGS}"
      echo "[quality-fail]" >> "$TRACE_LOG"
      echo "$QUALITY_WARNINGS" >> "$TRACE_LOG"
    else
      echo "[quality-pass]" >> "$TRACE_LOG"
    fi
  fi
fi

# ══════════════════════════════════════════════════════════════════════════
# CHECK 2 (SOFT): Gate Function markers in response
# Only active when last log entry has detected=true.
# ══════════════════════════════════════════════════════════════════════════
# Prefer reading last_assistant_message directly from INPUT (authoritative),
# fall back to tail transcript if that field is missing.
TAIL_CONTENT=$(echo "$INPUT" | jq -r '.last_assistant_message // empty')
if [ -z "$TAIL_CONTENT" ] && [ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ]; then
  TAIL_CONTENT=$(tail -100 "$TRANSCRIPT_PATH" 2>/dev/null || echo "")
fi

if [ "$RUN_SOFT_CHECK" = true ] && [ -n "$TAIL_CONTENT" ]; then

  HAS_SCAN=false
  HAS_RECORD=false
  HAS_CONFIRM=false
  SIGNAL_DETECTED=false
  HAS_ROOT_CAUSE=false

  # ── SCAN (RELAXED): allow markdown-bold forms + bare bold
  if echo "$TAIL_CONTENT" | grep -qE \
    "[Ss]can[:：]|\\*\\*[Ss]can\\*\\*|^[Ss]can |Scan: |\\*\\*SCAN\\*\\*|检测到.*(信号|signal|preference|friction|pitfall|偏好|摩擦|陷阱)"; then
    HAS_SCAN=true
  fi

  # ── RECORD (STRICT): require tool-call evidence, not just mention ─────
  # Must show evidence of actual append to observations.jsonl
  # (Bash tool call containing the path + append operator)
  if echo "$TAIL_CONTENT" | grep -qE \
    "observations\\.jsonl.*\"a\\)|observations\\.jsonl.*>>|f\\.write\\(json\\.dumps|append.*to.*observations|Bash.*logged"; then
    HAS_RECORD=true
  fi

  # ── CONFIRM (RELAXED): bold-markdown forms + confirmation phrasing
  if echo "$TAIL_CONTENT" | grep -qE \
    "\\*\\*CONFIRM\\*\\*|^CONFIRM|CONFIRM[: ]|\\*\\*确认\\*\\*|确认[:：]|检测到|detected.*(preference|pitfall|friction)|无(偏好)?信号|纯任务|no signal detected|base.rate|detected=false|detected=true|已记录"; then
    HAS_CONFIRM=true
  fi

  # ── Did CONFIRM indicate a signal was found? ─────────────────────────
  if echo "$TAIL_CONTENT" | grep -qi \
    "detected.*friction\|detected.*pitfall\|detected.*preference\|检测到.*摩擦\|检测到.*陷阱\|检测到.*偏好"; then
    SIGNAL_DETECTED=true
  fi

  # ── ROOT CAUSE (only required if signal detected) ────────────────────
  if [ "$SIGNAL_DETECTED" = true ]; then
    if echo "$TAIL_CONTENT" | grep -qi \
      "ROOT CAUSE\|root.cause\|根因\|根本原因\|underlying.*cause\|本质.*是\|根源"; then
      HAS_ROOT_CAUSE=true
    fi
  fi

  # ── Emit warnings ───────────────────────────────────────────────────
  if [ "$HAS_SCAN" = false ]; then
    WARNINGS="${WARNINGS}⚠️ SCAN step missing: No evidence of signal detection in response.\n"
  fi

  if [ "$HAS_RECORD" = false ]; then
    WARNINGS="${WARNINGS}⚠️ RECORD step missing: No evidence of observation log entry in response.\n"
  fi

  if [ "$HAS_CONFIRM" = false ]; then
    WARNINGS="${WARNINGS}⚠️ CONFIRM step missing: No scan result stated to user.\n"
  fi

  if [ "$SIGNAL_DETECTED" = true ] && [ "$HAS_ROOT_CAUSE" = false ]; then
    WARNINGS="${WARNINGS}⚠️ ROOT CAUSE missing: Signal detected but no root cause analysis. Ask: what is the underlying cause? What general rule prevents this CLASS of error (not just this instance)?\n"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════
# OUTPUT
# ══════════════════════════════════════════════════════════════════════════
if [ -n "$WARNINGS" ]; then
  MSG=$(echo -e "$WARNINGS" | head -10)
  # Use decision=block → CC forces model to re-engage BEFORE truly stopping.
  # continue=false also ensures the stop is rejected until gate is satisfied.
  # Stop event schema does NOT allow hookSpecificOutput — all context must go in `reason`.
  # (Only PreToolUse / UserPromptSubmit / PostToolUse support hookSpecificOutput.)
  OUTPUT=$(jq -n --arg msg "$MSG" '{
    "decision": "block",
    "continue": false,
    "stopReason": "Gate Function incomplete — resolve before stopping",
    "reason": ("🔴 PREFERENCE-TRACKER GATE CHECK FAILED\n\nMissing steps detected:\n" + $msg + "\nGate Function: SCAN → RECORD → CONFIRM → ROOT CAUSE (if signal detected)\n\nYou cannot stop until you: (1) SCAN the user message for preference/pitfall/friction signals, (2) RECORD an entry to observations.jsonl, (3) CONFIRM the scan result to the user, (4) ROOT CAUSE if signal detected.\n\nRoot cause = the general rule that prevents this CLASS of error, not the specific instance.\n\nDo this NOW in a brief follow-up, then stop. Not optional.")
  }')
  echo "[output] $OUTPUT" >> "$TRACE_LOG"
  echo "[exit_code] 2 (BLOCKING)" >> "$TRACE_LOG"
  echo "" >> "$TRACE_LOG"
  echo "$OUTPUT"
  exit 2
fi

echo "[output] (no warnings — pass)" >> "$TRACE_LOG"
echo "[exit_code] 0 (OK)" >> "$TRACE_LOG"
echo "" >> "$TRACE_LOG"
exit 0
