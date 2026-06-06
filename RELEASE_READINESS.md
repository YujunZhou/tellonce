# 公开发布前修复清单（Release Readiness）

> 状态：**P0 安全项已基本修完**，剩 P0-3（需真实 Copilot 实测）+ P1/P2。
> 创建：2026-06-04 ｜ 维护方式：改完一条就在前面打 `[x]`，不要删行（留追溯）。
> 范围：主要针对 `copilot/`（公开发布目标）；`codex/` 与根目录 Claude 版仅在被波及时标注。

---

## ✅ 本 session 已修复（2026-06-04，改动未提交 git，待 review diff）

核心决策：**公开版默认 = 观察模式（observe-only）**——只扫描偏好 + 记录 + 提醒，**绝不硬阻断、绝不调用 LLM**。作者做研究时 `set PT_ENFORCE=1 PT_SHADOW=1` 一键恢复完整强制。

| 修复 | 改动文件 | 验证 |
|------|---------|------|
| 新增 `enforcement_enabled()` / `shadow_enabled()` 主开关（env > config > 默认 False） | `lib/path_config.py` | ✅ 离线：默认 False/False，`PT_ENFORCE=1`/`PT_SHADOW=1` → True |
| deterministic 硬阻断默认关（含作者个人语言规则） | `lib/deterministic_block.py` | ✅ 离线：默认不拦；`PT_ENFORCE=1` → exit 2 + JSON |
| B4 pending gate 默认关 | `lib/verify_compliance.py` | ✅ 编译 + gate 条件加 `enforcement_enabled()` |
| 影子 LLM 判官默认关 | `lib/verify_retry_shadow.py` | ✅ 默认 disabled，`PT_SHADOW=1` 才跑 |
| 影子判官发送前脱敏 | `lib/verify_retry_shadow.py` | ✅ 离线：API/AWS key、password 被 `[REDACTED]` |
| observation-log gate 默认不阻断（仅 `PT_ENFORCE` 时阻断） | `hooks/check-observation-log.sh` | bash 逻辑改，调 `enforcement_enabled()` |
| **Windows UTF-8 stdout 修复（新发现的真 BLOCKER，见 P0-5）** | `lib/path_config.py`、`lib/session_start_inject.py` | ✅ 离线：修复前 block 在 Windows 静默失败，修复后 exit 2 + 合法 JSON |
| 移除 install 写全局 `project_root`（跨项目污染） | `install.ps1` | ✅ parse OK |
| 默认值 + 隐私披露 + 如何开强制 | `skills/.../SKILL.md` 顶部 banner、`install.ps1` 结尾 | — |
| **一键切换工具 `pt_mode.py`**（observe/enforce/full/status，合并写 config、保留其他 key） | `lib/pt_mode.py`（新） | ✅ 离线：四种模式正确翻转 + round-trip 进 enforcement_enabled/shadow_enabled + 保留其他 key |
| **安装时直接选模式 + 两条安装命令**（`-Mode`/`--mode` observe\|enforce\|full，默认 observe；自动写开关、打印一键切换命令） | `install.ps1`、`install.sh`（也移除了 install.sh 的 project_root） | ✅ ps1 parse OK、sh LF bash -n OK |
| **Copilot 快速上手（两条安装命令：安全版/强制版）** | `copilot/README.md`（新） | — |
| **config UTF-8 BOM BLOCKER 修复**（实机测试发现）：3 处读取改 `utf-8-sig`（path_config / pt_mode / retrieve_inject）+ install.ps1 写 config 改无 BOM；并修复了 live config（剥 BOM + 移除残留 project_root，保留 retrieve key） | `lib/path_config.py`、`lib/pt_mode.py`、`lib/retrieve_inject.py`、`install.ps1` | ✅ 离线：带 BOM config 正确读出 key + pt_mode 保留 key + enforcement_enabled 生效；live config 已重存无 BOM 并 re-sync |
| **Copilot hook 契约适配 BLOCKER 修复**（第二轮实机测试发现的根因）：新增 `lib/transcript_adapter.py` 统一 stdin 字段（camelCase/snake_case）+ 两种 transcript schema（Claude `assistant`/`message.content[]` 与 Copilot `assistant.message`/`data.content`）；3 个 Stop 模块改用 adapter；/tmp 规则增扫 tool 命令；bash hook 加 camelCase 兜底 | `lib/transcript_adapter.py`(新)、`deterministic_block.py`、`verify_compliance.py`、`verify_retry_shadow.py`、`hooks/check-observation-log.sh` | ✅ 离线：Claude/Copilot 两格式提取+触发规则一致、english-bypass 正确；**live 插件**对真实 Copilot transcript+camelCase stdin 在 enforce 下 exit2+block JSON（修复前是 no-op）|

