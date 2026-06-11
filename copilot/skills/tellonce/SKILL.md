---
name: tellonce
description: "EVERY-MESSAGE enforcement: scan for preference/pitfall/friction signals, record to memory, log observations. Also handles memory audit/restructure. Use on EVERY user message — even simple ones, even during intensive technical work, even when you think there's nothing to detect. If you're not invoking this, you're skipping compliance."
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, AskUserQuestion
---

# Tellonce

## 运行模式与默认值（公开发布版）

**默认 = 观察模式（observe-only）**：本 skill 默认只做「扫描偏好 → 记录 → 告知用户」，**绝不硬阻断会话**，**绝不调用 LLM**。刚装上的新用户不会被任何硬规则拦截，也不会把对话内容发给第三方模型。

- **硬阻断强制**（deterministic block / pending gate / observation-log gate）默认**关**。
- **影子 LLM 判官**（把对话发给 `copilot -p` 做语义判分）默认**关**。隐私提示：开启后每轮会把「最后一条 user 消息 + 助手回复」（已脱敏 API key / 密码等）发给该模型。
- `seed_memory/` **出厂为空**（只有一个 README，不预装任何规则）；你的规则由 Gate Function 在你纠正/表达偏好时记录积累。

**切换模式只用一条命令**（不用记环境变量、不用手改 JSON）：

```
python <plugin>/lib/pt_mode.py enforce     # 开硬拦截
python <plugin>/lib/pt_mode.py full        # 硬拦截 + AI 判官
python <plugin>/lib/pt_mode.py observe      # 回到安全默认
python <plugin>/lib/pt_mode.py status       # 看当前模式
python <plugin>/lib/dashboard.py            # 一眼看状态（模式/注册/规则数/记录数）
```

> 安装时也能直接选：`install.ps1 -Mode enforce`（或 `--mode enforce` for bash）。下文 Infrastructure / Gate 等章节描述的是**强制模式开启后**的行为。

> **Copilot 平台提示（注入时机）**：在 Copilot CLI 上，已记录的偏好只能在**会话开始时（SessionStart）**注入上下文。如果你在会话**进行中**新记了一条偏好，它会**立即存盘**，但要等**下次会话**才会被重新端到 agent 面前（Copilot 的 UserPromptSubmit / PreToolUse hook 不处理 stdout，无法注入 context —— 属平台限制，非本工具 bug）。想让新偏好立刻生效，开一个新会话即可。Claude Code / Codex 变体无此限制（每轮都重新注入）。

## Infrastructure

这个 skill 不止是 Iron Law + Gate Function. 装了 3 层基础设施, 以**自动 hook** 形式运行, 不需要显式 Skill 调用就生效.

> **路径占位说明**: 下文出现的 `<skill_dir>` 默认是 `<skill_dir>/`，`<project_root>` 是当前项目根，`<state_dir>` 是 `<project_root>/.copilot/tellonce-state/`。所有路径在运行时由 `lib/path_config.py` 解析（env > `~/.tellonce.config.json` > 自动 detect 三层兜底），SKILL.md 不写绝对路径以免污染 Claude 输出。

### Deterministic fingerprint retrieval (SessionStart hook)

Copilot 变体**没有** per-prompt 的 `memory-retrieve-inject.sh` hook（平台限制，见上方注入时机提示）。规则注入发生在**会话开始时**：`<plugin_root>/hooks/session-start-inject.sh` 会:
1. 读 `<skill_dir>/lib/fingerprints.yaml`（**出厂为空**；你自己的规则放 gitignored 的 `fingerprints.user.yaml` overlay，两者加载时合并）+ memory 规则
2. 把 critical/high 规则作 `additionalContext` 注入到本会话 context 开头
3. 格式长这样 — **不是外部 noise, 是 skill infra 的 rule 提示, 必须 respect**:

```
### Fingerprint retrieval — memory rules auto-matched for this turn:
- **[fmt-pref-001]** (critical) 缩进用 4 空格, 不用 tab
    • triggered by: indent
- **[tool-pref-002]** (critical) 装依赖优先用项目自带的包管理器 / lockfile
    • action: 新增依赖走 lockfile, 不手动改版本号
    • applies_when: 新增 / 升级依赖时;
```

每条带 `applies_when` 字段的 — 这是 applicability gate. 我要**自己判断** applies_when 是否对当前 turn 成立, 不成立就 skip (例如 user 只是顺带提到某个触发词, 但当前 turn 并不真的进入该 rule 的适用场景, 就不强制应用).

### Applicability gate (soft, within the session-start injection)

session-start 注入的每条 rule 后面带 `applies_when: ...` 和 `condition: ...` 从 memory .md frontmatter 里读出来. 我判断:
- applies_when 条件成立 → apply rule
- 条件不成立 → 明说"gate filter out: <reason>"然后 skip
- 不明 → 保守 apply

### Log-only compliance tracker (Stop hook)

每轮我 stop 时, `<plugin_root>/hooks/memory-verify-compliance.sh` 读 transcript 取最后 assistant text, 往 `<state_dir>/obs_log/compliance_log.jsonl` append 一行:
- `response_excerpt` (前 400 字)
- `fp_rules_in_response` (response 里触发了哪些 rule 关键词)
- `lang_ratio.chinese_ratio` (中英文比例)

**不 blocking** (还不自动 retry). 后续若 FP rate 足够低, 可再考虑开启 blocking.

### Infrastructure 文件清单

| 角色 | 路径 (placeholder; runtime 用 path_config 解析) |
|------|------|
| Fingerprint 规则库 | `<skill_dir>/lib/fingerprints.yaml` |
| Retrieve handler | `<skill_dir>/lib/retrieve_inject.py` |
| Compliance tracker | `<skill_dir>/lib/verify_compliance.py` |
| SessionStart hook（规则注入） | `<plugin_root>/hooks/session-start-inject.sh` |
| Stop hook 链（依次执行） | `check-observation-log.sh` → `memory-deterministic-block.sh` → `memory-verify-compliance.sh` → `memory-shadow-judge.sh` → `memory-pending-promote.sh`（均在 `<plugin_root>/hooks/`） |
| Compliance log | `<state_dir>/obs_log/compliance_log.jsonl` |
| Hooks 注册 | `<plugin_root>/hooks/hooks.json` |

> **要看你机器上真实 path**: 跑 `python3 <skill_dir>/lib/path_config.py` 会打印当前 detect 出来的所有 path. 不要根据这份 SKILL.md 的占位字面量去写 / 创建文件 — 用 path_config 给的 runtime 值.

### 规则新增 / 更新时要同步动

当 Gate Function record 了新 rule 到 memory, 如果是**高价值 deterministic rule** (例如用户明说 "以后缩进都用 4 空格" / "commit message 用祈使句"), 要同时在 `fingerprints.yaml` 加一条 keyword trigger, 下 session retrieve 才能自动召回.

**不需要 FP 的**: semantic / 场景依赖 / meta rule (如「继承来的 plan 不能全信」这类靠 model 判断, 不靠关键词的规则).

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
- "NOOP / UPDATE doesn't need confirmation_text" → 错; detected=true 路径必填 confirmation (见 `## 确认策略`)

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
   - Including first-person value statements ("我喜欢/我希望/我觉得 X 好"), normative claims ("X 应该/必须/最好是 Y"), comparative preferences ("X 比 Y 好")
   - Regardless of whether it's phrased as instruction, reason, complaint, or aside

2. **Any clause expressing frustration or correcting your behavior** → friction or pitfall
   - Frustration markers: repetition ("又"/"还是" / "again"/"still"), exasperation ("怎么还" / "why is it still…"), rhetorical questions ("是不是说过了" / "didn't I already say…"), sarcasm ("算了" / "never mind")
   - Even if softened ("其实"/"没事"/"算了" / "actually"/"it's fine"/"never mind"), the softener often masks a real signal

