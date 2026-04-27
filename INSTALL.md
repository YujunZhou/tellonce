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

## 报问题 / 反馈

内测期间 (2026-04-27 起) 直接邮件 yzhou25@nd.edu, 不需走 issue tracker。

详细常见问题见 `FAQ.md` (15 条), 设计与实现见 `README.md`.
