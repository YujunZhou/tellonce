# Installing the `preference-tracker` skill (Claude Code)

Tracks and blocks, in real time, preferences your Claude Code agent keeps
violating (for example a Chinese reply mixing in plain English loanwords, or
`/tmp/` in production code), together with an adaptive-threshold advisor.

Environment: Linux / macOS (POSIX), Python 3.7+, Claude Code CLI.

> This guide covers the **Claude Code** variant (repo root). For GitHub Copilot
> CLI see [`copilot/README.md`](copilot/README.md); for Codex see
> [`codex/docs/README.md`](codex/docs/README.md).

---

## Step 1: get the source (either method)

**SSH (recommended, if your GitHub key is set up):**

```bash
git clone git@github.com:YujunZhou/preference-tracker.git ~/.claude/skills/preference-tracker
```

**HTTPS (no SSH):**

```bash
git clone https://github.com/YujunZhou/preference-tracker.git ~/.claude/skills/preference-tracker
```

---

## Step 2: register hooks — pick one

### Option A: user-global (recommended — install once, applies to every project)

```bash
python3 ~/.claude/skills/preference-tracker/lib/_install_merge_settings.py --settings ~/.claude/settings.json --hooks-dir ~/.claude/skills/preference-tracker/hooks --add
```

Writes to `~/.claude/settings.json` (Claude Code user-global), so every directory
you later `cd` into and run Claude Code from is covered automatically. state /
memory / obs_log are still partitioned per project by the current cwd, so data
never crosses between projects.

**Good for:** people who want PT on for several projects at once (e.g. editing a
few papers / repos in parallel) without installing per project.

