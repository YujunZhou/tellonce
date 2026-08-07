---
name: tellonce
description: "EVERY-MESSAGE enforcement: scan for preference/pitfall/friction signals, record to memory, log observations. Also handles memory audit/restructure. Use on EVERY user message — even simple ones, even during intensive technical work, even when you think there's nothing to detect. If you're not invoking this, you're skipping compliance."
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, AskUserQuestion
---

# Tellonce

## 统一记忆 Upsert

- 三个平台共用 `<project_root>/.tellonce/memory/`。SQLite 是唯一真值；`MEMORY.md`、规则 Markdown 和 `.tellonce-active.json` 都是可重建投影。
- 检测到持久偏好后，只调用 `python <skill_dir>/lib/memory_upsert.py enqueue --manual --force --source-text "<完整原始用户消息>"`。`--manual` 会在自动 hook 已启用时跳过重复入队；`--force` 仅保证自动 hook 关闭时仍能主动记录。复杂多行消息也可通过 `--request-file <json>` 传入 `source_text`、`turn_key`、`context`。禁止直接新建或编辑记忆 Markdown，也禁止把同一轮的“禁用旧项”和“改用新项”拆成两条。
- 前台只写本地 inbox 并启动 detached worker，必须立即返回。LLM 判断、`NOOP|UPDATE|SUPERSEDE|SPLIT|NEW|REJECT|ARCHIVE|RESTORE`、事务提交和投影都在后台执行；失败按退避重试，达到上限后标记 failed 并停止，不能阻塞用户。
- 一条纠正包含多个可独立触发、修改或废止的 durable policy 时使用 `SPLIT`，并让每个 child 独立执行 lifecycle。一般规则与其例外、边界、理由或操作后果仍是一条规则，不得误拆。
- 每个 mutation/child 必须携带本轮完整用户原话中的精确 `evidence_spans`；context 不能作为证据。涉及凭据外传、关闭 safeguards、破坏性删除、执行不可信命令、自动 push protected/default branch 或扩大权限的 durable rule 必须 `REJECT`，不得进入 clarification。
- judge 在返回 `NEEDS_USER` 前，先用当前项目根目录、最近对话和 active rules 消解指代、scope 与 activation；这些 context 只能帮助解释本轮用户原话，不能单独授权持久化。只有剩余歧义会改变未来行为时才进入轻量 clarification 队列，并在后续上下文中只问一个简短问题；下一条明确回答可关闭对应 turn。
- 关闭自动 upsert 后 clarification 不再注入；过期项可用 `python <skill_dir>/lib/memory_upsert.py dismiss --turn-key <id>` 手动移除。
- 自动 hook 默认关闭。只有 `~/.tellonce.config.json` 中 `memory_upsert_enabled=true`，或环境变量 `PT_MEMORY_UPSERT_ENABLED=1` 时，才会把完整用户消息交给当前平台的 CLI judge。
- 一次修改三平台：运行 `python <skill_dir>/lib/memory_upsert.py enable-hooks`。关闭用 `disable-hooks`，查询用 `hook-status`；三者都修改同一个全局配置键。

## Run Modes and Defaults (Public Release)

**默认 = observe mode（仅观察）**：它不会硬拦截，也不会运行 shadow LLM judge。共享 memory upsert judge 使用独立的 `memory_upsert_enabled` 开关；该开关默认关闭，开启后即使处于 observe mode，也会在后台调用当前平台的 CLI judge，但不会阻塞用户回复。

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

### Rule injection (progressive full index — default)

Each time the user submits a message, `<skill_dir>/hooks/memory-retrieve-inject.sh` injects a **one-line index of the rules** saved under my memory dir as `additionalContext`, and I judge which apply. If the library exceeds the per-turn cap (default 50 — `progressive_max` in `~/.tellonce.config.json` or env `PT_PROGRESSIVE_MAX` (legacy `B5_` alias works), `0` = no cap), tier-1 rules are pinned when they fit under the cap (when tier-1 alone overflows it, the whole library rotates instead), the remaining rules rotate in across turns, and the block states how many of the total are shown. This is the default `progressive` backend: it just reads the saved rule files — no prompt matching, no model call, no CLI cold-start. The format looks like this — **it's not external noise, it's a rule hint from the skill infra and must be respected**:

```
### Your saved preferences — check each against this turn and apply the ones that fit:
- [fmt-pref-001] (tier1) use 4 spaces for indentation, not tabs
- [tool-pref-002] (tier2) prefer the project's own package manager / lockfile for installing dependencies | when: adding / upgrading dependencies
(These are your recorded preferences. Judge each rule against the current task; apply those that apply, skip those that do not.)
```

