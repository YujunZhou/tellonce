# Tellonce

[English](README.md) · **中文**

[![arXiv](https://img.shields.io/badge/arXiv-2606.13174-b31b1b.svg)](https://arxiv.org/abs/2606.13174)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 别再一遍遍跟你的 AI 编码助手重复同样的话。Tellonce 记住你做过的纠正，
> 并在你需要时强制执行，让同一个错误不再回来。在我们的评测里，编码 agent
> 任务上的留出偏好违规率从 **100% 降到 2.0%**（分布外任务，见下方研究背书）。

你让助手别往 `/tmp` 写临时文件、让它用你的语言回复、让它别动无关代码——结果三轮之后
它又犯了。Tellonce 可以扫描对话中的偏好（preference）、陷阱（pitfall）和摩擦
（friction），把它们保存到共享本地真值，并对你在意的规则做硬性强制。

它**默认不阻断，也不自动调用模型记录**：安装后只启用本地规则检索；
`memory_upsert_enabled`、硬拦截和逐回复 shadow judge 都默认关闭。显式运行
`memory_upsert.py enable-hooks` 后，detached worker 才会把完整用户 turn 脱敏后交给
当前平台的 CLI 模型解析。

## ✨ 亮点

- 🧠 **按需从纠正中学习**：自动 memory upsert 是 opt-in；本地规则检索不调用模型。
- 🛡️ **可选的强制执行**：打开后，违反你已存规则的回复会被拦下，助手在同一轮里改正。
- 🔒 **本机保存真值**：SQLite memory 留在本机；模型支持的 upsert 与可选 shadow
  judge 只接收脱敏文本，并走你自己的订阅。（「检索相关规则」默认**完全本地、零模型调用**
  ——`progressive` 后端只读你已存的规则文件；想用小模型语义匹配可设
  `PT_RETRIEVE_BACKEND=cli`。）
- ⚡ **支持 Claude Code、Codex、GitHub Copilot CLI**（Copilot 一键安装）——三者共享同一份记忆。
- 🎛️ **三种模式，一个开关**：`observe` → `enforce` → `full`。

## 📄 背后的研究

Tellonce 是 **TRACE**（Test-time Rule Acquisition and Compiled Enforcement，
测试时规则获取与编译强制）研究的可部署产物——把用户纠正编译成编码 agent 的
运行时强制（[arXiv:2606.13174](https://arxiv.org/abs/2606.13174)）。
论文的模拟用户在环评测显示：

- **记住 ≠ 遵守**：在由匿名真实用户摩擦案例衍生的任务上，即使配上最先进的记忆层
  （Mem0），**仍有 57.5% 的适用偏好检查被违反**——记忆能想起纠正，但没有任何机制
  让 agent 照做。
- **编译强制关上这个缺口**：在编码 agent 任务（ClawArena）上，TRACE 把留出偏好
  违规率从 **100% 降到 2.0%**（分布外任务）、从 **100% 降到 37.6%**（分布内）。
- **且不牺牲任务质量**：在记忆密集型任务（MemoryArena 衍生）上，TRACE 降低违规的
  同时，**任务通过率打平或超过最强记忆基线**。

真实日常使用中，纠正率的走势正是你希望看到的：作者本人的规则库在头两个月的密集
使用中长到约 280 条——随后在持续同样强度的日常工作下，**新增规则数骤降 97%**
（此后每月只有个位数新规则），因为已有的库覆盖了原来需要反复纠正的东西。
**88% 的规则一次写对**，从未需要二次修订。

实验代码：[YujunZhou/TRACE_exp](https://github.com/YujunZhou/TRACE_exp) ·
引用：[BibTeX](#citation)

## 🚀 快速开始（Claude Code）

原生方式——**在 Claude Code 里**敲这两条命令：

```
/plugin marketplace add YujunZhou/tellonce
/plugin install tellonce@tellonce
```

hooks 会自动注册，开个新会话即生效。默认进安全的 `observe` 模式（本地检索、不自动
upsert、绝不拦截）。要启用后台记录，运行
`python3 <plugin>/lib/memory_upsert.py enable-hooks`；硬拦截仍需单独设置
`export PT_ENFORCE=1`。

<details>
<summary>或手动安装（git clone + 注册）</summary>

```bash
git clone https://github.com/YujunZhou/tellonce.git ~/.claude/skills/tellonce
python3 ~/.claude/skills/tellonce/lib/_install_merge_settings.py --settings ~/.claude/settings.json --hooks-dir ~/.claude/skills/tellonce/hooks --add
```

这样注册到用户级 `~/.claude/settings.json`（所有项目生效；状态/记忆仍按项目隔离）。
只想对单个项目：`cd <project> && bash ~/.claude/skills/tellonce/install.sh`。完整指南
（强制执行、卸载）见 [`INSTALL.md`](INSTALL.md)。**只用一种方式**：如果你既走 settings.json
注册又用 `/plugin install`，hooks 会触发两次——加一个前先把另一个移除（`...--remove`）。
</details>

## 🚀 快速开始（Codex）

原生方式——Codex CLI 插件市场（Codex CLI 需 ≥ 2026 年 3 月的插件版本）：

```bash
codex plugin marketplace add YujunZhou/tellonce
codex plugin add tellonce --marketplace tellonce
# 验证: codex plugin list --marketplace tellonce  ->  installed, enabled
```

默认进安全的 `audit_only` 模式（只审计，不拦截）；自动 memory upsert 仍需显式启用。
（安装动词是 `codex plugin add`，不是 `install`。）Codex 的清单已对现行 Codex CLI 验证通过
（`codex plugin marketplace add` + 插件 validator 都过）；如果 `/plugin install` 在你的
Codex 版本上没装上 hooks，请用下面的手动安装。

<details>
<summary>或手动安装（git clone + 安装脚本）</summary>

```bash
git clone https://github.com/YujunZhou/tellonce.git ~/.codex/skills/tellonce
cd /path/to/your/project
bash ~/.codex/skills/tellonce/codex/install.sh   # 在 codex/ 下，不是仓库根的 install.sh
bash ~/.codex/skills/tellonce/codex/doctor.sh
```

模式与 wrapper 流程见 [`codex/docs/README.md`](codex/docs/README.md)。
</details>

## 🚀 快速开始（GitHub Copilot CLI）

一键引导脚本（推荐——钉在不可变 tag `v1.5.1`、SHA256 已公布，可在管道前核对，见
[`copilot/README.md`](copilot/README.md#verify-integrity)）：

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/tellonce/v1.5.1/copilot/bootstrap.ps1 | iex"
```

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/YujunZhou/tellonce/v1.5.1/copilot/bootstrap.sh | bash
```

它会：下载 **Copilot 适配版插件**（`copilot/` 子插件——SessionStart 注入、
Copilot 的 Stop 拦截约定、Windows `run.ps1` 垫片）→ 放进 Copilot 插件目录 →
装可选依赖 → 注册（hook 才加载）→ 设 `observe` 模式 → 记录 Python 路径。
**装完重启 Copilot。**

> ⚠ 不要用 `copilot plugin install tellonce@tellonce` 从本仓库市场安装——那个
> 条目是 Claude Code 变体（仓库根目录），不适配 Copilot 的 hook 面（没有逐轮注
> 入、拦截约定不同、状态写在 `.claude/` 下）。想走原生插件安装的话，用
> `copilot plugin install YujunZhou/tellonce:copilot` 装子插件，然后跑一次
> `bash <plugin_root>/install.sh`（见 [`copilot/README.md`](copilot/README.md)）。

## 支持的平台

| 平台 | 状态 | 安装 | 文档 |
|---|---|---|---|
| **Claude Code** | ✅ 推荐（用户量最大） | `/plugin install`（见上） | [`docs/claude-code.md`](docs/claude-code.md) |
| **Codex** | 实验性 | `/plugin install`（见上） | [`codex/docs/README.md`](codex/docs/README.md) |
| **GitHub Copilot CLI** | 支持（一键安装） | 一条命令（见上） | [`copilot/README.md`](copilot/README.md) |

三者共享同一份用户偏好记忆。Claude Code 与 Codex 在 `UserPromptSubmit` 注入规则，
Copilot 只在 `SessionStart` 注入；Claude Code 与 Copilot 可用 `Stop` 检查最终回复，
Codex 使用原生工具 hooks，并可选用 wrapper 检查 subprocess 最终输出。详见
[`codex/docs/CC_PARITY_MATRIX.md`](codex/docs/CC_PARITY_MATRIX.md)。

## 三种模式

| 模式 | 硬拦截 | LLM 判官 | 说明 |
|---|---|---|---|
| **observe**（默认） | 关 | 关 | 本地检索已存规则；自动 memory upsert 由独立 opt-in 开关控制 |
| **enforce** | 开 | 关 | 确定性硬拦截层 **加上"扫描完整性"停止闸门**。确定性层**不带任何内置规则**（opt-in 扩展点），所以不会拦你的内容；停止闸门首次运行会自动播种 |
| **full** | 开 | 开 | `enforce` + 小模型 LLM 判官，按你记录的偏好逐条检查回复（多花时间/额度） |

每个完整用户 turn 只提交一个 SQLite transaction，mutation 集合为
`NOOP|UPDATE|SUPERSEDE|SPLIT|NEW|NEEDS_USER|REJECT|ARCHIVE|RESTORE`。每个 mutation
必须引用该 turn 的精确短引文，context 不能授权规则。危险 durable rule 直接记为
`REJECT`，不澄清、不安装；`ARCHIVE` 会停止检索用户明确指定的规则并保留 SQLite
版本历史，`RESTORE` 可重新激活。

随时切换（Copilot 版）：

```bash
python "<plugin>/lib/pt_mode.py" observe   # 回到安全默认
python "<plugin>/lib/pt_mode.py" enforce   # 开硬拦截
python "<plugin>/lib/pt_mode.py" full      # 硬拦截 + LLM 判官
python "<plugin>/lib/pt_mode.py" status    # 看当前模式
```

**隐私**：SQLite 真值和 `progressive` 检索留在本机。启用后，记录或合并偏好会把
当前 turn 脱敏后交给当前平台的 CLI 模型判断；显式手工 `--force` enqueue 在自动 hook
关闭时也会执行一次。`full` 还会把脱敏后的最近消息和回复发送给 CLI 模型做合规评分。
如需完全离线，请保持 memory upsert 与 shadow judge 关闭。

## 它怎么工作

1. **规则注入**——Claude Code / Codex 上每次提交消息（UserPromptSubmit）都注入
   已存规则；Copilot 上每个会话开始时注入一次（它唯一的注入点）。
2. **观察与 memory upsert 分离**——平台 hook 可以写本地观察日志，但只有显式启用
   `memory_upsert_enabled`（或手工 forced enqueue）才会执行模型支持的记忆 mutation。
3. **在 `full` 下**——小模型 LLM 判官按你在 `PT_SHADOW_RULE_IDS` 里列出的规则逐条
   检查回复，标出违规让助手改正。（`enforce` 的确定性层**不带任何内置规则**，是
   opt-in 扩展点，不会拦你的内容。）

## 自检 / 卸载（Copilot 版）

```bash
python "<plugin>/lib/doctor.py"        # 自检：python / 注册 / 模式 / 钩子
python "<plugin>/lib/dashboard.py"     # 一眼看状态：模式 / 规则数 / 记录数
python "<plugin>/lib/uninstall.py"     # dry-run：看会删什么
python "<plugin>/lib/uninstall.py --all"
copilot plugin uninstall tellonce
```

`<plugin>` 在安装结束时会打印，即
`~/.copilot/installed-plugins/tellonce/tellonce`。

## 目录结构

```
README.md                 # 英文落地页
README.zh.md              # 本文（中文）
copilot/                  # GitHub Copilot CLI 变体——公开发布版
codex/                    # Codex 变体（wrapper 驱动）
docs/claude-code.md       # Claude Code 变体详解
hooks/ lib/ SKILL.md ...  # Claude Code 变体（位于仓库根目录）
seed_memory/              # 默认为空，新用户从空白开始
LICENSE
```

## Citation

如果你的研究使用了 Tellonce 或基于 TRACE：

```bibtex
@article{zhou2026trace,
  title   = {Getting Better at Working With You: Compiling User Corrections
             into Runtime Enforcement for Coding Agents},
  author  = {Zhou, Yujun and Guo, Kehan and Zhuang, Haomin and Wang, Xiangqi
             and Huang, Yue and Liang, Zhenwen and Chen, Pin-Yu and Gao, Tian
             and Moniz, Nuno and Chawla, Nitesh V. and Zhang, Xiangliang},
  journal = {arXiv preprint arXiv:2606.13174},
  year    = {2026}
}
```

## License

MIT——见 [`LICENSE`](LICENSE)。用于研究 in-session LLM 偏好强制的开源研究产物，
欢迎 issue 和 PR。
