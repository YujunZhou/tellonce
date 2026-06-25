---
name: tellonce
description: "EVERY-MESSAGE enforcement: scan for preference/pitfall/friction signals, record to memory, log observations. Also handles memory audit/restructure. Use on EVERY user message — even simple ones, even during intensive technical work, even when you think there's nothing to detect. If you're not invoking this, you're skipping compliance."
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, AskUserQuestion
---

# Tellonce

## Run Modes and Defaults (Public Release)

**Default = observe mode (observe-only)**: by default this skill only does "scan preferences → record → tell the user". It **never hard-blocks the session** and **never calls an LLM**. A new user who just installed it won't be intercepted by any hard rule, nor will their conversation content be sent to a third-party model.

- **Hard-block enforcement** (deterministic block / pending gate / observation-log gate) is **off** by default; you must explicitly set `PT_ENFORCE=1` to enable it.
- **The shadow LLM judge** (sending the conversation to an external model for semantic scoring) is **off** by default; you must explicitly set `PT_SHADOW=1` to enable it. Privacy note: once enabled, each turn sends "the last user message + assistant reply" (with API keys / passwords etc. redacted) to that model.
- `seed_memory/` is **intentionally shipped empty** (only a README for explanation): no one's preference rules are preinstalled; your rules are accumulated one by one in use by the Gate Function.

**Switching modes** (environment variables; setting neither = the safe default observe-only):

```
PT_ENFORCE=1   # enable hard interception
PT_SHADOW=1    # enable the AI judge (shadow mode)
```

> The Infrastructure / Gate sections below describe the behavior **after enforcement mode is enabled**. In the default observe mode, these gates only record and do not block.

## Infrastructure

This skill is more than the Iron Law + Gate Function. It installs 3 layers of infrastructure that run as **automatic hooks**, taking effect without an explicit Skill invocation.

> **Path placeholder note**: in the text below, `<skill_dir>` defaults to `~/.claude/skills/tellonce/`, `<project_root>` is the current project root, and `<state_dir>` is `<project_root>/.claude/tellonce-state/`. All paths are resolved at runtime by `lib/path_config.py` (a three-level fallback: env > `~/.tellonce.config.json` > auto-detect); SKILL.md does not hardcode absolute paths to avoid polluting Claude's output.

### Deterministic fingerprint retrieval (UserPromptSubmit hook)

Each time the user submits a message, `<skill_dir>/hooks/memory-retrieve-inject.sh` will:
1. Scan the user prompt against `<skill_dir>/lib/fingerprints.yaml` (**shipped empty** — `fingerprints: {}`; you add entries yourself, or put them in the private overlay `fingerprints.user.yaml`, and the two are merged at load time)
2. Inject the matched atomic_id as `additionalContext` at the start of my context
3. The format looks like this — **it's not external noise, it's a rule hint from the skill infra and must be respected**:

```
### Fingerprint retrieval — memory rules auto-matched for this turn:
- **[fmt-pref-001]** (critical) use 4 spaces for indentation, not tabs
    • triggered by: indent
- **[tool-pref-002]** (critical) prefer the project's own package manager / lockfile for installing dependencies
    • action: add dependencies via the lockfile, don't manually edit version numbers
    • applies_when: when adding / upgrading dependencies;
```

Each one carrying an `applies_when` field — this is an applicability gate. I have to **judge for myself** whether applies_when holds for the current turn, and skip it if it doesn't (e.g. the user merely mentioned a trigger word in passing, but the current turn doesn't actually enter the rule's applicable scenario, so don't force it).

### Applicability gate (soft, within the retrieval hook)

Each rule injected by the retrieval hook carries `applies_when: ...` and `condition: ...` read from the memory .md frontmatter. I judge:
- applies_when condition holds → apply rule
- condition doesn't hold → explicitly say "gate filter out: <reason>" then skip
- unclear → conservatively apply

### Log-only compliance tracker (Stop hook)

