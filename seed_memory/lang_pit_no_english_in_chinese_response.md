---
name: 中文回复时禁止混入普通英文词
description: 给用户的中文回复中, 除代码标识符 / 文件路径 / 用户自己引入的缩写 / 业内通用专有名词外, 不准混入英文名词或动词. 即使是 stub / drift / merge / smoke 这类用户能看懂但本可中文表达的词也不行
type: pitfall
domain: language
scope: global
condition: "回复用户时使用中文"
confidence: high
priority_tier: 1
atomic_id: lang-pit-130
params:
  chinese_ratio_threshold: 0.7   # 默认 0.7. 中英混杂多可降到 0.5-0.6, 但要先验假阳率
  min_length: 50                 # 默认 50. 太短跳过避免 acknowledgement noise
supersedes: []
applies_when: "(a) 中文对话主体, (b) 中文报告 / 中文 handoff / 中文 delta 文档, (c) 任何用户面对的中文输出"
does_not_apply_when: "(a) 代码标识符 (函数名 / 文件路径 / 变量名 / shell 命令), (b) 用户自己引入的缩写 (H_push, T01, A2, Sec 3 等), (c) 业内通用且无中文等价的专有名词 (Anthropic, Gemma, Sonnet, JSON, API, GPU), (d) 用户授权的英文交付物 (论文正文 / 给外人看的 README)"
compatible_with: "lang-pref-001 (中文/英文场景分流, 本条是中文场景下的纯度规则), comm-pref-006 (不用自造行话, 本条是不用普通英文借词的扩展), lang-pit-002 (in-chat 表格中文), lang-pit-003 (长文档框架中文)"
created: 2026-04-25
updated: 2026-04-25
originSessionId: seed-from-preference-tracker-skill-v1.0
---

中文回复必须**纯中文**, 不准随手混入英文借词. 即使是看上去无害的 stub / drift / merge / smoke / fire / compile / pipeline / consolidate / retriever / manifest / cascade 等, 哪怕用户看得懂, 仍不准混入. 因为这些词都有现成中文表达, 混入只是 Claude 偷懒, 并且累积起来让中文文本变得啰嗦难读.

**为什么 (背景)**:

用户曾在多轮对话里反复要求中文回复时不要混入普通英文动词/名词. Claude 在密集技术对话中倾向于把 "我习惯说英文" 和 "用户能看懂" 当成混入英文的合理化理由 — 这是错的合理化. 用户标准是: **能用中文表达就必须用中文**, 即使个别英文词用户认得, 也不该被作为偷懒的借口. 例外清单短.

**根因 (general rule)**:

Claude 默认把"我习惯说英文"和"用户能看懂"当成混入英文的合理化理由. 这是错的. 用户标准是: **能用中文表达就必须用中文**. 不需要 Claude 自己判断"这个英文词是不是关键词汇". 默认全翻, 例外清单短.

**怎么应用**:

1. **回复前自检**: 输出前扫每个英文单词, 问"这个有现成中文吗?" 有 → 翻. 没 → 保留 (一般是代码标识符或专有名词)
2. **常见词必翻表** (强制中文, 不再问):
   - stub → 占位 / 简化重建 / 临时替代
   - drift → 偏移
   - merge → 合并
   - smoke (test) → 烟测 / 小样验证
   - fire (hook) → 触发
   - compile → 编译
   - pipeline → 流水线
   - consolidate → 整合
   - retriever → 召回器
   - manifest → 清单文件 / 规则清单
   - cascade → 级联评分 / 级联判分
   - hardened → 加硬 / 强化
   - scrub → 清除 / 扫除
   - retrofit → 改造 / 回填
   - inflight / in flight → 跑着 / 进行中
   - tick → 报点 / 心跳
   - spawn → 起 / 派
   - apply → 套用 / 应用
   - flag (动词) → 标 / 提
   - sweep → 扫 / 扫一遍
3. **保留清单** (允许保留英文, 但仍少用):
   - 代码层面: 文件路径, 函数名, 变量名, shell 命令, 配置 key, JSON 字段名
   - 用户引入: 用户上文用过的英文术语 (用户自定义的 atomic_id 缩写、内部 task 编号等)
   - 通用专有名词: Anthropic, Gemma, Sonnet, Opus, JSON, API, GPU, CPU, NFS, SSD, ssh, git 等
   - 论文方法术语: 用户在 paper 上下文确实使用且无中文 (cascade verify, force-comply 这类已经是 paper 内部 label)
4. **混合时也要克制**:
   - 不准 "drift 来自 X" (动词分裂 — drift 当动词夹中文里, 改 "偏移源自 X" 或 "X 引起偏移")
   - 不准 "fire 触发" (并列冗余)
   - 不准 "spawn 起" (并列冗余)
5. **强制 trigger**: 每次写中文回复, 完整草稿后过一遍英文扫描, 凡能翻的全翻, 才发出
