# Preference-Tracker

In-session enforcement system: detect & 阻断 LLM agent reply 里反复违反的 user preferences. 当前提供两个平台实现:

| 平台 | 路径 | 装包 | 机制 |
|---|---|---|---|
| **Claude Code** | repo 根 (本目录) | `bash install.sh` | Stop hook 链, 5-hook UserPromptSubmit + 5-hook Stop, deterministic 硬阻断 + LLM 影子判官 + 软注入 |
| **Codex** | `codex/` 子目录 | `bash codex/install.sh` | Wrapper-driven (`codex_preftrack exec`), 项目本地 ledger (`.codex/preference-tracker/events.jsonl`), audit-only / wrapper / hooks_experimental 三档 |

两端共享同一份 user preference memory + 设计哲学 (Iron Law / Gate Function / scan→record→confirm), 但底层机制按平台 runtime surface 适配 — Codex 没 Stop hook, 改走 wrapper 路径. 详见 [`codex/docs/CC_PARITY_MATRIX.md`](codex/docs/CC_PARITY_MATRIX.md).

**Status**: CC v1.0 (2026-04-27 ship 同学内测) + Codex v1 core (2026-04-29)
**测试**: CC 38+ unit + 12 chaos + 1 smoke; Codex 23 core unit
**Paper**: arXiv (NeurIPS 2026 submission, paper Sec 6 dogfood port)

下文说 Claude Code 版. Codex 版见 [`codex/docs/README.md`](codex/docs/README.md).

---

## 一句话原理

每次 Claude `Stop` (回复结束), 后台跑 3 条 deterministic 规则 (中文混英文借词 / `/tmp/` 在 active code / 全英文长 reply 跟中文 prompt 不匹) + LLM 影子判官. 触发硬阻断时, Claude 在同 turn 续写修正; 软违规生成 alert 下 turn `additionalContext` inject "上轮违反 X" 提示.

---

## 装

```bash
# 1. 拷 skill 整目录到 ~/.claude/skills/
cp -r /path/to/preference-tracker ~/.claude/skills/

# 2. 跑 install.sh 一键装
cd /path/to/your/project   # 同学的项目根
bash ~/.claude/skills/preference-tracker/install.sh
```

`install.sh` 5 段全鲁棒 (准备 → 安装 → 收集 → 执行 → 卸载机制就绪): 自动 detect cwd / OS user / Python / Claude CLI; versioned 备份 settings; 跑 doctor 自检 PASS 才算装好; 失败 trap ERR rollback.

---

## 关键路径

| 项 | 默认 | Override |
|---|---|---|
| skill | `~/.claude/skills/preference-tracker/` | (固定) |
| hooks | `<cwd>/.claude/hooks/` (install 时 cp) | (固定) |
| state | `<cwd>/.claude/preference-tracker-state/runtime/` | env `B5_STATE_DIR` 或 `~/.preference-tracker.config.json` |
| obs_log | `<cwd>/.claude/preference-tracker-state/obs_log/` | env `B5_OBS_LOG_DIR` 或 config |
| memory | `~/.claude/projects/<cwd_escaped>/memory/` | env `B5_MEMORY_DIR` 或 config |

---

## 怎么 disable / 自定义

```bash
# 关三层 (任一组合)
export B5_DETERMINISTIC_DISABLED=1   # 关硬阻断
export B5_SHADOW_DISABLED=1          # 关 LLM 影子判官
export B5_INJECT_DISABLED=1          # 关软注入

# 阈值 (frontmatter 没设 params 时用 default; 自适应阈值见 Phase 7)
# (默认: lang-pit-130 chinese_ratio>=0.7 + length>50;
#       lang-pref-001 chinese_ratio<0.1 + length>200;
#       oth-pref-001 active code block 含 /tmp/)

# 加专名 whitelist (per-user 增量, 不动全局)
echo "MyProject" >> ~/.claude/skills/preference-tracker/lib/deterministic_block_whitelist_user.txt
echo "MyAdvisor" >> ~/.claude/skills/preference-tracker/lib/deterministic_block_whitelist_user.txt
# 不需 reload, 下次 hook 调用自然读

# 影子判官走 SDK 不走 CLI (浪费 API 额度, 默认 False)
export B5_USE_SDK=1

# Cost cap
export B5_DAILY_COST_CAP=1.00   # default 0.50 USD
export ANTHROPIC_CREDIT_OK=1     # default 1 (CLI 模式不读); SDK 模式必须 set

# Streak bypass (同 rule 连续触发 N 次自动放行)
export B5_STREAK_BYPASS=3        # default 3
```

