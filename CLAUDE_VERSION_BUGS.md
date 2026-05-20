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
