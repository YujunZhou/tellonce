# SESSION_HANDOFF_v2 — 真实 Copilot 验证清单

> **更新 2026-06-05 第二轮**：B 和 C 已被验证会话实测确认；A 的 hook 侧也实测通过，**只剩一个 30 秒手动收尾**（真交互 TTY 里确认 enforce 真拦）。最大根因（插件没被 Copilot 注册→hook 根本没加载）已修。详见 `AUDIT_REPORT_2026-06-05.md` §4。

---

## §0 先决条件（重要：先确认插件已注册）

- **hook 只有在插件注册进 Copilot 的 `~/.copilot/config.json` 时才会加载**（这是上一轮一直"不拦"的真根因）。先确认：
  `python "C:\Users\t-yujunzhou\.copilot\installed-plugins\preference-tracker\preference-tracker\lib\register_plugin.py" --status`
  期望：`[OK] preference-tracker IS registered`。若是 `[WARN] NOT registered`，跑 `python "...\lib\register_plugin.py"` 注册（幂等+自动备份 config.json），然后**重启 Copilot**。
- 当前模式 = observe（安全）。开关命令（下称 `PT`）：
  `python "C:\Users\t-yujunzhou\.copilot\installed-plugins\preference-tracker\preference-tracker\lib\pt_mode.py" <observe|enforce|full|status>`
- 自检：`python "...\lib\doctor.py"`（现含 plugin registration 检查），期望 0 FAIL。
- **必须新开一个 Copilot 会话**才会加载新钩子。

---

## §1 验证 A —— enforce 真拦（hook 侧已实测通过，只剩 30s TTY 收尾）

> 验证会话已确认：hook 真被调用、输出 `{"decision":"block"}` 中文提示、命中 oth-pref-001、exit 0 正确、run.ps1 避开 WindowsApps stub。读 bundle 源码也确认 Copilot 主 loop 在 `decision==="block"` 时 `enqueueUserMessage(reason)` = 真交互会 re-prompt。**只差你在真交互 TTY 里亲眼确认一次。**

**30 秒收尾步骤**：
1. 确认已注册（§0）+ 新开 Copilot 会话。
2. `PT enforce` → `PT status` 确认 enforce。
3. 在会话里：「写一个 bash 脚本，把日志写到 /tmp/mylog.log」。
4. **预期**：被拦 + agent 收到中文修正提示 + 自发 `[修正]` 改掉 /tmp。
5. 若不拦：先 `python "...\lib\doctor.py"` 看 registration / python 是否 OK；看 `<cwd>\.copilot\preference-tracker-state\obs_log\compliance_log.jsonl` 这轮有没有写入（有写入=hook 跑了，没写入=没注册或 python 没找到）。
6. 测完 `PT observe` 还原。

**两个已修根因（背景）**：
- RC1：`pwsh -c "python …exit 2"` 被 pwsh 改成 exit 1 → Copilot 丢 stdout。已修：默认 exit 0 + launcher `exit $LASTEXITCODE`。
- RC2：裸 `python` 命中 WindowsApps stub → 脚本没跑。已修：`hooks/run.ps1` 用 install 记录的绝对 python 路径 + 跳过 stub。

---

## §1-OLD 原始 验证 A（保留作参考）

---

## §2 验证 B —— SessionStart 注入读哪个字段？（决定记忆注入是否生效）

**问题**：Copilot 是公开版唯一的记忆注入通道（每轮注入被砍）。它在 SessionStart 读 `hookSpecificOutput.additionalContext`（我们当前默认）还是顶层 `additionalContext`？schema 严不严？

**Sentinel 测试（30 秒定真相）**：
1. 临时让 SessionStart 一定有内容可注入：保证 memory 里至少有 1 条 critical/high 规则（seed 里的 lang-pref-001 就是 critical；用 `doctor.py` 看 memory 规则数，或先 `PT enforce` 让 seed 生效——其实 observe 也注入，注入与 enforce 无关）。
2. 新开 Copilot 会话，第一句直接问它：
   > 「你现在的 system context / 开场注入里，有没有出现 `Session-start memory rules` 或任何 `[lang-pref-001]` 之类的规则摘要？如果有，原文贴出来。」
3. 判断：
   - 它能复述出注入的规则摘要 → **当前 `hookSpecificOutput` 形状生效**，B 关闭。
   - 完全没有 → 可能 Copilot 要顶层字段。设 `PT_SESSIONSTART_TOPLEVEL=1`（env 或 config）重开会话再问一次。
     - 顶层能注入成功 → 把默认改成顶层（我把 session_start_inject 默认切过去）。
     - 还是没有 → Copilot SessionStart 可能不支持 additionalContext，或 schema 更严。记下来，这影响整个"跨 session 记忆"卖点。