3. **Any reason/justification clause in the message** → scan independently
   - User structure `[instruction] + [reason]` — the reason often states WHY they have this preference, which IS the preference content
   - Markers: 因为/主要是/我想/我希望/因此/所以/这样 — or English: because / mainly / I want / I'd like / so that

4. **Any meta-question about your behavior** → friction (you did something they want reconsidered)
   - "这个算 X 吗" / "你觉得 Y 怎样" / "为啥你这样做" / "这是不是 Z" (or "is this X?" / "what do you think of Y?" / "why did you do it this way?") — user is questioning your choice, not asking opinion

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
| "那你要不要看看别的" / "shouldn't you check elsewhere?" | Question | **friction**: you should have done this already | Follows your mistake |
| "又..." / "还是..." / "怎么还..." (or "again…" / "still…" / "why is it still…") | Frustration | **pitfall**: same error repeated | 2nd+ occurrence |
| "我之前是不是说过了" / "didn't I already tell you?" | Rhetorical question | **friction**: rule exists but wasn't followed | References memory |
| "对"/"yes" + correction | Partial agreement | **preference**: strengthening existing rule | Subtle redirect |
| Accepts unusual approach silently | No pushback | **preference**: validated judgment call | Absence of correction |
| **"因为...我想..." / "主要是...我希望..."** (or "because… I want…") | Justification for request | **preference**: the "because" clause states the rule itself | Rationalization clauses often contain the preference, not just context |
| **"verify一下/验证一下，因为我想..."** (or "verify it, because I want to…") | Task instruction | **preference**: preferred mode of answering (empirical > theoretical) | The "because" clause reveals a working-style preference, separate from the task |
| **"我觉得有点算/这个算不算X"** (or "does this count as X?") | Meta-question about classification | **friction**: you misclassified something last turn | User is correcting your signal detection, not asking opinion |
| **"我不是很懂...你自己尝试"** (or "I don't really get this, you try it yourself") | Delegation | **preference**: grants autonomy for unfamiliar domain | User trusts you to experiment; don't ask follow-up Qs, just do |

**Default**: When in doubt, detect. User saying "no" costs 1 second. Missing a signal costs it forever.

### Rationalization-clause pattern

When user structures message as `[instruction] + [因为/主要是/reason]`, the **reason clause frequently states a preference** separate from the instruction:

- ❌ Wrong: treat "reason" as mere context, ignore it
- ✅ Right: scan "reason" clause independently for preference content

Examples:
- `"先用 SQLite，因为我想本地快速验证一下"` → task: use SQLite + preference: 倾向先本地快速验证再上重型方案
- `"这次先不加测试了，主要是想先把接口定下来"` → task: skip tests for now + preference: 接口设计优先于测试的阶段性偏好
- `"这个不用 agent，你自己做，我想看你怎么处理"` → task: inline + preference: 用户想看你的推理过程, 别外包给 agent

---

## 概述

本 skill 有三个职责：
1. **每条消息强制执行**（Gate Function）：扫描信号 → 记录 → 存 memory
2. **初始化/审计模式**（用户调用时）：审计整个 memory 结构，迁移到结构化格式
3. **手动管理**：处理复杂冲突、批量整理、删除操作、大规模重组

---

## 信号类型定义

### preference（偏好）
用户明确表达希望如何做某事。面向未来的行为指导。

示例：
- "函数名用 camelCase，常量用 UPPER_SNAKE"
- "PR 描述要写清动机和测试方式"
- "提交前先跑一遍 lint 和单测"

### pitfall（陷阱）
反复出现的技术坑/错误模式。"别再犯"类。通常来自用户纠正或反复踩坑。

示例：
- "嵌套 ``` 会导致 markdown 结构乱，要用 4+ 反引号"
- "忘了 await 异步调用会静默吞掉错误"
- "某两个依赖版本互不兼容，升级前要查 changelog"

