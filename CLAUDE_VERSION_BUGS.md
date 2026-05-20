# Claude-version bug observations

> Surfaced while reading the Claude-version code in preparation for the Copilot CLI port.
> **Not fixed in this PR** — each item is for the user to triage separately.
> Generated 2026-05-19 by `discover-claude-pref-tracker` explore agent.

---

### `false_positive_n` is never populated → dead FP-rate logic
- **File:line**: `lib/threshold_advisor.py:216`
- **Symptom / risk**: `false_positive_n` stays zero, so the FP-rate branch of `suggest_threshold` is effectively dead; threshold suggestions may be wrong on rules that *do* have false positives.
- **Severity**: medium
- **Fix idea (not implemented)**: populate FP counts from actual log schema, or remove the FP branch entirely if it's no longer used.

---

### Crude `p95_latency_ms` computation
- **File:line**: `lib/analyze_b5_compliance.py:138`
- **Symptom / risk**: `sorted(...)[int(len*0.95)]` is a coarse percentile that can skew high (and silently IndexError on edge cases when `len*0.95` rounds to `len`).
- **Severity**: low
- **Fix idea**: proper percentile with clamp / linear interpolation (e.g. `statistics.quantiles` or `numpy.percentile`).

---

### `doctor.sh` writes to fixed `/tmp/doctor_test_$$`
- **File:line**: `doctor.sh:68-77`
- **Symptom / risk**: on shared hosts (e.g. lab clusters) and inside CI, `$$` collisions and leftover files from previous failed runs can be confusing; also doesn't work on Windows without `/tmp`.
- **Severity**: low
- **Fix idea**: use `mktemp` (POSIX) or write under `${STATE_DIR}/.doctor_tmp_*` so it's cleaned by uninstall and works cross-platform.

---

### `pending-promote` hook drains/re-feeds stdin it doesn't use
- **File:line**: `hooks/memory-pending-promote.sh:34-38`
- **Symptom / risk**: comment says `promote` doesn't read stdin, but the wrapper still drains and re-feeds it. Harmless today but very easy to regress (drop the re-feed → break a downstream consumer).
- **Severity**: low
- **Fix idea**: make the stdin contract explicit in a comment, or remove the drain entirely if no downstream needs it.

---

### Nested `claude -p` in `retrieve_inject.py` relies on brittle settings/cwd guard
- **File:line**: `lib/retrieve_inject.py:218-235`
- **Symptom / risk**: nested invocation depends on `--setting-sources project` + clean cwd to avoid re-triggering the hook chain (infinite recursion). One env-leak / cwd change and you get a recursion storm.
- **Severity**: medium
- **Fix idea**: belt-and-braces — also set `PT_DISABLE_HOOKS=1` env var that the hook entrypoints check at the top and exit 0 if set; the nested call sets it.

---

### Seed-memory copy only scrubs `originSessionId`
- **File:line**: `install.sh:298-327`
- **Symptom / risk**: other frontmatter keys (author IDs, internal tags, original cwd, etc.) in `seed_memory/*.md` can leak into a new user's memory dir.
- **Severity**: low
- **Fix idea**: maintain an explicit allowlist of frontmatter keys to keep, drop everything else; or scrub a documented blocklist.

---

### `check-observation-log.sh` header comment says 120s threshold but actual default is 1800s
- **File:line**: `hooks/check-observation-log.sh:13` vs `line 111`
- **Symptom / risk**: comment says "HARD: observation log file must be updated within 120s" but actual env-tunable default is 1800s. Misleads anyone reading the header.
- **Severity**: low (documentation only; runtime behavior correct)
- **Fix idea**: update header comment to say 1800s and note env-tunability.

---

### `check-observation-log.sh` advertised timestamp check never implemented
- **File:line**: `hooks/check-observation-log.sh:91-96`
- **Symptom / risk**: comments say "Last entry's timestamp within 30s of now" is one of the checks, but no code ever parses or validates the entry timestamp. The check only validates file mtime + session_id match.
- **Severity**: low (extra check would be defense-in-depth but not critical)
- **Fix idea**: either implement the timestamp check or remove the comment.

---

### `memory-deterministic-block.sh` uses GNU `timeout` which doesn't exist on macOS
- **File:line**: `hooks/memory-deterministic-block.sh:30`
- **Symptom / risk**: `timeout 5 python3 detect_user_prefer.py` fails on macOS/BSD (no `timeout`), falls through to `|| echo u`, making `_PREFER_SC="u"` which can incorrectly skip deterministic blocking for non-urgent user preferences.
- **Severity**: medium (blocks may be skipped on macOS)
- **Fix idea**: use `gtimeout` fallback, or Python-side timeout, or inline the timeout logic in the Python script.

---

### `verify_retry_shadow.py` docstring model/env defaults contradict code
- **File:line**: `lib/verify_retry_shadow.py:24,32`
- **Symptom / risk**: docstring says default judge model is Sonnet 4.6 (line 32) but code uses `claude-haiku-4-5`. Says `ANTHROPIC_CREDIT_OK=1` required (default OFF) but code defaults it ON.
- **Severity**: low (documentation misleads; runtime works correctly)
- **Fix idea**: update docstring to match code.

---

### `verify_retry_shadow.py` nested `claude -p` lacks recursion/env hardening
- **File:line**: `lib/verify_retry_shadow.py:293-295`
- **Symptom / risk**: unlike `retrieve_inject.py` (which strips env vars, sets recursion guard, isolates cwd), shadow judge's `claude -p` subprocess has no such protections. Could trigger hook chain recursion if hooks are loaded in the nested session.
- **Severity**: medium (recursion risk under certain configurations)
- **Fix idea**: add `B5_SHADOW_RECURSION_GUARD` env var, strip session env, set cwd to temp dir — same pattern as `retrieve_inject.py`.

---

### `verify_compliance.py` cross-session pending count contamination
- **File:line**: `lib/verify_compliance.py` (count_recent_pending function)
- **Symptom / risk**: uses time-window filter as proxy for session_id (because obs entries lack session_id). Two overlapping sessions can inflate each other's pending count, causing false B4 gate blocks.
- **Severity**: low (rare in practice; multi-session usage uncommon)
- **Fix idea**: add session_id field to obs entries, or accept as known limitation.

---

### `path_config.py` Windows path encoding bug
- **File:line**: `lib/path_config.py:148-150`
- **Symptom / risk**: `cwd.replace('/', '-')` ignores `\` and `:` on Windows. `C:\foo\bar` produces `C:\foo\bar` (unchanged) or a broken path fragment, making memory dir resolution wrong on Windows.
- **Severity**: medium (Claude Code rarely runs on Windows, but still a latent bug)
- **Fix idea**: normalize path separators before escaping, or use a hash-based encoding.
