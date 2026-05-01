---
name: preference-tracker
description: Use when handling any user message; records and enforces user preferences with Codex-native audit/wrapper support.
---

# Preference Tracker for Codex

This skill preserves the Claude Code preference-tracker behavior using Codex-native mechanisms (Codex doesn't have Stop / UserPromptSubmit hooks, so enforcement is wrapper-driven instead of hook-driven).

## Core rules (every turn)

- **Scan** every user message for `preference`, `pitfall`, `friction`, or `none`.
- **Apply** known preferences before responding (read `<state>/index/active_memories.json`).
- **Record** durable evidence through `codex_preftrack scan` when installed.
- **Wrap** any subprocess that produces user-facing output via `codex_preftrack exec -- <cmd>` so its stdout is verified and audited.

## Mode state machine

```
audit_only  ──first wrapper run──▶  wrapper  ──(future, when blocking shipped)──▶  blocking
```

- `audit_only`: scan + record only, no enforcement intervention. This is the default after install.
- `wrapper`: at least one `codex_preftrack exec` run has completed. Future logic may use this state to require wrapper coverage.
- `blocking`: not yet implemented — reserved for future B4-style refusal gate.

The state lives in `<state_root>/mode.json`. `register_project` only writes the default mode on first install; later CLI invocations preserve any wrapper-mode upgrade.

## When to call which command

| You want to ... | Run |
|---|---|
| Bootstrap state for this project | `codex_preftrack install --project-root .` |
| Record a scan event for the latest user message | `codex_preftrack scan --project-root . --message "..."` |
| Audit a subprocess's stdout (the main wrapper path) | `codex_preftrack exec --project-root . -- <cmd...>` |
| Promote a candidate to durable memory (programmatic) | call `codex_preftrack.promote.promote_candidate(state, candidate)` directly |
| Health check + leak audit | `codex_preftrack doctor --project-root .` |
| Summary | `codex_preftrack dashboard --project-root .` |
| Uninstall integration (keep data) | `codex_preftrack uninstall --project-root .` |
| Uninstall + delete all state | `codex_preftrack uninstall --project-root . --purge-state` |

### `--` is required for `exec`

```bash
codex_preftrack exec --project-root /path -- claude -p "do thing"
#                    ^^^^^^^^^^^^^^^^^^^   ^^
#                    codex_preftrack flags  separator     wrapped command
```

Without `--`, argparse may swallow flags meant for the wrapped binary. The CLI prints an explicit error if `--` is missing.

### Timeout

`codex_preftrack exec` defaults to 600s. Override with `--timeout 1200` or `CODEX_PT_TIMEOUT=1200` env. Long LLM sessions need this — the prior 120s default cut every real session.

## Whitelist for inline-English check

Codex's `verify_output` flags inline English tokens in mostly-Chinese responses (rule `lang-pit-130`). To avoid false positives:

- A small base whitelist (programming terms like `api`, `json`, `http`, model names like `claude`, `gpt`) is built in.
- Add project-specific tokens to `<state_root>/whitelist.txt` (one per line, `#` for comments).
- Or set `CODEX_PT_WHITELIST=/path/to/file` for a global file.

## Doctor states (what's normal)

`doctor.run_doctor()` returns `wrapper={PASS, NOT_USED}`. **`NOT_USED` is normal on a fresh install** — it just means no `codex_preftrack exec` has run yet. It's not an error.

## Privacy

- Subprocess stdout/stderr go through `sanitize()` before disk (redacts API keys, DB URIs, JWT, SSH private-key blocks, etc.).
- Files under `<state_root>` are written with mode `0o600` (user-only), and `<state_root>` itself is `0o700`.
- The wrapped subprocess gets a filtered env: anything matching `*_TOKEN` / `*_SECRET` / `*PASSWORD*` / `*_API_KEY` / `*_AUTH` is dropped before the subprocess starts.
- See `codex_preftrack.ledger.SECRET_PATTERNS` for the redaction patterns; extend via PR if your stack has a key prefix not yet covered.

## Setup

```bash
bash install.sh   # from the project root
bash doctor.sh    # verify
```
