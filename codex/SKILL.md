---
name: tellonce
description: Use when handling any user message; records and enforces user preferences with Codex-native audit/wrapper support.
---

# Tellonce for Codex

Codex actually exposes the same hook system as Claude Code (`PreToolUse /
PostToolUse / SessionStart / UserPromptSubmit / PermissionRequest`). The
codex variant of tellonce installs into `~/.codex/skills/tellonce/`
+ `~/.codex/hooks.json` and uses native hooks for retrieval + enforcement.
The wrapper path (`tellonce_codex exec --`) is still the way to enforce
on the FINAL agent text response (codex doesn't fire a Stop hook for that;
PostToolUse only sees tool inputs/outputs).

## Core rules (every turn)

- **Scan** every user message for `preference`, `pitfall`, `friction`, or `none`.
- **Apply** known preferences before responding (read `<state>/index/active_memories.json`).
- **Record** durable evidence through `tellonce_codex scan` when installed.
- **Wrap** any subprocess that produces user-facing output via `tellonce_codex exec -- <cmd>` so its stdout is verified and audited.

```
audit_only  ──first wrapper run──▶  wrapper  ──opt-in──▶  blocking
```

- `audit_only`: scan + record + advisory stderr; PostToolUse hook never blocks. Default after install.
- `wrapper`: at least one `tellonce_codex exec` run has completed; same advisory behavior as audit_only.
- `blocking`: PostToolUse hook returns exit 2 + `decision:block` JSON when violations detected. **Opt-in only** — set by editing `<state_root>/mode.json` (write_mode enforces monotonicity: never downgrade).

The state lives in `<state_root>/mode.json`. `register_project` only writes the default mode on first install; later CLI invocations preserve any wrapper-mode upgrade.

## When to call which command

| You want to ... | Run |
|---|---|
| Bootstrap state for this project | `tellonce_codex install --project-root .` |
| Record a scan event for the latest user message | `tellonce_codex scan --project-root . --message "..."` |
| Audit a subprocess's stdout (the main wrapper path) | `tellonce_codex exec --project-root . -- <cmd...>` |
| Promote a candidate to durable memory (programmatic) | call `tellonce_codex.promote.promote_candidate(state, candidate)` directly |
| Health check + leak audit | `tellonce_codex doctor --project-root .` |
| Summary | `tellonce_codex dashboard --project-root .` |
| Uninstall integration (keep data) | `tellonce_codex uninstall --project-root .` |
| Uninstall + delete all state | `tellonce_codex uninstall --project-root . --purge-state` |

### `--` is required for `exec`

```bash
tellonce_codex exec --project-root /path -- claude -p "do thing"
#                    ^^^^^^^^^^^^^^^^^^^   ^^
#                    tellonce_codex flags  separator     wrapped command
```

Without `--`, argparse may swallow flags meant for the wrapped binary. The CLI prints an explicit error if `--` is missing.

### Timeout

`tellonce_codex exec` defaults to 600s. Override with `--timeout 1200` or `CODEX_PT_TIMEOUT=1200` env. Long LLM sessions need this — the prior 120s default cut every real session.

## Whitelist for inline-English check

Codex's `verify_output` flags inline English tokens in mostly-Chinese responses (rule `lang-pit-130`). To avoid false positives:

- A small base whitelist (programming terms like `api`, `json`, `http`, model names like `claude`, `gpt`) is built in.
- Add project-specific tokens to `<state_root>/whitelist.txt` (one per line, `#` for comments).
- Or set `CODEX_PT_WHITELIST=/path/to/file` for a global file.

## Doctor states (what's normal)

`doctor.run_doctor()` returns `wrapper={PASS, NOT_USED}`. **`NOT_USED` is normal on a fresh install** — it just means no `tellonce_codex exec` has run yet. It's not an error.

## Privacy

- Subprocess stdout/stderr go through `sanitize()` before disk (redacts API keys, DB URIs, JWT, SSH private-key blocks, etc.).
- Files under `<state_root>` are written with mode `0o600` (user-only), and `<state_root>` itself is `0o700`.
- The wrapped subprocess gets a filtered env: anything matching `*_TOKEN` / `*_SECRET` / `*PASSWORD*` / `*_API_KEY` / `*_AUTH` is dropped before the subprocess starts.
- See `tellonce_codex.ledger.SECRET_PATTERNS` for the redaction patterns; extend via PR if your stack has a key prefix not yet covered.

## Setup

```bash
# From the project root. Installs:
#   1. global runtime — tellonce_codex/ + shared_lib/ (CC lib copy)
#      + hooks/ + seed_memory/ + SKILL.md. Default target is
#      ~/.codex/skills/tellonce/; if your git clone occupies that
#      path, the runtime goes to ~/.codex/skills/tellonce-runtime/
#      (keeps the clone clean).
#   2. ~/.codex/hooks.json — registers UserPromptSubmit (3) + PostToolUse + SessionStart
#   3. <project>/.codex/tellonce/ — per-project state (audit_only mode by default)
bash <repo>/codex/install.sh    # the repo-root install.sh is the Claude Code variant

# Verify (state + hooks status + private-path leak scan) — easiest:
bash <repo>/codex/doctor.sh
# or module form (point PYTHONPATH at wherever the runtime landed):
PYTHONPATH=~/.codex/skills/tellonce-runtime python3 -m tellonce_codex doctor  # clone layout
PYTHONPATH=~/.codex/skills/tellonce python3 -m tellonce_codex doctor          # plain layout
```

### Hook flow

| Hook event | Script | Purpose |
|---|---|---|
| UserPromptSubmit | `userpromptsubmit-retrieve-inject.sh` | match user prompt against fingerprints + memory rules, inject `additionalContext` |
| UserPromptSubmit | `userpromptsubmit-pending-inject.sh` | warn about pending memory entries from prior session crashes |
| UserPromptSubmit | `userpromptsubmit-shadow-alert-inject.sh` | inject "last turn violated rule X" reminder so this turn fixes it |
| PostToolUse | `posttooluse-deterministic-block.sh` | regex/fingerprint scan agent's tool input (Write content / Edit / Bash); audit_only logs, blocking mode exits 2 + decision:block |
| SessionStart | `sessionstart-init.sh` | lazy-init project state on first codex SessionStart in a fresh project |

### Mode state machine

The three modes are rank-ordered (`_MODE_RANK` in `tellonce_codex/mode.py`):
`audit_only` (0) → `wrapper` (1) → `blocking` (2). Per project, the persisted
mode only latches upward — `write_mode` raises `ModeDowngradeError` on any
silent downgrade (`allow_downgrade=True` exists for test fixtures only).
`blocking` is opt-in only; nothing auto-promotes into it. `wrapper_seen`
latches `True` the first time `tellonce_codex exec` is used and never resets,
so doctor/dashboard can tell whether wrapper enforcement has ever run here.

