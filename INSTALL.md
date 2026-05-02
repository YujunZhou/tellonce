# 装 `preference-tracker` skill

实时追踪并阻止 Claude Code 反复违反你已设的偏好 (例如中文回复混普通英文借词 / `/tmp/` 在生产代码里), 配合阶段 7 自适应阈值顾问。

适用环境: Linux / macOS (POSIX), Python 3.7+, Claude Code CLI.

---

## 第一步: 拉源代码 (两种方式都行, 二选一)

**SSH (推荐, 已配 GitHub 公钥):**

```bash
git clone git@github.com:YujunZhou/preference-tracker.git ~/.claude/skills/preference-tracker
```

**HTTPS (没配 SSH):**

```bash
git clone https://github.com/YujunZhou/preference-tracker.git ~/.claude/skills/preference-tracker
```

---

## 第二步: 注册 hooks — 二选一

### 方式 A: 装到 user-global (推荐 — 一次装, 所有项目自动生效)

```bash
python3 ~/.claude/skills/preference-tracker/lib/_install_merge_settings.py --settings ~/.claude/settings.json --hooks-dir ~/.claude/skills/preference-tracker/hooks --add
```

写入 `~/.claude/settings.json` (CC user-global), 你以后任何 `cd` 进的目录跑 Claude Code 都自动生效。state / memory / obs_log 仍按当前 cwd 自动分项目, 项目之间数据不串。

**适合**: 想给多个项目都开 PT (比如同时改几个 paper / 几个 repo) 的人, 不想每个项目装一次。

**临时关单个项目** (不想要 PT 跑的那个 shell):

```bash
export B5_DETERMINISTIC_DISABLED=1 B5_SHADOW_DISABLED=1 B5_INJECT_DISABLED=1
```

### 方式 B: 装到单个项目 (传统)

```bash
cd /path/to/your/working/project    # 你 Claude Code 平时跑的项目根
bash ~/.claude/skills/preference-tracker/install.sh
```

写入 `<project>/.claude/settings.local.json` (项目本地, gitignore), 只在该项目生效。会同时初始化 state 目录 + 写 `~/.preference-tracker.config.json` 锚定 PROJECT_ROOT.

**适合**: 只想给 1-2 个项目开 PT, 其他项目完全干净的人。

**多个项目就重复跑**: 每个项目目录里跑一次 `bash install.sh`. 互不影响。

---

## Codex 装法 (跟 CC 平行, runtime 都装一次)

```bash
# 跟 CC 共享同一份代码: ~/.claude/skills/preference-tracker/codex/install.sh
# 或者直接从 repo 跑
cd /path/to/your/codex/project
bash ~/.claude/skills/preference-tracker/codex/install.sh
```

`codex/install.sh` 三段:

1. **Global runtime** → `~/.codex/skills/preference-tracker/` 装入
   `codex_preftrack/` (wrapper-driven 强制) + `shared_lib/` (CC 端 lib 镜像) +
   `hooks/` (5 个 hook 脚本) + `seed_memory/` + `SKILL.md`. 幂等, 重跑安全。
2. **Hooks 注册** → `~/.codex/hooks.json` 加 `UserPromptSubmit` (3 hook) +
   `PostToolUse` (deterministic_block) + `SessionStart` (lazy init). 不动 user
   原有 hook (gws-axi 之类保留).
3. **Per-project state** → `<project>/.codex/preference-tracker/` 初始化
   (registration.json + mode.json + install_record.json), 默认 audit_only.

升 blocking 模式: 改 `<project>/.codex/preference-tracker/mode.json` 的 `mode`
字段为 `"blocking"`. monotone — 以后 install 命令 / wrapper 不会自动降回去。

Codex doctor:
```bash
PYTHONPATH=~/.codex/skills/preference-tracker python3 -m codex_preftrack doctor
```

期望: `state=PASS, private_paths=PASS, wrapper=NOT_USED, hooks=PASS, install=OBSERVE_ONLY`. wrapper=NOT_USED 是默认 (没跑过 `codex_preftrack exec --` 之前都这样, 不算错).