---

## 跑 dashboard 看效果

```bash
bash ~/.claude/skills/preference-tracker/dashboard.sh
# 输出最近 7 天:
# - deterministic block 次数 (按 rule 分桶)
# - shadow violation 数 (alerted vs filtered)
# - judge 失败率 / cost / latency
# - 推荐改阈值 (待实施 threshold_advisor.py)
```

---

## 误杀防御 (FAQ 入口)

| 场景 | 现象 | 处理 |
|---|---|---|
| 中文回复用 `PostgreSQL` / `Redis` 被错杀 | exit 2 + lang-pit-130 触发 | whitelist 全局已含; 若仍有: `echo X >> whitelist_user.txt` |
| 全英文 log dump 被错杀 | exit 2 + lang-pref-001 触发 | 暂关: `export B5_DETERMINISTIC_DISABLED=1` 或在 prompt 明示 'in english' |
| 同 rule 反复触发 transcript 长 | streak 计数到 3 → 自动 bypass 该 rule 该 session | 不需手动; rule 仍写 log |
| install 跑一半失败 | settings 回滚, hooks 部分留 | 重跑 install (idempotent) 或 doctor.sh --rollback |

详见 `FAQ.md`.

---

## 卸载

```bash
bash ~/.claude/skills/preference-tracker/uninstall.sh

# 完全删 (含 state + obs_log):
bash ~/.claude/skills/preference-tracker/uninstall.sh --purge-state

# 保留 skill 目录 (重装方便):
bash ~/.claude/skills/preference-tracker/uninstall.sh --keep-skill-dir
```

uninstall 默认**不动** memory + state + obs_log (用户数据保留, 重装恢复).

---

## 故障排查

```bash
# 跑 doctor 自检
bash ~/.claude/skills/preference-tracker/doctor.sh

# 跑 doctor 仅 unit-level (跳 subprocess):
bash ~/.claude/skills/preference-tracker/doctor.sh --quick

# 装坏了一键回滚 settings:
bash ~/.claude/skills/preference-tracker/doctor.sh --rollback

# 看 install log:
cat ~/.claude/skills/preference-tracker/install.log

# 查 path_config detect 出来什么:
python3 ~/.claude/skills/preference-tracker/lib/path_config.py
```

---

## 架构图

```
UserPromptSubmit chain (5):
  preemptive-scan-reminder.sh
  memory-retrieve-inject.sh    [B1 fingerprint retrieve, atomic_id 注入]
  memory-pending-inject.sh     [pending memory 跨 session 提醒]
  memory-shadow-alert-inject.sh [B5 软注入: 上轮违反 X]
  skill-autoload-gate.sh

→ Claude generates response

Stop chain (5):
  check-observation-log.sh        [Iron Law: obs log 必 append]
  memory-deterministic-block.sh   [B5 Tier A item 1: 3 regex 硬阻断]
  memory-verify-compliance.sh     [B3-lite + B4 拒停 gate]
  memory-shadow-judge.sh          [B5 Tier A item 2: LLM judge log-only]
  memory-pending-promote.sh       [pending obs → queue]
```

阻断 / 通过 / cost / streak 写到 `<state>/runtime/{b5_*, b4_*}/`. 7 天 audit 跑 `dashboard.sh`.

---

## License + Citation

Internal research artifact (paper Sec 6 dogfood port). 学术使用请 cite paper:
> Anonymous. *Self-Improving Agents via Closed-Loop Preference Compilation*. NeurIPS 2026 (under review).

---

**Files**:
- `SKILL.md` — Claude Code skill 入口 (Iron Law + Gate Function + 详细规范)
- `README.md` — 本文 (装/用/卸/故障)
- `FAQ.md` — 15 条常见问题
- `install.sh` / `doctor.sh` / `uninstall.sh` / `dashboard.sh`
- `lib/` — 8 lib `.py` + path_config + tests
- `hooks/` — 8 hook `.sh` wrappers
- `seed_memory/` — 3 enforce rules (装时 cp 给新 user 如不存在)
