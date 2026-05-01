# FAQ — Preference-Tracker

15 条常见问题. 详细架构见 `README.md` + `SKILL.md`.

---

## 装包 / 卸载

### Q1: 装到一半失败怎么办?

`install.sh` 用 `set -euo pipefail` + `trap ERR`, 失败自动 rollback settings 备份 (但 hooks .sh / state / memory 保留留 debug). 重跑 `install.sh` 是幂等的, 不会重复注册 hook 或重创 state.

如果 settings 回滚没成功, 手动:
```bash
ls -t ~/your-project/.claude/settings.local.json.v3_pre_pt_*.json | head -1
# cp 那个 latest backup 回 settings.local.json
```

或:
```bash
bash ~/.claude/skills/preference-tracker/doctor.sh --rollback
```

---

### Q2: 装好了但 Claude 不阻断, 怎么 verify?

```bash
# 跑 doctor 自检
bash ~/.claude/skills/preference-tracker/doctor.sh

# 验 hooks 在 settings.local.json 注册:
python3 ~/.claude/skills/preference-tracker/lib/_install_merge_settings.py \
    --settings ~/your-project/.claude/settings.local.json \
    --hooks-dir ~/your-project/.claude/hooks \
    --verify

# Manual smoke: 故意触发违规
echo '{"session_id":"test","transcript_path":"/tmp/t.jsonl"}' | \
    python3 ~/.claude/skills/preference-tracker/lib/deterministic_block.py
# (期望 exit 2 / 0 取决于 transcript 内容)
```

---

### Q3: 卸载干净吗? memory 会丢吗?

`uninstall.sh` 默认**不动** memory + state + obs_log. 只撤 hooks 注册 + rm hooks .sh. 重装可恢复全部状态.

完全删用 `--purge-state` flag.

---

### Q4: 装了影响别的 hook / skill 吗?

`install.sh` 改 settings.local.json 是 **additive** (追加, 不删现有). 改前 versioned cp. 重跑用 set 语义去重 (idempotent), 不重复加同一 hook.

---

## 误杀

### Q5: 中文回复用 `PostgreSQL`/`Redis`/`React` 被错杀!

全局 219 条 whitelist 已含主流 DB / framework. 若漏了:
```bash
echo "新词" >> ~/.claude/skills/preference-tracker/lib/deterministic_block_whitelist_user.txt
```
一行一个, # 开头注释行 skip, 不区分大小写. 不需 reload.

或临时 disable:
```bash
export B5_DETERMINISTIC_DISABLED=1
```

---

### Q6: 我贴 stack trace / log 给 Claude debug, 它 reply 全英文被错杀

`lang-pref-001` 触发 (chinese_ratio<0.1 + length>200). 在 prompt 里明示就 bypass:
- "请帮我看这个 log, in english 也行" → bypass (`in english` 关键词)
- "draft the abstract for the paper" → bypass (paper 关键词)

或 hook level:
```bash
export B5_DETERMINISTIC_DISABLED=1
```

---

### Q7: 同一 rule 反复触发, transcript 越来越长

Streak 安全阀: 同 rule 连续 3 次后该 rule 在剩余 session 自动 bypass. 不需手动. 阈值 `B5_STREAK_BYPASS=3` 可调.

---

## 配置 / 阈值

### Q8: 怎么改阈值?

**简易版** (Phase 7 已实施): 改 enforce rule 的 memory `.md` frontmatter `params:` 块:
```yaml
---
atomic_id: lang-pit-130
params:
  chinese_ratio_threshold: 0.55   # default 0.7
  min_length: 80                   # default 50
---
```
不需 reload. 下次 hook 调用读 frontmatter.

**完整版 threshold_advisor.py**: 跑数据建议改阈值, 用户拍板. 见 `lib/threshold_advisor.py` 顶端 docstring.

---

### Q9: 影子判官花我多少 API 钱?

默认走 `claude -p` CLI (订阅, 0 额度). 设 `B5_USE_SDK=1` 切 SDK (按 token 收费).

cost cap default `$0.50/天`, 触发后当天 disable. 改:
```bash
export B5_DAILY_COST_CAP=1.00
```

---

### Q10: 没装 Claude CLI 怎么办?

shadow judge 跑不了, deterministic 仍 work. 在 `.bashrc` set:
```bash
export B5_SHADOW_DISABLED=1
```
或装 Claude Code: https://claude.com/code

---

### Q11: 阻断后 Claude 续写很啰嗦怎么办?

`build_block_reason` 已含禁令:
- 不道歉 / 不重述 / 不解释规则 / 不铺垫
- 强制 `[修正]` 前缀
- 软注入静默语气 (不鼓励本轮前置铺垫)

如果仍啰嗦, 报 issue, 我们调禁令文本. 测试期间 streak >= 3 就放行该 rule.

---

## 跨平台 / 跨用户

### Q12: 用户项目结构跟我不一样, 路径 detect 错怎么办?

`install.sh` 默认 detect: `<cwd>` 是项目根, hooks 装到 `<cwd>/.claude/hooks/`, state 在 `<cwd>/.claude/preference-tracker-state/runtime/`, memory 在 `~/.claude/projects/<cwd_escaped>/memory/`.

不对就 override:
```bash
B5_STATE_DIR=/custom/state bash install.sh
B5_OBS_LOG_DIR=/custom/obs bash install.sh
B5_PROJECT_ROOT=/custom/project bash install.sh
```

或写 `~/.preference-tracker.config.json`（schema:
`{"project_root":"...","state_dir":"...","obs_log_dir":"...","memory_dir":"...","whitelist_user":"..."}`，
任一字段没设走自动 detect）.

---

### Q13: macOS / Windows 兼容?

- macOS: `~/.claude/` 路径相同, bash + python3 通. install.sh 直接 work.
- Windows: 没测. WSL 可能 work (POSIX 兼容). 不保证 native PowerShell.
- Linux (Ubuntu / RHEL / HPC clusters): 主测目标, work.

---

### Q14: Codex / OpenClaw runtime 怎么用?

Claude Code 是主线 (本仓库 lib/ + hooks/). Codex 用 wrapper-driven 适配器, 见 `codex/` 子目录 — 安装 `bash codex/install.sh`, 详见 `codex/SKILL.md`.

---

## 故障排查

### Q15: hooks 没被调用, settings 看起来对的

```bash
# 1. 验 hooks 文件 executable
ls -la <project>/.claude/hooks/memory-*.sh
# 应有 -rwxr-xr-x 权限. 不是的话 chmod +x

# 2. 看 install.log:
cat ~/.claude/skills/preference-tracker/install.log | tail -50

# 3. 跑 doctor 全检:
bash ~/.claude/skills/preference-tracker/doctor.sh

# 4. 手动跑 hook 看输出:
echo '{"session_id":"test","transcript_path":"/tmp/dummy.jsonl"}' | \
    bash <project>/.claude/hooks/memory-deterministic-block.sh
# (应 exit 0, 因 transcript 不存在)

# 5. 看现 path_config detect 出来什么 (env / config / default):
python3 ~/.claude/skills/preference-tracker/lib/path_config.py
```

---

更多 issue:
- GitHub Issues: https://github.com/YujunZhou/preference-tracker/issues
- bug / 误杀 / 想加 whitelist / 阈值不灵 → 开 issue 时附 doctor.sh 输出 + dashboard 7 天数据更易诊断