**Temporarily disable for one project** (the shell where you don't want PT):

```bash
export PT_DETERMINISTIC_DISABLED=1 PT_SHADOW_DISABLED=1 PT_INJECT_DISABLED=1
```

> Note: the legacy `B5_*` env-var names still work (backward-compat aliases); `config.json` keys are unchanged.

### Option B: single project (classic)

```bash
cd /path/to/your/working/project    # the project root you normally run Claude Code in
bash ~/.claude/skills/preference-tracker/install.sh
```

Writes to `<project>/.claude/settings.local.json` (project-local, gitignored);
applies only to that project. It also initializes the state directory and writes
`~/.preference-tracker.config.json` anchoring PROJECT_ROOT.

**Good for:** people who only want PT on 1-2 projects and the rest kept clean.

**For more projects, repeat:** run `bash install.sh` once in each project
directory. They don't interfere with each other.

---

## Codex install (parallel to Claude Code; install the runtime once)

```bash
# Shares the same code as Claude Code: ~/.claude/skills/preference-tracker/codex/install.sh
# Or run it straight from the repo
cd /path/to/your/codex/project
bash ~/.claude/skills/preference-tracker/codex/install.sh
```

`codex/install.sh` has three parts:

1. **Global runtime** → installs into `~/.codex/skills/preference-tracker/`:
   `codex_preftrack/` (wrapper-driven enforcement) + `shared_lib/` (mirror of the
   Claude Code lib) + `hooks/` (5 hook scripts) + `seed_memory/` + `SKILL.md`.
   Idempotent, safe to re-run.
2. **Hook registration** → adds `UserPromptSubmit` (3 hooks) + `PostToolUse`
   (deterministic_block) + `SessionStart` (lazy init) to `~/.codex/hooks.json`,
   leaving the user's existing hooks untouched.
3. **Per-project state** → initializes `<project>/.codex/preference-tracker/`
   (registration.json + mode.json + install_record.json), default `audit_only`.

To move to blocking mode: set the `mode` field in
`<project>/.codex/preference-tracker/mode.json` to `"blocking"`. It is monotone —
later install / wrapper commands won't silently downgrade it.

Codex doctor:

```bash
PYTHONPATH=~/.codex/skills/preference-tracker python3 -m codex_preftrack doctor
```

Expected: `state=PASS, private_paths=PASS, wrapper=NOT_USED, hooks=PASS,
install=OBSERVE_ONLY`. `wrapper=NOT_USED` is the default (it stays that way until
you run `codex_preftrack exec --`; not an error).

Codex uninstall:

```bash
bash ~/.claude/skills/preference-tracker/codex/uninstall.sh                 # keep state + hooks + skill dir
bash ~/.claude/skills/preference-tracker/codex/uninstall.sh --purge-hooks   # remove the ~/.codex/hooks.json registration
bash ~/.claude/skills/preference-tracker/codex/uninstall.sh --purge-skill   # delete ~/.codex/skills/preference-tracker
bash ~/.claude/skills/preference-tracker/codex/uninstall.sh --purge-state   # delete this project's state
```

---

## Upgrading (from an older version / older install method)

If you installed an older version (which registered project-local paths like
`<project>/.claude/hooks/...`), upgrading is strongly recommended — the old paths
carry a hostile-repo RCE risk; the new version only registers
`~/.claude/skills/preference-tracker/hooks/...`, which a project can't override.

```bash
# 1. Pull the latest code
cd ~/.claude/skills/preference-tracker && git pull

# 2a. Old per-project install → re-run install.sh; it removes the old registration and writes the new one
cd /path/to/old-pt-project && bash ~/.claude/skills/preference-tracker/install.sh

# 2b. Switch to user-global at the same time → remove the old per-project registration, then install global:
cd /path/to/old-pt-project
python3 ~/.claude/skills/preference-tracker/lib/_install_merge_settings.py --settings .claude/settings.local.json --hooks-dir .claude/hooks --remove
python3 ~/.claude/skills/preference-tracker/lib/_install_merge_settings.py --settings .claude/settings.local.json --hooks-dir ~/.claude/skills/preference-tracker/hooks --remove
python3 ~/.claude/skills/preference-tracker/lib/_install_merge_settings.py --settings ~/.claude/settings.json --hooks-dir ~/.claude/skills/preference-tracker/hooks --add
```

Upgrading preserves: registered hooks, written preference thresholds, and the
state + memory already accumulated in each project.

---

## Post-install check

```bash
bash ~/.claude/skills/preference-tracker/doctor.sh        # 12 test groups + a real-violation block smoke test
bash ~/.claude/skills/preference-tracker/dashboard.sh     # 7-day compliance summary + threshold advice
```

Expected: doctor all-pass; dashboard shows no data yet (fresh install) or recent
trigger records.

---

## Uninstall

**Installed user-global, want to remove it:**

```bash
python3 ~/.claude/skills/preference-tracker/lib/_install_merge_settings.py --settings ~/.claude/settings.json --hooks-dir ~/.claude/skills/preference-tracker/hooks --remove
```

**Installed per-project, want to remove it:**

```bash
cd /path/to/your/project
bash ~/.claude/skills/preference-tracker/uninstall.sh
```

By default this keeps your accumulated compliance log / state directory /
preference-memory files and only removes the hook registration. Add
`--purge-state` to wipe everything. To also delete the old `.sh` files an earlier
install left in `<project>/.claude/hooks/` (PT v1+ no longer manages those and
keeps them by default in case you have a same-named hook of your own):
`--purge-legacy-project-hooks`.

---

## Retrieve backend (defaults to `cli`)

The UserPromptSubmit hook defaults to **CLI small-model semantic matching**
(`PT_RETRIEVE_BACKEND=cli`):

| Runtime | CLI used | Default model | Channel |
|---|---|---|---|
| Claude Code | `claude -p` | `claude-haiku-4-5` | Pro/Max subscription quota, free |
| Codex | `codex exec --ephemeral` | `gpt-5.4-mini` | Codex subscription quota, free |

Adds 1-2s latency per prompt, but its hit rate is far higher than keyword
matching. Use this default when you want the best retrieval quality (e.g. for
paper experiments).

### Trade-off

| Mode | Hit rate | Latency | Cost | Maintenance |
|---|---|---|---|---|
| `cli` (default) | semantic; synonyms covered automatically | 1-2s/prompt | 0 (subscription quota) | no trigger keywords to write |
| `keyword` (legacy) | depends on how complete your triggers are | <10ms | 0 | write `triggers` per rule |

### Switch back to keyword (if you want a fast, LLM-free path)

```bash
echo 'export PT_RETRIEVE_BACKEND=keyword' >> ~/.bashrc
```

### Explicitly choose CLI / model (to override the per-runtime defaults)

```bash
export PT_RETRIEVE_CLI=claude       # claude (Claude Code default) or codex (Codex default)
export PT_RETRIEVE_MODEL=claude-haiku-5    # default haiku-4-5 (claude) / gpt-5.4-mini (codex)
export PT_RETRIEVE_TIMEOUT=12       # seconds, default 12
```

> Note: the legacy `B5_RETRIEVE_*` names still work (backward-compat aliases).

### Known limitations

- A nested CLI call itself fires the UserPromptSubmit hook → infinite recursion.
  `retrieve_inject` sets `B5_RETRIEVE_RECURSION_GUARD=1` in the child; the hook
  script detects it at the top and exits 0, breaking the recursion.
- On failure (CLI missing / timeout / non-JSON output) it falls back to the
  `keyword` backend automatically, so switching never costs you functionality.
- To see whether it's actually working: `export PT_RETRIEVE_DEBUG=1`; the log goes
  to `<state>/runtime/retrieve_debug.jsonl`, including latency / stdout length /
  the parsed atomic_id list.

---

## Privacy / data flow (read before installing)

**On every Stop / UserPromptSubmit, preference-tracker triggers the following
data flows:**

1. **Anthropic CLI / API** (ON by default):
   - `lib/verify_retry_shadow.py` calls a `claude -p` subprocess as a compliance
     judge on each Stop, sending `last_user[:400] + response[:4000]` to
     Anthropic's servers.
   - Defaults to the CLI subscription (free), but the prompt content still passes
     through Anthropic.
   - Disable: `export PT_SHADOW_DISABLED=1`.

2. **Local on-disk** (default `chmod 600` — readable only by you):
   - `<state>/runtime/b5_shadow_alerts/b5_shadow_log.jsonl` — evidence + feedback excerpts
   - `<state>/obs_log/compliance_log.jsonl` — `response_excerpt[:400]`
   - `<state>/runtime/b5_shadow_alerts/B5_SHADOW_ALERT.md` — full text of the latest 3 violations
   - `~/.claude/projects/<cwd_escaped>/memory/*.md` — preferences you added yourself

3. **No uploads** of any data to third parties (other than the Anthropic CLI/API
   channel above); no email / Slack / GitHub.

4. **API-key billing:**
   - `lib/detect_user_prefer.py` is OFF by default (`PT_PREFER_BACKEND=off`).
     It calls no LLM and always returns 'urgent'. To enable adaptive
     classification: `export PT_PREFER_BACKEND=cli` (subscription) or `=sdk`
     (charges `ANTHROPIC_API_KEY`).

5. **Redacting sensitive data** (advised, not automatic):
   - This version does not auto-scan prompts for secrets. You are responsible for
     not sharing secrets with Claude (a general requirement of Anthropic's terms).
   - A future version may add `PT_REDACT_BEFORE_JUDGE=1` to auto-mask patterns
     like `sk-ant-` / `password=`; it is off by default because a regex can
     wrongly strip legitimate content.

---

## Reporting issues / feedback

GitHub Issues: https://github.com/YujunZhou/preference-tracker/issues

See [`FAQ.md`](FAQ.md) (15 entries) for common questions, and
[`docs/claude-code.md`](docs/claude-code.md) for design and implementation.