> 已知干扰：之前看到过 superpowers 插件在 SessionStart 报 `run-hook.cmd` ParserError，那是**别的插件**，不是本插件；但同处一条 SessionStart 链，留意是否互相影响。

---

## §3 验证 C —— `copilot -p` 会不会重新触发 Stop/SessionStart？（决定 fork-bomb 防护是否必要）

**问题**：shadow judge（full 模式）会 spawn `copilot -p` 子会话。如果子会话又 fire Stop hook，可能递归。我已加 `PT_CHILD_SESSION` 守卫让子会话里所有 hook 早退，但需确认它确实起作用、且不递归。

**步骤**（仅在你想验证 full 模式时做）：
1. `PT full` → status 确认 full。
2. 新会话里随便说句中文让它正常回一段。每个 Stop 会触发 shadow judge → `copilot -p`。
3. 观察：会话是否正常结束、**没有**无限挂起/疯狂起子进程、CPU 没爆。
4. 看 `<cwd>\.copilot\preference-tracker-state\runtime\b5_shadow_alerts\b5_shadow_log.jsonl` 是否只有父会话的记录、没有被子会话污染。
5. 测完 `PT observe` 还原（full 会调 LLM、花额度，别长期开）。

若发现子会话仍在 fire hook 且未被守卫挡住 → 记下来，我加强守卫。

---

## §4 验证完怎么反馈

把每项结论（A/B/C 各属于哪种情况）写回本文件末尾「## 测试记录」，或开会话贴给我。我据此：
- A：必要时把 `stop_block_exit_code()` 默认改 2。
- B：必要时把 SessionStart 默认切顶层，或换注入机制。
- C：必要时加强 child 守卫。

全部 OK 后：把仓库改动 `git commit`（仍未提交），发布前再 push。剩余非阻断项见 `AUDIT_REPORT_2026-06-05.md` §5（SKILL.md 瘦身 / README 重写 / dashboard / LICENSE 等）。

## §6 Codex 变体验证（如果你也想发 Codex 版）

今晚我**只改了 Codex 一个文件**：`codex/codex_preftrack/codex_posttooluse_block.py`——之前它在 tool input（写文件内容 / shell 命令）上跑语言规则，会**误拦合法的英文文件写入**（blocking 模式下）。已改成 tool input 只跑路径/bib 规则，丢掉 `lang-*`。

**在真实 Codex 验证**（需要 Codex blocking 模式）：
1. 把 Codex 设成 blocking（`<project>/.codex/preference-tracker/mode.json` 的 mode 改 "blocking"）。
2. 让 agent **写一个纯英文的文件**（>200 字，无中文）——**预期：不再被拦**（修复前会被 lang-pref-001 误拦）。
3. 让 agent **写一个含 `/tmp/` 的命令/文件**——**预期：仍被拦**（oth-pref-001 保留）。
4. 若英文文件仍被拦 → 修复没生效，记下来。

**另一个 Codex 预存问题（不是我今晚改的，但发布前要处理）**：`codex/hooks/userpromptsubmit-retrieve-inject.sh` 引用了 codex 树里**不存在**的 `retrieve_inject.py` → 这个 hook 永远静默 exit 0（检索注入在 Codex 上根本没跑）。要么从 CC 端拷/软链 `retrieve_inject.py`+`fingerprints.yaml`，要么删掉这个 hook。详见 `AUDIT_REPORT_2026-06-05.md` §1 Codex 缺口。

## §7 Claude Code 变体

**今晚没动 root tree（Claude Code 变体）一个字**——所有改动都在 `copilot/` 和上面那一个 codex 文件。所以 Claude Code 版**不需要因我的改动做验证**，它和之前一样。（root 仍有 8 个测试文件 + 38 unit + 12 chaos，是最成熟的。）

---

## §5 一键回滚（保命）

- 临时关强制：`PT observe`。
- 还原旧插件：`robocopy "C:\Users\t-yujunzhou\.preference-tracker-backups\_backup_preELI_*" "C:\Users\t-yujunzhou\.copilot\installed-plugins\preference-tracker\preference-tracker" /E`
- 停用插件：`~\.copilot\settings.json` 里 `"preference-tracker@preference-tracker": false`。

---

## 测试记录

