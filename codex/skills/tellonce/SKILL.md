---
name: tellonce
description: Use when handling any user message; records and enforces user preferences with Codex-native audit/wrapper support.
---

# Tellonce for Codex

## 统一记忆 Upsert

- 三个平台共用 `<project_root>/.tellonce/memory/`。SQLite 是唯一真值；旧的 `.codex/tellonce/memories/active` 只作为首次迁移来源。
- 持久偏好只能通过 `shared_lib/memory_upsert.py enqueue` 入队；agent 主动记录使用 `python <skill_dir>/shared_lib/memory_upsert.py enqueue --manual --force --source-text "<完整原始用户消息>"`，自动 hook 已启用时会跳过重复入队。复杂多行消息可改用 `--request-file <json>`。禁止直接写 active Markdown；`promote_candidate()` 也只保留为入队兼容入口。
- UserPromptSubmit 前台只写 inbox 并启动 detached worker，立即返回。LLM 判断与 SQLite 提交都在后台进行，任何失败都不能阻塞用户。
- judge 在返回 `NEEDS_USER` 前，先用当前项目根目录、最近对话和 active rules 消解指代、scope 与 activation；这些 context 只能帮助解释本轮用户原话，不能单独授权持久化。只有剩余歧义会改变未来行为时才进入轻量 clarification 队列，并在后续上下文中只问一个简短问题；下一条明确回答可关闭对应 turn。
- 关闭自动 upsert 后 clarification 不再注入；过期项可用 `python <skill_dir>/shared_lib/memory_upsert.py dismiss --turn-key <id>` 手动移除。
- 自动 hook 默认关闭。设置 `memory_upsert_enabled=true` 或 `PT_MEMORY_UPSERT_ENABLED=1` 后才启用。
- 一次修改三平台：运行 `python <skill_dir>/shared_lib/memory_upsert.py enable-hooks`；`disable-hooks` 关闭，`hook-status` 查询。

Codex actually exposes the same hook system as Claude Code (`PreToolUse /
PostToolUse / SessionStart / UserPromptSubmit / PermissionRequest`). The
codex variant of tellonce installs into `~/.codex/skills/tellonce/`
+ `~/.codex/hooks.json` and uses native hooks for retrieval + enforcement.
The wrapper path (`tellonce_codex exec --`) is still the way to enforce
on the FINAL agent text response (codex doesn't fire a Stop hook for that;
PostToolUse only sees tool inputs/outputs).

## Core rules (every turn)

- **Scan** every user message for `preference`, `pitfall`, `friction`, or `none`.
- **Apply** known preferences before responding from the shared SQLite-derived
  `.tellonce/memory/.tellonce-active.json` projection.
- **Record** durable evidence through `tellonce_codex scan` when installed.
- **Wrap** any subprocess that produces user-facing output via `tellonce_codex exec -- <cmd>` so its stdout is verified and audited.

## Rule injection (progressive full index — default)

Each time the user submits a message, `userpromptsubmit-retrieve-inject.sh` injects a **one-line index of the rules** saved under the project memory dir as `additionalContext`, and I judge which apply. If the library exceeds the per-turn cap (default 50 — `progressive_max` in `~/.tellonce.config.json` or `PT_PROGRESSIVE_MAX`, `0` = no cap), tier-1 rules are pinned when they fit under the cap (when tier-1 alone overflows it, the whole library rotates instead), the remaining rules rotate in across turns, and the block states how many of the total are shown. This is the default `progressive` backend: it just reads the saved rule files — no prompt matching, no model call, no CLI cold-start. The format looks like this — **it's not external noise, it's a rule hint from the skill infra and must be respected**:

```
### Your saved preferences — check each against this turn and apply the ones that fit:
- [fmt-pref-001] (tier1) use 4 spaces for indentation, not tabs
- [tool-pref-002] (tier2) prefer the project's own package manager / lockfile for installing dependencies | when: adding / upgrading dependencies
(These are your recorded preferences. Judge each rule against the current task; apply those that apply, skip those that do not.)
```

Each line carries the rule's `rule_text`/`description` and (when present) a `when:` applicability hint. I **judge for myself** whether it holds for the current turn, and skip rules that don't apply.

> Legacy backends (`PT_RETRIEVE_BACKEND=cli` / `keyword` / `api`) instead inject only the rules matched for the current prompt, under a `### Fingerprint retrieval — ...` header. The judgement I apply is the same.

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
| 提交候选偏好 | 调用 `tellonce_codex.promote.promote_candidate(state, candidate)`；该入口只入队，不直接写 Markdown |
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

Codex's `verify_output` flags inline English tokens in mostly-Chinese responses (rule `lang-pit-130`). **Both built-in verify rules are env-gated OFF by default** — enable with `CODEX_PT_LANG_RULE=1` (inline-English) and `CODEX_PT_TMP_RULE=1` (`/tmp` paths); without those flags the wrapper only audits/redacts. To avoid false positives once enabled:

- A small base whitelist (programming terms like `api`, `json`, `http`, model names like `claude`, `gpt`) is built in.
- Add project-specific tokens to `<state_root>/whitelist.txt` (one per line, `#` for comments).
- Or set `CODEX_PT_WHITELIST=/path/to/file` for a global file.

## Doctor states (what's normal)

`doctor.run_doctor()` returns `wrapper={PASS, NOT_USED}`. **`NOT_USED` is normal on a fresh install** — it just means no `tellonce_codex exec` has run yet. It's not an error.

## Privacy

- Subprocess stdout/stderr go through `sanitize()` before disk (redacts API keys, DB URIs, JWT, SSH private-key blocks, etc.).
- Files under `<state_root>` are written with mode `0o600` (user-only), and `<state_root>` itself is `0o700`.
- The wrapped subprocess gets a filtered env: anything matching `*TOKEN*` / `*SECRET*` / `*PASSWORD*` / `*API_KEY*` / `*AUTH*` etc. is dropped before the subprocess starts, **except** an explicit allowlist of standard LLM/dev-tool credentials (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GH_TOKEN`, ... — see `wrapper._ENV_ALLOW_NAMES`) that the wrapped CLIs need to function. `CODEX_PT_STRICT_ENV=1` disables that allowlist for a pure deny-list.
- See `tellonce_codex.ledger.SECRET_PATTERNS` for the redaction patterns; extend via PR if your stack has a key prefix not yet covered.

## Setup

```bash
# From the project root. Installs:
#   1. global runtime — tellonce_codex/ + shared_lib/ (CC lib copy)
#      + hooks/ + seed_memory/ (reference rule examples only — nothing
#      auto-loads them) + SKILL.md. Default target is
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

### One-time hook trust approval (required — hooks are silently skipped until then)

Codex trusts hooks by a hash of their exact definition. **Newly installed or
changed hooks do not run** — no error, no log, they are simply skipped —
until you review and approve them once in an interactive Codex session
(Codex prompts on the next session start; accept the tellonce entries).
This applies after first install AND after any upgrade that touches
`hooks.json` (including re-running `install.sh`, whose remove-then-add
re-orders the definitions). If hooks seem dead ("installed but nothing
happens"), a missing trust approval is the first thing to check. One-off
verification without trust: `codex exec --dangerously-bypass-hook-trust ...`.

### Hook flow

| Hook event | Script | Purpose |
|---|---|---|
| UserPromptSubmit | `userpromptsubmit-retrieve-inject.sh` | inject a one-line index of the saved rules (default `progressive` backend; legacy backends match the prompt via fingerprints/CLI/API instead) as `additionalContext` |
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
