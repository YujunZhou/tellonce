# 全面审核报告 — 2026-06-05（公开发布准备）

> 范围：对 preference-tracker 三个变体（Claude Code 根 / Codex / Copilot）做最全面审核。
> 方法：每个审核维度派 1 个 Opus 4.8 subagent 深读代码（Wave 1：功能对等 / 边界情况 / bug），
> 我据此修复 Copilot 变体，再派 3 个 Opus 4.8 subagent（code-review / rubber-duck / 独立执行验证）复审我的修复（Wave 2）。
> 维护：本文件是审核快照；改完一项在清单打 `[x]`，不删行。所有记录放项目主目录（按你偏好）。

---

## 0. 一句话总结

公开发布的**默认是安全的**（observe-only：不拦截、不调 LLM）。今晚修掉了一批真 bug（含两个会让"硬拦截"在 Windows/全平台静默失效的契约 bug、一个 shadow 模式 fork-bomb 风险），补齐了 Copilot 缺失的 doctor/uninstall + 首次加了 12 个自动化测试。**还剩 3 个必须在真实 Copilot 上验证的 runtime 假设**（见 §4 / handoff_v2），和 3 个需要你拍板的**架构级议题**（memory 规模化，见 §3）。

---

## 1. 三变体功能对等（Claude=参考）

| 能力 | Claude(根) | Codex | Copilot | 备注 |
|------|:--:|:--:|:--:|------|
| Deterministic 硬阻断（3 规则） | ✅ | 🟡 缺第3条 | ✅ | Copilot 今晚修了 exit-code |
| Shadow LLM judge | ✅ | ❌ | ✅ | Codex 硬编码 DISABLED |
| Cost cap / Streak bypass | ✅ | ❌ | ✅ | |
| Fingerprint 检索 + 每轮注入 | ✅ | 🟡 空壳 | 🟡 退化为 SessionStart | runtime 限制 |
| Pending-queue 跨 session | ✅ | 🟡 仅 dry-run | ✅ | |
| Observation/Iron-Law gate | ✅ | ❌ | 🟡 Windows no-op | |
| B4 拒停 gate | ✅ | ❌ | ✅ | 今晚修了 exit-code |
| Threshold advisor / auto-retire | ✅ | ❌ | ✅ | |
| Redaction | ✅ | ✅ | ✅+ | Copilot 额外送 LLM 前脱敏 |
| **doctor / dashboard / uninstall** | ✅ | ✅ | **doctor✅ uninstall✅(今晚补) / dashboard 仍缺** | |
| 测试 | ✅ 8 文件 | 🟡 23 | **✅ 12 smoke(今晚补)** | |
| observe-only 安全默认 + pt_mode 开关 | ❌ | 🟡 三档 | ✅ Copilot 独有 | 公开发布关键 |

**Copilot 剩余缺口**：dashboard（低优，可从 codex 移植）；Windows obs-gate 是 echo no-op（observe 默认下影响低）。
**Codex 缺口**（多为 by-design 取舍，非疏漏）：shadow judge / B4 / streak / 第3条规则 / cost cap 全无；`userpromptsubmit-retrieve-inject.sh` 引用了 codex 树里不存在的 `retrieve_inject.py`（静默失效，发布前要么补软链要么删 hook）。

---

## 2. 今晚已修的 bug（全部离线验证 + re-sync live + 12 smoke 通过）

| ID | 严重度 | 问题 | 修复 |
|----|------|------|------|
| H1 | HIGH | B4 verify_compliance 的 `exit 2` 在 bash wrapper 没归一化 → B4 拒停全平台退化为警告 | wrapper 改捕获 rc 归一化 + python 改用可配置 exit code |
| H2 | HIGH | deterministic_block 的 `exit 2` 在 Windows(powershell 直调) 无人归一化 → 硬拦截 Windows 静默失效 | `path_config.stop_block_exit_code()`（默认0，per PORT_DESIGN），Windows+bash 行为统一 |
| H3 | HIGH | shadow judge 的 `copilot -p` 无递归守卫 → 若子会话回灌 Stop 则 fork-bomb | 子进程 env 关 enforcement/shadow/inject + 递归守卫 + `PT_CHILD_SESSION=1` |
| H4 | HIGH | SessionStart 注入 JSON 形状与子模块不一致（顶层 vs hookSpecificOutput） | 统一成单一 `hookSpecificOutput`（rubber-duck 指出"both-keys 在严格 schema 下双雷管"）+ env 可切顶层 |
| M1 | MED | transcript_adapter 用 `readlines()` 全量载入（每个 Stop hook 每模块各跑一次） | 改 `deque(maxlen=2000)` 尾读；50k 行 transcript 实测 0.04s |
| M2 | MED | Codex 在 tool input 上跑语言规则 → 误拦合法英文文件写入 | tool input 只跑路径/bib 规则，丢 `lang-*` |
| M3 | MED | bash 短路只读 `.session_id` 不读 Copilot 的 `.sessionId` → 短路恒不触发 | jq 加 `// .sessionId` 兜底 |
| L1/L2/L4/L5 | LOW | 文件句柄泄漏 / config 字符串布尔被忽略 / pt_mode 非原子写 / 过期 docstring | 全部修 |

