# Tellonce（GitHub Copilot CLI）

[English](README.md) · **中文**

你的 AI 编码助手会记录你教它的偏好、陷阱和工作流规则，不再重复你已经纠正过的错误。
**默认安全**：只**记录和提醒**，绝不打断你，也绝不把你的对话发往任何地方——除非你
主动开启。

项目总览和其它平台见[仓库落地页](../README.zh.md)。

---

## 一键安装（复制一条命令，不用管你的环境）

> 前提：已装好 GitHub Copilot CLI 和 Python 3.7+，其余全自动。装完**重启 Copilot**。
> 命令钉在不可变的 release tag `v1.2.0`（不会因 `main` 变动而改），更安全。

### Windows (PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/tellonce/v1.2.0/copilot/bootstrap.ps1 | iex"
```

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/YujunZhou/tellonce/v1.2.0/copilot/bootstrap.sh | bash
```

这条命令会自动：下载插件 → 放进 Copilot 的插件目录 → 装好可选依赖 → 注册进 Copilot
（hook 才会加载）→ 设成安全的 `observe` 模式 → 记录你的 Python 路径。**装完重启 Copilot。**

> 默认 **observe**（只记录+提醒，不拦截、不调用 LLM）。

### 核对脚本完整性

如果不想把脚本直接管道进 shell，可以先下载读一遍，并核对 SHA256（应等于 `v1.2.0`
公布的值）：

```bash
# Windows: irm ".../v1.2.0/copilot/bootstrap.ps1" -OutFile bootstrap.ps1; Get-FileHash bootstrap.ps1 -Algorithm SHA256
# macOS/Linux: curl -fsSL ".../v1.2.0/copilot/bootstrap.sh" -o bootstrap.sh; sha256sum bootstrap.sh
```

| 文件 | SHA256 (v1.2.0) |
|------|------------------|
| `bootstrap.ps1` | `7f3e5fc50fd63c9e395950f4a080b8d627eada8873047b4390d5ca2822b88e5d` |
| `bootstrap.sh`  | `d2ac14a22658d50bb75d60ea2f90700c9cfd53d47c0bc661a7ed544387a23251` |

---

## 三种模式 + 一键切换

```bash
python "<plugin>/lib/pt_mode.py" enforce     # 开硬拦截
python "<plugin>/lib/pt_mode.py" full        # 硬拦截 + LLM 判官
python "<plugin>/lib/pt_mode.py" observe     # 回到安全默认
python "<plugin>/lib/pt_mode.py" status      # 看当前模式
```

`<plugin>` = `~/.copilot/installed-plugins/tellonce/tellonce`；
完整路径在安装结束时会打印。

| 模式 | 硬拦截 | LLM 判官 | 说明 |
|------|--------|----------|------|
| **observe**（默认） | 关 | 关 | 只记录偏好并提醒，绝不打断 |
| **enforce** | 开 | 关 | 确定性硬拦截层 **加上"扫描完整性"停止闸门**。确定性层**不带任何内置规则**（opt-in 扩展点），所以不会拦你的内容；停止闸门首次运行会自动播种 |
| **full** | 开 | 开 | `enforce` + 小模型 LLM 判官，按 `PT_SHADOW_RULE_IDS` 里列出的已记录偏好（逗号分隔的 atomic_id）逐条检查回复；未设置时 `pt_mode.py full` 会打印提醒（多花时间/额度） |

> **Windows 注意**：「扫描完整性」停止闸门的 hook 在 Windows 上目前只是占位
> （PowerShell 条目只回显一行），所以 `enforce` 模式在 Windows 上比 macOS/Linux 弱。

**隐私**：`observe` / `enforce` 全程只在本机；只有 `full` 才把「最后一条消息 + 回复」
（已脱敏）发给 `copilot -p`。

---

## 自检 / 卸载

**一键卸载**（先移除 hook 注册让 hook 停止触发，再删插件文件；你保存的 memory 会保留）：

Windows (PowerShell):
```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/tellonce/v1.2.0/copilot/uninstall.ps1 | iex"
```
macOS / Linux:
```bash
curl -fsSL https://raw.githubusercontent.com/YujunZhou/tellonce/v1.2.0/copilot/uninstall.sh | bash
```
**卸载后重启 Copilot。** 若还想清掉保存的 memory/state，先下载脚本，再带
`-Purge`（PowerShell）/ `--purge`（bash）运行。注意 `--purge` / `--all` 删的是
**当前项目**的 memory/state——按你运行命令时所在的目录解析（逐项目，不是全局）；
在多个项目里用过的话要逐个项目执行。

> 只删插件文件是不够的——只要插件还注册在 `~/.copilot/config.json` 里，hook
> 就会继续触发。卸载脚本会先移除该注册。

手动 / 细粒度替代：
```bash
python "<plugin>/lib/doctor.py"                 # 自检（python / 注册 / 模式 / 钩子）
python "<plugin>/lib/dashboard.py"              # 一眼看状态（模式 / 注册 / 规则数 / 记录数）
python "<plugin>/lib/uninstall.py"              # dry-run：看会删什么
python "<plugin>/lib/uninstall.py --all"        # 删当前项目的 state + memory、config 键 + 反注册（在该项目目录下运行）
copilot plugin uninstall tellonce     # 删插件代码本身
```

---

## 装完没反应？

hook 只有在插件**注册进 Copilot 的 `~/.copilot/config.json`** 时才加载。一键脚本会
自动注册；若手动装漏了：

```bash
python "<plugin>/lib/register_plugin.py"        # 注册（幂等 + 备份）
python "<plugin>/lib/register_plugin.py --status"
```

注册后**必须重启 Copilot**。还不行就跑 `doctor.py` 看哪一项 FAIL。

## 注意：会话进行中新记的偏好

在 GitHub Copilot CLI 上，已记录的偏好只在**会话开始时**注入 agent 的上下文。你在会话**进行中**新记的偏好会**立即存盘**，但要等**下次会话**才会被重新端给 agent —— Copilot 的逐条 prompt hook（`UserPromptSubmit` / `PreToolUse`）不能注入 context（属平台限制，非本工具 bug）。想让新偏好立刻生效，开一个新会话即可。（Claude Code 与 Codex 变体每轮都重新注入，没有此限制。）
