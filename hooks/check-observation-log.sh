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

set -euo pipefail

INPUT=$(cat)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null || echo "")

# C1 fix (Phase 8 code review): scope check + path_config 驱动, 不写死 yzhou25 path/scope
# Legacy yzhou25 path 仅作 fallback; 同学装包走 path_config detect
SCRATCH365_LEGACY=false
if [[ "$CWD" == *example-research-project* ]]; then
  SCRATCH365_LEGACY=true
fi

TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || echo "")

# Detect OBS_LOG via path_config (single source of truth)
OBS_LOG=$(python3 -c "
import sys
sys.path.insert(0, '${HOME}/.claude/skills/preference-tracker/lib')
import path_config
print(path_config.get_observations_log_path())
" 2>/dev/null || echo "")

# Fallback if path_config unavailable
if [[ -z "${OBS_LOG}" ]]; then
  if [[ "${SCRATCH365_LEGACY}" == true ]]; then
    OBS_LOG="/home/user/zyj/example-research-project/skill_observation_log/observations.jsonl"
  else
    # silent skip — no obs log configured (同学装包前没 path_config 时不 spam)
    exit 0
  fi
fi

# Trace log: TMPDIR if set, else /tmp (per `tool-pit-130` 写 state/runtime/ 不 /tmp;
# 但 trace log 是 transient debug only, 不属 production 数据, /tmp 兜底 OK)
TRACE_LOG="${TMPDIR:-/tmp}/hook-trace.log"
TRACE_ID="inv-$(date +%s.%N)"
echo "═══════════ $TRACE_ID ═══════════" >> "$TRACE_LOG"
echo "[timestamp] $(date +%Y-%m-%d\ %H:%M:%S.%N)" >> "$TRACE_LOG"
echo "[input] $INPUT" >> "$TRACE_LOG"
_orig_hook_output_file="/tmp/hook-trace-output-$TRACE_ID"

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
  LAST_MOD=$(stat -c "%Y" "$OBS_LOG" 2>/dev/null || echo "0")
  NOW=$(date +%s)
  AGE=$((NOW - LAST_MOD))

  # Threshold: env-tunable (I4 fix per Phase 8 review). Default 600s 适合长 autonomous block;
  # 同学短 turn 项目可调小 (e.g. 180s); 长跑可调大 (e.g. 1800s).
  OBS_AGE_THRESHOLD="${OBSERVATION_LOG_AGE_THRESHOLD_SEC:-600}"
  if [ "$AGE" -gt "$OBS_AGE_THRESHOLD" ]; then
    WARNINGS="${WARNINGS}⚠️ OBSERVATION LOG last modified ${AGE}s ago (threshold ${OBS_AGE_THRESHOLD}s; tune via env OBSERVATION_LOG_AGE_THRESHOLD_SEC). The log likely wasn't appended this turn.\n"
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
# 下面 line 153-227 SOFT check 段历史保留作 legacy reference (per `wf-pref-027` 永不
# overwrite). 若想用 grep-based 文本 marker 检查请: (a) RUN_SOFT_CHECK=true; 或 (b)
# 删除 line 153-227 那段 (Phase 9 fix per I4 review). 当前版**绝不会执行**该段.

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