### friction（摩擦）
工作流中的持续痛点。不一定有解法，但需要意识到。

示例：
- "每次换窗口都要重新解释 context"
- "memory 粒度不对齐"
- "大批量 API 调用 rate limit 导致中断"

### 保留原有类型
原有的 `user`, `project`, `reference` 类型继续使用，定义不变。
`feedback` 类型在新记忆中不再使用，逐步迁移到 `preference` 或 `pitfall`。

---

## Memory 文件格式

存储位置 (path_config-driven): 默认 `<project_root>/.copilot/tellonce/memory/`（项目本地）。迁移兜底: 若该目录没有任何 .md 文件而旧的 Claude Code 路径 `~/.claude/projects/<cwd_escaped>/memory` 有内容，则继续使用旧路径（`<cwd_escaped>` = cwd 把 `/` 换成 `-`；见 `lib/pt_platform.py:default_memory_dir`）。真实路径跑 `python3 <skill_dir>/lib/path_config.py` 看 `memory_dir` 字段，或读 `<skill_dir>/lib/path_config.py:get_memory_dir()`.

### Frontmatter 规范

```yaml
---
name: <简短名称>
description: <一行描述，用于未来判断相关性，要具体>
type: preference | pitfall | friction | user | project | reference
domain: formatting | language | workflow | coding | tools | experiment | writing | communication | other
scope: global | project:<project_name>
condition: "<可选，适用条件，如 when writing shell scripts>"
confidence: high | medium | low
atomic_id: <domain_abbrev>-<type_abbrev>-<3位序号>
supersedes: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

### 缩写映射

**type:**
| 全称 | 缩写 |
|------|------|
| preference | pref |
| pitfall | pit |
| friction | fric |
| user | usr |
| project | proj |
| reference | ref |

**domain:**
| 全称 | 缩写 |
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

### 文件命名规范

`<type_abbrev>_<descriptive_name>.md`

示例：
- `pref_indent_style.md`
- `pit_md_nested_codeblock.md`
- `fric_cross_session_memory.md`
- `usr_role.md`
- `proj_repo_layout.md`
- `ref_api_pagination.md`

### Body 结构

```markdown
<核心内容：这条记忆说的是什么>

**Why:** <为什么要记住这个——原因、背景、触发事件>

**How to apply:** <在什么情况下、如何应用这条记忆>
```

---

## MEMORY.md 索引格式

按 domain 分组，每条 < 150 字符。domain 内按 type 排列（preference → pitfall → friction → 其他）。

```markdown
# Memory

## Formatting
- [fmt-pref-001](pref_indent_style.md) — 缩进用4空格不用tab
- [fmt-pit-001](pit_md_nested_codeblock.md) — 嵌套```用4+反引号包裹

## Language
- [lang-pref-001](pref_reply_language.md) — 回复 / 交付物的语言偏好（示例）

## Workflow
- [wf-pref-001](pref_branch_workflow.md) — 某项工作流偏好（示例）
- [wf-fric-001](fric_context_handoff.md) — 某个反复出现的摩擦点（示例）

## Experiment
...

## Project
...

## Reference
...
```

MEMORY.md 不超过 200 行。如果接近上限，合并同 domain 的细粒度记忆。

---

## Memory 整理触发机制

### 自动触发：每新增 10 条

在 Gate Function 的 RECORD 步骤后，检查当前 memory 文件总数。如果自上次整理以来新增了 10 条以上：

1. 统计 memory/ 下非 archived 的 .md 文件数（排除 MEMORY.md）
2. 对比 MEMORY.md 索引里的条目数
3. 如果差值 ≥ 10：对**最新 10 条**做快速整理（检查分类、去重、更新 MEMORY.md 索引）
4. 不动旧的——只排最新的

```bash
# 快速检查：文件数 vs 索引数
FILE_COUNT=$(ls memory/*.md | grep -v MEMORY | grep -v _archived | wc -l)
INDEX_COUNT=$(grep -c '^\- \[' memory/MEMORY.md)
DIFF=$((FILE_COUNT - INDEX_COUNT))
# DIFF >= 10 → 触发快速整理
```