---

## 第 0 节 · 几个概念澄清（你问的）

### 0.1 影子 LLM judge 是什么
`copilot/lib/verify_retry_shadow.py`。它是跑在 **Stop hook** 上的**第二道、基于 LLM 的语义合规检查**，与正则硬阻断（`deterministic_block.py`）并行：

- 判同样那 3 条 enforce 规则（`lang-pit-130` / `oth-pref-001` / `lang-pref-001`），但用 LLM 语义判分，抓正则漏掉的违规。
- **永远 `exit 0`，从不阻断**（所以叫"影子"）。它只做三件事：写 `b5_shadow_log.jsonl` 历史、更新滚动告警 `B5_SHADOW_ALERT.md`（最近 3 条、24h TTL）、写 `compliance_log.jsonl`。
- 那个告警会在**下一个 session 开始**时被注入成"上轮违反了 X"提示。
- 设计意图：先用影子模式收集"语义判官准不准"的数据，准了再升级成会阻断的正式 gate。
- **默认是开的**（`B5_SHADOW_DISABLED` 默认关；`ANTHROPIC_CREDIT_OK` 默认 `'1'`，见 `verify_retry_shadow.py:56-57`）。走 `copilot -p` + `claude-haiku-4-5`，日成本上限 $0.50。
- ⚠️ 隐私问题见 P0-4：它把 user 消息 + 回复发给 LLM，且默认开、披露不足。

### 0.2 "选哪条规则需要 LLM" 现在怎么处理
这指的是 **retrieve（召回）步骤**：`copilot/lib/retrieve_inject.py`，决定把哪几条 memory 规则注入进当前上下文。

- `B5_RETRIEVE_BACKEND` 默认 = **`cli`**（Round-10 起）：用小模型（copilot/claude haiku）**语义匹配** user prompt 对 memory 规则，挑相关的 atomic_id。1-2s 延迟，走订阅额度、不要 API key、不用维护关键词。
- fallback = `keyword`：老的 `fingerprints.yaml` 字面/正则匹配，免费即时，但只取决于关键词写得全不全。
- CLI 调用失败 → 静默退回 keyword。

> ⚠️ **Copilot 版的关键退化**：Copilot CLI 不支持每条 prompt 注入（`userPromptSubmitted` 的 stdout 被忽略）。所以召回被挪到了 **SessionStart 只跑一次**（`session_start_inject.py:99-110`），而且 `--session-start` 模式是**按优先级注入 top critical/high 规则，不做 LLM 语义匹配**。
> **结论**：在你要发布的 Copilot 平台上，"每条 prompt 用 LLM 选规则"其实**没在跑**——只是 session 开始时按优先级 dump 一次。这是已知退化（`PORT_DESIGN.md` 第 7 节），但 README/SKILL 没讲清，用户会误以为有语义召回。

### 0.3 我必须每条消息加载吗？618 行是 memory 吗？
- **618 行是 `SKILL.md`（技能指令），不是 memory。** memory 是 memory 目录下另外那些**小 `.md` 文件**（3 条种子 + 之后积累的），由 `MEMORY.md` 索引，每条几十行。
- **不必每条消息加载全文。** 机制上：SKILL.md 正文只在技能被**触发/调用**时进上下文；每条消息真正在跑的是 **hooks（shell/python）**，hook 不会把 618 行塞进模型上下文。
- 问题在于 description 写了 "use on EVERY user message"，会诱导模型频繁加载整份 618 行。**正解（见 P1-7）**：把 SKILL.md 砍成一个很小的"常驻核心"（Iron Law + Gate + 侦测原则），把审计/重排/memory 格式/冲突算法等**重型参考拆成单独文件，只在真正做那些操作时才读**。

