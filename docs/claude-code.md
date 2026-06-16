# Tellonce — Claude Code variant

This is the in-depth guide for the **Claude Code** implementation, which lives at
the repository root (`hooks/`, `lib/`, `install.sh`, `SKILL.md`, …). For the
GitHub Copilot CLI release see [`copilot/README.md`](../copilot/README.md); for
Codex see [`codex/docs/README.md`](../codex/docs/README.md).

The Claude Code variant runs a 3-hook `UserPromptSubmit` chain plus a 5-hook
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
git clone git@github.com:YujunZhou/tellonce.git ~/.claude/skills/tellonce
```

**Step 2 — register hooks (pick one):**

```bash
# Option A — user-global (recommended; applies to every project at once):
python3 ~/.claude/skills/tellonce/lib/_install_merge_settings.py \
  --settings ~/.claude/settings.json \
  --hooks-dir ~/.claude/skills/tellonce/hooks --add

# Option B — per-project (only enable for one project):
cd /path/to/your/project && bash ~/.claude/skills/tellonce/install.sh
```

See [`../INSTALL.md`](../INSTALL.md) for the full comparison plus upgrade /
uninstall steps. The per-project `install.sh` is robust across all five phases
(prepare → install → collect → execute → uninstall-ready): it auto-detects cwd /
OS user / Python / Claude CLI, versioned-backs-up `settings.json`, runs a doctor
self-check before declaring success, and rolls back via `trap ERR` on failure.

## Key paths

| Item | Default | Override |
|---|---|---|
| skill | `~/.claude/skills/tellonce/` | (fixed) |
| hooks | `<skill_dir>/hooks/` (registered into settings directly; nothing is copied into the project) | (fixed) |
| state | `<cwd>/.claude/tellonce-state/runtime/` | env `PT_STATE_DIR` or `~/.tellonce.config.json` |
| obs_log | `<cwd>/.claude/tellonce-state/obs_log/` | env `PT_OBS_LOG_DIR` or config |
| memory | `~/.claude/projects/<cwd_escaped>/memory/` | env `PT_MEMORY_DIR` or config |

> Note: the legacy `B5_*` env-var names still work (backward-compat aliases); `config.json` keys are unchanged.

## Disable / customize

```bash
# Turn off any of the three layers
export PT_DETERMINISTIC_DISABLED=1   # disable hard-blocking
export PT_SHADOW_DISABLED=1          # disable the LLM shadow judge
export PT_INJECT_DISABLED=1          # disable soft injection

# The public release ships NO built-in enforcement rules; enforcement only acts
# on the preferences you record (and is opt-in via PT_ENFORCE / PT_SHADOW).

# Run the shadow judge through the SDK instead of the CLI (uses API credit; default False)
export PT_USE_SDK=1

# Cost cap
export PT_DAILY_COST_CAP=1.00   # default 0.50 USD
export ANTHROPIC_CREDIT_OK=1    # default 1 (ignored in CLI mode); required in SDK mode

# Streak bypass (auto-pass a rule after it fires N times in a row)
export PT_STREAK_BYPASS=3       # default 3
```

## Dashboard

```bash
bash ~/.claude/skills/tellonce/dashboard.sh
# Recent shadow-judge alerts (the shadow judge is opt-in via PT_SHADOW=1).
```

## Uninstall

```bash
# Removes the hook registration (both project-local AND user-global
# ~/.claude/settings.json) so the hooks actually STOP firing, then prompts about
# the skill dir. Your memory/state is kept.
bash ~/.claude/skills/tellonce/uninstall.sh

# Full removal (also state + obs_log):
bash ~/.claude/skills/tellonce/uninstall.sh --purge-state

# Keep the skill directory (easier reinstall):
bash ~/.claude/skills/tellonce/uninstall.sh --keep-skill-dir

# Keep the user-global registration (only uninstall this project):
bash ~/.claude/skills/tellonce/uninstall.sh --keep-global
```

> Note: deleting the skill files alone does NOT stop the hooks — they keep firing
> as long as they're registered in `~/.claude/settings.json`. The uninstaller
> removes that registration first (by default, both global and project-local).

By default uninstall leaves memory + state + obs_log untouched (your data is kept
and restored on reinstall).

## Troubleshooting

```bash
# Doctor self-check
bash ~/.claude/skills/tellonce/doctor.sh

# Unit-level only (skip subprocess checks)
bash ~/.claude/skills/tellonce/doctor.sh --quick

# Roll back settings if an install broke them
bash ~/.claude/skills/tellonce/doctor.sh --rollback

# Read the install log
cat ~/.claude/skills/tellonce/install.log

# See what path_config detects
python3 ~/.claude/skills/tellonce/lib/path_config.py
```

## Architecture

```
UserPromptSubmit chain (3):
  memory-retrieve-inject.sh       [retrieve relevant saved rules, inject by atomic_id]
  memory-pending-inject.sh        [cross-session pending-memory reminder]
  memory-shadow-alert-inject.sh   [soft injection: "you violated X last turn"]

→ Claude generates a response

Stop chain (5):
  check-observation-log.sh        [Iron Law: the obs log must be appended]
  memory-deterministic-block.sh   [regex hard-blocks; ships with no built-in rules — opt-in extension point]
  memory-verify-compliance.sh     [compliance + refuse-to-stop gate]
  memory-shadow-judge.sh          [LLM judge, log-only; off by default]
  memory-pending-promote.sh       [pending obs → queue]
```

Block / pass / cost / streak data is written to
`<state>/runtime/{b5_*, b4_*}/`. Run `dashboard.sh` for a 7-day audit.