### 手动触发：完整从头重排

用户调用 `/tellonce` 或说"整理 memory"时执行。

**关键：完整重排不基于旧分类。** 因为随着 memory 积累，domain 分类可能变化（比如之前分在 workflow 的现在更适合分在 experiment）。必须从头看每条 memory 的内容重新分类，不能只在旧索引上修补。

### Step 1: 全量审计（从零开始）

读取 memory/ 下所有 .md 文件和 MEMORY.md，对每个文件检查：

| 检查项 | 说明 |
|--------|------|
| frontmatter 完整性 | 是否有所有必要字段 |
| type 准确性 | feedback → 应该是 preference 还是 pitfall？ |
| domain 分类 | 是否缺少 domain 字段 |
| atomic_id | 是否有唯一标识 |
| 文件命名 | 是否符合 `<type_abbrev>_<name>.md` 规范 |
| 内容重复 | 跨文件是否有语义重复 |
| body 结构 | 是否有 Why + How to apply |
| scope | 是否区分了 global 和 project-specific |

### Step 2: 生成审计报告

以表格形式向用户展示：

```
📋 Memory 审计报告

文件总数：N
符合新规范：X
需要迁移：Y

需要变更的文件：
| 文件 | 当前状态 | 建议操作 |
|------|----------|----------|
| feedback_md_formatting.md | type=feedback, 无atomic_id | → type=pitfall, 重命名为 pit_md_nested_codeblock.md |
| feedback_language_preference.md | type=feedback, 无domain | → type=preference, domain=language |
| ... | ... | ... |

疑似重复/可合并：
| 文件A | 文件B | 关系 |
|-------|-------|------|
| ... | ... | 语义重复 / 可合并 |

建议的 MEMORY.md 新结构：
（展示重组后的索引预览）
```

### Step 3: 用户确认

- 逐组展示变更（按 domain 分组，不要一个一个文件问）
- 用户可以：全部接受 / 逐组确认 / 修改某些建议
- **必须等用户确认后才执行写入**

### Step 4: 执行迁移

1. 更新每个文件的 frontmatter
2. 重命名文件（如需要）
3. 重建 MEMORY.md 索引
4. 展示最终结果

---

## 冲突解决算法

写入新记忆时执行：

```
1. 确定新记忆的 domain 和 type
2. 读取该 domain 下所有现有记忆文件
3. 对每条现有记忆，判断语义关系：
   a) 比较 description 和 body 内容
   b) 判断关系类型：

      无关（完全不同的事）        → 继续检查下一条
      相同（说的是同一件事，内容一致）→ NOOP：不写入，告知用户"已有此记录"
      互补（同一主题，新内容是补充） → UPDATE：合并新内容到现有文件
      矛盾（同一主题，结论相反）    → SUPERSEDE：新建文件，旧文件的 atomic_id 加入 supersedes

4. 如果关系不明确（介于互补和矛盾之间）：
   → 展示两条记忆给用户，让用户决定：合并 / 替代 / 独立保留

5. 所有操作结果都更新 MEMORY.md 索引
```

### ⚠ Pre-write verification checklist

> **不与 §Gate mechanics 矛盾, 是分层**: §Gate mechanics 把 SCAN 的 text-marker 关掉了, 因为 SCAN 每 turn 都跑 + wording 漂移 → 误报多. memory-write 是低频高风险事件 (~1-3 次/session, 不漂移), 这里**重新启用** text-marker, 仅限 memory-write 场景. SCAN gate 仍 only-HARD-check the structured log; Pre-write 是 memory-write 上的额外 layer.

写 memory 文件 (Write/Edit 任何 `memory/*.md` 新文件 / 改 atomic_id) **前**, 在 response 里说明你**检查了哪些现有记忆**以及**做了什么决策** (NOOP / UPDATE / SUPERSEDE / NEW + 一句话理由). 措辞和语言不限; 下面是一种推荐示例格式 (开启强制模式时, 可选的 Stop-hook 会识别这个格式):

