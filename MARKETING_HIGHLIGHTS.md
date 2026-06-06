# 宣传亮点与卖点（Marketing Highlights）

> 用途：公开发布的定位、卖点、文案、use-case 素材库。
> 创建：2026-06-05 ｜ 来源：作者自己的想法 + 三个 brainstorm agent（定位/用例/竞品，分别用 Opus / GPT-5.5 / Gemini）。
> 维护：追加为主，挑用得上的进 README / launch post。

---

## 0. 作者自己想到的亮点（原始，勿删）

1. **即插即用**（plug-and-play）。
2. 最核心：**作为 preference 的记录者，让开发更不生气**——agent 不再重复犯你已经纠正过的错。
3. **轻量化的「流程编译执行」**：用命令行/自然语言把一个任务流程规定一次，低代价记住，之后通过 compile-force 让 agent 严格按流程执行。**远比 skill-creator 之类轻量**。
   - 触发这个想法的例子：让 agent 去做 citation 的 .bib collect 并确保安全，它做得很好。
   - （需要更 general 的例子，见第 5 节。）

---

## 1. 一句话定位 / Taglines（候选，挑 1-2 个主打）

- **"Your AI coding agent has amnesia. This fixes it."**（推荐主打——痛点即时可懂，3 秒传达价值）
- **"Teach once, remembered forever."**（学习持久性）
- **"Your coding agent finally stops pissing you off."**（情绪共鸣）
- **"One correction. Zero repeats."**（极简因果）
- **"The `.gitignore` for your agent's bad habits."**（熟悉概念类比）
- **"Preferences as infrastructure, not repetition."**（技术定位）

---

## 2. Hero 价值主张（对没听说过的人讲）

1. **纠正一次，永久生效**：你说"别用 /tmp"、"讨论用中文"、"跑全量前先 3 样本预检"——它记住，**跨 session、跨项目**都记住。你不用再当人肉 system prompt 维护员。
2. **用一句大白话定义流程，agent 自动强制执行**：不用学 YAML/DSL，也不用写完整 custom skill。一句话描述你的安全流程（"先从 DOI 解析 bib，永远别改 citation key"），它编译成确定性执行规则。**比写 skill 轻一个数量级**。
3. **装一次、全局生效、默认不侵入**：观察模式零风险——不阻断、不调 LLM、不发数据。想要硬执行？一个开关。**渐进信任模型**。

---

## 3. 目标早期用户（Ideal Early Adopter）

> 一句话痛点："**我已经告诉它 5 遍了，它还是把文件写到 /tmp。**"

- Copilot CLI / Claude Code 重度用户（日均 20+ turns），有稳定个人规范但 agent 记不住。
- 做研究/写论文的开发者——流程有严格顺序（citation 规范、数据 pipeline 安全），agent 乱来代价高。
- 多项目切换的工程师——受够了每个新 session 重新 onboard agent。

---

## 4. 竞品差异化矩阵（它们让你做什么 / 痛点 vs 我们怎么解）

| 对手 | 它们的痛点 | preference-tracker 怎么解 |
|------|-----------|---------------------------|
| **skill-creator / 写完整 Skill** | 重型开发：手写文档 + schema + 测试，门槛高 | **自然语言即编译**：日常对话里定规矩，自动转成确定性 hook 执行，零代码 |
| **Cursor Rules / Copilot 指令 / CLAUDE.md** | 静态提示词，靠模型"听话"，长上下文注意力漂移、被忽略，要手动维护 | **Hook 级物理拦截**：规则在 `Stop` 阶段硬阻断/软注入，不是建议而是保证 |
| **内置 model-managed memory** | 软性记忆，模型自己决定记不记、用不用，跨会话常失效 | **观察 + 强制双轨**：把记忆变成不可绕过的系统级护栏 |
| **obra/superpowers（方法论包）** | 自上而下的既定流程（如 TDD），靠用户 invoke / agent 自觉 | **自下而上的护栏**：不定义流程，但强制执行你选的任何流程（互补，见第 8 节）|
| **裸 prompt engineering / 上下文文件** | 堆系统提示词→上下文臃肿、稀释注意力、涨成本 | **按需软注入**：平时上下文干净，检测到违规风险才在下轮精准注入提醒 |

### 3 个真正的独家卖点
1. **基于 Hook 的确定性执行力**——别人在 prompt 层"说服"模型，我们在 pipeline 层"阻断"模型，错误甚至不会发生。
2. **无感知的摩擦力捕获**——不用打开配置文件写规则，后台从你的日常纠正里自动提取并固化为跨项目规则。
3. **影子判官安全兜底**——复杂、正则写不出的偏好，由独立 LLM judge 后台非阻塞评判，只记录或温和提醒，强制力与心流平衡。