> 记录人：Copilot CLI 会话（Claude Opus 4.8），2026-06-05。
> 方法：当前交互会话直接 introspect + 用 `copilot -p` / stdin 子会话作受控测试台（env 隔离 `PT_ENFORCE`，不动全局 observe），逐个对照 Copilot 自己的进程日志 `~\.copilot\logs\process-*.log`（记录了每个 hook 的 `Executing hook` 与 `[hook stdout]`）。

### 🔴 头号发现（根因）：插件的 hook 从来没被 Copilot 加载

A/B/C 三项一开始全失败，根因是同一个：**preference-tracker 没有注册进 Copilot 的 `~\.copilot\config.json` 的 `installedPlugins` 列表**。

- 现象：子会话日志里 `Loaded 1 hook(s) from 1 plugin(s)` —— 那 1 个是 superpowers 的 SessionStart，**本插件 0 个 hook 被加载**。`compliance_log.jsonl` 这轮零写入（不是 exit code、不是 launcher 找不到 python，是根本没被调用）。
- 为什么：`settings.json` 的 `enabledPlugins` 里有 `preference-tracker@preference-tracker: true`，文件也在 `installed-plugins\` 里，但 **Copilot 加载 plugin hooks 读的是 `config.json` 的 `installedPlugins` 数组**（每条带 `cache_path` → 读其 `hooks.json`）。那个数组里只有 document-skills / example-skills / superpowers，**没有 preference-tracker**。
- 为什么会这样：本插件是「手动拷文件 + 手改 settings.json」side-load 进去的；`install.ps1/sh` 只写插件自己的 `~/.preference-tracker.config.json`（mode/路径），**从不写 Copilot 的 `config.json`**。真正的 `/plugin install` 流程才会写那条注册。所以 Copilot 不认它是已安装插件 → 不读它的 hooks.json。
- **离线验证为什么没发现**：之前所有 smoke test 都是「直接把 JSON 管道喂给 run.ps1/python」，绕过了 Copilot 的 plugin 加载层，所以验证了脚本本身能跑、却没验证 Copilot 会不会调用它们。

**已修复（live）**：手动把下面这条加进 `~\.copilot\config.json` 的 `installedPlugins`（已备份 `config.json.bak-pt-verify`）：
```json
{ "name": "preference-tracker", "marketplace": "preference-tracker", "version": "1.0.0",
  "installed_at": "...", "enabled": true,
  "cache_path": "C:\\Users\\t-yujunzhou\\.copilot\\installed-plugins\\preference-tracker\\preference-tracker" }
```
加了之后子会话日志变成 **`Loaded 7 hook(s) from 2 plugin(s)`**（本插件 5 Stop + 1 SessionStart + superpowers 1），所有 hook 正常 `Executing` 并产出。多次重起 `copilot` 进程都稳定加载，没被自动清掉。

> ⚠️ durability：`config.json` 顶部写着「This file is managed automatically」。手动那条目前稳，但更稳妥的长期做法是走真正的 `/plugin` 安装（需要把插件发布成一个 marketplace/git 源），或让 `install.ps1/sh` 自己把这条 upsert 进 `config.json`（注意它是 auto-managed，外部写有被覆盖的风险）。**当前不要再「只拷 installed-plugins 文件」就以为装好了。**

注意：**本会话是在加注册之前启动的**，所以当前这个会话自己没加载本插件 hook（hooks 在 session 启动时一次性加载）。下面 A 的「真拦 + re-prompt」最后一步要在**注册之后新开的交互会话**里确认。

### A —— Stop block 真拦吗？→ hook 侧全部 OK；交互 re-prompt 走 Copilot 主 agent loop（强证据，待一次 30s 交互确认）

注册修好后，`PT_ENFORCE=1` + 让 agent 回一段含 `/tmp/` 代码块：

- ✅ Stop hook 真被调用：`Executing hook ... run.ps1 deterministic_block.py`
- ✅ 真产出 block：`[hook stdout] {"decision": "block", "reason": "⛔ oth-pref-001 触发 ... → 改 /tmp/ → 项目内 ..."}`（中文提示、命中 oth-pref-001、exit 0）
- ✅ RC2（WindowsApps stub）：`run.ps1` launcher 正常，用 `.python_path.txt` 里的 Miniconda python 跑通，没踩 stub。
- ✅ RC1（exit code）：**Copilot 确实读 stdout 的 `{"decision":"block"}`**，`stop_block_exit_code()` 默认 0 是对的，不用改成 2。
- ⚠️ **但 `copilot -p` 和「stdin 喂 prompt」这两种非交互模式：block 被记录了，却没有 re-prompt**（日志 `finish_reason` 只出现 1 次 = 只跑了一轮，没自发 `[修正]`）。原因是非交互是 one-shot，跑完就退，不会 drain pending 队列。
- 🔬 **交互模式会不会 re-prompt**：查了 Copilot CLI 自己的 bundle（`app.js` 主 agent loop），`end_turn` 时触发 `agentStop` hook，若 `decision==="block" && reason` → `this.enqueueUserMessage({prompt: reason})` 把 block 理由当新 user message 重新入队继续跑。**所以真交互 TTY 会 re-prompt**。这是强代码级证据，但我在 autopilot 里起不了真 TTY，最后一步请你手动确认：注册后新开会话 → `PT enforce` → 「写 bash 脚本日志到 /tmp/mylog.log」→ 预期被拦 + 收中文提示 + 自发 `[修正]`。

**结论**：A 的 hook 机制（拦截判定 + block JSON + launcher + exit code）已全部实测通过；唯一没在本环境实测的是 Copilot 交互层把 block 理由 re-prompt 回 agent 这一步，但 bundle 源码证明它会。

### B —— SessionStart 注入读哪个字段？→ 顶层 `additionalContext`（已确认 + 已改默认）

实测（`copilot -p` 子会话直接问它 context 里有没有 `### Session-start memory rules summary`）：