```
**I checked**: memory/<domain>/*.md, candidates considered = [<atomic_id_1>, <atomic_id_2>, ...]
**Decision**: NOOP | UPDATE existing <atomic_id> | SUPERSEDE existing <atomic_id> | NEW — because <one-sentence reason>
```

**为啥**: advisory rule alone 不够 — agent 在密集写入时容易 short-circuit conflict resolution. 显式写出"检查了什么 + 决策" = forcing function: 确保每次 memory write 前真的走了 dedup / conflict 判断.

**适用 (applies_when)**:
- 准备 Write 一个 `memory/*.md` 新文件
- 准备 Edit 一个 `memory/*.md` 文件的 `atomic_id` 字段
- 在 confirmation_text 里 promise "saving memory" / "存进 memory" / "记录偏好"
- response 里出现触发词 "新原则" / "保存这个" / "记录偏好" / "存进 memory" → 即便没真 invoke Write tool, 也要走

**不适用 (does_not_apply_when)** — explicit allowlist (非 denylist):
- Read-only 操作 (Read / Grep / Bash 查 memory)
- 修 memory 文件的 typo / 修 `created`、`updated` 日期 / 加 `superseded_by` 标记 / 修 description 措辞 (不动 atomic_id 也不动 supersedes)
- MEMORY.md 索引 entry 增删 (这是 derived 操作, 不创建 atomic_id)

**例外捷径 (legitimate skip)**:
1. **多步 audit 显式预声明**: 仅当本 turn 早期已显式列出的 candidates list **覆盖当前要写的 atomic_id** — list 里 explicitly 列出 "X-pref-NNN: NEW because Y" 这条. 否则**每个新文件都要单独走一遍**. 模糊"我之前 audit 过"不算 override.
2. **User 显式 explicit-disable 措辞**: User 明确说 "不用 check" / "直接存别 verify" / "跳过 conflict resolution" 这种 explicit-disable. 隐式 OK ("存吧" / "go ahead" / "记下来" / "save it") **不算 override**, 仍要走 checklist.

**Stop hook 校验** (可选, 默认 advisory):

当前 stop hook (`memory-verify-compliance.sh`) 在 turn 末扫 transcript. 若该 turn 写了 `memory/*.md` 但 response 文本里没出现 Pre-write 双行 → log warning into `compliance_log.jsonl` (advisory, 不 block). 收 1 周数据后决定是否升 blocking exit-2.

**可选 Stop-hook 的 regex** (仅当开启强制模式时, hook 用它识别上述示例格式):

```regex
^\*\*I checked\*\*:.*candidates considered = \[.*\]$
^\*\*Decision\*\*: (NOOP|UPDATE|SUPERSEDE|NEW)\b.*— because .+$
```

两行必须成**连续 pair** (相邻或仅隔 1 空行) 才算一次有效 verdict. 若 turn 内出现 ≥2 对匹配, 取最后一对作 verdict (handoff/explain 文本里的 quoting 不算). False-positive 防御: handoff/skill-content/code-review-paste 等 quoting 场景写不出 "**I checked**: ... candidates = [actual_atomic_ids_with_concrete_reason]" 这种 concrete pair, 仅靠 regex + concrete-id 结构区分.

### SUPERSEDE 协议

当新记忆替代旧记忆时：
1. 新文件的 `supersedes` 字段列出被替代的 atomic_id
2. 旧文件**不删除**，但在其 frontmatter 中添加 `superseded_by: <新atomic_id>`
3. MEMORY.md 索引中只保留新文件，旧文件从索引移除（但文件保留以供追溯）

---

## 确认策略

