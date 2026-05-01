# Codex Preference Tracker — Claude Code Parity Matrix

Date: 2026-04-29
Status: draft before implementation

## Principle

Codex must preserve the corrected Claude Code version's user-visible behavior. It does not need to copy Claude Code mechanisms when Codex lacks the same runtime surface.

No CC capability may be removed from the Codex package without an explicit user decision. Staging is allowed; dropping is not. The install UX must still look simple from the outside: install one `preference-tracker` skill/package, run doctor, then use it.

## Legend

- `codex_core`: required in first stable release.
- `codex_wrapper`: enforced through `codex_preftrack exec`.
- `hooks_experimental`: only available when Codex hooks are explicitly enabled and verified.
- `deferred_with_reason`: postponed with an explicit replacement or rationale.

## Matrix

| Claude Code capability | Current CC mechanism | Codex parity target | Status |
|---|---|---|---|
| Skill entry and per-message scan | `SKILL.md` iron law + observation JSONL | Codex skill + `codex_preftrack scan` event ledger | `codex_core` |
| Install | `install.sh` with backup + merge settings | transaction install with manifest, backup, smoke, commit | `codex_core` |
| Doctor | unit + path + hook registration + smoke | behavior doctor: state, skill, wrapper, scan, rollback, uninstall dry-run | `codex_core` |
| Rollback | latest settings backup + hook removal | transaction-scoped rollback by install id | `codex_core` |
| Uninstall | keep data by default, optional purge | ownership manifest; keep data default; purge-state explicit | `codex_core` |
| Path config | `B5_*` env > config > cwd defaults | Codex project registration + explicit fallback | `codex_core` |
| Observation logging | `observations.jsonl` + compliance logs | authoritative `events.jsonl`; observations export only | `codex_core` |
| Retrieval injection | UserPromptSubmit hook | wrapper prompt injection; native interactive is advisory | `codex_wrapper` |
| Pending inject | UserPromptSubmit hook | dashboard + wrapper context; hooks optional | `codex_wrapper` |
| Pending promote | Stop hook | explicit two-phase `promote` command | `codex_core` |
| B4 refusal gate | Stop hook blocks if pending unresolved | warning/dashboard first; hard refusal only after false-positive review | `deferred_with_reason` |
| Deterministic rules | Stop hook hard block | same detectors as verifier rules, default warning; hard block opt-in | `codex_core` then opt-in |
| Per-user whitelist | package base + user whitelist file | Codex base + user whitelist under config/state | `codex_core` |
| Streak bypass | repeated rule auto-bypass | required before enabling any hard block | `codex_core` for blocking readiness |
| Rule params | frontmatter `params` parser | preserve schema and defaults | `codex_core` |
| Threshold advisor | suggests threshold updates | dashboard advisory after telemetry exists | `deferred_with_reason` |
| Shadow judge | Claude CLI / SDK | provider-pluggable; disabled by default; no Claude dependency | `deferred_with_reason` |
| Shadow alert injection | next-turn additional context | wrapper context injection; hooks optional | `codex_wrapper` |
| Auto-light-entry fallback | hook fallback for missing obs | doctor-visible degraded status + wrapper audit fallback | `codex_core` |
| Dashboard | 7-day compliance summary | mode, scans, warnings, wrapper coverage, false-positive counters | `codex_core` |
| Chaos tests | 12 chaos tests | fake HOME, interrupt, corrupt log, multi-project, uninstall, migration | `codex_core` |
| Cost cap | shadow judge budget | provider module later; disabled default | `deferred_with_reason` |
| Hook short-circuit | skip expensive hooks on no signal | not needed in core; hooks experimental must include short-circuit | `hooks_experimental` |

## Known CC Bugs / Portability Holes

- Installs into `~/.claude/skills/preference-tracker`, not Codex paths.
- Registers `.claude/hooks` and `.claude/settings.local.json`, which Codex does not consume.
- Defaults memory to `~/.claude/projects/<cwd_escaped>/memory`.
- Existing Codex skill hardcodes yzhou25-local paths.
- Doctor uses `/tmp/doctor_test_$$` scratch and proves Claude hook registration, not Codex behavior.
- Rollback is based on latest-looking settings backup, not an install transaction id.
- Hook copy/update behavior can leave stale or partial hook files.
- Shadow judge can depend on `claude` CLI, which a Codex-only colleague may not have.
- Stop hook behavior has no reliable Codex equivalent, so final-output checks need wrapper ownership.

## Acceptance Rule

Every implementation task must update this matrix if it implements, defers, or changes a parity row. A task cannot be marked complete if it weakens a CC parity row without adding a replacement path and test.
