# Preference-Tracker

[English](README.md) · **中文**

> 别再一遍遍跟你的 AI 编码助手重复同样的话。Preference-Tracker 记住你做过的纠正，
> 并在你需要时强制执行，让同一个错误不再回来。

你让助手别往 `/tmp` 写临时文件、让它用你的语言回复、让它别动无关代码——结果三轮之后
它又犯了。Preference-Tracker 在每一轮观察对话，自动记录它检测到的偏好（preference）、
陷阱（pitfall）、摩擦（friction），并能对你在意的规则做硬性强制。

它**默认安全**：开箱即用只记录、只提醒，绝不打断你，也绝不把你的对话发往任何地方——
除非你主动开启。

## ✨ 亮点

- 🧠 **从你的纠正中学习**：每一轮都会被扫描出 preference / pitfall / friction
  信号并自动记录。
- 🛡️ **可选的强制执行**：打开后，违反你已存规则的回复会被拦下，助手在同一轮里改正。
- 🔒 **默认私密**：`observe` 和 `enforce` 全程只在本机运行；除非你开启可选的 LLM
  判官，否则没有任何东西离开你的机器——而判官也只看到脱敏片段，并走你自己的订阅。
- ⚡ **一键安装**（GitHub Copilot CLI），同时也支持 Claude Code 和 Codex。
- 🎛️ **三种模式，一个开关**：`observe` → `enforce` → `full`。

## 🚀 快速开始（GitHub Copilot CLI）

> 前提：已装好 GitHub Copilot CLI 和 Python 3.7+，其余全自动。**装完重启 Copilot。**
> 命令钉在不可变的 release tag `v1.0.0`，不会因 `main` 变动而改，更安全。

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/preference-tracker/v1.0.0/copilot/bootstrap.ps1 | iex"
```

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/YujunZhou/preference-tracker/v1.0.0/copilot/bootstrap.sh | bash
```

这条命令会自动：下载插件 → 放进 Copilot 的插件目录 → 装好可选依赖 → 注册进 Copilot
（hook 才会加载）→ 设成安全的 `observe` 模式 → 记录你的 Python 路径。然后重启 Copilot。

谨慎的话，可在管道执行前先核对脚本——见 [`copilot/README.md`](copilot/README.md)
里公布的每个 bootstrap 脚本的 SHA256。

## 支持的平台

| 平台 | 状态 | 安装 | 文档 |
|---|---|---|---|
| **GitHub Copilot CLI** | ✅ 推荐（公开发布版） | 一条命令（见上） | [`copilot/README.md`](copilot/README.md) |
| **Claude Code** | 支持 | clone + 注册 hooks | [`docs/claude-code.md`](docs/claude-code.md) |
| **Codex** | 实验性 | `bash codex/install.sh` | [`codex/docs/README.md`](codex/docs/README.md) |

三者共享同一份用户偏好记忆与设计哲学（Iron Law / Gate Function / scan → record →
confirm）。底层机制按运行时适配：Claude Code 和 Copilot 走 `Stop` hook，而 Codex
没有 `Stop` hook，改走 wrapper。详见
[`codex/docs/CC_PARITY_MATRIX.md`](codex/docs/CC_PARITY_MATRIX.md)。

## 三种模式

| 模式 | 硬拦截 | LLM 判官 | 说明 |
|---|---|---|---|
| **observe**（默认） | 关 | 关 | 记录偏好并提醒，绝不打断 |
| **enforce** | 开 | 关 | 确定性硬拦截层。**不带任何内置规则**（opt-in 扩展点），所以单独开它什么也不拦 |
| **full** | 开 | 开 | `enforce` + 小模型 LLM 判官，按你记录的偏好逐条检查回复（多花时间/额度） |

随时切换（Copilot 版）：

```bash
python "<plugin>/lib/pt_mode.py" observe   # 回到安全默认
python "<plugin>/lib/pt_mode.py" enforce   # 开硬拦截
python "<plugin>/lib/pt_mode.py" full      # 硬拦截 + LLM 判官
python "<plugin>/lib/pt_mode.py" status    # 看当前模式
```

**隐私**：`observe` 和 `enforce` 全程只在本机；只有 `full` 才把「最后一条消息 + 回复」
（已脱敏）发给 `copilot -p` 判分，且走你自己的订阅。

## 它怎么工作

1. **会话开始**——把与当前项目相关的已存规则注入助手的上下文。
2. **每轮结束（`Stop`）**——扫描该轮中新的 preference / pitfall / friction 信号，
   记录到观察日志。
3. **在 `full` 下**——小模型 LLM 判官按你记录的偏好逐条检查回复，标出违规让助手改正。
   （`enforce` 的确定性层**不带任何内置规则**，是 opt-in 扩展点，单独开它不拦任何东西。）

## 自检 / 卸载（Copilot 版）

```bash
python "<plugin>/lib/doctor.py"        # 自检：python / 注册 / 模式 / 钩子
python "<plugin>/lib/dashboard.py"     # 一眼看状态：模式 / 规则数 / 记录数
python "<plugin>/lib/uninstall.py"     # dry-run：看会删什么
python "<plugin>/lib/uninstall.py --all"
copilot plugin uninstall preference-tracker
```

`<plugin>` 在安装结束时会打印，即
`~/.copilot/installed-plugins/preference-tracker/preference-tracker`。

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