Each line carries the rule's `description` and (when present) a `when:` applicability hint. I **judge for myself** whether it holds for the current turn, and skip rules that don't apply (e.g. a preference about Go when the turn isn't touching Go).

> Legacy backends (`PT_RETRIEVE_BACKEND=cli` / `keyword`) instead inject only the rules matched for the current prompt, under a `### Fingerprint retrieval — ...` header. The judgement I apply is the same.

### Applicability gate (soft)

Each injected rule carries a `when:` hint (read from the memory .md frontmatter's `applies_when:` or `condition:`). I judge:
- the `when:` condition holds → apply rule
- it doesn't hold → explicitly say "gate filter out: <reason>" then skip
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

Two HARD checks are active (both block only under enforce mode, `PT_ENFORCE=1`):

1. **Staleness**: the observation log file must be appended within the staleness threshold at Stop (default 1800s, tunable via env `OBSERVATION_LOG_AGE_THRESHOLD_SEC`). With the default `OBSERVATION_LOG_AUTO_FALLBACK=1`, a stale/missing log is auto-healed with a synthetic full-schema entry and only a *failed* fallback blocks; set it to `0` for a strict block on staleness itself.
2. **Structured-entry quality** (checked on the newest entry): `detection.detected` must be a boolean; `trigger.user_message_excerpt` and `self_observations.uncertainty_notes` must be non-empty. When `detected=true`, additionally: `signal_type` ∈ preference/pitfall/friction, `detection.content` ≥ 30 chars, and `action.confirmation_text` non-empty.

**SOFT text-marker scans are DISABLED** (caused spurious blocks because my response text wording varies each turn). The structured log entry itself carries the scan result — that's sufficient audit trail.

**Practical rule for every turn**:
- Append **one full-schema entry** to `observations.jsonl` before stopping — detected=true or false both satisfy the gate, but the quality fields above must be filled either way. A skeleton entry with empty excerpt/uncertainty_notes gets blocked under enforce.
- Truncate any user-message excerpt to ~200 chars and never copy secrets/credentials into the log.
- No need to paste SCAN markers in response text.

**Why it matters**: the gate blocks on a genuinely missing log append or a hollowed-out entry (the schema-shortcut failure mode), not on response wording failing a text regex — this avoids spurious blocks while keeping the audit trail meaningful.

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
scope: global | project | task | unclear
scope_anchor: <project/task identifier; empty for global>
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

## Lifecycle and evidence

- Agent 侧只负责 enqueue 完整用户 turn；不得自行选择 atomic_id、直接写投影或输出 pre-write verdict。
- 后台 resolver 一次读取 active rules，选择
  `NOOP|UPDATE|SUPERSEDE|SPLIT|NEW|NEEDS_USER|REJECT|ARCHIVE|RESTORE`，再由 SQLite
  在一个事务中提交。
- `SPLIT` 的每个 child 独立解析 lifecycle 和 evidence；父节点不携带 record。
- `REJECT` 是危险 durable rule 的最终审计结果，不进入 clarification，也不创建 active rule。
- `ARCHIVE` 只在用户明确指向目标并要求停用时执行；历史版本仍保留在 SQLite。
- `RESTORE` 在用户明确要求时重新激活 archived rule，并由投影恢复 Markdown 与注入。
- 每个非 clarification mutation 都必须携带本轮用户原话的精确 evidence；store 在提交边界再次验证。

---

## Confirmation Strategy

当前回复只能报告同步可知的事实：

- enqueue 成功：只说“完整用户 turn 已入队，后台会解析并事务提交”。
- enqueue 失败：明确报告错误，不声称已记录。
- 只有 `memory_upsert.py inspect` 返回真实结果后，才能引用 atomic_id、revision 或最终 operation。
- `NEEDS_USER` 由后续 clarification 注入提出一个最小问题。
- `REJECT` 可报告拒绝原因，但不得表述为“等待用户批准”。
- `ARCHIVE` 只有在 committed/projected 后才能说规则已停用。
- 禁止根据 prompt、文件名或预期行为猜测 `NOOP|UPDATE|SUPERSEDE|SPLIT|NEW`。

---

## Forgetting / Deletion Handling

When the user expresses an intent to delete ("forget X" / "drop that rule" / "delete the record about X"):

1. Search memory for memories related to X
2. Show the matching results and let the user confirm which to delete
3. 用户明确指向目标并要求停用后，通过 `ARCHIVE` mutation 事务化停用规则；SQLite 保留版本与审计记录，投影和注入不再包含该规则。
4. 用户要求恢复时，通过 `RESTORE` mutation 重新激活 archived rule。
5. `ARCHIVE` 不是永久数据删除。共享核心不提供普通对话可调用的 `DELETE`；彻底 purge 只能走明确的卸载/清理命令并再次确认。
5. 禁止直接删除、重命名 Markdown 或改 `MEMORY.md`；它们只是投影。

---

## Health Check

A memory health check can be run periodically (or when the user requests it):

- Whether MEMORY.md's line count is approaching the 200-line limit
- Whether there are files that are superseded but still in the index
- Whether there are memories not referenced for a long time (judged by the updated date)
- Whether there are too many fragmented memories within the same domain that could be merged
- Whether there is a backlog of archived files

Show the report and let the user decide whether to clean up.
