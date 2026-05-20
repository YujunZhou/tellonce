# PORT_DESIGN.md — Preference-Tracker → Copilot CLI Plugin

> Design document for porting Claude Code hooks to Copilot CLI plugin format.
> Created 2026-05-20.

---

## 1. Critical Finding: Shared Format, Different Semantics

Copilot CLI and Claude Code share **the same `hooks.json` wire format** (field names,
structure, version field). Both PascalCase (Claude Code) and camelCase (Copilot CLI)
event names are accepted.

**Two semantics diverge:**

| Concern | Claude Code | Copilot CLI |
|---------|-------------|-------------|
| `UserPromptSubmit` output | stdout `additionalContext` injected into model | **Fire-and-forget** — stdout ignored |
| `Stop` blocking | `exit 2` blocks | `exit 2` = warning only; must emit `{"decision":"block","reason":"..."}` on stdout + `exit 0` |

---

## 2. Event Mapping

| Claude hook script | Claude event | Copilot event name | Behaviour in port |
|---|---|---|---|
| `check-observation-log.sh` | `Stop` | `Stop` (PascalCase) | Block via stdout JSON (not exit 2) |
| `memory-deterministic-block.sh` | `Stop` | `Stop` | Block via stdout JSON |
| `memory-verify-compliance.sh` | `Stop` | `Stop` | Log-only (exit 0) |
| `memory-shadow-judge.sh` | `Stop` | `Stop` | Log-only (exit 0) |
| `memory-pending-promote.sh` | `Stop` | `Stop` | Side-effect only (exit 0) |
| `memory-retrieve-inject.sh` | `UserPromptSubmit` | `SessionStart` | **Moved** — inject via `sessionStart` `additionalContext` |
| `memory-pending-inject.sh` | `UserPromptSubmit` | `SessionStart` | **Moved** — inject via `sessionStart` |
| `memory-shadow-alert-inject.sh` | `UserPromptSubmit` | `SessionStart` | **Moved** — inject via `sessionStart` |

### 2.1 UserPromptSubmit → SessionStart Strategy

Since Copilot CLI's `userPromptSubmitted` ignores stdout, we **cannot** inject context
on every prompt. The best-available alternative:

- **`SessionStart`** fires once per session and supports `additionalContext` injection.
- We combine the 3 inject hooks into a single `SessionStart` hook that:
  1. Retrieves relevant memory rules (was `retrieve_inject.py`)
  2. Surfaces any pending queue items (was `pending_queue_manager.py`)
  3. Surfaces any recent shadow alerts (was `shadow_alert_inject.py`)
  4. Emits one unified `additionalContext` blob

**Degradation**: Claude version injects on *every* prompt; Copilot version injects once
at session start. This means mid-session new observations won't be surfaced until next
session. Acceptable for v1 — document as known limitation.

**Future**: If Copilot CLI adds per-prompt injection (e.g. `notification` hook gains
stability), upgrade to per-prompt.

### 2.2 Stop Hooks — Exit Code Migration

All Stop hooks that block must change from:
```bash
echo "$JSON_OUTPUT"
exit 2
```
to:
```bash
echo '{"decision":"block","reason":"..."}'
exit 0
```

Non-blocking Stop hooks (compliance/shadow/promote) stay `exit 0` with no stdout.

---

## 3. Path Migration

| Claude path | Copilot equivalent | Strategy |
|---|---|---|
| `~/.claude/skills/preference-tracker/` | Plugin install dir (`${PLUGIN_ROOT}`) | Use `${CLAUDE_PLUGIN_ROOT}` token in hooks.json commands |
| `<cwd>/.claude/preference-tracker-state/` | `<cwd>/.copilot/preference-tracker-state/` | Change in `path_config.py` default |
| `~/.claude/projects/<escaped>/memory/` | `<cwd>/.copilot/preference-tracker/memory/` | Simplify — project-local |
| `~/.preference-tracker.config.json` | Same (unchanged) | Config file is tool-agnostic |
| `${HOME}/.claude/skills/preference-tracker/lib` | `${CLAUDE_PLUGIN_ROOT}/lib` | Via env var set in hooks.json |

### 3.1 `path_config.py` Changes

```python
# Old defaults
STATE_DIR = "<cwd>/.claude/preference-tracker-state/runtime"
OBS_LOG_DIR = "<cwd>/.claude/preference-tracker-state/obs_log"
MEMORY_DIR = "~/.claude/projects/<escaped>/memory"

# New defaults (Copilot)
STATE_DIR = "<cwd>/.copilot/preference-tracker-state/runtime"
OBS_LOG_DIR = "<cwd>/.copilot/preference-tracker-state/obs_log"
MEMORY_DIR = "<cwd>/.copilot/preference-tracker/memory"
```

The 3-layer priority (env > config > default) stays identical.

---

## 4. CLI Command Migration

| Claude | Copilot | Used in |
|--------|---------|---------|
| `claude -p "..."` | `copilot -p "..."` | `retrieve_inject.py`, `verify_retry_shadow.py`, `detect_user_prefer.py` |

The `copilot -p` command accepts a prompt and returns the model response on stdout,
same as `claude -p`. The recursion guard env var `B5_RETRIEVE_RECURSION_GUARD` stays.

---

## 5. Plugin Layout

