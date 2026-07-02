# Installing the `tellonce` skill (Claude Code)

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
git clone git@github.com:YujunZhou/tellonce.git ~/.claude/skills/tellonce
```

**HTTPS (no SSH):**

```bash
git clone https://github.com/YujunZhou/tellonce.git ~/.claude/skills/tellonce
```

---

## Step 2: register hooks — pick one

### Option A: user-global (recommended — install once, applies to every project)

```bash
python3 ~/.claude/skills/tellonce/lib/_install_merge_settings.py --settings ~/.claude/settings.json --hooks-dir ~/.claude/skills/tellonce/hooks --add
```

Writes to `~/.claude/settings.json` (Claude Code user-global), so every directory
you later `cd` into and run Claude Code from is covered automatically. state /
memory / obs_log are still partitioned per project by the current cwd, so data
never crosses between projects.

**Good for:** people who want Tellonce on for several projects at once (e.g. editing a
few papers / repos in parallel) without installing per project.

**Temporarily disable for one project** (the shell where you don't want PT):

```bash
export PT_DETERMINISTIC_DISABLED=1 PT_SHADOW_DISABLED=1 PT_INJECT_DISABLED=1
```

> Note: the legacy `B5_*` env-var names still work (backward-compat aliases); `config.json` keys are unchanged.

### Option B: single project (classic)

```bash
cd /path/to/your/working/project    # the project root you normally run Claude Code in
bash ~/.claude/skills/tellonce/install.sh
```

Writes to `<project>/.claude/settings.local.json` (project-local, gitignored);
applies only to that project. It also initializes the state directory and writes
`~/.tellonce.config.json` anchoring PROJECT_ROOT.

**Good for:** people who only want Tellonce on 1-2 projects and the rest kept clean.

**For more projects, repeat:** run `bash install.sh` once in each project
directory. They don't interfere with each other.

---

## Codex install (parallel to Claude Code; install the runtime once)

```bash
# Shares the same code as Claude Code: ~/.claude/skills/tellonce/codex/install.sh
# Or run it straight from the repo
cd /path/to/your/codex/project
bash ~/.claude/skills/tellonce/codex/install.sh
```

`codex/install.sh` has three parts:

1. **Global runtime** → installs into `~/.codex/skills/tellonce/`:
   `tellonce_codex/` (wrapper-driven enforcement) + `shared_lib/` (mirror of the
   Claude Code lib) + `hooks/` (5 hook scripts) + `seed_memory/` + `SKILL.md`.
   Idempotent, safe to re-run.
2. **Hook registration** → adds `UserPromptSubmit` (3 hooks) + `PostToolUse`
   (deterministic_block) + `SessionStart` (lazy init) to `~/.codex/hooks.json`,
   leaving the user's existing hooks untouched.
3. **Per-project state** → initializes `<project>/.codex/tellonce/`
   (registration.json + mode.json + install_record.json), default `audit_only`.

To move to blocking mode: set the `mode` field in
`<project>/.codex/tellonce/mode.json` to `"blocking"`. It is monotone —
later install / wrapper commands won't silently downgrade it.

Codex doctor:

```bash
PYTHONPATH=~/.codex/skills/tellonce python3 -m tellonce_codex doctor
```

Expected: `state=PASS, private_paths=PASS, wrapper=NOT_USED, hooks=PASS,
install=OBSERVE_ONLY`. `wrapper=NOT_USED` is the default (it stays that way until
you run `tellonce_codex exec --`; not an error).

Codex uninstall:

```bash
bash ~/.claude/skills/tellonce/codex/uninstall.sh                 # keep state + hooks + skill dir
bash ~/.claude/skills/tellonce/codex/uninstall.sh --purge-hooks   # remove the ~/.codex/hooks.json registration
bash ~/.claude/skills/tellonce/codex/uninstall.sh --purge-skill   # delete ~/.codex/skills/tellonce
bash ~/.claude/skills/tellonce/codex/uninstall.sh --purge-state   # delete this project's state
```

---

## Upgrading (from an older version / older install method)

Tellonce was renamed from preference-tracker (v1.2.0), changing the
install directories, state directories, and the global config filename. There
is no in-place migration: if you installed a pre-rename version, remove it
with ITS OWN uninstaller first, then install Tellonce fresh:

```bash
# 1. Remove the old install (note the OLD directory name)
cd /path/to/your/project
bash ~/.claude/skills/preference-tracker/uninstall.sh
rm -rf ~/.claude/skills/preference-tracker ~/.preference-tracker.config.json