Each turn when I stop, `<skill_dir>/hooks/memory-verify-compliance.sh` reads the transcript, takes the last assistant text, and appends one line to `<state_dir>/obs_log/compliance_log.jsonl`:
- `response_excerpt` (first 400 chars)
- `fp_rules_in_response` (which rule keywords were triggered in the response)
- `lang_ratio.chinese_ratio` (Chinese-to-English ratio)

**Not blocking** (no automatic retry yet). Later, if the FP rate is low enough, enabling blocking can be reconsidered.

### Infrastructure file inventory

| Role | Path (placeholder; resolved at runtime by path_config) |
|------|------|
| Fingerprint rule library | `<skill_dir>/lib/fingerprints.yaml` |
| Retrieve handler | `<skill_dir>/lib/retrieve_inject.py` |
| Compliance tracker | `<skill_dir>/lib/verify_compliance.py` |
| UserPromptSubmit hook | `<skill_dir>/hooks/memory-retrieve-inject.sh` |
| Stop hook | `<skill_dir>/hooks/memory-verify-compliance.sh` |
| Compliance log | `<state_dir>/obs_log/compliance_log.jsonl` |
| Hooks registration | `<project_root>/.claude/settings.local.json` (registers the `<skill_dir>/hooks/` paths directly, without copying files into the project) |

> **To see the real paths on your machine**: run `python3 ~/.claude/skills/tellonce/lib/path_config.py` to print all currently detected paths. Don't write / create files based on the placeholder literals in this SKILL.md — use the runtime values given by path_config.

### Keep things in sync when adding / updating rules

When the Gate Function records a new rule to memory, if it's a **high-value deterministic rule** (e.g. the user explicitly says "from now on always use 4 spaces for indentation" / "use the imperative mood for commit messages"), also add a keyword trigger in `fingerprints.yaml` so the next session can auto-recall it on retrieval.

**Rules that don't need an FP**: semantic / context-dependent / meta rules (such as "don't fully trust an inherited plan", which rely on model judgment rather than keywords).

---

## The Iron Law

```
NO RESPONSE IS COMPLETE WITHOUT A PREFERENCE SCAN.
```

If you haven't scanned for signals and recorded the result, your response is incomplete. This applies to EVERY message, no exceptions.

**Violating the letter of this rule is violating the spirit of this rule.**

---

## Progress Document Maintenance

When updating long-lived progress/state files such as `PROGRESS.md`, keep them as **current state + operations dashboards**, not session transcripts.

Keep in the current file:
- Active status that future work depends on: current results, blockers, running processes, pending decisions, active infrastructure, and next actions.
- Project sections that are still part of the current structure unless the user explicitly removes or retires them.
- Current project plans and concrete details needed for continuation.

Move to an archive file:
- Historical session logs, stale runs, replaced plans, deprecated routes, old answered questions, and debug narratives.
- Strike-through or "was replaced by" entries; rewrite the current file to the current fact and preserve old context in archive.
- Paused or out-of-scope branches that are no longer needed for immediate continuation.

Do not encode temporary project-specific exclusions, benchmark names, or current experimental choices into durable preference text. Those belong in the project progress file itself. The durable rule is the maintenance policy: current file stays current and actionable; old narrative moves to archive.

After cleanup, grep the progress file for stale route names, old debug terms, and strike-through markers before committing.

---

## Gate Function

```
BEFORE considering your response complete:

1. SCAN: Read user message + task execution — preference/pitfall/friction signal?
2. RECORD: Write observation log. If detected=true, write/update memory.
3. CONFIRM: If signal detected, tell user at end of response.

Skip any step = compliance failure.
```

### Gate mechanics

**Only HARD check is active**: the observation log file must be appended within the staleness threshold at Stop (default 1800s, tunable via env `OBSERVATION_LOG_AGE_THRESHOLD_SEC`). That's the entire gate.

**SOFT text-marker scans are DISABLED** (caused spurious blocks because my response text wording varies each turn). The structured log entry itself carries the scan result — that's sufficient audit trail.