### 高置信（用户明确说出 + 表述清晰 + scope 明确）
用一句话告诉用户你记录了哪条偏好, 并邀请纠正 (措辞 / 语言自定). 例如:
> 已记录偏好 [fmt-pref-002]: <一句话内容>. 如有误请指出. (Recorded preference [fmt-pref-002]: …; let me know if it's wrong.)

### 中置信（表述较清晰但 scope 或持久性不明确）
简短询问：
> 检测到偏好：输出要简洁。这是所有场景都适用，还是只针对当前任务？

### 低置信（可能是偏好也可能是一次性指令）
详细询问：
> 你提到"这个太长了"——要记录为长期偏好（以后回复都简短），还是只是这次的指令？

### 静默模式
如果用户说过"别问了直接记" / "不用确认"：
- 记录此 meta-preference
- 之后写入静默执行
- 仅在 SUPERSEDE（替代旧记忆）时通知用户
- 用户随时可以说"恢复确认"来重新开启

### ⚠ 关键: detected=true 时 confirmation_text 永远不能空 (含 NOOP/UPDATE)

**Stop hook 硬检查**: `detection.detected=true AND action.confirmation_text 空 → 阻塞 stop`. 这条独立于 conflict_resolution (NOOP / UPDATE / SUPERSEDE / NEW) — 即使决定不写新文件 (NOOP) 或只更新现有文件 (UPDATE), `confirmation_text` 字段必须填非空字符串告诉用户你扫到了什么.

**易踩陷阱**: 误把 "NOOP = 不写新 memory" 等价于 "silent = 不需 confirm". 这是错的. NOOP 表示**memory 层不写**, 但 user-facing **CONFIRM 层仍要走**.

**每个 conflict_resolution 的 confirmation_text 要传达的内容**（措辞 / 语言不限，下面的句子仅为示例）:

| Resolution | confirmation_text 应传达的内容（示例措辞） |
|------------|------------------------------------------------------|
| **NEW**    | 记录了哪条新偏好 + atomic_id，并邀请纠正。例: `Recorded preference [<atomic_id>]: <one line>. Let me know if that's wrong.` |
| **UPDATE** | 更新了哪条已有偏好 + 加入的增量，原 rule 保留。例: `Updated [<atomic_id>] with <delta>; original rule kept.` |
| **SUPERSEDE** | 与哪条旧偏好冲突、新建哪条替代、旧文件标 superseded_by。例: `Conflicts with [<old id>]; created [<new id>] to supersede it.` |
| **NOOP**   | 扫到了什么偏好、已被哪条已存在 atomic_id cover、不重写。例: `Detected "<content>" — already covered by [<existing id>], no new file.` |

**例外**: 仅当用户明示**全局静默模式**且本次 detected=false 时, confirmation_text 可空. 任何 detected=true 路径都必须填.

**模板里的 `<atomic_id>` 怎么填**: 必须是 conflict-resolution algorithm 找到的真实 ID (grep `memory/MEMORY.md` 索引或 `memory/*.md` 文件). 如果 hook 触发了 NOOP/UPDATE 模板提示但你已经记不起当时匹配的 atomic_id, **重新跑 grep memory** 而不是猜 — 猜出错的 ID 会误导 user 以为某条规则存在.

---

## 遗忘处理

当用户表达删除意图（"忘掉 X" / "那个规则不要了" / "删除关于 X 的记录"）：

1. 搜索 memory 中与 X 相关的记忆
2. 展示匹配结果，让用户确认要删哪些
3. 确认后：
   - 从 MEMORY.md 索引中移除
   - 文件重命名为 `_archived_<原文件名>.md`（不硬删，留追溯）
   - 或如果用户说"彻底删除"，则真正删除文件

---

## 健康检查

可以定期（或用户要求时）执行 memory 健康检查：

- MEMORY.md 行数是否接近 200 行上限
- 是否有 superseded 但仍在索引中的文件
- 是否有长期未被引用的记忆（通过 updated 日期判断）
- 同 domain 下是否有过多碎片化记忆可以合并
- 是否有 archived 文件积压

展示报告，用户决定是否清理。