# 2. Install Tellonce (Step 1/2 above)
```

Your memory files under `~/.claude/projects/<cwd_escaped>/memory/` are not
touched by either step and keep working — the memory location did not change.
Project state under `.claude/preference-tracker-state/` is left behind by the
default uninstall; new state accumulates under `.claude/tellonce-state/`.
---

## Post-install check

```bash
bash ~/.claude/skills/tellonce/doctor.sh        # install-health checks (3 groups), incl. a real-violation block smoke test
bash ~/.claude/skills/tellonce/dashboard.sh     # recent shadow-judge alerts (if shadow enabled)
```

Expected: doctor all-pass; dashboard shows recent shadow alerts, or nothing if
the shadow judge is off.

---

## Uninstall

**Installed user-global, want to remove it:**

```bash
python3 ~/.claude/skills/tellonce/lib/_install_merge_settings.py --settings ~/.claude/settings.json --hooks-dir ~/.claude/skills/tellonce/hooks --remove
```

**Installed per-project, want to remove it:**

```bash
cd /path/to/your/project
bash ~/.claude/skills/tellonce/uninstall.sh
```

By default this keeps your accumulated compliance log / state directory /
preference-memory files and only removes the hook registration. Add
`--purge-state` to wipe everything. To also delete the old `.sh` files an earlier
install left in `<project>/.claude/hooks/` (PT v1+ no longer manages those and
keeps them by default in case you have a same-named hook of your own):
`--purge-legacy-project-hooks`.

---

## Retrieve backend (defaults to `progressive`)

The UserPromptSubmit / SessionStart hook defaults to **`progressive`**: it scans
your memory dir and injects a one-line index of **every** saved rule each turn
(Claude Code) or at session start (Copilot), and lets the main model judge which
apply. Zero LLM calls, zero keyword matching, zero CLI cold-start — and because
it doesn't depend on `fingerprints.yaml` priority tags, it also closes Copilot's
old SessionStart "0 rules" gap.

> Codex also defaults to `progressive`. Because Codex promotes rules to
> `<project>/.codex/tellonce/memories/active` (not the CC `~/.claude/...` path
> the shared lib resolves by default), its UserPromptSubmit hook bridges
> `B5_MEMORY_DIR` to that dir so retrieval reads where promotion writes.

Alternate backends (opt in with `PT_RETRIEVE_BACKEND=...`):

| Backend | What it does | Latency | Cost |
|---|---|---|---|
| `progressive` (default) | inject full rule index, main model self-selects | ~0, local | 0 |
| `cli` | small-model semantic match (`claude -p` / `codex exec`) | 1-2s/prompt | 0 (subscription quota) |
| `keyword` (legacy) | `fingerprints.yaml` literal/regex triggers | <10ms | 0 |
| `api` | OpenAI-compatible HTTP endpoint | network RTT | provider-billed |

`cli` gives the highest small-model retrieval precision; set
`PT_RETRIEVE_BACKEND=cli` to reproduce the pre-`progressive` behavior (e.g. for an
apples-to-apples retrieval experiment). Its per-runtime CLI/model defaults:

| Runtime | CLI used | Default model |
|---|---|---|
| Claude Code | `claude -p` | `claude-haiku-4-5` |
| Codex | `codex exec --ephemeral` | `gpt-5.4-mini` |

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

**Default posture: everything that can send data anywhere is OFF or local.**
The default `observe` mode records to local files and reminds you — the shadow
LLM judge is **OFF by default** (`path_config` public default `shadow=false`;
it only runs in `full` mode / `PT_SHADOW=1`).

1. **Memory retrieve**: the default `progressive` backend is **100% local and
   makes zero model calls** — it just reads your saved rule files and injects a
   one-line index, so nothing leaves your machine. Only the opt-in
   `PT_RETRIEVE_BACKEND=cli` flow spawns a CLI: it then sends your prompt (first
   2000 chars) + your rule descriptions to a small model via your own `claude -p`
   / `codex exec` subscription to pick relevant rules. `api` is the only backend
   that talks to a third party. A fresh install has no rules, so nothing runs
   regardless.

2. **Shadow LLM judge** (OFF by default; only in `full` mode):
   - `lib/verify_retry_shadow.py` calls a `claude -p` subprocess as a compliance
     judge on Stop, sending a **redacted** `last_user[:400] + response[:4000]`
     (see `lib/redaction.py` — API keys / tokens / password patterns are masked
     before anything leaves the machine).
   - Uses the CLI subscription (no API key billed), but the redacted prompt
     content still passes through Anthropic.
   - Keep it off: don't enable `full` mode; or `export PT_SHADOW_DISABLED=1`
     as a hard kill-switch.

3. **Local on-disk** (default `chmod 600` — readable only by you):
   - `<state>/runtime/b5_shadow_alerts/b5_shadow_log.jsonl` — evidence + feedback excerpts
   - `<state>/obs_log/compliance_log.jsonl` — `response_excerpt[:400]`
   - `<state>/runtime/b5_shadow_alerts/B5_SHADOW_ALERT.md` — full text of the latest 3 violations
   - `~/.claude/projects/<cwd_escaped>/memory/*.md` — preferences you added yourself
   - All persisted excerpts pass through `redaction.sanitize()` first.

4. **No uploads** of any data to third parties (other than the Anthropic CLI
   channels above, both on your own subscription); no email / Slack / GitHub.

5. **API-key billing:**
   - `lib/detect_user_prefer.py` is OFF by default (`PT_PREFER_BACKEND=off`).
     It calls no LLM and always returns 'urgent'. To enable adaptive
     classification: `export PT_PREFER_BACKEND=cli` (subscription) or `=sdk`
     (charges `ANTHROPIC_API_KEY`).

6. **Redaction is automatic but not perfect**: the masking patterns cover
   common token shapes (`sk-`, `ghp_`, `password=`, DB URIs, …) — don't rely
   on it as your only line of defense; avoid pasting secrets into prompts.

---

## Reporting issues / feedback

GitHub Issues: https://github.com/YujunZhou/tellonce/issues

See [`FAQ.md`](FAQ.md) for common questions, and
[`docs/claude-code.md`](docs/claude-code.md) for design and implementation.
