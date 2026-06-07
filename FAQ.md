# FAQ — Preference-Tracker

15 common questions. For the full architecture see
[`docs/claude-code.md`](docs/claude-code.md) + `SKILL.md`.

---

## Install / uninstall

### Q1: What if the install fails partway through?

`install.sh` uses `set -euo pipefail` + `trap ERR`, so on failure it
auto-rolls-back the settings backup (but keeps the hooks `.sh` / state / memory
for debugging). Re-running `install.sh` is idempotent — it won't double-register
hooks or recreate state.

If the settings rollback didn't succeed, do it manually:

```bash
ls -t ~/your-project/.claude/settings.local.json.v3_pre_pt_*.json | head -1
# cp that latest backup back to settings.local.json
```

Or:

```bash
bash ~/.claude/skills/preference-tracker/doctor.sh --rollback
```

---

### Q2: Installed, but Claude doesn't block — how do I verify?

```bash
# Run the doctor self-check
bash ~/.claude/skills/preference-tracker/doctor.sh

# Verify the hooks are registered in settings.local.json:
python3 ~/.claude/skills/preference-tracker/lib/_install_merge_settings.py \
    --settings ~/your-project/.claude/settings.local.json \
    --hooks-dir ~/your-project/.claude/hooks \
    --verify

# Manual smoke: deliberately trigger a violation
echo '{"session_id":"test","transcript_path":"/tmp/t.jsonl"}' | \
    python3 ~/.claude/skills/preference-tracker/lib/deterministic_block.py
# (exit 2 / 0 depends on the transcript content)
```

---

### Q3: Is uninstall clean? Will I lose my memory?

`uninstall.sh` by default **does not touch** memory + state + obs_log. It only
removes the hook registration and the hooks `.sh`. Reinstalling restores all
state.

Use the `--purge-state` flag to delete everything.

---

### Q4: Does installing affect my other hooks / skills?

`install.sh` edits settings.local.json **additively** (appends, doesn't delete
existing entries), with a versioned copy beforehand. Re-running uses set
semantics to dedupe (idempotent) and won't add the same hook twice.

---

## False positives

> Note: the public build ships **no built-in rules**, so the language/`/tmp`
> false-positives below no longer occur by default. They are kept as reference
> for anyone who adds their own deterministic rules.

### Q5: A Chinese reply using `PostgreSQL`/`Redis`/`React` got blocked!

The global 219-entry whitelist already covers mainstream DBs / frameworks. If one
is missing:

```bash
echo "NewTerm" >> ~/.claude/skills/preference-tracker/lib/deterministic_block_whitelist_user.txt
```

One per line; lines starting with `#` are skipped; case-insensitive; no reload
needed.

Or temporarily disable:

```bash
export B5_DETERMINISTIC_DISABLED=1
```

---

### Q6: I paste a stack trace / log for Claude to debug and its all-English reply gets blocked

`lang-pref-001` fired (chinese_ratio < 0.1 + length > 200). State it in the prompt
to bypass:
- "help me read this log, in english is fine" → bypass (the `in english` keyword)
- "draft the abstract for the paper" → bypass (the `paper` keyword)

Or at the hook level:

```bash
export B5_DETERMINISTIC_DISABLED=1
```

---

### Q7: The same rule fires over and over and the transcript keeps growing

Streak safety valve: after a rule fires 3 times in a row it auto-bypasses for the
rest of the session. No manual action needed. The threshold `B5_STREAK_BYPASS=3`
is tunable.

---

## Configuration / thresholds

### Q8: How do I change a threshold?

**Simple version** (Phase 7, already shipped): edit the `params:` block in the
enforce rule's memory `.md` frontmatter:

```yaml
---
atomic_id: lang-pit-130
params:
  chinese_ratio_threshold: 0.55   # default 0.7
  min_length: 80                   # default 50
---
```

No reload needed. The next hook call reads the frontmatter.

**Full version, `threshold_advisor.py`:** runs on your data to suggest threshold
changes for you to approve. See the docstring at the top of
`lib/threshold_advisor.py`.

---

### Q9: How much API money does the shadow judge cost me?

It defaults to the `claude -p` CLI (subscription, no credit). Set `B5_USE_SDK=1`
to switch to the SDK (charged per token).

The cost cap defaults to `$0.50/day`; once hit it disables for the day. Change it:

```bash
export B5_DAILY_COST_CAP=1.00
```

---

### Q10: What if I don't have the Claude CLI installed?

The shadow judge can't run, but the deterministic layer still works. Set in
`.bashrc`:

```bash
export B5_SHADOW_DISABLED=1
```

Or install Claude Code: https://claude.com/code

---

### Q11: After a block, Claude's follow-up is too wordy — what can I do?

`build_block_reason` already includes prohibitions:
- no apologizing / no restating / no explaining the rule / no preamble
- forces a `[correction]` prefix
- soft-injection keeps a quiet tone (doesn't encourage a preamble this turn)

If it's still wordy, file an issue and we'll tune the prohibition text. During
testing, a streak of >= 3 bypasses that rule.

---

## Cross-platform / cross-user

### Q12: My project layout differs from yours and a path is detected wrong

`install.sh` detects by default: `<cwd>` is the project root, hooks install to
`<cwd>/.claude/hooks/`, state lives in
`<cwd>/.claude/preference-tracker-state/runtime/`, memory in
`~/.claude/projects/<cwd_escaped>/memory/`.

If that's wrong, override:

```bash
B5_STATE_DIR=/custom/state bash install.sh
B5_OBS_LOG_DIR=/custom/obs bash install.sh
B5_PROJECT_ROOT=/custom/project bash install.sh
```

Or write `~/.preference-tracker.config.json` (schema:
`{"project_root":"...","state_dir":"...","obs_log_dir":"...","memory_dir":"...","whitelist_user":"..."}`;
any field left unset falls back to auto-detect).

---

### Q13: macOS / Windows compatibility?

- macOS: the `~/.claude/` paths are the same, bash + python3 work; install.sh
  works directly.
- Windows: untested. WSL probably works (POSIX-compatible). Native PowerShell is
  not guaranteed. (For native Windows, use the Copilot variant.)
- Linux (Ubuntu / RHEL / HPC clusters): the main test target; works.

---

### Q14: How do I use the Codex / other runtime?

Claude Code is the mainline (this repo's `lib/` + `hooks/`). Codex uses a
wrapper-driven adapter — see the `codex/` subdirectory: install with
`bash codex/install.sh`, details in `codex/SKILL.md`.

---

## Troubleshooting

### Q15: Hooks aren't being called, but settings look correct

```bash
# 1. Check the hook files are executable
ls -la <project>/.claude/hooks/memory-*.sh
# should be -rwxr-xr-x; if not, chmod +x

# 2. Read install.log:
cat ~/.claude/skills/preference-tracker/install.log | tail -50

# 3. Run the full doctor check:
bash ~/.claude/skills/preference-tracker/doctor.sh

# 4. Run a hook manually and watch the output:
echo '{"session_id":"test","transcript_path":"/tmp/dummy.jsonl"}' | \
    bash <project>/.claude/hooks/memory-deterministic-block.sh
# (should exit 0, since the transcript doesn't exist)

# 5. See what path_config detects (env / config / default):
python3 ~/.claude/skills/preference-tracker/lib/path_config.py
```

---

More issues:
- GitHub Issues: https://github.com/YujunZhou/preference-tracker/issues
- For a bug / false positive / whitelist request / threshold not working — attach
  the `doctor.sh` output + 7 days of `dashboard` data to your issue for easier
  diagnosis.