**Practical rule for every turn**:
- Append **one** entry to `observations.jsonl` before stopping. Any entry. detected=true or detected=false, doesn't matter for the gate.
- Keep doing rich structured entries (detection fields, root_cause notes, confirmation_text) — they make the local memory/audit trail more useful, even though the gate doesn't check them. Truncate any user-message excerpt to ~200 chars and never copy secrets/credentials into the log.
- No need to paste SCAN markers in response text.

**Why it matters**: the gate blocks only when the log genuinely wasn't written (a real miss), not when response wording fails a text regex — this avoids spurious blocks.

---

## Red Flags — STOP

If you catch yourself thinking any of these, STOP and do the scan:

- "This is just a status check / simple question"
- "I'll do the scan after the task"
- "The task is more urgent than scanning"
- "I already scanned recently, skip this one"
- "There's obviously nothing here"
- "I'm in the middle of something complex"
- "This message is too short to contain signals"
- "NOOP / UPDATE doesn't need confirmation_text" → wrong; the detected=true path requires confirmation (see `## Confirmation Strategy`)

---

## Rationalization Prevention

| Excuse for not saving | Reality |
|----------------------|---------|
| "This is a methodology decision, not a preference" | Methodology decisions ARE preferences |
| "It'll be in the code" | Code isn't memory. Next session won't read the code |
| "Too obvious to save" | If it's obvious, why did you violate it? Save it |
| "Already covered by existing memory" | Cite the atomic_id or it's not covered |
| "Not reusable / one-time instruction" | Then say so in the response — let user correct you |
| "I'm confident this isn't a signal" | Confidence ≠ evidence. Over-detect, don't under-detect |

---

## Principle-based Detection (always primary)

**Patterns below are seed examples, NOT an exhaustive checklist.** User phrasing varies; literal pattern-matching misses most signals. Apply the principles semantically first, use patterns as cues.

### Detection Principles (apply in this order each turn)

1. **Any clause expressing how the user wants things done** → preference
   - Including first-person value statements ("I like / I hope / I think X is good"), normative claims ("X should / must / had better be Y"), comparative preferences ("X is better than Y")
   - Regardless of whether it's phrased as instruction, reason, complaint, or aside

2. **Any clause expressing frustration or correcting your behavior** → friction or pitfall
   - Frustration markers: repetition ("again" / "still"), exasperation ("why is it still…"), rhetorical questions ("didn't I already say…"), sarcasm ("never mind")
   - Even if softened ("actually" / "it's fine" / "never mind"), the softener often masks a real signal

3. **Any reason/justification clause in the message** → scan independently
   - User structure `[instruction] + [reason]` — the reason often states WHY they have this preference, which IS the preference content
   - Markers: because / mainly / I want / I'd like / therefore / so / so that

4. **Any meta-question about your behavior** → friction (you did something they want reconsidered)
   - "is this X?" / "what do you think of Y?" / "why did you do it this way?" / "is this Z?" — user is questioning your choice, not asking opinion

5. **Silent acceptance of unusual approach or clean pivot after your suggestion** → validated preference
   - No pushback IS signal. Especially when you made a judgment call they could have corrected.

### The cost asymmetry (defaults)

- **Cost of false positive** (mark non-signal as signal): user says "no, one-time" → you learn something. ~1 turn loss.
- **Cost of miss**: user frustrated over rounds, same mistake repeats session after session, eventual correction is high-effort.
- → **Default: detect, ask when low-confidence, save when medium+.**

### Don't stop at patterns

If message doesn't match any listed pattern but **any of the 5 principles** fires → **still a signal**. Patterns are anchors; principles are the rule.

---

## Implicit Signal Detection

**Below are concrete examples of the principles above — seed pattern library, not the full set.** Scan semantically first (see principles), use these as cues:

| User says | Surface meaning | Actual signal | Clue |
|-----------|----------------|---------------|------|
| "shouldn't you check elsewhere?" | Question | **friction**: you should have done this already | Follows your mistake |
| "again…" / "still…" / "why is it still…" | Frustration | **pitfall**: same error repeated | 2nd+ occurrence |
| "didn't I already tell you?" | Rhetorical question | **friction**: rule exists but wasn't followed | References memory |
| "yes" + correction | Partial agreement | **preference**: strengthening existing rule | Subtle redirect |
| Accepts unusual approach silently | No pushback | **preference**: validated judgment call | Absence of correction |
| **"because… I want…"** | Justification for request | **preference**: the "because" clause states the rule itself | Rationalization clauses often contain the preference, not just context |
| **"verify it, because I want to…"** | Task instruction | **preference**: preferred mode of answering (empirical > theoretical) | The "because" clause reveals a working-style preference, separate from the task |
| **"does this count as X?"** | Meta-question about classification | **friction**: you misclassified something last turn | User is correcting your signal detection, not asking opinion |
| **"I don't really get this, you try it yourself"** | Delegation | **preference**: grants autonomy for unfamiliar domain | User trusts you to experiment; don't ask follow-up Qs, just do |

**Default**: When in doubt, detect. User saying "no" costs 1 second. Missing a signal costs it forever.

### Rationalization-clause pattern

When user structures message as `[instruction] + [because/mainly/reason]`, the **reason clause frequently states a preference** separate from the instruction:

- ❌ Wrong: treat "reason" as mere context, ignore it
- ✅ Right: scan "reason" clause independently for preference content

Examples:
- `"let's use SQLite first, because I want to validate locally quickly"` → task: use SQLite + preference: tends to validate locally and quickly first before adopting a heavier solution
- `"let's skip tests this time, mainly because I want to nail down the interface first"` → task: skip tests for now + preference: a phase-based preference that interface design takes priority over tests
- `"don't use an agent for this, do it yourself — I want to see how you handle it"` → task: inline + preference: the user wants to see your reasoning process, don't outsource it to an agent

---

## Overview

This skill has three responsibilities:
1. **Per-message enforcement** (Gate Function): scan signals → record → store memory
2. **Init/audit mode** (when invoked by the user): audit the entire memory structure and migrate to the structured format
3. **Manual management**: handle complex conflicts, batch consolidation, deletion operations, large-scale reorganization

---

## Signal Type Definitions

### preference
The user explicitly expresses how they want something done. Forward-looking behavioral guidance; prefer recording a concrete action or check over an adjective/attitude (see the Actionability gate below).

Examples:
- "use camelCase for functions, UPPER_SNAKE for constants"
- "PR descriptions should clearly state the motivation and how it was tested"
- "run lint and unit tests once before committing"

### pitfall
A recurring technical trap / error pattern. The "don't do it again" kind. Usually comes from user corrections or repeatedly stepping into the same trap; when possible capture the prevention action or check, not just a warning.

Examples:
- "nested ``` breaks the markdown structure; use 4+ backticks"
- "forgetting to await an async call silently swallows errors"
- "two specific dependency versions are incompatible; check the changelog before upgrading"

### friction
An ongoing pain point in the workflow. Not necessarily solvable, but worth being aware of.

Examples:
- "having to re-explain context every time the window switches"
- "memory granularity is misaligned"
- "rate limits on large batches of API calls cause interruptions"

### Retained existing types
The existing `user`, `project`, `reference` types continue to be used, with unchanged definitions.
The `feedback` type is no longer used in new memories and is gradually migrated to `preference` or `pitfall`.

---

## Memory File Format

Storage location (path_config-driven): `~/.claude/projects/<cwd_escaped>/memory/`, where `<cwd_escaped>` is the current project cwd with `/` replaced by `-`. For the real path, run `python3 ~/.claude/skills/tellonce/lib/path_config.py` and look at the `memory_dir` field, or read `<skill_dir>/lib/path_config.py:get_memory_dir()`.