Codex uninstall:
```bash
bash ~/.claude/skills/preference-tracker/codex/uninstall.sh                    # 保留 state + hooks + skill dir
bash ~/.claude/skills/preference-tracker/codex/uninstall.sh --purge-hooks     # 撤 ~/.codex/hooks.json 注册
bash ~/.claude/skills/preference-tracker/codex/uninstall.sh --purge-skill     # 删 ~/.codex/skills/preference-tracker
bash ~/.claude/skills/preference-tracker/codex/uninstall.sh --purge-state     # 删本项目 state
```

---

## 升级 (从老版本 / 老安装方式切到新的)

如果你之前装过老版本 (注册的是 `<project>/.claude/hooks/...` 这种 project-local 路径), 强烈建议升级 — 老路径有 hostile-repo RCE 风险 (Round-4 C1 fix), 新版只注册 `~/.claude/skills/preference-tracker/hooks/...` 不可被项目覆盖。

```bash
# 1. 拉最新代码
cd ~/.claude/skills/preference-tracker && git pull

# 2a. 老的 per-project 安装 → 重跑 install.sh, 它会自动撤老注册写新注册
cd /path/to/old-pt-project && bash ~/.claude/skills/preference-tracker/install.sh

# 2b. 想顺便改成 user-global → 先撤老 per-project 注册再装 global:
cd /path/to/old-pt-project
python3 ~/.claude/skills/preference-tracker/lib/_install_merge_settings.py --settings .claude/settings.local.json --hooks-dir .claude/hooks --remove
python3 ~/.claude/skills/preference-tracker/lib/_install_merge_settings.py --settings .claude/settings.local.json --hooks-dir ~/.claude/skills/preference-tracker/hooks --remove
python3 ~/.claude/skills/preference-tracker/lib/_install_merge_settings.py --settings ~/.claude/settings.json --hooks-dir ~/.claude/skills/preference-tracker/hooks --add
```

升级保留: 已注册的钩子 / 已写入的偏好阈值 / 你的本地白名单 (`lib/deterministic_block_whitelist_user.txt`) / 项目里已积累的 state + memory.

---

## 装完检查

```bash
bash ~/.claude/skills/preference-tracker/doctor.sh        # 12 组测试 + 真违规阻断冒烟
bash ~/.claude/skills/preference-tracker/dashboard.sh     # 7 天合规摘要 + 阈值建议
```

期望: doctor 全过, dashboard 显示尚无数据 (新装) 或最近触发记录。

---

## 卸载

**user-global 模式装的, 想撤:**

```bash
python3 ~/.claude/skills/preference-tracker/lib/_install_merge_settings.py --settings ~/.claude/settings.json --hooks-dir ~/.claude/skills/preference-tracker/hooks --remove
```

**per-project 模式装的, 想撤:**

```bash
cd /path/to/your/project
bash ~/.claude/skills/preference-tracker/uninstall.sh
```

默认保留你已积累的合规日志 / 状态目录 / 偏好记忆文件, 仅撤钩子注册。完全清除加 `--purge-state`. 想顺便删老安装在 `<project>/.claude/hooks/` 里的 .sh (PT v1+ 不再管理这些文件, 默认保留以防误删用户自己的同名 hook): `--purge-legacy-project-hooks`.

---

## Retrieve backend 选择 (Round-10 起默认 cli)

UserPromptSubmit hook 默认走 **CLI 小模型语义匹配** (`B5_RETRIEVE_BACKEND=cli`):

| 运行时 | 用什么 CLI | 默认模型 | 通道 |
|---|---|---|---|
| Claude Code | `claude -p` | `claude-haiku-4-5` | Pro/Max 订阅 quota, 0 元 |
| Codex | `codex exec --ephemeral` | `gpt-5.4-mini` | Codex 订阅 quota, 0 元 |

每条 prompt 多 1-2s 延迟, 但语义匹配命中率远高于关键词. 论文实验跑想呈现最佳 retrieve 效果就用这个默认.

