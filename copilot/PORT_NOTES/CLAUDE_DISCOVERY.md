# Claude-version discovery report

> Internal note for the Copilot CLI port. Read-only analysis of the existing Claude Code implementation.
> Generated 2026-05-19.

## 1. Hooks inventory

| Hook | Claude event | stdin JSON keys | stdout / exit | Side effects | Deps |
|---|---|---|---|---|---|
| `hooks/check-observation-log.sh` | `Stop` | `session_id`, `transcript_path`, `cwd`, `last_assistant_message` | emits `{decision:"block",continue:false,...}` and `exit 2` if Gate Function incomplete; else `exit 0` | trace log; may call `verify_compliance.py --auto-light-fallback` | `jq`, `python3`, `path_config`, `verify_compliance.py` |
| `hooks/memory-deterministic-block.sh` | `Stop` | `session_id`, `transcript_path` | if violation: `{decision:"block",reason:...}` + `exit 2`; else `exit 0` | logs compliance/streak via `deterministic_block.py` | `jq`, `python3`, `stat`, `tail`, `date` |
| `hooks/memory-verify-compliance.sh` | `Stop` | `session_id`, `transcript_path` | never blocks; `exit 0` | appends compliance record via `verify_compliance.py` | `jq`, `python3`, `stat`, `tail`, `date` |
| `hooks/memory-shadow-judge.sh` | `Stop` | `session_id`, `transcript_path` | never blocks; `exit 0` | appends shadow log + alert md + compliance log via `verify_retry_shadow.py` | `jq`, `python3`, `stat`, `tail`, `date` |
| `hooks/memory-pending-promote.sh` | `Stop` | `session_id` (pass-through) | never blocks; `exit 0` | promotes pending obs to `pending_queue.jsonl` | `jq`, `python3`, `stat`, `tail`, `date` |
| `hooks/memory-retrieve-inject.sh` | `UserPromptSubmit` | (none directly) | emits `hookSpecificOutput.additionalContext` JSON on hit; `exit 0` | injects retrieved memory; nested `claude -p` / `codex exec` | `python3`, Claude CLI / Codex CLI |
| `hooks/memory-pending-inject.sh` | `UserPromptSubmit` | (none) | emits `hookSpecificOutput.additionalContext` JSON if pending queue non-empty; `exit 0` | reads pending queue (read-only) | `python3` |
| `hooks/memory-shadow-alert-inject.sh` | `UserPromptSubmit` | (none) | emits `hookSpecificOutput.additionalContext` if recent alerts; `exit 0` | consumes alert keys | `python3` |

## 2. `lib/` module map

- `path_config.py` — central path resolver. Hard-codes `~/.claude/...` and Claude project memory dir (29-225).
- `_install_merge_settings.py` — merges PT hook entries into `settings.local.json` (Claude-schema specific).
- `deterministic_block.py` — Stop-hook deterministic blocker; ships with **zero built-in rules** (`evaluate_rules()` is the opt-in extension point; rules accumulate from user corrections).
- `verify_compliance.py` — compliance logger + Gate Function checker. Reads Claude `transcript_path`, writes Stop decision JSON.
- `verify_retry_shadow.py` — shadow LLM judge. Hard-codes `claude-haiku-4-5`, `claude -p`.
- `retrieve_inject.py` — UserPromptSubmit retrieval. Hard-codes `claude -p`, `codex exec` backends.
- `shadow_alert_inject.py` — recent-alerts → additionalContext.
- `pending_queue_manager.py` — promote/inject/prune pending memory.
- `analyze_b5_compliance.py` — daily compliance summary.
- `threshold_advisor.py` — threshold tuning suggestions.
- `apply_threshold.py` — edits memory frontmatter `params:`.
- `auto_retire_superseded.py` — archives superseded memory.
- `detect_user_prefer.py` — classifies last user prompt (urgent/clarity). Hard-codes `claude -p`.
- `rule_params.py` — parses rule frontmatter `params:`.
- `redaction.py` — secret-pattern redaction.
- `fingerprints.yaml`, `*_whitelist*.txt` — data.

## 3. `install.sh` phase summary

1. **Prepare** (114-176): validate HOME, Python 3.7+, `jq`, `claude` CLI; check existing settings JSON; verify `lib/` + `hooks/` present.
2. **Install** (184-340): compute paths → versioned backup of `settings.local.json` → register hooks (from `~/.claude/skills/preference-tracker/hooks`) into `settings.local.json` via Python merge → create state subdirs → write `~/.preference-tracker.config.json` → seed memory if absent.
3. **Collect** (343-360): verify state dirs writable.
4. **Execute** (362-372): run `doctor.sh`.
5. **Uninstall ready** (374-407): confirm `uninstall.sh` executable.

Rollback: `trap ERR` restores backed-up settings only; hooks/state/memory preserved.

## 4. Claude-specific coupling (port targets)

| Claude site | Why it must change |
|---|---|
| `~/.claude/...` paths | Must become `~/.copilot/...` (or honor a config var) |
| `claude -p` calls (shadow/retrieve/detect) | Must become `copilot -p` (or equivalent) |
| `Stop` / `UserPromptSubmit` event names | Must map to Copilot CLI hook event names (TBD per Copilot docs) |
| `~/.claude/settings.local.json` merge schema | Must become Copilot plugin `hooks.json` schema |
| `~/.claude/projects/<cwd_escaped>/memory/` | Must become Copilot-equivalent or fall back to project-local `.copilot/memory/` |
| `transcript_path` stdin field | Copilot hooks pass different fields — port logic must read whatever Copilot supplies (TBD) |