### 核心护城河（Wedge）
**消除"给 agent 当监工"的隐性成本**。开发者最恨的不是 AI 犯错，而是它**在不同项目/会话里重复犯已纠正过的同一个错**。复利效应：用得越久，个性化护栏越厚，迁移成本越高。

---

## 5. 通用 use-case 素材库（替代"citation bib"的更 general 例子）

> 模式：开发者一句话定义流程 → 之后每次自动 compile-force。⭐ = 最适合 launch 主打。

1. ⭐ **"完成"前必须自证（Definition of Done）**：代码变更宣称完成前，必须跑相关测试 + 看 git diff + 列验证证据；没证据不准说"完成了"。
2. ⭐ **破坏性命令必须先 dry-run**：任何 `rm` / `DROP` / `DELETE` / 迁移前先 dry-run 展示将影响的对象与数量再执行；没 dry-run 不准跑。
3. ⭐ **尊重项目包管理器**：装依赖前先认 lockfile（pnpm/conda/poetry…），只能用项目指定工具并更新对应 lockfile；用错包管理器拦截。
4. **主分支危险 Git 操作零容忍**：永不在 main 上 force-push / hard reset / 改历史，需要时先开分支。
5. **Secrets 只走环境变量**：任何 key / 连接串只能从 env 读，禁止写进代码、README、测试 fixture；提交/输出前扫描拦截。
6. **架构边界不被绕过**：新代码必须沿用分层（UI → service → repository），不能跨层 import；违反则阻止完成。
7. **Release/Deploy 固定 checklist**：version、changelog、tests、migration、rollback 五项缺一不准说"ready to release"。
8. **ML/数据任务先小样本再全量**：长跑前先小样本跑通 + 固定 seed + 存 config/日志路径；否则不准启动全量或宣称实验有效。
9. **输出格式保持团队风格**：状态更新必须"结论 / 风险 / 下一步 / 需你决定"四栏；不符就重写。
10. **Bugfix 必须先复现**：修前先复现失败，修后用同一命令证明失败消失；没 before/after 证据不准说"已修复"。

---

## 6. 质疑与回击（提前准备）

| 质疑 | 回击 |
|------|------|
| "又一个 system prompt wrapper？我写 `.cursorrules` 就行。" | 那是静态文件、要手动维护、还得自己想到写什么；我们是**动态**从对话自动抓取你的纠正，而且能 **enforce**（不只是建议）。你在 `.cursorrules` 写"别用 /tmp"然后照样被无视过吧？ |
| "每条消息跑 hook，会拖慢吗？" | 默认是本地正则 + 写文件，<50ms，无 LLM、无网络。影子判官 opt-in 且异步不阻塞。比你等 agent 出字小两个数量级。 |
| "它读我对话，隐私？" | 默认 100% 本地，偏好只写你自己机器；影子 LLM 模式显式 opt-in（`PT_SHADOW=1`）且发送前自动脱敏 key/password。代码不出本机，除非你主动开那个开关。 |

---

## 7. 诚实的弱点（messaging 要提前管理）

1. **对宿主 CLI hook 机制强耦合**：依赖 Copilot/Claude/Codex 未完全文档化的 hook，官方重构底层可能让它瘫痪。
   - 话术：定位为前沿"机制探索者"，并用 wrapper 模式提供跨平台 fallback。
2. **延迟与 token 成本**：扫描/影子判官增加每轮几秒 + token。
   - 话术：用机器算力换人类心智；提供 dashboard 展示 ROI（拦了多少低级错误）+ 关闭开关 + cost cap，控制权给用户。

---

## 8. 互补集成（不是零和）

- **× superpowers**：superpowers 提供好动作（如 TDD）；preference-tracker 当"政委"——发现 agent 写业务代码却没走 TDD，就软注入拉回轨道。
- **× skill-creator**：preference-tracker 是"skill 孵化器"——一条偏好被拦截执行几十次、复杂到值得固化时，再用 skill-creator 正式升格为 custom skill。

---

## 9. Launch 建议

- 主标题：**"Your AI coding agent has amnesia. This fixes it."**
- 正文结构：① frustration story（第 5 次告诉它别写 /tmp）→ ② demo GIF（一次纠正→下次 session 自动记住）→ ③ workflow-compile 作为 power-user 彩蛋。
- 平台：HN / Twitter 偏好"我有这个痛点 → 这东西直接解决了"的叙事弧。
- 配一个 30 秒 demo GIF（见 RELEASE_READINESS.md P2-10）。
