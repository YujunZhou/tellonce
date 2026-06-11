# Codex Tellonce — Claude Code Parity Matrix

Date: 2026-05-01 (Round-7 update)
Status: native hook integration shipped; codex now wires PreToolUse /
PostToolUse / SessionStart / UserPromptSubmit (no Stop hook — that's the
one CC capability codex's runtime can't provide; PostToolUse +
wrapper-driven enforcement together cover the gap).

## Principle

Codex must preserve the corrected Claude Code version's user-visible behavior. It does not need to copy Claude Code mechanisms when Codex lacks the same runtime surface.

No CC capability may be removed from the Codex package without an explicit user decision. Staging is allowed; dropping is not. The install UX must still look simple from the outside: install one `tellonce` skill/package, run doctor, then use it.

## Legend

- `codex_core`: required in first stable release.
- `codex_wrapper`: enforced through `tellonce_codex exec`.
- `hooks_experimental`: only available when Codex hooks are explicitly enabled and verified.
- `deferred_with_reason`: postponed with an explicit replacement or rationale.

## Matrix

| Claude Code capability | Current CC mechanism | Codex parity target | Status |
|---|---|---|---|
| Skill entry and per-message scan | `SKILL.md` iron law + observation JSONL | Codex skill + `tellonce_codex scan` event ledger | `codex_core` |
| Install | `install.sh` with backup + merge settings | idempotent copy install (global runtime + hook registration + project state) with a versioned `hooks.json` backup (`.v3_pre_pt_*`); no manifest / smoke / commit phases | `codex_core` |
| Doctor | unit + path + hook registration + smoke | doctor checks: state, private_paths, wrapper, hooks, shadow (`doctor.run_doctor`) | `codex_core` |
| Rollback | latest settings backup + hook removal | manual: restore `~/.codex/hooks.json` from the versioned `.v3_pre_pt_*` backup written at install/uninstall; no rollback command yet | `deferred_with_reason` |
| Uninstall | keep data by default, optional purge | ownership manifest; keep data default; purge-state explicit | `codex_core` |
| Path config | `B5_*` env > config > cwd defaults | Codex project registration + explicit fallback | `codex_core` |
| Observation logging | `observations.jsonl` + compliance logs | authoritative `events.jsonl`; observations export only | `codex_core` |
| Retrieval injection | UserPromptSubmit hook | UserPromptSubmit hook (codex native) — `userpromptsubmit-retrieve-inject.sh` | `codex_core` (Round-7) |
| Pending inject | UserPromptSubmit hook | UserPromptSubmit hook (codex native) — `userpromptsubmit-pending-inject.sh` | `codex_core` (Round-7) |
| Pending promote | Stop hook | explicit two-phase `promote` command (codex has no Stop hook) | `codex_core` |
| B4 refusal gate | Stop hook blocks if pending unresolved | warning/dashboard first; hard refusal only after false-positive review | `deferred_with_reason` |
| Deterministic rules | Stop hook hard block | PostToolUse hook (`posttooluse-deterministic-block.sh`) scans agent-authored tool input; mode-aware (audit_only / wrapper / blocking). Wrapper-driven enforcement covers final stdout. | `codex_core` (Round-7) |
| Per-user whitelist | package base + user whitelist file | Codex base + user whitelist under config/state | `codex_core` |
| Streak bypass | repeated rule auto-bypass | implemented in the PostToolUse adapter (`codex_posttooluse_block.py`): per-session counter in the state dir, threshold `PT_STREAK_BYPASS`/`B5_STREAK_BYPASS` (default 3), kill-switch `PT_DETERMINISTIC_DISABLED`/`B5_DETERMINISTIC_DISABLED` honored | `codex_core` |
| Rule params | frontmatter `params` parser | preserve schema and defaults | `codex_core` |
| Threshold advisor | suggests threshold updates | dashboard advisory after telemetry exists | `deferred_with_reason` |
| Shadow judge | Claude CLI / SDK | provider-pluggable; disabled by default; no Claude dependency | `deferred_with_reason` |
| Shadow alert injection | next-turn additional context | UserPromptSubmit hook (codex native) — `userpromptsubmit-shadow-alert-inject.sh` | `codex_core` (Round-7) |
| Auto-light-entry fallback | hook fallback for missing obs | doctor-visible degraded status + wrapper audit fallback | `codex_core` |
| Dashboard | 7-day compliance summary | prints mode, hooks, blocking, scan_count, wrapped_turns (`dashboard.py`) | `codex_core` |
| Chaos tests | 12 chaos tests | current suite is unit/integration only (`tests/test_core.py`, 43 tests); fault-injection chaos suite (fake HOME, interrupt, corrupt log, multi-project) not yet ported | `deferred_with_reason` |
| Cost cap | shadow judge budget | provider module later; disabled default | `deferred_with_reason` |
| Hook short-circuit | skip expensive hooks on no signal | not needed in core; hooks experimental must include short-circuit | `hooks_experimental` |