---

## 第 1 节 · P0（必修，阻断公开发布）

### [x] P0-1 · 硬编码个人语言规则，默认开启拦截 — 已修
- **证据**：`copilot/lib/deterministic_block.py:338-345`（`lang-pref-001`：回复>200字 且 中文占比<0.1 且 user 上条 prompt 无 `in english`/`paper` 关键词 → 判违规）。它**不看 user 自己说什么语言**。`seed_memory/` 默认 copy 进去的 3 条全是作者个人偏好。
- **后果**：英文母语用户装上后，几乎**每条实质英文回复都被判违规**，还收到中文"改中文回复"提示。
- **✅ 已修**：deterministic block 现默认**关**（gate 加 `path_config.enforcement_enabled()`，默认 False）。新用户装上后这些规则不被强制；它们只作为示例 memory 存在。开强制：`PT_ENFORCE=1`。已离线验证默认不拦、`PT_ENFORCE=1` 时正常拦。

### [~] P0-2 · Windows 上 Iron Law gate 被直接关掉 — 风险已消除（Python 端口仍 TODO）
- **证据**：`copilot/hooks/hooks.json:8` 的 powershell 分支 = `echo '... skipped on Windows'`，原生 Windows 上 observation-log 硬门禁什么都不做。
- **✅ 已消除 brick 风险**：obs-log gate 现仅在 `PT_ENFORCE=1` 时才阻断（默认 observe-only 永不阻断），且 P0-5 修了 Windows UTF-8 → 强制开启时块也能在 Windows 正确发出。
- **⬜ 仍待（非阻断）**：Windows 上 `check-observation-log` 仍是 echo-skip，没有 Python 日志端口（强制开启时 Windows 不写 obs-gate 日志）。可后续补 `check_observation_log.py`。

### [~] P0-3 · Stop 阻断协议没按 Copilot 正确迁移 — 仍需真机实测
- **证据**：`PORT_DESIGN.md:18-20,63-66` 要求 Copilot 阻断 = stdout `{"decision":"block"}` + **`exit 0`**；但代码是 `print(JSON)` 后 `sys.exit(2)`。
- **进展**：P0-5 修复后，block JSON 现在能在 Windows 正确以 UTF-8 打到 stdout（之前因编码静默失败）。是否还需把 `exit 2`→`exit 0` 取决于 Copilot 实际读取语义——**需在真实 Copilot 上跑一次确认**。`exit 2` 即使被当警告也是 fail-open（不会锁人），所以非紧急。
- **需你**：一次真机实测（你在 Copilot 里故意触发强制阻断，看是 block 还是 warning）。

### [~] P0-4 · 影子 judge 默认开 + 隐私披露不足 — 已基本修完
- **证据**：默认开、prompt 经命令行参数传 `copilot -p`、发送前无脱敏。
- **✅ 已修**：① 默认**关**（`PT_SHADOW`，默认 False）；③ 发送前 `redaction.redact()` 脱敏（`verify_retry_shadow.py:391`，离线验证 key/password 被 `[REDACTED]`）；⑤ 披露写进 SKILL.md banner + install 结尾。
- **⬜ 仍待（小）**：② 首启一次性同意提示；④ argv→stdin（需先确认 `copilot -p` 是否读 stdin）。

### [x] P0-5 · （新发现）Windows UTF-8 stdout BLOCKER — 已修
- **证据**：hook 模块 `print(json.dumps(..., ensure_ascii=False))` 输出含中文/emoji；Windows 管道 stdout 默认 cp1252 → `UnicodeEncodeError` → 被 hook 的防御性 `except` 吞成 exit 0。**后果：Windows 上即使开了强制，硬阻断和 SessionStart 记忆注入都静默失效**。
- **✅ 已修**：`path_config.py` 在 import 时调 `force_utf8_io()`（`sys.stdout/stderr.reconfigure(encoding='utf-8')`），所有 import path_config 的 hook 自动 UTF-8；`session_start_inject.py`（不 import path_config）单独加同样守卫。离线验证：修复前 block 静默失败（exit 0 空输出），修复后 exit 2 + 合法 UTF-8 JSON。

