# SESSION_HANDOFF_v1 — preference-tracker（Copilot 版）实机测试交接

> 给「下一个新开的 Copilot 会话」用。新会话会加载**已同步的新插件代码**，所以要在新会话里测。
> 本文件可整篇贴给新会话的 agent，也可你自己照着做。

---

## §0 TL;DR（先看这个）

- **背景**：上个会话把 preference-tracker 的 Copilot 版做了安全化改造（默认只观察不拦截、修了 Windows 编码 bug、加了一键开关 `pt_mode`），并**已经把新代码同步进你正在用的插件**。
- **你要做的 3 件事**：
  1. **关掉当前会话，新开一个 Copilot 会话**（这样才会加载新钩子）。
  2. 跑下面 §3 的 4 个测试（约 10 分钟）。
  3. 把每个测试的结果（尤其是**测试 B**：是真拦还是只警告）记下来反馈。
- **最关键的一个未知数**：enforce 模式下，Copilot 到底是**真的硬拦截**（强制 agent 改）还是**只弹个警告**。这个我在后台测不了，必须你在真实 Copilot 里看一眼。

---

## §1 背景：上个会话改了什么（context）

公开发布前的安全化改造，全部只动 `repo\copilot\`（Copilot 变体）：

- **默认改成「观察模式」**：新装的用户只会被「记录偏好 + 提醒」，**绝不硬拦截、绝不调用 LLM**。原来的问题是默认用作者个人的「必须回中文」规则拦所有人（英文用户每句被拦）。
- **修了一个 Windows 隐藏 BLOCKER**：钩子往管道打印中文/emoji 时，Windows 默认 cp1252 编码会报错被吞掉 → 结果**Windows 上拦截和记忆注入一直是静默失效的**。已用 `force_utf8_io()` 修复。
- **影子 LLM 判官**：默认关；开启时发送给 LLM 前先脱敏（API key / 密码）。
- **加了一键开关 `pt_mode.py`** + 安装时 `-Mode` 选择 + `copilot/README.md` 两条安装命令。
- 修了 install 脚本写全局 `project_root` 的跨项目污染。

详细清单见 repo 根目录 `RELEASE_READINESS.md`；宣传卖点见 `MARKETING_HIGHLIGHTS.md`。

---

## §2 当前状态（已做 + 已验证）

**已同步到 live 插件**（你机器上正在跑的那份）：
- 插件路径：`C:\Users\t-yujunzhou\.copilot\installed-plugins\preference-tracker\preference-tracker\`
- 已确认含新文件：`lib\pt_mode.py`、`path_config.py` 里有 `enforcement_enabled` 和 `force_utf8_io`。
- 6 个模块在该路径下 import 全部 OK。
- 旧版备份在：`C:\Users\t-yujunzhou\.preference-tracker-backups\_backup_preELI_*`（回滚用）。
- 独立的 model-facing skill 文档 `~\.copilot\skills\preference-tracker\SKILL.md` 也已更新（含模式 banner）。

**离线已验证（不用再测）**：
- 默认不拦、`PT_ENFORCE=1` 时正确拦（exit 2 + 合法 UTF-8 JSON）。
- `pt_mode` 四种模式翻转正确、保留 config 其他 key。
- 安装脚本语法 OK。

**还没做**：repo 改动**未 commit**；P0-3（exit 码语义）需实机确认（= 测试 B）。

**当前模式**：`observe`（安全默认）。配置文件：`C:\Users\t-yujunzhou\.preference-tracker.config.json`。

---

## §3 测试清单（核心 —— 照着做）

> 开关命令统一用这个路径（下面简称 `PT`）：
> `C:\Users\t-yujunzhou\.copilot\installed-plugins\preference-tracker\preference-tracker\lib\pt_mode.py`

### 测试 A —— 冒烟 / 观察模式不打扰（默认）
1. 新开 Copilot 会话，随便让它做个小任务（例如「列一下当前目录的文件」）。
2. **预期**：一切正常，没有报错、没有看不懂的中文「拦截」提示、回复不被打断。
3. **证明**：新插件能正常加载；observe 默认是安全的、不干扰。
- ❌ 异常信号：出现 `🔴 GATE CHECK FAILED` / `⛔` / 乱码 / agent 卡住转圈 → 记下来。

### 测试 B —— enforce 模式到底是「真拦」还是「只警告」（★最重要）
1. 开强制：在 PowerShell（任意，不必在 copilot 里）跑：
   `python "<PT>" enforce`
   再 `python "<PT>" status` 确认显示 `enforce`。
2. 在 **Copilot 会话**里给一个**会违规的任务**（最稳的触发点是 `/tmp` 规则）：
   > 「写一个 bash 脚本，把日志写到 /tmp/mylog.log」
   agent 的回复里会出现含 `/tmp/` 的代码块 → 命中 `oth-pref-001`。
3. **看现象，二选一记录**：
   - **(真拦)**：这一轮被打断，agent 收到中文修正提示，紧接着自己发一条 `[修正]` 开头的改正（把 `/tmp/` 改成项目内路径）。→ 说明 Copilot 认 `exit 2 + stdout JSON`，硬拦截**可用**。
   - **(只警告)**：你只看到一段警告文字，但 agent 正常结束、没被强制改。→ 说明 Copilot 把 `exit 2` 当警告，需要我把代码改成 `exit 0`（见 §6）。
4. **同时确认 Windows 修复**：那段中文拦截提示**显示正常、不是乱码、没报编码错**。

### 测试 C —— 切回安全 + 开关生效
1. 跑 `python "<PT>" observe`，再 `status` 确认 `observe`。
2. 重复测试 B 的违规任务（写 /tmp 脚本）。
3. **预期**：这次**不拦**（observe 模式），agent 正常完成。
4. **证明**：开关在真实会话里能来回切（config 实时读取，不用重启会话）。

### 测试 D（可选）—— SessionStart 记忆注入不报错
1. 新开会话时留意开头有没有「memory rules / 记忆」相关的注入，且**没有报错/乱码**。
2. **证明**：`session_start_inject` 在 Windows 上正常（UTF-8 修复覆盖到注入路径）。

---

## §4 看到什么算正常 / 算异常

| 现象 | 正常吗 | 含义 |
|------|--------|------|
| observe 模式下完全没动静 | ✅ 正常 | 默认就该安静 |
| enforce 模式 `/tmp` 任务被拦 + 出现 `[修正]` | ✅ 最理想 | 硬拦截可用 |
| enforce 模式只弹警告、没强制改 | ⚠️ 可接受但要改 | 需 exit2→exit0，见 §6 |
| 任何乱码 / `UnicodeEncodeError` / 编码报错 | ❌ 异常 | UTF-8 修复没生效，记下来 |
| agent 反复卡住、无法结束会话（被锁死） | ❌ 严重 | 立刻按 §5 回滚并反馈 |

---

## §5 出问题怎么回滚（保命）

- **临时关掉强制**：`python "<PT>" observe`（observe 不会拦任何东西）。
- **彻底还原旧插件**：把备份拷回去——
  `robocopy "C:\Users\t-yujunzhou\.preference-tracker-backups\_backup_preELI_*" "C:\Users\t-yujunzhou\.copilot\installed-plugins\preference-tracker\preference-tracker" /E`
  （或直接 `copilot plugin install` 重装，会拉 GitHub 上的旧版。）
- **整个停用插件**：在 `~\.copilot\settings.json` 里把 `"preference-tracker@preference-tracker"` 设为 `false`。

---

## §6 测完之后的下一步

- 把测试 B 的结论告诉我：**真拦 / 只警告 / 乱码**。
  - 若「只警告」→ 我把 `deterministic_block.py` 和 `verify_compliance.py` 的 `sys.exit(2)` 改成 `sys.exit(0)`（stdout 已经在打 `{"decision":"block"}`），再同步一次。
  - 若「真拦」→ P0-3 关闭，enforce 模式可用。
- 全部 OK 后：把 repo 改动 **git commit**（仍未提交），需要发布给别人时再 push 到 GitHub。
- 剩余 P1（已记在 `RELEASE_READINESS.md`）：补 copilot 自动化测试、SKILL.md 瘦身、README 重写。

---

> 测试结果可以直接写在本文件末尾「## 测试记录」里，或开会话时贴给我。

---

## 测试记录

> 验证日期：2026-06-05　执行：新开的 Copilot 会话（已加载同步后的插件）
> 范围：只读验证 + 离线模拟。**未改动任何代码 / 配置 / config 文件**（应用户要求）。

### 结论速览

| 测试 | 结果 | 一句话 |
|------|------|--------|
| A 冒烟 / observe 不打扰 | ✅ 通过 | 插件加载正常，6 模块 import OK，observe 生效，全程无报错/乱码/卡死 |
| B enforce 真拦 vs 只警告 | ⚠️ 被新 BLOCKER 阻断 | 「Copilot 是否认 exit 2」这一★核心问题**仍未实测**；但 hook 引擎本身离线已验证正确 |
| C 切回 observe | ⏸ 未做 | 依赖 B |
| D SessionStart 注入 | ✅ 通过 | exit 0，合法 JSON `additionalContext`，中文不乱码 |

### 🔴 新发现 BLOCKER：config 文件 UTF-8 BOM → 读取侧静默失效

上个会话修了**输出侧**编码（`force_utf8_io`），但**读取侧没处理 BOM**。`~/.preference-tracker.config.json` 当前带 UTF-8 BOM（实测头 3 字节 `EF BB BF`），导致：

1. **`path_config._read_config_file()`**（`open()` 无 encoding）→ `json.load` 报 `Expecting value: line 1 column 1` → 被 `except` 吞 → 返回 `{}`。
   - 后果：**所有 config key 被静默忽略**（`project_root`、`retrieve_*`、`enforce` 全部失效）。`get_project_root()` 退回 cwd。
   - 对 enforce 的影响：config 里写 `enforce:true` 也读不到，**当前只有 `PT_ENFORCE=1` 环境变量能真正开 enforce**。

2. **`pt_mode._load()`**（`encoding='utf-8'`，**不是 `utf-8-sig`，不剥 BOM**）→ 同样返回 `{}`。
   - 后果更重：跑 `pt_mode enforce/observe/full` 会从空 dict 重写，**抹掉 `retrieve_model / project_root / retrieve_cli / retrieve_backend` 四个 key**（已用模拟 `_load()` + `_apply()` 确认，未真写）。
   - 这直接推翻 §2 的「pt_mode 翻转保留 config 其他 key」和 pt_mode docstring 的「preserving every other key」——**在本机（config 带 BOM）该说法不成立**。

**对 Test B 的关键含义**：照 handoff 原步骤跑 `pt_mode enforce`，(a) 会破坏 retrieve 配置；(b) 即使 pt_mode 重写成无 BOM 文件让 enforce 生效，整个失败也会被误判成「config 没进去」而非「Copilot 忽略 exit 2」。**所以现在还无法干净地回答 §0 那个最关键未知数。**

### 已离线验证为正确的部分（hook 引擎本身没问题）

- `deterministic_block.py` 在 `PT_ENFORCE=1`（env，绕开坏掉的 config 读）+ 含 `/tmp/` 的 bash 代码块 transcript 下：**exit 2 + 合法 UTF-8 JSON `{"decision":"block"}`**，命中 `oth-pref-001`，中文修正提示显示正常、无乱码。→ 证明输出侧 UTF-8 修复 + 拦截判定逻辑 OK。
- `session_start_inject.py`：exit 0，输出合法 JSON，注入到 memory 规则摘要，中文正常。
- 6 模块（path_config / pt_mode / deterministic_block / verify_compliance / redaction / session_start_inject）import 全 OK。
- `path_config` 含 `enforcement_enabled()` 与 `force_utf8_io()`；当前 `enforcement_enabled()` 返回 False（observe）。

### 证据要点（可复现）

- config 头 8 字节：`EF BB BF 7B 0D 0A 20 20`（BOM + `{`）。
- `path_config._read_config_file()` → `{}`；`pt_mode._load()` → `{}`；仅 `open(..., encoding='utf-8-sig')` 能正确解析出 4 个 key。
- 模拟 `pt_mode enforce`：结果 cfg = `{'enforce': True}`，`retrieve_model`/`project_root` **均丢失**。

### 建议修复（待你拍板，本次未动手）

- 两处读取改 `encoding='utf-8-sig'`（同时兼容有/无 BOM）：
  - `path_config.py` → `_read_config_file()` 的 `open(CONFIG_PATH)`
  - `pt_mode.py` → `_load()` 的 `open(CONFIG_PATH, encoding='utf-8')`
- `_save()` 已是无 BOM utf-8，OK。可顺手把现有 config 重存一次剥掉 BOM。
- 追查 BOM 来源（疑似 install / retrieve 配置写入用了 PowerShell `Set-Content`/`Out-File` 默认带 BOM），否则修了读取侧后仍可能被再次写回 BOM（读取侧用 utf-8-sig 后即使再被写 BOM 也能容忍）。
- 修完后再正式跑 live Test B/C，才能回答「Copilot 真拦 / 只警告」。

---

## §7 修复记录（2026-06-05，已修完并同步到 live 插件）

上面那个 BOM BLOCKER **已全部修复并验证**：

**改了 4 处**（repo `copilot/`，已 re-sync 进 live 插件）：
1. `lib/path_config.py` `_read_config_file()` → `open(..., encoding='utf-8-sig')`（兼容有/无 BOM）。
2. `lib/pt_mode.py` `_load()` → `utf-8-sig`（不再因 BOM 返回 {} 而抹 key）。
3. `lib/retrieve_inject.py` `_load_user_config()` → `utf-8-sig`（同一 BOM 隐患）。
4. `install.ps1` 写 config 改用 `[System.IO.File]::WriteAllText(..., UTF8Encoding($false))`（不再写 BOM；根因就在 PS5.1 `Set-Content -Encoding UTF8`）。

**已修复 live 环境**：
- 用修好的 `pt_mode observe` 把 `~/.preference-tracker.config.json` 重存成**无 BOM**（头字节 `EF BB BF` → `7B`），4 个 retrieve/mode key 全保留。
- 顺手移除了 config 里残留的 `project_root`（BOM 修好后它会突然"复活"造成跨项目污染）。移除后在 repo 目录下 `memory_dir` 解析不变（仍是 `repo\.copilot\preference-tracker\memory`），无记忆丢失。
- 已 re-sync 新代码进 `installed-plugins\...\preference-tracker\`，确认 `path_config` 能读出全部 key、`enforcement_enabled()=False`（observe）。

**离线验证**：带 BOM 的 config 现在能正确读出 key；`pt_mode enforce` 保留其他 key 且重存为无 BOM；`enforcement_enabled()` 正确反映 config。

### 现在可以干净地重跑 Test B 了
- 之前的顾虑（pt_mode 会抹 retrieve key、失败会被误判成"config 没进去"）**已消除**。
- 重跑步骤照 §3 测试 B（新开会话 → `pt_mode enforce` → 让它写 `/tmp/...` 脚本 → 看真拦 vs 只警告）。
- 现在如果还是"只警告"，那就**确实是 Copilot 把 exit 2 当警告**，我再把 `sys.exit(2)`→`sys.exit(0)`（stdout 已在打 `{"decision":"block"}`）。

---

## 测试记录（第二轮 — 2026-06-05，live Test B/C）

> 执行环境：当前 Copilot 会话（`session.resume` 恢复会话，钩子**确实在跑** —— events.jsonl 里有真实 `agentStop`/`sessionStart` 事件）。
> 本轮**改动**：仅用 `pt_mode` 切了 enforce/observe 做测试，最后已还原成 **observe**（安全默认）。未改任何插件代码。

### 结论速览

| 测试 | 结果 | 一句话 |
|------|------|--------|
| B enforce 真拦 vs 只警告 | 🔴 **问题前移，无法按原问法回答** | `/tmp` deterministic 拦截在 Copilot 上是 **no-op**：Stop 钩子触发了，但 `deterministic_block.py` 读不懂 Copilot 的 hook 契约，**根本没产出 exit 2**，所以「Copilot 认不认 exit 2」这一问**还没机会被测到** |
| C 切回 observe + 开关 | ✅ 配置层通过；⚠️ live 拦截层 moot | `pt_mode` 在 observe↔enforce 间来回切，`enforcement_enabled()` 实时正确反映（False/True/False）。但因 enforce 本身就拦不住，「observe 不拦」无法反衬出开关的拦截效果 |

### 🔴 核心发现：Copilot 的 Stop 钩子契约与插件不匹配 → 拦截器形同虚设

**Copilot CLI 的原生钩子事件**是 `preToolUse / postToolUse / userPromptSubmitted / agentStop / sessionStart`。插件 `hooks.json` 里的 `Stop` 被映射到 **`agentStop`**，`SessionStart` → `sessionStart`。

`agentStop` 给钩子的 `input`（实测自 events.jsonl）：

```json
{"timestamp":..., "cwd":"...\\Code", "sessionId":"a2042497...",
 "transcriptPath":"...\\events.jsonl", "stopReason":"end_turn"}
```

**两个契约不匹配**（任意一个就足以让拦截失效，这里两个都中）：

1. **字段名 camelCase vs snake_case**：Copilot 给的是 `transcriptPath` / `sessionId`；`deterministic_block.py` 读的是 `transcript_path` / `session_id`（`verify_compliance.py` 同样：`json.load(sys.stdin)` + `data.get('session_id')` + `'transcript_path' in data`）。→ `.get('transcript_path')` 拿到 `None`，**transcript 根本没打开就放行**。
2. **transcript schema 不同**：Copilot 的 `events.jsonl` 把 assistant 文本存成 `type:"assistant.message"` + `data.content`（字符串）+ `data.toolRequests`；而 parser 找的是 Claude 格式的 `type:"assistant"` + `message.content[].text`。→ 即便修好字段名，parser 也**找不到 assistant 回复**，依旧 `response=''` → 放行。

### 证据（可复现）

实测我那轮违规（`echo "hello" >> /tmp/mylog.log`）于 `09:16:23` 触发了真实 `agentStop`，返回 `output:{}, success:true` —— **没有拦截**，控制权正常回到用户。我的违规文本**确实在** transcript 里（5 条 `assistant.message` 含 `/tmp/`），是 parser 看不见它。

手动用 `PT_ENFORCE=1` 复跑 `deterministic_block.py`：
- 喂 Copilot 的 camelCase stdin → exit 0、判 `pass`、延迟 **0.06ms**（瞬间放行：`transcript_path` 取到 None，没读文件）。
- 喂 snake_case stdin（指向 events.jsonl）→ exit 0、判 `pass`、延迟 296ms（读了文件，但 `type=='assistant'` 一条都没匹配到 → `response=''`）。

### ⚠️ 第二个独立问题（尚未定位）：真实 agentStop 没写任何 compliance log

手动复跑会写 `pass` 日志，但**真实那次 agentStop 一条日志都没写**。说明真实钩子执行链路另有问题，候选原因（未区分）：
(a) Copilot 没真正调用 `deterministic_block.py`；(b) 调了但 stdin 不是 JSON（`json.load(sys.stdin)` 抛错被 `except: sys.exit(0)` 吞掉、不写日志）；(c) `${CLAUDE_PLUGIN_ROOT}` 在 powershell 变体里没正确展开；(d) cwd/config/日志路径与复跑不同。`output:{}` 只代表「没捕获到 stdout」，不证明每条命令都正确跑过。

### exit 2 到底认不认 —— 仍未测到（但不是"测不了"）

因为拦截器在 Copilot 契约下根本到不了 block 分支，本轮**没机会**验证 Copilot 是否把 `exit 2` 当硬拦截。这不是天生测不了，而是被上游的契约不匹配挡住了。

**最干净的下一步诊断**（一步同时回答所有未知数）：在 `agentStop` 链里临时加**一条**最小钩子命令，放在真拦截之前，它只做三件事：①把 cwd / argv / env / stdin 的 keys 落一个 sentinel 文件；②往 stdout/stderr 打唯一标记；③**无条件** `print({"decision":"block",...})` + `sys.exit(2)`。一跑就能确认：命令到底被没被调用、stdin 长什么样、cwd/路径是什么、以及 **Copilot 认不认 exit 2**。（注意：加钩子要改 `hooks.json` 并**重启 Copilot 进程**才生效 —— 这步需要你来做，我没在本会话动它。）

### 建议修复（按优先级）

1. **适配 Copilot 的 hook 契约**（P0，根因）：Stop 系列脚本（`deterministic_block.py`、`verify_compliance.py` 等）读 stdin 时兼容 camelCase（`transcriptPath`/`sessionId`）**且**能解析 Copilot `events.jsonl`（`type:"assistant.message"` + `data.content` 字符串、`data.toolRequests` 里的代码/路径）。建议抽一个 transcript adapter，先判 schema 再取 last assistant text。
2. **先加上面的 forced-block 诊断钩子**，确认 stdin 投递方式 + exit 2 语义，再决定 `sys.exit(2)` 要不要改 `sys.exit(0)`（原 handoff §6 那个改动在契约修好前都是空谈）。
3. 顺带修 `sessionStart`：本会话的 `sessionStart` 事件 `success:false`，报错来自 **superpowers 插件**的 `run-hook.cmd`（PowerShell ParserError `Unexpected token 'session-start'`），**不是本插件**，但同处一个 sessionStart 链，值得留意是否互相影响（Test D 待查）。

### 现场状态

- 模式已还原为 **observe**（`enforcement_enabled()=False`）。
- 本轮新增的临时分析脚本已清理；插件代码 / hooks.json **未改动**。

---

## §8 修复记录（2026-06-05 第二批 —— Copilot hook 契约适配，已修完并同步 live）

第二轮测试发现的**根因 BLOCKER（字段名 camelCase + transcript schema 不匹配 → 拦截器在 Copilot 上完全空操作）已修复并端到端验证**。

**根因**：插件按 Claude 格式读 stdin（`transcript_path`/`session_id`）和 transcript（`type:"assistant"` + `message.content[]`）；但 Copilot 给的是 `transcriptPath`/`sessionId` + `events.jsonl` 里 `type:"assistant.message"` + `data.content`（字符串）。两者都不匹配 → 拿不到 response → 一律放行。

**改了什么**（repo `copilot/`，已 re-sync 进 live 插件）：
1. **新增 `lib/transcript_adapter.py`**：统一读 stdin 字段（camelCase + snake_case 都认）+ 解析两种 transcript schema，输出 `(response, last_user, tool_commands, raw_lines)`。
2. `deterministic_block.py` / `verify_compliance.py` / `verify_retry_shadow.py` 三个 Stop 模块全部改用该 adapter 取 session_id + response + last_user。
3. `deterministic_block.evaluate_rules` 新增扫 **tool 命令**里的 `/tmp/`（Copilot agent 常通过工具调用执行命令，而非 markdown 代码块）→ `/tmp` 规则现在在 Copilot 上也能触发。
4. `check-observation-log.sh` 的 jq 读取加 camelCase 兜底（`.transcriptPath`/`.sessionId`/`.workingDirectory`）。

**端到端验证（含 live 插件）**：
- Claude 格式 + Copilot 格式经 adapter 提取 response/user **完全一致**；lang-pref-001 在两种格式下都正确触发；english-bypass（user 说 "in english"）两种格式都正确放行。
- 用**真实 Test-B transcript**（`session-state\a2042497...\events.jsonl`）+ 真实 Copilot camelCase stdin 跑 **live 安装的** `deterministic_block.py`：observe=放行(exit0)；**enforce=阻断(exit2 + 合法 block JSON)**，命中 `oth-pref-001` + `lang-pref-001`。→ 修复前这里是 no-op，现在到达 block 分支。

### §8.1 现在仍未验证的、唯一剩下的未知数
**Copilot 到底认不认这个 block（exit 2 + stdout JSON）**。修复前根本到不了 block 分支，所以没测过；现在能产出 block 了，可以干净地测了。

### §8.2 下一个测试会话请做（重跑 Test B，现在是干净的）
1. **新开 Copilot 会话**（加载已同步的新代码）。
2. `python "<PT>\lib\pt_mode.py" enforce` → `status` 确认 enforce。
3. 给违规任务：「写个 bash 脚本，把日志写到 /tmp/mylog.log」。
4. **看现象（这次能真正回答 §0 的核心问题）**：
   - **(真拦)** 这轮被打断 + agent 自发 `[修正]` 改掉 /tmp → Copilot 认 exit 2，硬拦截可用 → P0-3 关闭。
   - **(只警告)** 只看到提示但没强制改 → Copilot 把 exit 2 当警告 → 告诉我，我把三个 Stop 模块的 `sys.exit(2)` 改成 `sys.exit(0)`（stdout 已在打 `{"decision":"block"}`，符合 PORT_DESIGN）。
5. 顺带确认拦截提示**中文不乱码**、`compliance_log.jsonl` 这轮**有写入**（路径在 `<当前cwd>\.copilot\preference-tracker-state\obs_log\`；注意 Copilot 的 cwd 可能是父目录 `...\Code`，日志可能写在那）。
6. 测完 `pt_mode observe` 还原，结果追加写回本文件「## 测试记录」。

---

## 测试记录（第三轮 — 2026-06-05，live Test B + Copilot 源码级根因定位）

> 执行环境：新开的 Copilot 会话（已加载同步后插件，含 §8 的 `transcript_adapter` 修复）。模型 claude-opus-4.8。
> 本轮改动：**只用 `pt_mode` 切 enforce/observe**；未改任何插件代码 / hooks.json；测完已还原 **observe**。
> 方法：先在真实会话里跑 Test B（让 agent 写 `/tmp` 脚本），再用 `events.jsonl` + Copilot CLI 1.0.60 的 `app.js` 源码 + Node 精确复现，把「为什么没拦」一路挖到底。

### 结论速览

| 测试 | 结果 | 一句话 |
|------|------|--------|
| A 冒烟 / observe 不打扰 | ✅ 通过 | 插件加载正常、7+ 模块 import OK、observe 不打扰、全程无报错/乱码 |
| B enforce 真拦 vs 只警告 | 🔴 仍不拦，但**根因已彻底定位（两个）** | live 下既不拦也不警告：`agentStop` 钩子触发了，但 `deterministic_block.py` **根本没真正跑**（零 compliance log）；就算跑了，exit 2 也会被 powershell 改成 exit 1 而不被 Copilot 认 |
| C 切回 observe | ✅ 通过 | `pt_mode` 实时翻转、`enforcement_enabled()` 正确反映 |
| **§0 核心问题（认不认 exit 2 / 真拦 vs 警告）** | ✅ **已从源码回答：是真拦** | Copilot 的 `agentStop` 拿到 `{decision:"block",reason}` 会把 `reason` 当一条**新 user message 入队、让 agent 续写** = 硬拦截/reprompt，**不是只警告** |

### ★ §0 终于有答案（Copilot `app.js` 源码确认）

`agentStop` 钩子拿到 `{decision:"block", reason}` 后（`app.js` 内）：
```
if (xe?.decision === "block" && xe.reason)
    this.enqueueUserMessage({ prompt: xe.reason }, true) ...   // 把 reason 当新 user message 入队，agent 继续
```
→ 这就是我们想要的「真拦」：agent 收到中文修正提示后自发 `[修正]`。**不是弹个警告就完事。**

而且 Copilot 读 `{decision}` 的来源是钩子的 **stdout JSON**，且 **exit 0 和 exit 2 都会 parse stdout**（exit 2 走 `ece` catch 取 `p.stdout`；exit 0 走 resolve 取 `g=stdout`）。所以 `deterministic_block.py` 往 stdout 打 `{"decision":"block",...}` 的契约是对的。**剩下的问题全在「脚本要真跑起来」+「退出码别被吞」这两关 → 下面两个根因。**

### 🔴 根因 1（已确认，决定性）：powershell/pwsh 把 python 的 `exit 2` 改成 `exit 1` → Copilot 不认

Copilot 跑 powershell 钩子的实际方式是 `pwsh.exe -nop -nol -c "python ...\deterministic_block.py"`（源码 `mJe`/`fJe`：win32 优先 `pwsh.exe`，本机已装 PS7；flags 是 `-nop -nol`，**无 `-NonInteractive`**）。Node 精确复现（`pwsh -nop -nol -c`，等同 Copilot 的 `CTe` spawn）实测：

| 内层命令 | pwsh 退出码 |
|------|------|
| `python -c "...sys.exit(2)"`（裸） | **1**（被改写！）|
| `python -c "...sys.exit(2)"; exit $LASTEXITCODE` | **2** ✓ |
| `python -c "...print('X');sys.exit(2)"; exit $LASTEXITCODE` | 2，stdout 保留 ✓ |
| exit(0)/exit(1) 同理 | 0 / 1 ✓ |

Copilot 的 `CTe` 只把 **exactly exit 2**（`T===2`）当 block（走 `ece` → 取 stdout）；其它非 0（含被改写的 1）→ 当普通 hook 报错（`tce`）→ `t0` 直接 rethrow，**stdout 里的 `{decision}` 被丢弃**。
→ 实测（带正确 PATH 的复现）：`deterministic_block.py` 确实在 stdout 打了合法 block JSON、也写了 block log，但 pwsh 退出码是 1，所以 Copilot 不拦。

**两种修法（原理都验证过）：**
- **(最省事，= 你 §6 的提案，而且它本身就是正解)** 把三个 Stop 模块（`deterministic_block.py` / `verify_compliance.py` / `verify_retry_shadow.py`）的 `sys.exit(2)` 改成 `sys.exit(0)`（继续往 stdout 打 `{"decision":"block",...}`）。Copilot 在 exit 0 时也会 parse stdout 取 decision → 照样硬拦。**注意纠正：原 §6 把这个改动写成「只在『只警告』时才做」，但实际上它就是根因 1 的直接正解，不用等。**
- (或) hooks.json 里每个 powershell 命令尾部加 `; exit $LASTEXITCODE`，让 exit 2 正确透传（实测可靠）。

### 🔴 根因 2（已确认现象，强证据指向 python stub）：live 钩子里脚本根本没跑（零 log）

本轮 Test B：enforce 下我让 agent 写 `/tmp/mylog.log` bash 脚本，turn 结束时 `events.jsonl` 里**确实**触发了一次真实 `agentStop`（20:42:19，input 完全正确：`transcriptPath`/`sessionId`/`cwd`/`stopReason` 全对，且当时 enforce 是 on），但：
- `hook.end`：`output={}, success=true`，**没拦**、也没 reprompt（我收到的是 autopilot 的续跑提示，不是中文 reason）。
- `compliance_log.jsonl` **整个 user profile 30 分钟内零写入** → 脚本没走到 `log_check`（pass/block/disabled 三种结局前都会写 log，所以「零 log」= 脚本压根没跑完逻辑）。

复现对比定位：
- 在**普通 tool-call shell** 里用 Copilot 的精确 spawn（`pwsh -nop -nol -c "python ...deterministic_block.py"` + 同款 camelCase stdin）跑 → 脚本正常跑、读到了 stdin（log 里 session_id 来自 piped JSON）、写了 log。**所以命令本身没问题，stdin 也确实透传。**
- 唯一关键差异 = **PATH 上 `python` 解析到谁**。本机 `python` 在 PATH 上有两个：
  1. `C:\Users\...\Miniconda3\python.exe`（真 python）
  2. `C:\Users\...\AppData\Local\Microsoft\WindowsApps\python.exe`（Windows Store stub，实测存在）
  我的 tool-call shell 里 Miniconda3 排前面（conda 激活过），所以 `python` = 真 python。但钩子用 `-nop`（不加载 profile）+ 继承 Copilot 进程的**原始 PATH**（源码 `DA` 实测是「黑名单」式继承：只剥 Copilot/GitHub 密钥类变量，**PATH 是继承的**）。**若 Copilot 进程启动时没激活 conda，其原始 PATH 很可能只有 WindowsApps stub 在前** → 钩子的 `python` 命中 stub → stub 被管道 stdin 非交互调用时不执行脚本、静默退出 → 没 log、没 stdout、没拦。与 live 现象（`success=true, output={}`, 零 log）完全吻合，也解释了 §7 反复出现的「真 agentStop 从不写 log」。

**修法**：hooks.json / install.ps1 **不要用裸 `python`**，改用**安装时探测到的绝对路径**（conda 的 `python.exe`，如 `where.exe python` 取第一个真 python，或复用 retrieve 那套配置机制）。注意：注册表 Machine/User PATH **不含** conda（实测把它 prepend 进 `$env:PATH` 仍找不到 conda python），所以必须落绝对路径，不能靠 prepend 注册表 PATH。

### 下一步排查顺序（给修复会话）

1. **先修根因 2**（让脚本真跑起来）：把钩子里的 `python` 换成 conda 绝对路径 → 改 hooks.json + **重启 Copilot** → 看 `compliance_log` 这轮**有没有**写 'block'。
2. **再处理根因 1**（退出码）：`sys.exit(2)→sys.exit(0)`（最省事），或 hooks 尾部 `; exit $LASTEXITCODE`。
3. 两个都修 + 重启后重跑 Test B：按 `app.js` 契约，应当看到**真拦**（agent 被打断、收到中文 reason、自发 `[修正]`）。
4. （§7 那个 forced-block 诊断钩子仍是最快的一次性确认：能直接看到 live 的 argv/stdin/exit/cwd/`python` 解析到谁。需要改 hooks.json + 重启，本会话动不了。）

### 现场状态

- 模式已还原 **observe**（`enforcement_enabled()=False`）。config 无 BOM、retrieve 四个 key 完好。
- 期间 `~/.preference-tracker.config.json` 的 `enforce` 被某进程多次重置成 false（只有 `pt_mode._save` 会写它；疑似环境共享/并发，**非本插件逻辑**），影响过个别中间测试，但不影响上述源码级结论。
- **未改任何插件代码 / hooks.json**；临时分析脚本（`%TEMP%` 下）已清理。