## Codex implementation notes (Round-7, 2026-05-01)

Codex actually exposes a hook system parallel to CC's (`~/.codex/hooks.json`,
hook events `PreToolUse / PostToolUse / SessionStart / UserPromptSubmit /
PermissionRequest`, JSON stdin/stdout, exit 2 + stderr reason for blocks).
The wire schema mirrors CC's almost exactly. Same scripts can be reused
modulo two adapters:

1. PostToolUse takes the place of Stop hook. CC fires Stop after every text
   reply; codex doesn't have that. Instead PT scans the AGENT'S tool input
   (Write content / Edit new_string / Bash command) on PostToolUse — that's
   where most CC-detectable violations actually originate. Pure-text
   violations (agent reply with no tool call) are caught only by the
   wrapper path (`tellonce_codex exec --`) since codex lacks a per-text
   hook.
2. Retrieve / pending / shadow-alert UPS hooks reuse CC's `lib/*.py`
   directly via the `shared_lib/` copy that codex install bundles into
   `~/.codex/skills/tellonce/`.

## Known CC Bugs / Portability Holes (historical)

- ~~Installs into `~/.claude/skills/tellonce`, not Codex paths.~~ Codex
  install now lays out `~/.codex/skills/tellonce/` with `tellonce_codex/`
  + `shared_lib/` + `hooks/` + `seed_memory/` + `SKILL.md` (Round-7).
- ~~Registers `.claude/hooks` and `.claude/settings.local.json`, which Codex does not consume.~~ Codex install
  registers into `~/.codex/hooks.json` via `install_codex_hooks --add` (Round-7).
- Memory still defaults to `~/.claude/projects/<cwd_escaped>/memory` — the codex variant honors
  `B5_MEMORY_DIR` env override the same way CC's path_config does.
- `shared_lib/` reuses CC's `pt_platform` defaults, so the UserPromptSubmit hooks
  create `.claude/tellonce-state/` directories inside Codex projects.
  Known cosmetic wart — harmless, and overridable via the `B5_*` path env vars.
- The legacy Codex skill (pre-PR-1) had developer-machine hardcoded paths in its install scripts; the current Codex variant runs through tellonce_codex.paths so it's portable, and tellonce_codex.doctor explicitly scans state files for those leaked tokens.
- Doctor scans state for private-path leaks + reports hook registration status (`hooks=PASS / NOT_INSTALLED / PARTIAL / FAIL`) post-Round-7.
- Rollback is based on latest-looking settings backup, not an install transaction id.
- Shadow judge can depend on `claude` CLI, which a Codex-only user may not have. Codex skips shadow_judge entirely (deferred — see `B5_JUDGE_BACKEND` todo).
- Stop hook behavior has no reliable Codex equivalent — PostToolUse + wrapper enforcement cover the gap for the cases that actually matter.

## Acceptance Rule

Every implementation task must update this matrix if it implements, defers, or changes a parity row. A task cannot be marked complete if it weakens a CC parity row without adding a replacement path and test.
