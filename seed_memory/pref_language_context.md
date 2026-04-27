---
name: Language preference for outputs
description: 给用户看的用中文，交给别人的用英文
type: preference
domain: language
scope: global
confidence: high
priority_tier: 1
atomic_id: lang-pref-001
params:
  chinese_ratio_threshold: 0.1   # 默认 0.1. 全英文 reply 触发 (cr<阈值). 同学英文项目可调高到 0.3
  min_length: 200                # 默认 200. 短回复跳过 (避免 log/code 短输出错杀)
supersedes: []
status: active
lifecycle_state: confirmed
memory_kind: procedural
created: 2026-03-13
updated: 2026-04-13
originSessionId: 4e0c5990-2242-4d50-a848-d47c5cd82ede
---
**给用户看的** = 中文：分析、讨论、评分、内部文档、打分报告、debug 输出、task 设计说明、任何用户自己要看要审的内容。

**交给别人的** = 英文：论文、交给合作者/advisor 的报告、commit message、code、specs、LaTeX progress report（给 Overleaf 的）。

**Why:** 用户是中文母语者，自己看中文更快更舒服；但研究产出要给英文环境的合作者和 reviewer 看。

**How to apply:** 判断标准是"这个东西最终谁看" —— 如果只有用户自己看 → 中文；如果要发出去 / 交付 → 英文。两者兼有时，讨论部分中文，交付部分英文。
