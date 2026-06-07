# Codex variant — verification guide

These are the checks that could not be run from the authoring environment (no
Codex CLI there). Run them on a machine that has Codex, then report anything that
deviates from the **Expected** line. Everything here is read-only or scoped to a
throwaway project directory.

## 0. Get the code & set up

```bash
git clone git@github.com:YujunZhou/preference-tracker.git
cd preference-tracker/codex
python3 --version          # 3.8+ expected
```

The package is `codex_preftrack`; run the commands below from `preference-tracker/codex/`
so it is importable, or after `install.sh` from the installed skill dir.

## 1. Unit suite

```bash
cd preference-tracker/codex
python -m unittest tests.test_core -v      # or: python -m pytest tests/test_core.py -q
```

**Expected:** all tests pass. (`tests/test_core.py` had a private path scrubbed
this session — A3-LEAK — so confirm nothing references real machine paths.)

## 2. Install + doctor on a fresh project

```bash
mkdir -p /tmp/pt-codex-smoke && cd /tmp/pt-codex-smoke && git init -q
bash ~/.codex/skills/preference-tracker/install.sh    # after cloning to that path, per codex/docs/README.md
bash ~/.codex/skills/preference-tracker/doctor.sh
```

**Expected:** install is idempotent (safe to re-run), doctor prints a status line
with mode = `audit_only` and no FAIL. The project gets `.codex/preference-tracker/`
with `mode.json` + `events.jsonl`.

## 3. Scan classifies signals

```bash
python -m codex_preftrack scan --project-root . --message "from now on always answer me in Chinese"
```

**Expected:** a `scan_recorded` event is appended to `events.jsonl` classifying
the message as a preference (not `none`).

## 4. Wrapper exec records a ledger event + verdict

```bash
python -m codex_preftrack exec --project-root . -- definitely-missing-codex-binary
```

**Expected:** a `wrapper_run_completed` event is recorded (even though the binary
is missing) and a verifier verdict is produced. No crash; non-zero child exit is
handled gracefully.

## 5. The three modes behave as documented

```bash
python -m codex_preftrack dashboard --project-root .   # shows mode + counts
# inspect/flip mode via mode.json (the mode authority)
```

**Expected:**
- `audit_only` (default): records scans/warnings, never claims hard enforcement.
- `wrapper`: only output produced through `codex_preftrack exec` is checked.
- `hooks_experimental`: opt-in only; not the default.

## 6. M2 regression — PostToolUse must not false-positive on ordinary tool calls

This is the bug fixed this session: `codex_posttooluse_block.py` scans
`tool_input` (agent-submitted text) for violations, which used to wrongly fire
the language rules on normal tool input.

```bash
# Feed the hook a normal tool call (e.g. a Chinese commit message or a plain bash command)
echo '{"tool_name":"Bash","tool_input":{"command":"git commit -m \"修复登录 bug\""},"tool_response":{"output":"ok"},"cwd":"."}' \
  | python -m codex_preftrack.codex_posttooluse_block
```

**Expected:** **no block** for an ordinary tool call. The language rules must not
fire on tool input; only a genuine active-`/tmp/`-in-code style violation should
block. Confirm a real violation (e.g. `echo x >> /tmp/a` inside code) still does.

## 7. Empty-seed default

```bash
ls ~/.codex/skills/preference-tracker/seed_memory/    # shipped seed
```

**Expected:** a new user starts blank (no personal rules pre-loaded), matching
the Copilot variant. If you keep personal rules, confirm they live in a gitignored
user overlay, not the shipped seed.

## 8. SessionStart + UserPromptSubmit injection actually fires in real Codex

Start a real Codex session in the smoke project and send one prompt.

**Expected:** the `sessionstart-init.sh` and the three `userpromptsubmit-*.sh`
hooks run; relevant saved rules (if any) are injected into context. This is the
one surface that genuinely needs live Codex — please confirm the hooks are wired
in `~/.codex/hooks.json` and fire.

## 9. Private-path audit

```bash
bash ~/.codex/skills/preference-tracker/doctor.sh     # includes a private-path audit
```

**Expected:** no real machine paths / personal identifiers reported.

---

## What changed this session (sanity-check these)

- **M2** — `codex_preftrack/codex_posttooluse_block.py`: language rules no longer
  false-positive on tool input (check §6).
- **A3-LEAK** — `codex/tests/test_core.py`: a private path/paper name was scrubbed
  (check §1 still green).

## What to report back

For each section: ✅ / ❌ + the actual output line if it deviates. Section 8 (live
hook firing) and section 6 (M2 regression) are the highest-priority confirmations.
