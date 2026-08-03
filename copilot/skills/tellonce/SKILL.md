---
name: tellonce
description: "EVERY-MESSAGE enforcement: scan for preference/pitfall/friction signals, record to memory, log observations. Also handles memory audit/restructure. Use on EVERY user message — even simple ones, even during intensive technical work, even when you think there's nothing to detect. If you're not invoking this, you're skipping compliance."
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, AskUserQuestion
---

# Tellonce

## 统一记忆 Upsert

- Claude Code、Copilot CLI 和 Codex 共用 `<project_root>/.tellonce/memory/`。SQLite 是唯一真值，Markdown 与 active JSON 只是投影。
- 检测到持久偏好后，只调用 `python <plugin>/lib/memory_upsert.py enqueue --manual --force --source-text "<完整原始用户消息>"`。`--manual` 会在自动 hook 已启用时跳过重复入队；复杂多行消息可改用 `--request-file <json>`。禁止直接写记忆 Markdown，禁止把同一轮的禁用项与替代项拆开保存。
- enqueue 只写 inbox 并启动 detached worker，立即返回。LLM 语义判断、合并、事务和投影全部在后台运行；失败按退避重试，达到上限后标记 failed 并停止，不能阻塞当前回复。
- judge 在返回 `NEEDS_USER` 前，先用当前项目根目录、最近对话和 active rules 消解指代、scope 与 activation；这些 context 只能帮助解释本轮用户原话，不能单独授权持久化。只有剩余歧义会改变未来行为时才进入轻量 clarification 队列，并在后续上下文中只问一个简短问题；下一条明确回答可关闭对应 turn。
- 关闭自动 upsert 后 clarification 不再注入；过期项可用 `python <plugin>/lib/memory_upsert.py dismiss --turn-key <id>` 手动移除。
- 自动 hook 默认关闭。设置 `memory_upsert_enabled=true` 或 `PT_MEMORY_UPSERT_ENABLED=1` 后才启用。
- 一次修改三平台：运行 `python <plugin>/lib/memory_upsert.py enable-hooks`；`disable-hooks` 关闭，`hook-status` 查询。

## Run Modes and Defaults (Public Release)

**默认 = observe mode（仅观察）**：它不会硬拦截，也不会运行 shadow LLM judge。共享 memory upsert judge 使用独立的 `memory_upsert_enabled` 开关；该开关默认关闭，开启后即使处于 observe mode，也会在后台调用当前平台的 CLI judge，但不会阻塞用户回复。

- **Hard-block enforcement** (deterministic block / pending gate / observation-log gate) is **off** by default.
- **The shadow LLM judge** (sending the conversation to `copilot -p` for semantic scoring) is **off** by default. Privacy note: once enabled, each turn sends "the last user message + assistant reply" (with API keys / passwords etc. redacted) to that model.
- `seed_memory/` is **intentionally shipped empty** (only a README, no preinstalled rules); your rules are accumulated by the Gate Function as you correct / express preferences.

**Switch modes with a single command** (no need to remember environment variables or hand-edit JSON):

```
python <plugin>/lib/pt_mode.py enforce     # enable hard interception
python <plugin>/lib/pt_mode.py full        # hard interception + AI judge
python <plugin>/lib/pt_mode.py observe      # back to the safe default
python <plugin>/lib/pt_mode.py status       # see the current mode
python <plugin>/lib/dashboard.py            # see status at a glance (mode/registration/rule count/record count)
```

> You can also choose directly at install time: `install.ps1 -Mode enforce` (or `--mode enforce` for bash). The Infrastructure / Gate sections below describe the behavior **after enforcement mode is enabled**.

> **Copilot platform note (injection timing)**: on the Copilot CLI, recorded preferences can only be injected into the context **at session start (SessionStart)**. If you record a new preference **mid-session**, it is **saved to disk immediately**, but it won't be brought back in front of the agent until the **next session** (Copilot's UserPromptSubmit / PreToolUse hooks don't process stdout and can't inject context — this is a platform limitation, not a bug in this tool). To make a new preference take effect immediately, just start a new session. The Claude Code / Codex variants don't have this limitation (they re-inject every turn).

## Infrastructure

This skill is more than the Iron Law + Gate Function. It installs 3 layers of infrastructure that run as **automatic hooks**, taking effect without an explicit Skill invocation.

> **Path placeholder note**: in the text below, `<skill_dir>` defaults to `<skill_dir>/`, `<project_root>` is the current project root, and `<state_dir>` is `<project_root>/.copilot/tellonce-state/`. All paths are resolved at runtime by `lib/path_config.py` (a three-level fallback: env > `~/.tellonce.config.json` > auto-detect); SKILL.md does not hardcode absolute paths to avoid polluting Claude's output.

### Rule injection (progressive full index — default)

The Copilot variant **does not have** a per-prompt `memory-retrieve-inject.sh` hook (platform limitation, see the injection-timing note above). Rule injection happens **at session start**: `<plugin_root>/hooks/session-start-inject.sh` injects a **one-line index of the rules** saved under my memory dir as `additionalContext`. If the library exceeds the per-session cap (default 50 — `progressive_max` in `~/.tellonce.config.json` or env `PT_PROGRESSIVE_MAX` (legacy `B5_` alias works), `0` = no cap), tier-1 rules are pinned when they fit under the cap (when tier-1 alone overflows it, the whole library rotates instead), the remaining rules rotate in across sessions, and the block states how many of the total are shown. This is the default `progressive` backend: no prompt matching, no model call, and — unlike the old critical/high-only path — memory-only rules are no longer dropped. The format looks like this — **it's not external noise, it's a rule hint from the skill infra and must be respected**:

```
### Your saved preferences — check each against this turn and apply the ones that fit:
- [fmt-pref-001] (tier1) use 4 spaces for indentation, not tabs
- [tool-pref-002] (tier2) prefer the project's own package manager / lockfile for installing dependencies | when: adding / upgrading dependencies
(These are your recorded preferences. Judge each rule against the current task; apply those that apply, skip those that do not.)
```

Because injection is once per session, I re-read this list on **every** turn, judge each rule's `when:` hint against the current task, and skip the ones that don't apply (e.g. a preference about Go when the turn isn't touching Go).

### Applicability gate (soft, within the session-start injection)

Each injected rule carries a `when:` hint (read from the memory .md frontmatter's `applies_when:` or `condition:`). I judge:
- applies_when condition holds → apply rule
- condition doesn't hold → explicitly say "gate filter out: <reason>" then skip
- unclear → conservatively apply

### Log-only compliance tracker (Stop hook)

Each turn when I stop, `<plugin_root>/hooks/memory-verify-compliance.sh` reads the transcript, takes the last assistant text, and appends one line to `<state_dir>/obs_log/compliance_log.jsonl`:
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
| SessionStart hook (rule injection) | `<plugin_root>/hooks/session-start-inject.sh` |
| Stop hook chain (executed in order) | `check-observation-log.sh` → `memory-deterministic-block.sh` → `memory-verify-compliance.sh` → `memory-shadow-judge.sh` → `memory-upsert-enqueue.sh` (all in `<plugin_root>/hooks/`) |
| Compliance log | `<state_dir>/obs_log/compliance_log.jsonl` |
| Hooks registration | `<plugin_root>/hooks/hooks.json` |

> **To see the real paths on your machine**: run `python3 <skill_dir>/lib/path_config.py` to print all currently detected paths. Don't write / create files based on the placeholder literals in this SKILL.md — use the runtime values given by path_config.

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

**Only HARD check is active**: the observation log file must be appended within 1800s of Stop (default; tune via env `OBSERVATION_LOG_AGE_THRESHOLD_SEC`). That's the entire gate. If `observations.jsonl` is missing entirely, the hook self-seeds a synthetic detected=false entry instead of warning/blocking.

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

Storage location (path_config-driven): `<project_root>/.tellonce/memory/`，三平台共用。旧 Claude、Copilot、Codex 目录只用于首次迁移，不能继续作为 writer 目标。

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

1. 把用户确认后的变更编译成结构化 mutation plan。
2. 调用 `memory_upsert.py apply-plan` 事务化提交。
3. 由核心重建 `MEMORY.md`、规则 Markdown 和 active JSON 投影。
4. 展示最终结果；禁止直接修改或重命名投影文件。

---

## Conflict Resolution Algorithm

> **本节旧的手工文件流程已废弃。** 冲突解析现在只能由后台 `memory_upsert` worker 执行：一次读取全部 active rules，把完整 user turn 聚合为一个 plan，再用 SQLite 事务提交 NOOP/UPDATE/SUPERSEDE/NEW。下方关于直接 Write/Edit Markdown、手工分配 atomic_id 或手工改 `MEMORY.md` 的步骤仅保留为历史说明，禁止执行。

历史伪代码，仅用于解释旧版本为何会碎片化；禁止执行：

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

> **不再适用。** 正常记忆写入没有 agent 侧 pre-write；前台只能 enqueue。人工 audit 也必须把确认后的结果转为结构化 plan，并调用 `memory_upsert.py apply-plan`，不能直接改投影文件。

> **This does not contradict §Gate mechanics; it's layered**: §Gate mechanics turned off the SCAN text-marker because SCAN runs every turn + wording drifts → many false positives. memory-write is a low-frequency high-risk event (~1-3 times/session, doesn't drift), so here we **re-enable** the text-marker, limited to the memory-write scenario. The SCAN gate still only-HARD-checks the structured log; Pre-write is an additional layer on memory-write.

旧版本曾要求在直接写文件前输出以下文本；新版本禁止直接写文件，因此不要输出或执行：

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

异步 upsert 启用时，当前回复只确认“完整用户 turn 已入队，后台将合并或替代旧规则”，不得等待 worker，也不得猜测 atomic_id 或最终操作。只有 `inspect` 已返回 committed/projected 结果时，才使用下方带真实 atomic_id 的模板。

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
3. 当前共享核心尚未提供事务化 ARCHIVE/DELETE mutation，因此确认后标记为 `NEEDS_USER` 并说明尚未执行。禁止直接删除、重命名 Markdown 或改 `MEMORY.md`，否则 SQLite 会在下一次投影时恢复它。

---

## Health Check

A memory health check can be run periodically (or when the user requests it):

- Whether MEMORY.md's line count is approaching the 200-line limit
- Whether there are files that are superseded but still in the index
- Whether there are memories not referenced for a long time (judged by the updated date)
- Whether there are too many fragmented memories within the same domain that could be merged
- Whether there is a backlog of archived files

Show the report and let the user decide whether to clean up.
