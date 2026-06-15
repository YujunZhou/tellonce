# Tellonce

**English** · [中文](README.zh.md)

> Stop re-explaining yourself to your AI coding agent. Tellonce
> remembers the corrections you make and — when you ask it to — enforces them, so
> the same mistake doesn't come back.

You told your agent to stop writing scratch files to `/tmp`. To reply in your
language. To leave unrelated code alone. Three turns later it does it again.
Tellonce watches each turn, records the preferences, pitfalls, and
workflow rules it detects, and can hard-enforce the ones you care about.

It is **safe by default**: out of the box it only records and reminds. It never
blocks you and never sends your conversation anywhere until you opt in.

## ✨ Highlights

- 🧠 **Learns from your corrections.** Every turn is scanned for preference /
  pitfall / friction signals and recorded automatically.
- 🛡️ **Opt-in enforcement.** Turn it on and replies that violate your saved
  rules are blocked, and the agent fixes them in the same turn.
- 🔒 **Private by default.** All records stay on your machine, and the optional
  LLM judge is off by default — when enabled it only ever sees a redacted
  snippet and runs through your own subscription. (Once you have saved rules,
  rule *retrieval* also runs through your own subscription's small model; set
  `PT_RETRIEVE_BACKEND=keyword` to keep even that fully local.)
- ⚡ **Runs on Claude Code, Codex, and GitHub Copilot CLI** (one-command install
  on Copilot) — one shared memory across all three.
- 🎛️ **Three modes, one switch:** `observe` → `enforce` → `full`.

## 🚀 Quick start (Claude Code)

> Prerequisites: Claude Code CLI and Python 3.7+.

```bash
# 1. Clone into your Claude Code skills directory
git clone https://github.com/YujunZhou/tellonce.git ~/.claude/skills/tellonce

# 2. Register the hooks once, for every project (recommended):
python3 ~/.claude/skills/tellonce/lib/_install_merge_settings.py --settings ~/.claude/settings.json --hooks-dir ~/.claude/skills/tellonce/hooks --add
```

That registers Tellonce user-global in `~/.claude/settings.json`, so every
project you run Claude Code in is covered; state and memory are still kept
per-project. Prefer to scope it to one project instead? Run
`bash ~/.claude/skills/tellonce/install.sh` from that project's root (it also
runs a `doctor.sh` self-check). Starts in the safe `observe` mode. Full guide —
enforcement, per-project setup, uninstall — in [`INSTALL.md`](INSTALL.md).

## 🚀 Quick start (Codex)

> Prerequisites: Codex CLI and Python 3.7+. Experimental — wrapper-driven (Codex
> has no `Stop` hook).

```bash
# 1. Clone into your Codex skills directory
git clone https://github.com/YujunZhou/tellonce.git ~/.codex/skills/tellonce

# 2. Run the Codex installer (note: under codex/, NOT the repo-root install.sh)
cd /path/to/your/project
bash ~/.codex/skills/tellonce/codex/install.sh
bash ~/.codex/skills/tellonce/codex/doctor.sh
```

Starts in the default `audit_only` mode (records, never blocks). See
[`codex/docs/README.md`](codex/docs/README.md) for modes and the wrapper flow.

## 🚀 Quick start (GitHub Copilot CLI)

> Prerequisites: GitHub Copilot CLI and Python 3.7+. Everything else is
> automatic. **Restart Copilot after install.** The command is pinned to the
> immutable release tag `v1.2.0`, so a later change to `main` can't alter what
> you run.

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/tellonce/v1.2.0/copilot/bootstrap.ps1 | iex"
```

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/YujunZhou/tellonce/v1.2.0/copilot/bootstrap.sh | bash
```

This downloads the plugin, copies it into Copilot's plugin directory, installs
the optional dependency, registers it with Copilot (so the hooks load), sets the
safe `observe` mode, and records your Python path. Then restart Copilot.