### Frontmatter specification

```yaml
---
name: <short name>
description: <one-line description, used to judge relevance in the future, be specific>
type: preference | pitfall | friction | user | project | reference
domain: formatting | language | workflow | coding | tools | experiment | writing | communication | other
scope: global | project:<project_name>
condition: "<optional, applicable condition, e.g. when writing shell scripts>"
confidence: high | medium | low
atomic_id: <domain_abbrev>-<type_abbrev>-<3-digit sequence>
supersedes: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

### Abbreviation mapping

**type:**
| Full name | Abbrev |
|------|------|
| preference | pref |
| pitfall | pit |
| friction | fric |
| user | usr |
| project | proj |
| reference | ref |

**domain:**
| Full name | Abbrev |
|------|------|
| formatting | fmt |
| language | lang |
| workflow | wf |
| coding | code |
| tools | tool |
| experiment | exp |
| writing | wrt |
| communication | comm |
| other | oth |

### File naming convention

`<type_abbrev>_<descriptive_name>.md`

Examples:
- `pref_indent_style.md`
- `pit_md_nested_codeblock.md`
- `fric_cross_session_memory.md`
- `usr_role.md`
- `proj_repo_layout.md`
- `ref_api_pagination.md`

### Body structure

```markdown
<core content: what this memory says>

**Why:** <why this should be remembered — the reason, background, triggering event>

**How to apply:** <under what circumstances and how to apply this memory>
```

### Actionability gate (preference / pitfall only)

Before writing a `preference` or `pitfall`, run the **reusable self-check**: if a future agent reads this rule *without the original conversation*, can it pick a concrete action or check whose outcome may differ by context? If not, the rule is too soft — an adjective/attitude like "be thorough" guides nothing.

- **If you can compile it confidently** → record the actionable version.
- **If you can't** → don't invent a narrow, possibly-wrong step. Record your best operational interpretation, and make `confirmation_text` carry **both** the user's original wording **and** your proposed actionable version so the user can sharpen it. (Turning an attitude into an action needs the user's domain knowledge — surface the gap, don't paper over it.)

This gate does **not** apply to `friction`, `user`, `project`, or `reference` — those may stay descriptive.

| User signal | Too soft ❌ | Actionable ✅ |
|---|---|---|
| "think about design as meticulously as I do" | "Think very meticulously about design." | "Don't accept your own simplifying assumptions: for each, pressure-test it against real scenarios — would it hold in practice? any counterexamples? what complexity did it discard?" |
| "write better tests" | "Write high-quality tests." | "For a behavior change, cover the happy path, one edge/failure case, and the specific regression it protects." |
| "stop saying it's fixed when it isn't verified" | "Avoid premature confidence." | "Before claiming fixed/passing/done, run the relevant check and cite the result; if you can't verify, say what's unverified instead." |

---

## MEMORY.md Index Format

Grouped by domain, each entry < 150 characters. Within a domain, ordered by type (preference → pitfall → friction → others).

```markdown
# Memory

## Formatting
- [fmt-pref-001](pref_indent_style.md) — use 4 spaces for indentation, not tabs
- [fmt-pit-001](pit_md_nested_codeblock.md) — wrap nested ``` with 4+ backticks

## Language
- [lang-pref-001](pref_reply_language.md) — language preference for replies / deliverables (example)

## Workflow
- [wf-pref-001](pref_branch_workflow.md) — some workflow preference (example)
- [wf-fric-001](fric_context_handoff.md) — some recurring friction point (example)

## Experiment
...

## Project
...

## Reference
...
```

MEMORY.md must not exceed 200 lines. If it approaches the limit, merge fine-grained memories within the same domain.

---

## Memory Consolidation Triggers

### Automatic trigger: every 10 new entries

After the RECORD step of the Gate Function, check the current total number of memory files. If 10 or more have been added since the last consolidation:

1. Count the non-archived .md files under memory/ (excluding MEMORY.md)
2. Compare against the number of entries in the MEMORY.md index
3. If the difference is ≥ 10: do a quick consolidation of the **most recent 10 entries** (check classification, deduplicate, update the MEMORY.md index)
4. Don't touch the old ones — only tidy the most recent

```bash
# Quick check: file count vs index count
FILE_COUNT=$(ls memory/*.md | grep -v MEMORY | grep -v _archived | wc -l)
INDEX_COUNT=$(grep -c '^\- \[' memory/MEMORY.md)
DIFF=$((FILE_COUNT - INDEX_COUNT))
# DIFF >= 10 → trigger quick consolidation
```

### Manual trigger: full reorganization from scratch

Executed when the user invokes `/tellonce` or says "tidy up memory".

**Key: a full reorganization is not based on the old classification.** Because as memory accumulates, domain classification may change (e.g. something previously filed under workflow now fits better under experiment). You must re-classify by looking at each memory's content from scratch, not just patch the old index.

### Step 1: Full audit (starting from zero)

Read all .md files under memory/ and MEMORY.md, and for each file check:

| Check item | Description |
|--------|------|
| frontmatter completeness | Are all required fields present |
| type accuracy | feedback → should it be preference or pitfall? |
| domain classification | Is the domain field missing |
| atomic_id | Does it have a unique identifier |
| file naming | Does it follow the `<type_abbrev>_<name>.md` convention |
| content duplication | Is there semantic duplication across files |
| body structure | Does it have Why + How to apply |
| scope | Has global vs project-specific been distinguished |

### Step 2: Generate the audit report

Present to the user in table form:

```
📋 Memory Audit Report

Total files: N
Compliant with the new spec: X
Need migration: Y

Files that need changes:
| File | Current state | Suggested action |
|------|----------|----------|
| feedback_md_formatting.md | type=feedback, no atomic_id | → type=pitfall, rename to pit_md_nested_codeblock.md |
| feedback_language_preference.md | type=feedback, no domain | → type=preference, domain=language |
| ... | ... | ... |

Suspected duplicates / mergeable:
| File A | File B | Relationship |
|-------|-------|------|
| ... | ... | semantic duplication / mergeable |

Suggested new MEMORY.md structure:
(show a preview of the reorganized index)
```

### Step 3: User confirmation

- Present changes group by group (grouped by domain, don't ask file by file)
- The user can: accept all / confirm group by group / modify some suggestions
- **Must wait for user confirmation before executing writes**

### Step 4: Execute migration

1. Update each file's frontmatter
2. Rename files (if needed)
3. Rebuild the MEMORY.md index
4. Show the final result

---

## Conflict Resolution Algorithm

Executed when writing a new memory:

```
1. Determine the new memory's domain and type
2. Read all existing memory files under that domain
3. For each existing memory, judge the semantic relationship:
   a) Compare description and body content
   b) Determine the relationship type:

      Unrelated (a completely different thing)        → continue to the next one
      Same (about the same thing, consistent content) → NOOP: don't write, tell the user "already recorded"
      Complementary (same topic, new content adds to it) → UPDATE: merge the new content into the existing file
      Contradictory (same topic, opposite conclusion)    → SUPERSEDE: create a new file, add the old file's atomic_id to supersedes

4. If the relationship is unclear (between complementary and contradictory):
   → show both memories to the user and let them decide: merge / supersede / keep separate

5. Update the MEMORY.md index for the results of all operations
```

### ⚠ Pre-write verification checklist

> **This does not contradict §Gate mechanics; it's layered**: §Gate mechanics turned off the SCAN text-marker because SCAN runs every turn + wording drifts → many false positives. memory-write is a low-frequency high-risk event (~1-3 times/session, doesn't drift), so here we **re-enable** the text-marker, limited to the memory-write scenario. The SCAN gate still only-HARD-checks the structured log; Pre-write is an additional layer on memory-write.