```
copilot/
  plugin.json              # Copilot-format manifest
  hooks.json               # Hook registrations
  lib/
    path_config.py         # Ported path resolver
    deterministic_block.py # Ported (unchanged logic)
    verify_compliance.py   # Ported
    verify_retry_shadow.py # Ported (claude -p → copilot -p)
    retrieve_inject.py     # Ported (claude -p → copilot -p)
    shadow_alert_inject.py # Ported
    pending_queue_manager.py # Ported
    detect_user_prefer.py  # Ported (claude -p → copilot -p)
    rule_params.py         # Unchanged
    redaction.py           # Unchanged
    analyze_b5_compliance.py # Ported
    threshold_advisor.py   # Ported
    apply_threshold.py     # Unchanged
    auto_retire_superseded.py # Unchanged
    fingerprints.yaml      # Data file (copy)
    deterministic_block_whitelist.txt      # Data (copy)
    deterministic_block_whitelist_user.txt # Data (copy)
  hooks/
    check-observation-log.sh      # Stop — gate function
    memory-deterministic-block.sh # Stop — deterministic blocker
    memory-verify-compliance.sh   # Stop — compliance logger
    memory-shadow-judge.sh        # Stop — shadow LLM judge
    memory-pending-promote.sh     # Stop — promote pending obs
    session-start-inject.sh       # SessionStart — unified injection (NEW)
  seed_memory/             # Seed rules (copy from root)
  install.sh               # Unix post-install setup
  install.ps1              # Windows post-install setup
  PORT_NOTES/              # Discovery docs (not shipped to users)
```

### 5.1 `plugin.json`

```json
{
  "name": "preference-tracker",
  "description": "Automatic preference/pitfall/friction detection and memory enforcement for Copilot CLI",
  "version": "1.0.0",
  "author": { "name": "Yujun Zhou", "email": "yzhou25@nd.edu" }
}
```

### 5.2 `hooks.json`

```json
{
  "version": 1,
  "hooks": {
    "Stop": [
      {
        "type": "command",
        "bash": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/check-observation-log.sh",
        "powershell": "python ${CLAUDE_PLUGIN_ROOT}\\hooks\\check-observation-log.ps1",
        "timeoutSec": 30
      },
      {
        "type": "command",
        "bash": "${CLAUDE_PLUGIN_ROOT}/hooks/memory-deterministic-block.sh",
        "powershell": "& '${CLAUDE_PLUGIN_ROOT}\\hooks\\memory-deterministic-block.ps1'",
        "timeoutSec": 15
      },
      {
        "type": "command",
        "bash": "${CLAUDE_PLUGIN_ROOT}/hooks/memory-verify-compliance.sh",
        "powershell": "python ${CLAUDE_PLUGIN_ROOT}\\hooks\\memory-verify-compliance.ps1",
        "timeoutSec": 15
      },
      {
        "type": "command",
        "bash": "${CLAUDE_PLUGIN_ROOT}/hooks/memory-shadow-judge.sh",
        "powershell": "python ${CLAUDE_PLUGIN_ROOT}\\hooks\\memory-shadow-judge.ps1",
        "timeoutSec": 30
      },
      {
        "type": "command",
        "bash": "${CLAUDE_PLUGIN_ROOT}/hooks/memory-pending-promote.sh",
        "powershell": "python ${CLAUDE_PLUGIN_ROOT}\\hooks\\memory-pending-promote.ps1",
        "timeoutSec": 10
      }
    ],
    "SessionStart": [
      {
        "type": "command",
        "bash": "${CLAUDE_PLUGIN_ROOT}/hooks/session-start-inject.sh",
        "powershell": "python ${CLAUDE_PLUGIN_ROOT}\\hooks\\session-start-inject.ps1",
        "timeoutSec": 15
      }
    ]
  }
}
```

> **Note**: Using PascalCase event names for Claude-format compatibility.
> The `${CLAUDE_PLUGIN_ROOT}` token is resolved at runtime to the plugin's install path.

---

## 6. Windows Strategy

All bash hook scripts will have equivalent **Python** entry points (not .ps1 scripts):
- `powershell` field calls `python <path>` directly
- Python scripts read stdin JSON, write stdout JSON — same as bash wrappers
- This avoids maintaining bash + PowerShell + Python triplication

Revised hooks.json pattern:
```json
{
  "type": "command",
  "bash": "${CLAUDE_PLUGIN_ROOT}/hooks/memory-deterministic-block.sh",
  "powershell": "python \"${CLAUDE_PLUGIN_ROOT}\\lib\\deterministic_block.py\"",
  "timeoutSec": 15
}
```

The bash scripts remain thin wrappers that `exec python3 ... lib/<module>.py`.
On Windows, we skip the bash wrapper and call Python directly.

---

## 7. Known Degradations (v1)

| Feature | Claude Code | Copilot CLI v1 | Mitigation |
|---------|-------------|----------------|------------|
| Per-prompt context injection | Every prompt gets retrieved memory | Only at session start | Document; upgrade when API available |
| Recursion guard scope | Guards nested `claude -p` from re-firing hooks | `copilot -p` may or may not re-fire | Keep `B5_RETRIEVE_RECURSION_GUARD` env; verify |
| `last_assistant_message` in stdin | Available in Stop event stdin | **Verify** — may need transcript tail | Fall back to transcript_path reading |
| Transcript path | Always passed in Stop stdin | Passed as `transcript_path` (PascalCase) | Confirmed compatible |

---

## 8. Implementation Order

1. **`copilot/lib/path_config.py`** — `.claude/` → `.copilot/` defaults
2. **`copilot/hooks.json`** + **`copilot/plugin.json`** — manifest + hook config
3. **Stop hooks** — port all 5, change exit 2 → stdout JSON
4. **SessionStart hook** — new unified inject combining 3 Claude inject hooks
5. **`copilot/lib/` modules** — `claude -p` → `copilot -p` in 3 files
6. **`copilot/install.sh`** + **`copilot/install.ps1`** — post-install setup
7. **Smoke test** — install plugin, trigger hooks, verify ledger writes
8. **Code review** — parity comparison with Claude version
