# Tellonce

[English](README.md) · **中文**

> 别再一遍遍跟你的 AI 编码助手重复同样的话。Tellonce 记住你做过的纠正，
> 并在你需要时强制执行，让同一个错误不再回来。

你让助手别往 `/tmp` 写临时文件、让它用你的语言回复、让它别动无关代码——结果三轮之后
它又犯了。Tellonce 在每一轮观察对话，自动记录它检测到的偏好（preference）、
陷阱（pitfall）、摩擦（friction），并能对你在意的规则做硬性强制。

它**默认安全**：开箱即用只记录、只提醒，绝不打断你，也绝不把你的对话发往任何地方——
除非你主动开启。

## ✨ 亮点

- 🧠 **从你的纠正中学习**：每一轮都会被扫描出 preference / pitfall / friction
  信号并自动记录。
- 🛡️ **可选的强制执行**：打开后，违反你已存规则的回复会被拦下，助手在同一轮里改正。
- 🔒 **默认私密**：所有记录只存本机；可选的 LLM 判官默认关闭，开启后也只看到
  脱敏片段，并走你自己的订阅。（「检索相关规则」这一步默认**完全本地、零模型调用**
  ——`progressive` 后端只读你已存的规则文件；想用小模型语义匹配可设
  `PT_RETRIEVE_BACKEND=cli`。）
- ⚡ **支持 Claude Code、Codex、GitHub Copilot CLI**（Copilot 一键安装）——三者共享同一份记忆。
- 🎛️ **三种模式，一个开关**：`observe` → `enforce` → `full`。

## 🚀 快速开始（Claude Code）

原生方式——**在 Claude Code 里**敲这两条命令：

```
/plugin marketplace add YujunZhou/tellonce
/plugin install tellonce@tellonce
```

hooks 会自动注册，开个新会话即生效。默认进安全的 `observe` 模式（只记录提醒，绝不
拦截）；想开硬拦截，在 shell 里 `export PT_ENFORCE=1`。

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

默认进安全的 `audit_only` 模式（只记录，不拦截）。
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

原生市场（和 Claude Code / Codex 一致）：

```bash
copilot plugin marketplace add YujunZhou/tellonce
copilot plugin install tellonce@tellonce
```

重启 Copilot 加载 hooks。默认进安全的 `observe` 模式。

<details>
<summary>或一键引导脚本（已验证的 <code>curl | bash</code>）</summary>

引导脚本钉在不可变 tag `v1.3.0`、SHA256 已公布，可在管道前核对（见
[`copilot/README.md`](copilot/README.md#verify-integrity)）。

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/tellonce/v1.3.0/copilot/bootstrap.ps1 | iex"
```

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/YujunZhou/tellonce/v1.3.0/copilot/bootstrap.sh | bash
```

它会：下载插件 → 放进 Copilot 插件目录 → 装可选依赖 → 注册（hook 才加载）→ 设
`observe` 模式 → 记录 Python 路径。然后重启 Copilot。
</details>

## 支持的平台

| 平台 | 状态 | 安装 | 文档 |
|---|---|---|---|
| **Claude Code** | ✅ 推荐（用户量最大） | `/plugin install`（见上） | [`docs/claude-code.md`](docs/claude-code.md) |
| **Codex** | 实验性 | `/plugin install`（见上） | [`codex/docs/README.md`](codex/docs/README.md) |
| **GitHub Copilot CLI** | 支持（一键安装） | 一条命令（见上） | [`copilot/README.md`](copilot/README.md) |

三者共享同一份用户偏好记忆与设计哲学（Iron Law / Gate Function / scan → record →
confirm）。底层机制按运行时适配：Claude Code 和 Copilot 走 `Stop` hook，而 Codex
没有 `Stop` hook，改走 wrapper。详见
[`codex/docs/CC_PARITY_MATRIX.md`](codex/docs/CC_PARITY_MATRIX.md)。

## 三种模式

| 模式 | 硬拦截 | LLM 判官 | 说明 |
|---|---|---|---|
| **observe**（默认） | 关 | 关 | 记录偏好并提醒，绝不打断 |
| **enforce** | 开 | 关 | 确定性硬拦截层 **加上"扫描完整性"停止闸门**。确定性层**不带任何内置规则**（opt-in 扩展点），所以不会拦你的内容；停止闸门首次运行会自动播种 |
| **full** | 开 | 开 | `enforce` + 小模型 LLM 判官，按你记录的偏好逐条检查回复（多花时间/额度） |

随时切换（Copilot 版）：

```bash
python "<plugin>/lib/pt_mode.py" observe   # 回到安全默认
python "<plugin>/lib/pt_mode.py" enforce   # 开硬拦截
python "<plugin>/lib/pt_mode.py" full      # 硬拦截 + LLM 判官
python "<plugin>/lib/pt_mode.py" status    # 看当前模式
```

**隐私**：所有记录任何模式下都只存本机；只有 `full` 才把「最后一条消息 + 回复」
（已脱敏）发给 `copilot -p` 判分，且走你自己的订阅。「检索相关规则」默认**完全本地**
（`progressive` 后端只读你已存的规则文件、零模型调用）；`PT_RETRIEVE_BACKEND=cli`
才走你自己订阅的小模型。`full` 的判官还需要
设 `PT_SHADOW_RULE_IDS` 指定要检查的规则——`pt_mode.py full` 会打印提示。

## 它怎么工作

1. **会话开始**——把与当前项目相关的已存规则注入助手的上下文。
2. **每轮结束（`Stop`）**——扫描该轮中新的 preference / pitfall / friction 信号，
   记录到观察日志。
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

## License

MIT——见 [`LICENSE`](LICENSE)。用于研究 in-session LLM 偏好强制的开源研究产物，
欢迎 issue 和 PR。