**Before** writing a memory file (Write/Edit any new `memory/*.md` file / change an atomic_id), state in the response **which existing memories you checked** and **what decision you made** (NOOP / UPDATE / SUPERSEDE / NEW + a one-sentence reason). Wording and language are unrestricted; below is one recommended example format (when enforcement mode is on, the optional Stop-hook recognizes this format):

```
**I checked**: memory/<domain>/*.md, candidates considered = [<atomic_id_1>, <atomic_id_2>, ...]
**Decision**: NOOP | UPDATE existing <atomic_id> | SUPERSEDE existing <atomic_id> | NEW — because <one-sentence reason>
```

**Why**: an advisory rule alone isn't enough — the agent tends to short-circuit conflict resolution during intensive writing. Explicitly writing out "what was checked + the decision" = a forcing function: it ensures the dedup / conflict judgment is actually done before each memory write.

**Applies (applies_when)**:
- About to Write a new `memory/*.md` file
- About to Edit the `atomic_id` field of a `memory/*.md` file
- Promising "saving memory" / "store into memory" / "record the preference" in the confirmation_text
- A trigger word appears in the response such as "new principle" / "save this" / "record the preference" / "store into memory" → even if the Write tool wasn't actually invoked, still go through it

**Does not apply (does_not_apply_when)** — explicit allowlist (not a denylist):
- Read-only operations (Read / Grep / Bash querying memory)
- Fixing a typo in a memory file / fixing the `created`, `updated` dates / adding a `superseded_by` marker / fixing the description wording (without touching atomic_id or supersedes)
- Adding/removing a MEMORY.md index entry (this is a derived operation, it doesn't create an atomic_id)

**Legitimate skip (shortcut)**:
1. **Explicit pre-declaration in a multi-step audit**: only when a candidates list explicitly enumerated earlier in this turn **covers the atomic_id about to be written** — the list explicitly contains "X-pref-NNN: NEW because Y". Otherwise **each new file must be gone through individually**. A vague "I audited earlier" does not count as an override.
2. **Explicit user disable wording**: the user explicitly says "no need to check" / "just save it, don't verify" / "skip conflict resolution" — an explicit disable. Implicit OK ("save it" / "go ahead" / "note it down") **does not count as an override**; still go through the checklist.

**Stop hook verification** (optional, advisory by default):

The current stop hook (`memory-verify-compliance.sh`) scans the transcript at the end of the turn. If `memory/*.md` was written that turn but the Pre-write two lines don't appear in the response text → log a warning into `compliance_log.jsonl` (advisory, doesn't block). After collecting 1 week of data, decide whether to upgrade to blocking exit-2.

**The optional Stop-hook regex** (only when enforcement mode is on, the hook uses it to recognize the example format above):

```regex
^\*\*I checked\*\*:.*candidates considered = \[.*\]$
^\*\*Decision\*\*: (NOOP|UPDATE|SUPERSEDE|NEW)\b.*— because .+$
```

The two lines must form a **consecutive pair** (adjacent or separated by only 1 blank line) to count as a valid verdict. If ≥2 matching pairs appear within a turn, take the last pair as the verdict (quoting in handoff/explain text doesn't count). False-positive defense: handoff/skill-content/code-review-paste and other quoting scenarios can't produce a concrete pair like "**I checked**: ... candidates = [actual_atomic_ids_with_concrete_reason]"; rely solely on regex + concrete-id structure to distinguish.

### SUPERSEDE protocol

When a new memory supersedes an old one:
1. The new file's `supersedes` field lists the superseded atomic_id
2. The old file is **not deleted**, but `superseded_by: <new atomic_id>` is added to its frontmatter
3. Only the new file is kept in the MEMORY.md index; the old file is removed from the index (but the file is kept for traceability)

---

## Confirmation Strategy

### High confidence (user stated explicitly + clearly worded + clear scope)
Tell the user in one sentence which preference you recorded, and invite a correction (wording / language is up to you). For example:
> Recorded preference [fmt-pref-002]: <one-line content>. Let me know if it's wrong. (Recorded preference [fmt-pref-002]: …; let me know if it's wrong.)