Cautious? Verify the script before piping it to a shell — see
[`copilot/README.md`](copilot/README.md#verify-integrity) for the published
SHA256 of each bootstrap script.

## Supported platforms

| Platform | Status | Install | Docs |
|---|---|---|---|
| **Claude Code** | ✅ Supported | clone + register hooks (above) | [`docs/claude-code.md`](docs/claude-code.md) |
| **Codex** | Experimental | clone + `codex/install.sh` (above) | [`codex/docs/README.md`](codex/docs/README.md) |
| **GitHub Copilot CLI** | ✅ One-command install | one command (above) | [`copilot/README.md`](copilot/README.md) |

All three share the same user-preference memory and design philosophy (Iron Law /
Gate Function / scan → record → confirm). The underlying mechanism is adapted per
runtime: Claude Code and Copilot use `Stop` hooks, while Codex has no `Stop` hook
and runs through a wrapper instead. See
[`codex/docs/CC_PARITY_MATRIX.md`](codex/docs/CC_PARITY_MATRIX.md).

## Modes

| Mode | Hard block | LLM judge | What it does |
|---|---|---|---|
| **observe** (default) | off | off | Records preferences and reminds you. Never interrupts. |
| **enforce** | on | off | Deterministic hard-block layer **plus the scan-completeness stop gate**. The deterministic layer ships with **no built-in rules** (an opt-in extension point), so it blocks no content on its own; the stop gate self-seeds on first run. |
| **full** | on | on | `enforce` plus a small-model LLM judge that checks each reply against your recorded preferences (costs time / credit). |

Switch at any time (Copilot variant):

```bash
python "<plugin>/lib/pt_mode.py" observe   # back to the safe default
python "<plugin>/lib/pt_mode.py" enforce   # turn on hard blocking
python "<plugin>/lib/pt_mode.py" full      # hard blocking + LLM judge
python "<plugin>/lib/pt_mode.py" status    # show the current mode
```

**Privacy:** all records stay local in every mode. Only `full` sends the
last message and reply (redacted) to `copilot -p` for scoring, on your own
subscription. Rule retrieval (once you have saved rules) also runs through your
own subscription's small model; `PT_RETRIEVE_BACKEND=keyword` keeps it fully
local. The `full` judge additionally needs `PT_SHADOW_RULE_IDS` set to the rule
ids you want checked — `pt_mode.py full` prints a reminder.

## How it works

1. **Session start** — your saved rules relevant to the current project are
   injected into the agent's context.
2. **Each turn ends (`Stop`)** — the turn is scanned for new preference / pitfall
   / friction signals, which are recorded to an observation log.
3. **In `full`** — a small-model LLM judge checks each reply against the rules
   you list in `PT_SHADOW_RULE_IDS` and flags violations for the agent to fix.
   (The `enforce` deterministic layer ships with **no built-in rules** — it is
   an opt-in extension point, so it blocks no content by itself.)

## Self-check & uninstall (Copilot variant)

```bash
python "<plugin>/lib/doctor.py"        # self-check: python / registration / mode / hooks
python "<plugin>/lib/dashboard.py"     # status at a glance: mode / rules / records
python "<plugin>/lib/uninstall.py"     # dry-run: show what would be removed
python "<plugin>/lib/uninstall.py --all"
copilot plugin uninstall tellonce
```

`<plugin>` is printed at the end of install; it is
`~/.copilot/installed-plugins/tellonce/tellonce`.

## Project layout

```
README.md                 # this landing page (English)
README.zh.md              # Chinese companion
copilot/                  # GitHub Copilot CLI variant — the public release
codex/                    # Codex variant (wrapper-driven)
docs/claude-code.md       # Claude Code variant, in depth
hooks/ lib/ SKILL.md ...  # Claude Code variant (lives at the repo root)
seed_memory/              # empty by default; new users start with a blank slate
LICENSE
```

## License

MIT — see [`LICENSE`](LICENSE). An open-source research artifact for studying
in-session LLM preference enforcement. Issues and PRs welcome.
