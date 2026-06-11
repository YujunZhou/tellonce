# FAQ — Tellonce

12 common questions. For the full architecture see
[`docs/claude-code.md`](docs/claude-code.md) + `SKILL.md`.

---

## Install / uninstall

### Q1: What if the install fails partway through?

`install.sh` uses `set -euo pipefail` + `trap ERR`, so on failure it
auto-rolls-back the settings backup (but keeps state / memory for debugging; the
hook `.sh` files live in the skill directory and are never copied anywhere).
Re-running `install.sh` is idempotent — it won't double-register hooks or
recreate state.

If the settings rollback didn't succeed, do it manually:

```bash
ls -t ~/your-project/.claude/settings.local.json.v3_pre_pt_*.json | head -1
# cp that latest backup back to settings.local.json
```

Or:

```bash
bash ~/.claude/skills/tellonce/doctor.sh --rollback
```

---

### Q2: Installed, but Claude doesn't block — how do I verify?

```bash
# Run the doctor self-check
bash ~/.claude/skills/tellonce/doctor.sh

# Verify the hooks are registered in settings.local.json:
python3 ~/.claude/skills/tellonce/lib/_install_merge_settings.py \
    --settings ~/your-project/.claude/settings.local.json \
    --hooks-dir ~/.claude/skills/tellonce/hooks \
    --verify

# Manual smoke: deliberately trigger a violation
echo '{"session_id":"test","transcript_path":"/tmp/t.jsonl"}' | \
    python3 ~/.claude/skills/tellonce/lib/deterministic_block.py
# (exit 2 / 0 depends on the transcript content)
```

---

### Q3: Is uninstall clean? Will I lose my memory?

`uninstall.sh` by default **does not touch** memory + state + obs_log. It only
removes the hook registration; the hook `.sh` files stay where they are (they
live in the skill directory, not in your project). Reinstalling restores all
state.

Use the `--purge-state` flag to delete everything (it asks for confirmation
when run interactively).

---

### Q4: Does installing affect my other hooks / skills?

`install.sh` edits settings.local.json **additively** (appends, doesn't delete
existing entries), with a versioned copy beforehand. Re-running uses set
semantics to dedupe (idempotent) and won't add the same hook twice.

---

## Configuration / thresholds

### Q8: How do I change a threshold?

**Simple version** (already shipped): for an enforce rule you recorded, edit the
`params:` block in its memory `.md` frontmatter:

```yaml
---
atomic_id: <your-rule-id>
params:
  some_threshold: 0.55
---
```

No reload needed. The next hook call reads the frontmatter.

**Full version, `threshold_advisor.py`:** runs on your data to suggest threshold
changes for you to approve. See the docstring at the top of
`lib/threshold_advisor.py`.

---

### Q9: How much API money does the shadow judge cost me?

Nothing unless you turn it on: the shadow judge is **OFF by default** and only
runs in `full` mode (`PT_SHADOW=1`). When enabled, it defaults to the
`claude -p` CLI (subscription, no credit). Set `PT_USE_SDK=1` to switch to the
SDK (charged per token).

The cost cap defaults to `$0.50/day`; once hit it disables for the day. Change it:

```bash
export PT_DAILY_COST_CAP=1.00
```

---

### Q10: What if I don't have the Claude CLI installed?

The shadow judge can't run, but it's off by default anyway (it only runs in
`full` mode / `PT_SHADOW=1`), and the deterministic layer still works. If you
enabled `full` mode and want a hard kill-switch, set in `.bashrc`:

```bash
export PT_SHADOW_DISABLED=1
```

Or install Claude Code: https://claude.com/code

---

### Q11: After a block, the follow-up is too wordy — what can I do?

The block reason is intentionally minimal: it lists the flagged rule plus the
fix direction, and nothing else. The public release ships **no built-in
writing-style rules** — if you want a terse-reply preference, record one and it
becomes part of the enforced set. During testing, a streak of >= 3 consecutive
hits on the same rule auto-bypasses it to avoid livelock.

---

## Cross-platform / cross-user

### Q12: My project layout differs from yours and a path is detected wrong

`install.sh` detects by default: `<cwd>` is the project root, hooks stay in the
skill directory and are registered into `<cwd>/.claude/settings.local.json`,
state lives in `<cwd>/.claude/tellonce-state/runtime/`, memory in
`~/.claude/projects/<cwd_escaped>/memory/`.

If that's wrong, override:

```bash
# install.sh: override state / obs_log locations
PT_STATE_DIR=/custom/state bash install.sh
PT_OBS_LOG_DIR=/custom/obs bash install.sh
# install.sh always takes the project root from cwd — cd there first.

# uninstall.sh: additionally accepts the project root as an env var
PT_PROJECT_ROOT=/custom/project bash uninstall.sh
```

> Note: the legacy `B5_*` env-var names still work (backward-compat aliases); `config.json` keys are unchanged.

Or write `~/.tellonce.config.json` (schema:
`{"project_root":"...","state_dir":"...","obs_log_dir":"...","memory_dir":"..."}`;
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
# 1. Check the hook files are executable (they live in the skill dir, not the project)
ls -la ~/.claude/skills/tellonce/hooks/memory-*.sh
# should be -rwxr-xr-x; if not, chmod +x

# 2. Check the registrations in settings point at the skill dir:
python3 ~/.claude/skills/tellonce/lib/_install_merge_settings.py \
    --settings <project>/.claude/settings.local.json \
    --hooks-dir ~/.claude/skills/tellonce/hooks \
    --verify

# 3. Read install.log:
cat ~/.claude/skills/tellonce/install.log | tail -50

# 4. Run the full doctor check:
bash ~/.claude/skills/tellonce/doctor.sh

# 5. Run a hook manually and watch the output:
echo '{"session_id":"test","transcript_path":"/tmp/dummy.jsonl"}' | \
    bash ~/.claude/skills/tellonce/hooks/memory-deterministic-block.sh
# (should exit 0, since the transcript doesn't exist)

# 6. See what path_config detects (env / config / default):
python3 ~/.claude/skills/tellonce/lib/path_config.py
```

---

More issues:
- GitHub Issues: https://github.com/YujunZhou/tellonce/issues
- For a bug / false positive / whitelist request / threshold not working — attach
  the `doctor.sh` output + 7 days of `dashboard` data to your issue for easier
  diagnosis.