### Medium confidence (fairly clearly worded but scope or persistence is unclear)
Ask briefly:
> Detected a preference: output should be concise. Does this apply to all scenarios, or only the current task?

### Low confidence (might be a preference, might be a one-time instruction)
Ask in detail:
> You mentioned "this is too long" — should I record it as a long-term preference (keep replies short from now on), or was it just an instruction for this time?

### Silent mode
If the user has said "stop asking, just record it" / "no need to confirm":
- Record this meta-preference
- Write silently thereafter
- Notify the user only on a SUPERSEDE (replacing an old memory)
- The user can say "resume confirmation" at any time to re-enable it

### ⚠ Key: when detected=true, confirmation_text can never be empty (including NOOP/UPDATE)

**Stop hook hard check**: `detection.detected=true AND action.confirmation_text empty → block stop`. This is independent of conflict_resolution (NOOP / UPDATE / SUPERSEDE / NEW) — even if you decide not to write a new file (NOOP) or only update an existing file (UPDATE), the `confirmation_text` field must contain a non-empty string telling the user what you detected.

**Easy trap**: mistakenly equating "NOOP = don't write a new memory" with "silent = no need to confirm". This is wrong. NOOP means **nothing is written at the memory layer**, but the user-facing **CONFIRM layer still runs**.

**What each conflict_resolution's confirmation_text should convey** (wording / language unrestricted; the sentences below are only examples):

| Resolution | What the confirmation_text should convey (example wording) |
|------------|------------------------------------------------------|
| **NEW**    | which new preference was recorded + atomic_id, and invite a correction. E.g.: `Recorded preference [<atomic_id>]: <one line>. Let me know if that's wrong.` |
| **UPDATE** | which existing preference was updated + the added increment, with the original rule kept. E.g.: `Updated [<atomic_id>] with <delta>; original rule kept.` |
| **SUPERSEDE** | which old preference it conflicts with, which new one was created to supersede it, the old file marked superseded_by. E.g.: `Conflicts with [<old id>]; created [<new id>] to supersede it.` |
| **NOOP**   | what preference was detected, which existing atomic_id already covers it, not rewritten. E.g.: `Detected "<content>" — already covered by [<existing id>], no new file.` |

**For a soft `preference` / `pitfall`** (see the Actionability gate): if you could not confidently compile an actionable rule, the `confirmation_text` must also carry the user's original wording **and** your proposed actionable version, so the user can sharpen it.

**Exception**: only when the user has explicitly enabled **global silent mode** and this time detected=false may confirmation_text be empty. Any detected=true path must fill it in.

**How to fill the `<atomic_id>` in the template**: it must be the real ID found by the conflict-resolution algorithm (grep the `memory/MEMORY.md` index or the `memory/*.md` files). If the hook triggered the NOOP/UPDATE template hint but you can no longer recall the atomic_id matched at the time, **re-run grep memory** instead of guessing — a wrong guessed ID would mislead the user into thinking some rule exists.

---

## Forgetting / Deletion Handling

When the user expresses an intent to delete ("forget X" / "drop that rule" / "delete the record about X"):

1. Search memory for memories related to X
2. Show the matching results and let the user confirm which to delete
3. After confirmation:
   - Remove from the MEMORY.md index
   - Rename the file to `_archived_<original_filename>.md` (don't hard-delete, keep it for traceability)
   - Or, if the user says "delete permanently", actually delete the file

---

## Health Check

A memory health check can be run periodically (or when the user requests it):

- Whether MEMORY.md's line count is approaching the 200-line limit
- Whether there are files that are superseded but still in the index
- Whether there are memories not referenced for a long time (judged by the updated date)
- Whether there are too many fragmented memories within the same domain that could be merged
- Whether there is a backlog of archived files

Show the report and let the user decide whether to clean up.