### Trade-off

| 模式 | 命中率 | 延迟 | 成本 | 维护 |
|---|---|---|---|---|
| `cli` (默认, Round-10 起) | 语义级, 同义词自动覆盖 | 1-2s/prompt | 0 (订阅 quota) | 不用写 trigger 关键词 |
| `keyword` (legacy) | 取决于 trigger 写得全不全 | <10ms | 0 | 每条 rule 写 `triggers` |

### 切换回 keyword (如果想要快速无 LLM 路径)

```bash
echo 'export B5_RETRIEVE_BACKEND=keyword' >> ~/.bashrc
```

### 显式选 CLI / 模型 (如果想 override per-runtime 默认)

```bash
export B5_RETRIEVE_CLI=claude       # claude (CC 默认) 或 codex (Codex 默认)
export B5_RETRIEVE_MODEL=claude-haiku-5    # 默认 haiku-4-5 (claude) / gpt-5.4-mini (codex)
export B5_RETRIEVE_TIMEOUT=12       # 秒, default 12
```

### 已知限制

- nested CLI 嵌套调用本身会 fire UserPromptSubmit hook → 无穷递归. retrieve_inject 用 `B5_RETRIEVE_RECURSION_GUARD=1` env 在 child 里自动设, hook 脚本头部检测后 exit 0, 阻断递归
- 调用失败 (CLI 不在 / timeout / 输出非 JSON) 自动退化到 `keyword` backend, 你不会因为切换丢功能
- 想看实际有没 work: `export B5_RETRIEVE_DEBUG=1`, log 写到 `<state>/runtime/retrieve_debug.jsonl`, 包括 latency / stdout 长度 / 解析的 atomic_id 列表

---

## 隐私 / 数据流向 (装前必读)

**Preference-tracker 会在每次 Stop / UserPromptSubmit 触发以下数据流动**:

1. **Anthropic CLI / API** (默认 ON):
   - `lib/verify_retry_shadow.py` 在每个 Stop 调 `claude -p` 子进程做合规判官,
     传 `last_user[:400] + response[:4000]` 给 Anthropic 服务器
   - 默认走 CLI 订阅 (0 元), 但 prompt 内容仍经 Anthropic
   - 关闭: `export B5_SHADOW_DISABLED=1`

2. **本地落盘** (默认 chmod 600 — 仅 user 自己可读, H10 fix):
   - `<state>/runtime/b5_shadow_alerts/b5_shadow_log.jsonl` — 含 evidence + feedback excerpts
   - `<state>/obs_log/compliance_log.jsonl` — 含 response_excerpt[:400]
   - `<state>/runtime/b5_shadow_alerts/B5_SHADOW_ALERT.md` — 含 latest 3 violation 全文
   - `~/.claude/projects/<cwd_escaped>/memory/*.md` — 用户自己加的偏好记录

3. **不上传** 任何数据到第三方 (除上述 Anthropic CLI/API 通道), 也不发邮件 / Slack / GitHub.

4. **API key 计费**:
   - `lib/detect_user_prefer.py` 默认 OFF (PT_PREFER_BACKEND=off, C6 fix). 不调任何
     LLM, 总返 'urgent'. 想开自适应分类: `export PT_PREFER_BACKEND=cli` (订阅) 或
     `=sdk` (扣 ANTHROPIC_API_KEY).

5. **如何敏感数据 redact** (建议但不自动):
   - 现版本不自动扫 prompt 里的 secret. 在跟 Claude 聊 secret 之前自己负责
     (这本就是 Anthropic 服务条款的一般要求).
   - 未来版本可能加 `B5_REDACT_BEFORE_JUDGE=1` env 自动 mask `sk-ant-` / `password=`
     等 pattern; 当前不默认开是因为正则可能误删合法内容.

---

## 报问题 / 反馈

GitHub Issues: https://github.com/YujunZhou/preference-tracker/issues

详细常见问题见 `FAQ.md` (15 条), 设计与实现见 `README.md`.
