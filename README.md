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

The native way — run these two commands **inside Claude Code**:

```
/plugin marketplace add YujunZhou/tellonce
/plugin install tellonce@tellonce
```

The hooks auto-register; start a new session to activate. Tellonce begins in the
safe `observe` mode (records + reminds, never blocks); turn on hard blocking in a
shell with `export PT_ENFORCE=1`.

<details>
<summary>Or install manually (git clone + register)</summary>

```bash
git clone https://github.com/YujunZhou/tellonce.git ~/.claude/skills/tellonce
python3 ~/.claude/skills/tellonce/lib/_install_merge_settings.py --settings ~/.claude/settings.json --hooks-dir ~/.claude/skills/tellonce/hooks --add
```

That registers Tellonce user-global in `~/.claude/settings.json` (every project
covered; state/memory still per-project). Per-project instead:
`cd <project> && bash ~/.claude/skills/tellonce/install.sh`. Full guide —
enforcement, uninstall — in [`INSTALL.md`](INSTALL.md). **Pick one method:** if
you register via settings.json AND `/plugin install`, the hooks fire twice —
remove one (`...--remove`) before adding the other.
</details>

## 🚀 Quick start (Codex)

The native way — Codex CLI plugin marketplace (Codex CLI ≥ the March 2026
plugin release):

```bash
codex plugin marketplace add YujunZhou/tellonce
codex plugin add tellonce --marketplace tellonce
# verify: codex plugin list --marketplace tellonce  ->  installed, enabled
```

Tellonce begins in the safe `audit_only` mode (records, never blocks).
(The install verb is `codex plugin add`, not `install`.) The Codex marketplace
manifest is validated against the current Codex CLI (`codex plugin marketplace
add` + the plugin validator pass); if `/plugin install` doesn't load the hooks on
your Codex build, use the manual install below.

<details>
<summary>Or install manually (git clone + install script)</summary>

```bash
git clone https://github.com/YujunZhou/tellonce.git ~/.codex/skills/tellonce
cd /path/to/your/project
bash ~/.codex/skills/tellonce/codex/install.sh   # under codex/, NOT the repo-root install.sh
bash ~/.codex/skills/tellonce/codex/doctor.sh
```

See [`codex/docs/README.md`](codex/docs/README.md) for modes and the wrapper flow.
</details>

## 🚀 Quick start (GitHub Copilot CLI)

Native marketplace (matches Claude Code / Codex):

```bash
copilot plugin marketplace add YujunZhou/tellonce
copilot plugin install tellonce@tellonce
```

Restart Copilot to load the hooks. Starts in the safe `observe` mode.

<details>
<summary>Or the one-command bootstrap (a verified <code>curl | bash</code>)</summary>

The bootstrap is pinned to the immutable tag `v1.2.2` and its SHA256 is
published, so you can verify it before piping to a shell (see
[`copilot/README.md`](copilot/README.md#verify-integrity)).

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/tellonce/v1.2.2/copilot/bootstrap.ps1 | iex"
```

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/YujunZhou/tellonce/v1.2.2/copilot/bootstrap.sh | bash
```

It downloads the plugin, copies it into Copilot's plugin directory, installs the
optional dependency, registers it (so the hooks load), sets the safe `observe`
mode, and records your Python path. Then restart Copilot.
</details>

## Supported platforms

| Platform | Status | Install | Docs |
|---|---|---|---|
| **Claude Code** | ✅ Recommended (largest user base) | `/plugin install` (above) | [`docs/claude-code.md`](docs/claude-code.md) |
| **Codex** | Experimental | `/plugin install` (above) | [`codex/docs/README.md`](codex/docs/README.md) |
| **GitHub Copilot CLI** | Supported (one-command install) | one command (above) | [`copilot/README.md`](copilot/README.md) |

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