**Wave-2 加固**（采纳 rubber-duck）：统一 child-session 守卫（`PT_CHILD_SESSION` 让所有 Stop/SessionStart hook 在嵌套 `copilot -p` 子会话里早退，防止污染共享 queue/log）；install 脚本迁移老 config 的 `project_root`（防跨项目串数据）。

**新增**：`doctor.py`（只读自检，PASS/WARN/FAIL）、`uninstall.py`（默认 dry-run，`--purge-state/--purge-memory/--reset-config`）、`test_smoke.py`（12 测试，覆盖双 schema 解析 / exit-code 契约 / pt_mode / config 布尔 / child 守卫）。

---

## 3. 🔴 需要你拍板的架构议题（明天讨论 —— 你说的 memory 规模化问题，确认存在）

### 3.1 SCALE — 检索在 N≈40 条有"静默悬崖"
**结论**：context 不会膨胀（有硬上限，好），但代价是 **N>40 后召回静默丢失**。
- `RETRIEVE_HAIKU_RULE_LIMIT=40`：送给小模型语义匹配的候选规则只取前 40 条，**且截断前无相关性排序**（按 fingerprints.yaml 顺序 + `glob.glob` 文件系统顺序）。第 41 条起永久不可达，无论多相关。
- `MAX_SHOW=10`：每轮最多注入 10 条。
- Copilot 更窄：只在 SessionStart 注入 top-10 critical/high 的 fingerprints 规则，**memory-only 规则（没进 fingerprints.yaml 的）被显式跳过**。
- 失败不可见：截断默认不告警（只有 `B5_RETRIEVE_DEBUG=1` 才记）。

**候选解法（待讨论，今晚未动）**：
1. 截断前加**便宜的预排序**（关键词/嵌入打分选出最相关的 40 送 LLM）；或两段式检索（shortlist→语义重排）。
2. 让 keyword backend 也扫 memory-dir 规则（现在只扫 fingerprints.yaml）。
3. Copilot SessionStart 注入面扩到 memory-dir 规则的 critical/high frontmatter。
4. 注入时显示"共 N 条，本轮考虑 M 条"，让截断可见。
5. 给 memory 加 embedding 索引（最彻底，但引入依赖）。

### 3.2 HYGIENE — memory 维护全是"模型指令"，无代码强制
- 冲突解决/去重、"每 10 条整理"、MEMORY.md 200 行上限——**全是 SKILL.md 里给模型的文字指令，没有任何代码执行**。规模大时去重/supersede 判断正好在最需要时退化。
- `auto_retire_superseded.py` **没接进任何 hook**（其 docstring 自己说"手动跑"），且是 O(n²)（300 条 ≈ 9 万次 frontmatter 解析）。
- **候选解法**：把确定性的 retire/tidy 接进 Stop 链；加代码级重复-id 与近重复检测。

### 3.3 ATOMICID — 3 位序号模型分配，规模化会碰撞
- `atomic_id = <domain>-<type>-<3位序号>`，序号由**模型**扫现有文件取 max+1，无中央分配器。两个并发会话/一轮两次写 → 重复 id → 下游 last-write-wins 静默覆盖一条，并污染 supersede 图。3 位也只够 999/类。
- **候选解法**：代码级 id 分配器（扫全部 id 原子分配 max+1），或改 hash/uuid 后缀。

---

## 4. 真实 Copilot 验证结果（第二轮验证会话已全部跑完 A/B/C）

> **更新 2026-06-05 第二轮**：验证会话用"当前会话 introspect + copilot -p 子会话 + 比对 Copilot 运行日志 + 读 CLI bundle 源码"做了实测，三个假设全部确认。

### 🔴 头号根因（之前一直没发现）：plugin 从没被 Copilot 加载过 hook
- preference-tracker **不在 `~/.copilot/config.json` 的 `installedPlugins` 数组里**。Copilot 加载 plugin hooks 读的就是这个数组（每条带 cache_path）。我们之前是"手抄文件 + 改 settings.json"side-load，installer 只写插件自己的 config，**没注册进 Copilot 的 config**。
- 现象：子会话日志 `Loaded 1 hook(s) from 1 plugin(s)`（那 1 个是 superpowers），compliance_log 零写入——**不是 exit code、不是 launcher，是 hook 根本没被调用**。之前所有"离线 live 测试"都是直接管道喂脚本，绕过了加载层，所以没暴露。
- **已修**：①验证会话 live 把插件注册进了 config.json（你机器现在 `Loaded 7 hook(s) from 2 plugin(s)`，hook 在跑）；②我新增 `register_plugin.py`（幂等 upsert + 保留 JSONC 头注释 + 备份），并接进 `install.ps1`/`install.sh`（仅当从 installed-plugins 目录运行时自动注册）+ `uninstall.py --unregister`；③`doctor.py` 加了 `check_plugin_registration` 专门 catch 这个根因。

