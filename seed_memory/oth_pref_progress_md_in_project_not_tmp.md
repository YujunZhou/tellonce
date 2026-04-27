---
name: progress_report_status_md_in_project_not_tmp
description: Progress / status / report 类 MD 文件必须写在项目目录内 (experiment/ 或 blueprint/), 不放 /tmp; 结果数据 JSON 可放 /tmp
type: preference
domain: other
scope: global
confidence: high
priority_tier: 1
atomic_id: oth-pref-001
supersedes: []
status: active
lifecycle_state: confirmed
created: 2026-04-22
updated: 2026-04-22
originSessionId: "post-b0d00d07 session"
---
**规则**: User-facing 的 MD 文件 (progress / status / report / summary / handoff / audit) 必须写在项目目录内, **不放 /tmp**.

**对应位置**:
- `blueprint/` — 项目级 roadmap / NEURIPS paper plan
- `experiment/simulated_user/` — simulated user 模块相关 report
- `experiment/pilot_results/` — 实验结果分析 md
- `experiment/rule_extraction/output/` — 数据产物相关 md

**`/tmp/` 允许放**:
- Raw 结果 JSON / JSONL (intermediate data)
- Script 的 debug log
- 大文件 Intermediate 不值得进 git 的

**对已存在的 /tmp 下 user-facing md**: 搬到项目对应目录, 旧 /tmp 版本保留 symlink 或删除.

applies_when: 写 status / progress / report / summary / audit / handoff 类 user-facing MD 时
does_not_apply_when: 纯 intermediate JSON 数据; 临时 debug log; 非 user-facing 的 Claude 内部 artifacts

**Why:** 2026-04-22 user: "progress 的 md 不要写在 tmp 里, 就写在这个项目里". /tmp 是 ephemeral, 重启 / 清 cache 就丢; user 要 review 的文档应该跟项目一起有持久路径 + 能被 future session 找到.

**How to apply:** 写 `.md` 之前先问 "这是给 user 看的吗"? 是 → 项目目录. 不是 → /tmp 可以. **不确定就放项目里**(宁误放项目不误放 /tmp).
