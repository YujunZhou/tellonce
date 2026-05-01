# 装 `preference-tracker` skill

实时追踪并阻止 Claude Code 反复违反你已设的偏好 (例如中文回复混普通英文借词 / `/tmp/` 在生产代码里), 配合阶段 7 自适应阈值顾问。

适用环境: Linux / macOS (POSIX), Python 3.7+, Claude Code CLI.

---

## 一行命令装 (推荐 — 走 SSH 路径)

```bash
git clone git@github.com:YujunZhou/preference-tracker.git ~/.claude/skills/preference-tracker
cd /path/to/your/working/project    # 你 Claude Code 平时跑的项目根
bash ~/.claude/skills/preference-tracker/install.sh
```

如果你没配 SSH 公钥到 GitHub, 也可走 https:

```bash
git clone https://github.com/YujunZhou/preference-tracker.git ~/.claude/skills/preference-tracker
```

---

## 装完检查

```bash
bash ~/.claude/skills/preference-tracker/doctor.sh        # 12 组测试 + 真违规阻断冒烟
bash ~/.claude/skills/preference-tracker/dashboard.sh     # 7 天合规摘要 + 阈值建议
```

期望: doctor 全过, dashboard 显示尚无数据 (新装) 或最近触发记录。

---

## 升级

```bash
cd ~/.claude/skills/preference-tracker
git pull
bash doctor.sh    # 验旧版状态没坏
```

`git pull` 拉新版后, 已注册的钩子 / 已写入的偏好阈值 / 你的本地白名单 (`lib/deterministic_block_whitelist_user.txt`) 都会保留。

---

## 卸载

```bash
bash ~/.claude/skills/preference-tracker/uninstall.sh
```

默认保留你已积累的合规日志 / 状态目录 / 偏好记忆文件, 仅撤钩子注册 + 删 `~/.claude/skills/preference-tracker/` 子目录里的钩子拷贝。完全清除加 `--purge-state`.

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

3. **不上传** 任何数据到 yzhou25 / 其他用户机器, 也不发邮件 / Slack / GitHub.

4. **API key 计费**:
   - `lib/detect_user_prefer.py` 默认 OFF (PT_PREFER_BACKEND=off, C6 fix). 不调任何
     LLM, 总返 'urgent'. 想开自适应分类: `export PT_PREFER_BACKEND=cli` (订阅) 或
     `=sdk` (扣 ANTHROPIC_API_KEY).

5. **如何敏感数据 redact** (建议但不自动):
   - 现版本不自动扫 prompt 里的 secret. 在跟 Claude 聊 secret 之前自己负责
     (这本就是 Anthropic 服务条款的一般要求).
   - 未来版本会加 `B5_REDACT_BEFORE_JUDGE=1` env 自动 mask `sk-ant-` / `password=`
     等 pattern. 当前 Open issue (作者认为这条不适合默认开因为可能误删合法内容).

---

## 报问题 / 反馈

内测期间 (2026-04-27 起) 直接邮件 yzhou25@nd.edu, 不需走 issue tracker。

详细常见问题见 `FAQ.md` (15 条), 设计与实现见 `README.md`.