### A — Stop block：hook 侧全部实测通过
- 真被调用、输出 `{"decision":"block"}` 中文提示、命中 oth-pref-001、exit 0 正确、run.ps1 避开 WindowsApps stub 用 Miniconda python 跑通。**RC1/RC2 确认无误**。
- 唯一没在验证环境实测的：递交互 `-p`/stdin 是 one-shot（只记录 block 与 re-prompt）；但读 bundle 源码确认主 agent loop 在 `decision==="block"` 时 `enqueueUserMessage(reason)` 重新入队 = **真交互 TTY 会 re-prompt**。→ **只剩你 30s 手动收尾确认**（注册后新开会话 → PT enforce → 让 bash 脚本写 /tmp/mylog.log → 预期被拦 + [修正]）。

### B — SessionStart 注入字段：已确认是**顶层 additionalContext**（与我/rubber-duck 的猜测相反）
- 实测：默认 `hookSpecificOutput.additionalContext` → 子会话 `NO-INJECTION-FOUND`；顶层 `additionalContext` → 子会话能引用注入 + 列出 10 条 rule。bundle 的 SessionStart mapper 读 `c.additionalContext`（顶层）。
- **已改** `session_start_inject.py`（source + installed）**默认发顶层**，`PT_SESSIONSTART_HOOKSPECIFIC=1` 可切回 hookSpecificOutput（给 Claude 复用）。→ 跨 session 记忆注入在 Copilot 上**确认可用**。

### C — copilot -p 递归 / guard：已确认
- `copilot -p` **确实会 re-fire Stop+SessionStart**（递归风险真实）。`PT_CHILD_SESSION=1` guard 实测：6 个 hook 都被调用但 python 侧全部早退、零产出（无 block / 无注入 / 不 spawn 下层 judge）→ **fork-bomb 防住**。child guard 是必要的，不是多余。

---

## 5. 距离公开还差什么 + 怎样像两个模范 skill 那么好

参照 superpowers（方法论，极简 token 高效）和 awesome-ai-research-writing（低门槛、社会证明、复制即用）：

### 必做（发布前）
- [ ] **A 的 30s 手动收尾**（注册后新开会话实测 enforce 真拦，见 SESSION_HANDOFF_v2 §1）。B/C 已实测确认。
- [ ] **安装即注册**（本轮新发现的最大坑）：hook 只在插件被注册进 `~/.copilot/config.json` 时才加载。**已做成一键安装**：用户复制一条命令（`bootstrap.ps1`/`bootstrap.sh`，README 顶部）→ 自动下载+放进插件目录+装依赖+注册+设 observe+记录 python 路径。公开发布前唯一前置：**把仓库 commit + push 到 GitHub**（一键命令的 raw URL 才有内容）。post-install 链已端到端验证（幂等）。`doctor.py` 检测未注册。
- [ ] **SKILL.md 瘦身**：现在 600+ 行满是 `Phase B5`/日期/内部 ID。superpowers 的 `writing-skills` 硬规则：常驻加载的 skill <200 词、description 只写"何时用"不写流程、skill 不是流水账。把核心（Iron Law + Gate + 5 侦测原则）留 <200 词，其余拆 `reference/*.md` + `ARCHITECTURE.md` 按需读。
- [ ] **README 重写**：英文 quickstart + 一句话价值主张 + 隐私默认值 + 一张"会拦什么"图。现在满是内部黑话外人看不懂。
- [ ] Codex `userpromptsubmit-retrieve-inject.sh` 引用不存在的 lib → 补或删。

### 应做（提升采用）
- [ ] **一条命令安装**（marketplace），对齐 superpowers：`copilot plugin install`。
- [ ] **杀手级 demo GIF**：录"重复违反偏好被自动拦下并纠正"（见 MARKETING_HIGHLIGHTS.md）。
- [ ] dashboard 补齐（从 codex 移植 Python 版）。
- [ ] 扩测试（root 有 chaos 注入；copilot 现 12 smoke，可加并发/边界）。
- [ ] LICENSE / CONTRIBUTING / issue 模板 / CHANGELOG（两个模范都有，显得可信、可维护）。
- [ ] §3 的 memory-scale 至少做"截断可见"+"keyword 扫 memory-dir"两个低成本缓解。

### 定位（你已想到的卖点，见 MARKETING_HIGHLIGHTS.md）
即插即用 + preference 记录者（让 agent 不再重复犯错）+ 轻量"流程编译执行"（比 skill-creator 轻一个数量级）。最锋利差异点：**hook 级物理拦截 vs 静态提示词**，且**自动从对话抓取**而非手写规则。

---

## 6. 验证状态汇总

- 今晚所有代码修改：✅ 离线验证（compile + import + 行为测试）+ ✅ 12 smoke test 通过 + ✅ 3 Opus 复审无 blocker + ✅ re-sync live 插件。
- live 插件当前模式：observe（安全默认，已确认）。
- 仍需用户实测：§4 的 3 个 runtime 假设（写在 SESSION_HANDOFF_v2.md）。
- git：改动**未 commit**，等你 review。
