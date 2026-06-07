# Preference-Tracker

**English** · [中文](README.zh.md)

> Stop re-explaining yourself to your AI coding agent. Preference-Tracker
> remembers the corrections you make and — when you ask it to — enforces them, so
> the same mistake doesn't come back.

You told your agent to stop writing scratch files to `/tmp`. To reply in your
language. To leave unrelated code alone. Three turns later it does it again.
Preference-Tracker watches each turn, records the preferences, pitfalls, and
workflow rules it detects, and can hard-enforce the ones you care about.

It is **safe by default**: out of the box it only records and reminds. It never
blocks you and never sends your conversation anywhere until you opt in.

## ✨ Highlights

- 🧠 **Learns from your corrections.** Every turn is scanned for preference /
  pitfall / friction signals and recorded automatically.
- 🛡️ **Opt-in enforcement.** Turn it on and replies that violate your saved
  rules are blocked, and the agent fixes them in the same turn.
- 🔒 **Private by default.** `observe` and `enforce` run 100% on your machine.
  Nothing leaves it unless you enable the optional LLM judge — which only ever
  sees a redacted snippet and runs through your own subscription.
- ⚡ **One-command install** for GitHub Copilot CLI. Also runs on Claude Code and
  Codex.
- 🎛️ **Three modes, one switch:** `observe` → `enforce` → `full`.

## 🚀 Quick start (GitHub Copilot CLI)

> Prerequisites: GitHub Copilot CLI and Python 3.7+. Everything else is
> automatic. **Restart Copilot after install.** The command is pinned to the
> immutable release tag `v1.0.0`, so a later change to `main` can't alter what
> you run.

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/preference-tracker/v1.0.0/copilot/bootstrap.ps1 | iex"
```

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/YujunZhou/preference-tracker/v1.0.0/copilot/bootstrap.sh | bash
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
| **GitHub Copilot CLI** | ✅ Recommended (public release) | one command (above) | [`copilot/README.md`](copilot/README.md) |
| **Claude Code** | Supported | clone + register hooks | [`docs/claude-code.md`](docs/claude-code.md) |
| **Codex** | Experimental | `bash codex/install.sh` | [`codex/docs/README.md`](codex/docs/README.md) |

All three share the same user-preference memory and design philosophy (Iron Law /
Gate Function / scan → record → confirm). The underlying mechanism is adapted per
runtime: Claude Code and Copilot use `Stop` hooks, while Codex has no `Stop` hook
and runs through a wrapper instead. See
[`codex/docs/CC_PARITY_MATRIX.md`](codex/docs/CC_PARITY_MATRIX.md).

## Modes

| Mode | Hard block | LLM judge | What it does |
|---|---|---|---|
| **observe** (default) | off | off | Records preferences and reminds you. Never interrupts. |
| **enforce** | on | off | Replies that violate a saved rule are blocked and rewritten. |
| **full** | on | on | `enforce` plus a small-model semantic judge (costs time / credit). |

Switch at any time (Copilot variant):

```bash
python "<plugin>/lib/pt_mode.py" observe   # back to the safe default
python "<plugin>/lib/pt_mode.py" enforce   # turn on hard blocking
python "<plugin>/lib/pt_mode.py" full      # hard blocking + LLM judge
python "<plugin>/lib/pt_mode.py" status    # show the current mode
```

**Privacy:** `observe` and `enforce` stay entirely local. Only `full` sends the
last message and reply (redacted) to `copilot -p` for scoring, on your own
subscription.

## How it works

1. **Session start** — your saved rules relevant to the current project are
   injected into the agent's context.
2. **Each turn ends (`Stop`)** — the turn is scanned for new preference / pitfall
   / friction signals, which are recorded to an observation log.
3. **In `enforce` / `full`** — deterministic checks (and optionally an LLM judge)
   block replies that violate your rules; the agent must fix the violation before
   it can stop.

## Self-check & uninstall (Copilot variant)

```bash
python "<plugin>/lib/doctor.py"        # self-check: python / registration / mode / hooks
python "<plugin>/lib/dashboard.py"     # status at a glance: mode / rules / records
python "<plugin>/lib/uninstall.py"     # dry-run: show what would be removed
python "<plugin>/lib/uninstall.py --all"
copilot plugin uninstall preference-tracker
```

`<plugin>` is printed at the end of install; it is
`~/.copilot/installed-plugins/preference-tracker/preference-tracker`.

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
