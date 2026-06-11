# Codex Tellonce

Codex-native port of the corrected Claude Code `tellonce`.

## User Flow

The external UX should stay simple:

```bash
git clone <repo> ~/.codex/skills/tellonce
cd /path/to/project
bash ~/.codex/skills/tellonce/codex/install.sh
bash ~/.codex/skills/tellonce/codex/doctor.sh
```

> The Codex installer lives under `codex/` — the repo-root `install.sh` is the
> Claude Code variant and would register Claude Code hooks instead.

Internally the package records a project-local audit ledger and uses wrapper-based verification where possible. Users do not need to choose internal modes during normal install.

## Modes

- `audit_only`: default. Records scans and warnings; does not claim hard enforcement.
- `wrapper`: checks output produced through `tellonce_codex exec`.
- `blocking`: opt-in hard-block layer on PostToolUse (ships with no built-in
  rules, so on its own it blocks nothing until you add rules).

(The top-level README's `observe → enforce → full` naming maps to
`audit_only → blocking` here; Codex has no shadow-judge mode yet.)

## Current V1 Capabilities

- Project registration under `.codex/tellonce/`.
- `mode.json` as mode authority.
- Append-only `events.jsonl` with centralized redaction.
- Scan events for preference/pitfall/friction/none.
- Explicit memory promotion with intent and commit events.
- Active memory index rebuilt from committed memory.
- Doctor status line and private path audit.
- Conservative install/uninstall.
- Wrapper run capture and verifier verdict.
- Migration preview that does not write active memory.
- Skill package wrapper scripts.

## Non-Guarantees

- Native interactive Codex text is advisory unless routed through the wrapper.
- Plain `codex`, IDE integrations, and web Codex can bypass wrapper checks.
- Hard blocking is not default.
- Shadow judge is not enabled by default and must not require Claude CLI.

## Claude Code Parity

See `CC_PARITY_MATRIX.md`. No Claude Code capability should be dropped; features may be staged internally but must keep a parity row and a replacement path.

## Uninstall

```bash
# Per-project disable only (keeps the global runtime + ~/.codex/hooks.json entries
# so other projects keep working):
bash ~/.codex/skills/tellonce/codex/uninstall.sh

# FULL uninstall — also removes the hook registrations from ~/.codex/hooks.json
# (so the hooks stop firing everywhere) and the global runtime:
bash ~/.codex/skills/tellonce/codex/uninstall.sh --purge-hooks --purge-skill
```

> Note: the hooks keep firing as long as they're registered in
> `~/.codex/hooks.json`. The default uninstall intentionally keeps that
> registration (the global runtime is shared across projects); pass
> `--purge-hooks` to remove it and fully stop the hooks.

