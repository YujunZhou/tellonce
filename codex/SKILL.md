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

Run `bash install.sh` from the project root, then `bash doctor.sh`.