---

## 第 2 节 · P1（发布前应修）

- [ ] **P1-5 · `copilot/` 零测试**：补 hook 协议 + Windows + 隐私默认值测试（根目录有 38+，发布版一个都没有）。工作量：中（约 1d）。
- [x] **P1-6 · install.ps1 写全局 `project_root`** → 已移除（`install.ps1` 不再写 `project_root`，path_config 按每个项目 cwd 自动解析）。
- [ ] **P1-7 · SKILL.md 瘦身**：618 行 → `<200` 词常驻核心，重型参考拆到 `ARCHITECTURE.md` / `reference/`（详见第 4 节）。工作量：中。
- [ ] **P1-8 · README 重写**：现在满是 `B1/B5/Phase 7` 内部黑话，外部看不懂。改英文 quickstart + 一句话价值主张 + 隐私默认值 + 一张"会拦什么"的图。工作量：中。

---

## 第 3 节 · P2（采用增强）

- [ ] **P2-9 · 一条命令安装**（对齐 superpowers）：`copilot plugin marketplace add YujunZhou/...` → `copilot plugin install`，取代现在的 git clone + 手动 merge settings。
- [ ] **P2-10 · 杀手级 demo GIF**：录"重复违反偏好被自动拦下并纠正"的真实场景。
- [ ] **P2-11 · 用压力场景做 skill 测试**：RED→GREEN→REFACTOR（先看 agent 不带 skill 怎么违规，再验证 skill 真的改了行为）。

---

## 第 4 节 · SKILL.md 重写方案（"你看看怎么改"）

目标：从 618 行/23KB 砍到一个能频繁加载也不心疼的小核心。参照 superpowers `writing-skills` 三条硬规则：
1. **description 只写"何时用"，不写流程**。现有 `scan...record to memory, log observations` 是在描述流程，会让模型照 description 走、不读正文 → 删掉流程描述。
2. **频繁加载的 skill `<200` 词**。
3. **skill 是可复用参考，不是流水账** → 删掉所有日期 / `Phase B1/B5` / `2026-04-19 sprint` / `N11/N12` / `wf-pit-016` 这类内部痕迹（全文约 15 处）。

**拆分建议**：

| 去向 | 内容 |
|------|------|
| `SKILL.md`（瘦身后常驻核心，目标 <200 词） | Iron Law + Gate Function 三步 + 5 条侦测原则 + cost asymmetry 一句话 |
| `reference/detection.md`（侦测时才读） | Implicit Signal 表、Rationalization 表、Red Flags 清单 |
| `reference/memory-format.md`（写 memory 时才读） | frontmatter 规范、缩写映射、文件命名、body 结构、MEMORY.md 索引格式 |
| `reference/operations.md`（审计/重排/删除时才读） | 冲突解决算法、Pre-write 校验、SUPERSEDE 协议、确认策略、遗忘处理、健康检查 |
| `ARCHITECTURE.md`（开发者文档，模型一般不读） | B1/B2/B3/B5 基础设施说明、hook 链、infra 文件清单 |

> 注意：改 SKILL.md 前需先确认是否已在 web LLM 里做过方向决策（你的既有偏好）。本节只是方案，未动文件。

---

## 第 5 节 · 建议实施顺序

1. **先做不需你决策的机制修复**：P0-2（Windows gate）、P0-3（exit 协议）→ 让发布版在你机器上真能跑对。
2. **再定两个产品默认**（需你拍板）：P0-1（enforcement 默认开/关 + 个人规则是否硬编码）、P0-4（影子 judge 默认关 + 脱敏）。
3. **然后**：P1-5 测试 → P1-7/P1-8 文档瘦身与重写。
4. **最后**：P2 采用增强。
