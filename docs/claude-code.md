# Preference-Tracker — Claude Code variant

This is the in-depth guide for the **Claude Code** implementation, which lives at
the repository root (`hooks/`, `lib/`, `install.sh`, `SKILL.md`, …). For the
GitHub Copilot CLI release see [`copilot/README.md`](../copilot/README.md); for
Codex see [`codex/docs/README.md`](../codex/docs/README.md).

The Claude Code variant runs a 5-hook `UserPromptSubmit` chain plus a 5-hook
`Stop` chain: deterministic hard-blocks, an LLM shadow judge, and soft context
injection.

## How it works

The public build ships with **no built-in deterministic rules** (they were the
maintainer's personal preferences and were removed). On every `Stop` (end of a
reply) the deterministic hook runs but, by default, has nothing to enforce; the
optional LLM shadow judge (`full` mode) checks the reply against the preferences
you have recorded. The sections below describe the deterministic layer as an
opt-in extension point (and how it behaved when rules were present).

## Install

**Step 1 — get the source:**

```bash
git clone git@github.com:YujunZhou/preference-tracker.git ~/.claude/skills/preference-tracker
```

**Step 2 — register hooks (pick one):**

```bash
# Option A — user-global (recommended; applies to every project at once):
python3 ~/.claude/skills/preference-tracker/lib/_install_merge_settings.py \
  --settings ~/.claude/settings.json \
  --hooks-dir ~/.claude/skills/preference-tracker/hooks --add

# Option B — per-project (only enable for one project):
cd /path/to/your/project && bash ~/.claude/skills/preference-tracker/install.sh
```

See [`../INSTALL.md`](../INSTALL.md) for the full comparison plus upgrade /
uninstall steps. The per-project `install.sh` is robust across all five phases
(prepare → install → collect → execute → uninstall-ready): it auto-detects cwd /
OS user / Python / Claude CLI, versioned-backs-up `settings.json`, runs a doctor
self-check before declaring success, and rolls back via `trap ERR` on failure.

## Key paths

| Item | Default | Override |
|---|---|---|
| skill | `~/.claude/skills/preference-tracker/` | (fixed) |
| hooks | `<cwd>/.claude/hooks/` (copied at install) | (fixed) |
| state | `<cwd>/.claude/preference-tracker-state/runtime/` | env `B5_STATE_DIR` or `~/.preference-tracker.config.json` |
| obs_log | `<cwd>/.claude/preference-tracker-state/obs_log/` | env `B5_OBS_LOG_DIR` or config |
| memory | `~/.claude/projects/<cwd_escaped>/memory/` | env `B5_MEMORY_DIR` or config |

## Disable / customize

```bash
# Turn off any of the three layers
export B5_DETERMINISTIC_DISABLED=1   # disable hard-blocking
export B5_SHADOW_DISABLED=1          # disable the LLM shadow judge
export B5_INJECT_DISABLED=1          # disable soft injection

# The public release ships NO built-in enforcement rules; enforcement only acts
# on the preferences you record (and is opt-in via PT_ENFORCE / PT_SHADOW).

# Run the shadow judge through the SDK instead of the CLI (uses API credit; default False)
export B5_USE_SDK=1

# Cost cap
export B5_DAILY_COST_CAP=1.00   # default 0.50 USD
export ANTHROPIC_CREDIT_OK=1    # default 1 (ignored in CLI mode); required in SDK mode

# Streak bypass (auto-pass a rule after it fires N times in a row)
export B5_STREAK_BYPASS=3       # default 3
```

## Dashboard

```bash
bash ~/.claude/skills/preference-tracker/dashboard.sh
# Last 7 days:
#   - deterministic block counts (bucketed by rule)
#   - shadow violations (alerted vs filtered)
#   - judge failure rate / cost / latency
```

## Uninstall

```bash
# Removes the hook registration (both project-local AND user-global
# ~/.claude/settings.json) so the hooks actually STOP firing, then prompts about
# the skill dir. Your memory/state is kept.
bash ~/.claude/skills/preference-tracker/uninstall.sh

# Full removal (also state + obs_log):
bash ~/.claude/skills/preference-tracker/uninstall.sh --purge-state

# Keep the skill directory (easier reinstall):
bash ~/.claude/skills/preference-tracker/uninstall.sh --keep-skill-dir

# Keep the user-global registration (only uninstall this project):
bash ~/.claude/skills/preference-tracker/uninstall.sh --keep-global
```

> Note: deleting the skill files alone does NOT stop the hooks — they keep firing
> as long as they're registered in `~/.claude/settings.json`. The uninstaller
> removes that registration first (by default, both global and project-local).

By default uninstall leaves memory + state + obs_log untouched (your data is kept
and restored on reinstall).

## Troubleshooting

```bash
# Doctor self-check
bash ~/.claude/skills/preference-tracker/doctor.sh

# Unit-level only (skip subprocess checks)
bash ~/.claude/skills/preference-tracker/doctor.sh --quick

# Roll back settings if an install broke them
bash ~/.claude/skills/preference-tracker/doctor.sh --rollback

# Read the install log
cat ~/.claude/skills/preference-tracker/install.log

# See what path_config detects
python3 ~/.claude/skills/preference-tracker/lib/path_config.py
```

## Architecture

```
UserPromptSubmit chain (5):
  preemptive-scan-reminder.sh
  memory-retrieve-inject.sh       [retrieve relevant saved rules, inject by atomic_id]
  memory-pending-inject.sh        [cross-session pending-memory reminder]
  memory-shadow-alert-inject.sh   [soft injection: "you violated X last turn"]
  skill-autoload-gate.sh

→ Claude generates a response

Stop chain (5):
  check-observation-log.sh        [Iron Law: the obs log must be appended]
  memory-deterministic-block.sh   [3 regex hard-blocks]
  memory-verify-compliance.sh     [compliance + refuse-to-stop gate]
  memory-shadow-judge.sh          [LLM judge, log-only]
  memory-pending-promote.sh       [pending obs → queue]
```

Block / pass / cost / streak data is written to
`<state>/runtime/{b5_*, b4_*}/`. Run `dashboard.sh` for a 7-day audit.