- 默认 `hookSpecificOutput.additionalContext` 形状 → 子会话回 **NO-INJECTION-FOUND**（Copilot 不读这个）。
- `PT_SESSIONSTART_TOPLEVEL=1`（顶层 `additionalContext`）→ 子会话**逐字引用出**注入内容 + 列全 10 条 rule id。
- bundle 佐证：SessionStart 的 output mapper 读的是 `c.additionalContext`（顶层）。Copilot 的 parser 不严（没有 `additionalProperties:false`），当初怕「多一个 key 整个被拒」并没发生。

**已修复**：`session_start_inject.py`（source + installed 两份）默认改成**只发顶层 `additionalContext`**；保留 `PT_SESSIONSTART_HOOKSPECIFIC=1` 开关可切回 Claude 风格 envelope。改完无 env 复测 → 子会话 **INJECTION-OK**，跨 session 记忆注入这条卖点在 Copilot 上**确认可用**。

### C —— `copilot -p` 会重新触发 Stop/SessionStart 吗？guard 挡得住吗？→ 会触发；guard 实测挡住

- ✅ `copilot -p` **确实会** re-fire Stop 和 SessionStart hook（子会话日志里两类 hook 都 `Executing` 了）。所以 full 模式 shadow judge spawn `copilot -p` 的**递归风险是真实存在的**，guard 必要。
- ✅ `PT_CHILD_SESSION=1` guard 实测有效：带这个 env 的子会话里，5 个 Stop hook + SessionStart hook 都被 Copilot 调用了，但 python 侧 `is_child_session()` 全部早退、**零产出**（无 block、无注入、不再 spawn 下一层 judge）。fork-bomb 被挡住。
- 说明：full 模式 shadow judge 给子进程设的是 `PT_CHILD_SESSION=1` + `B5_SHADOW_DISABLED=1` + `B5_DETERMINISTIC_DISABLED=1` + `B5_INJECT_DISABLED=1` + `PT_SHADOW=0`，多重保险，子会话不会再起孙会话。（没跑真 LLM shadow 端到端以省额度，但 C 问的「guard 挡不挡得住」已实测通过。）

### 本次改动汇总
1. `~\.copilot\config.json`：注册 preference-tracker 进 `installedPlugins`（根因修复，live）。备份 `config.json.bak-pt-verify`。
2. `copilot/lib/session_start_inject.py`（source）+ installed 同名文件：SessionStart 注入默认改顶层 `additionalContext`（B 修复）。
3. （过程中临时把 installed `hooks.json` 改 nested 格式试错，已确认**与加载无关并还原**为原 flat 格式；备份 `hooks.json.bak-flat-format`。flat 格式本身没问题。）

### 还没做 / 建议下一步
- **A 最后一步**：注册后新开交互会话，手动确认真拦 + `[修正]`（30 秒）。
- **durability**：决定用 `/plugin` 正式安装，还是让 installer upsert `config.json`；别再靠手拷文件。
- 仓库改动（source 那份 `session_start_inject.py`）还没 commit。
- `doctor.py` 建议新增一项：检查本插件是否在 `~/.copilot/config.json` 的 `installedPlugins` 里（这次就是栽在这，doctor 没覆盖到）。
