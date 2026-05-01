# Codex Preference Tracker

Codex-native port of the corrected Claude Code `preference-tracker`.

## User Flow

The external UX should stay simple:

```bash
git clone <repo> ~/.codex/skills/preference-tracker
cd /path/to/project
bash ~/.codex/skills/preference-tracker/install.sh
bash ~/.codex/skills/preference-tracker/doctor.sh
```

Internally the package records a project-local audit ledger and uses wrapper-based verification where possible. Users do not need to choose internal modes during normal install.

## Modes

- `audit_only`: default. Records scans and warnings; does not claim hard enforcement.
- `wrapper`: checks output produced through `codex_preftrack exec`.
- `hooks_experimental`: future opt-in path when Codex hook behavior is verified.

## Current V1 Capabilities

- Project registration under `.codex/preference-tracker/`.
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
