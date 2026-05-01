---
name: preference-tracker
description: Use when handling any user message; records and enforces user preferences with Codex-native audit/wrapper support.
---

# Preference Tracker for Codex

This skill preserves the Claude Code preference-tracker behavior using Codex-native mechanisms.

Core rules:

- Scan every user message for `preference`, `pitfall`, `friction`, or `none`.
- Apply known preferences before responding.
- Record durable evidence through `codex_preftrack scan` when installed.
- Use `codex_preftrack exec` for wrapper-checked `codex exec` runs.

For `example-research-project/PROGRESS.md` maintenance:

- Treat it as current paper progress plus current operations summary, not a session transcript.
- Preserve paper-relevant sections (`Sec 3`, `Sec 4`, `Sec 5`, `Sec 6`) unless the user explicitly removes them.
- Keep current dashboard items that affect ongoing work: active results, blockers, running processes, pending memory, and active infra.
- Move history to `ARCHIVE.md`: old session logs, stale runs, deprecated benchmark routes, debug narratives, old answered questions, replaced plans, and strike-through old states.
- Keep Sec 6 aligned to the current ClawArena/all-ClawArena plan: train stream, frozen ID/OOD eval, `no_memory` / `prompt_memory` / `mem0_memory` / `compiled_enforcement`, metric directions, and Layer 2 status.
- Preserve the claim boundary: current ClawArena `compiled_enforcement` is harness-level compiled rule prompt injection, not Codex native Skill/hook enforcement.
- Remove inactive benchmark routes from the main file, especially SWE-Bench, Terminal-Bench, and PinchBench unless the user reactivates them.
- After cleanup, grep `PROGRESS.md` for stale route names and old debug terms before committing.

Run `bash install.sh` from the project root, then `bash doctor.sh`.
