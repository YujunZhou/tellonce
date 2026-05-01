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
