# Preference-Tracker（GitHub Copilot CLI）

[English](README.md) · **中文**

你的 AI 编码助手会记录你教它的偏好、陷阱和工作流规则，不再重复你已经纠正过的错误。
**默认安全**：只**记录和提醒**，绝不打断你，也绝不把你的对话发往任何地方——除非你
主动开启。

项目总览和其它平台见[仓库落地页](../README.zh.md)。

---

## 一键安装（复制一条命令，不用管你的环境）

> 前提：已装好 GitHub Copilot CLI 和 Python 3.7+，其余全自动。装完**重启 Copilot**。
> 命令钉在不可变的 release tag `v1.0.0`（不会因 `main` 变动而改），更安全。

### Windows (PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/preference-tracker/v1.0.0/copilot/bootstrap.ps1 | iex"
```

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/YujunZhou/preference-tracker/v1.0.0/copilot/bootstrap.sh | bash
```

这条命令会自动：下载插件 → 放进 Copilot 的插件目录 → 装好可选依赖 → 注册进 Copilot
（hook 才会加载）→ 设成安全的 `observe` 模式 → 记录你的 Python 路径。**装完重启 Copilot。**

> 默认 **observe**（只记录+提醒，不拦截、不调用 LLM）。

### 核对脚本完整性

如果不想把脚本直接管道进 shell，可以先下载读一遍，并核对 SHA256（应等于 `v1.0.0`
公布的值）：

```bash
# Windows: irm ".../v1.0.0/copilot/bootstrap.ps1" -OutFile bootstrap.ps1; Get-FileHash bootstrap.ps1 -Algorithm SHA256
# macOS/Linux: curl -fsSL ".../v1.0.0/copilot/bootstrap.sh" -o bootstrap.sh; sha256sum bootstrap.sh
```

| 文件 | SHA256 (v1.0.0) |
|------|------------------|
| `bootstrap.ps1` | `9a45c661f06c1c3e8a4ecfbd795472331a634786a805d5233f797de0a73bcac4` |
| `bootstrap.sh`  | `97680f207f5fc5289d15c5b521d809ef190d388f7924282b0ef60aca649a569a` |

---

## 三种模式 + 一键切换

```bash
python "<plugin>/lib/pt_mode.py" enforce     # 开硬拦截
python "<plugin>/lib/pt_mode.py" full        # 硬拦截 + LLM 判官
python "<plugin>/lib/pt_mode.py" observe     # 回到安全默认
python "<plugin>/lib/pt_mode.py" status      # 看当前模式
```

`<plugin>` = `~/.copilot/installed-plugins/preference-tracker/preference-tracker`；
完整路径在安装结束时会打印。

| 模式 | 硬拦截 | LLM 判官 | 说明 |
|------|--------|----------|------|
| **observe**（默认） | 关 | 关 | 只记录偏好并提醒，绝不打断 |
| **enforce** | 开 | 关 | 确定性硬拦截层——**不带任何内置规则**（opt-in 扩展点） |
| **full** | 开 | 开 | `enforce` + 小模型 LLM 判官，按你记录的偏好逐条检查回复（多花时间/额度） |

**隐私**：`observe` / `enforce` 全程只在本机；只有 `full` 才把「最后一条消息 + 回复」
（已脱敏）发给 `copilot -p`。

---

## 自检 / 卸载

```bash
python "<plugin>/lib/doctor.py"                 # 自检（python / 注册 / 模式 / 钩子）
python "<plugin>/lib/dashboard.py"              # 一眼看状态（模式 / 注册 / 规则数 / 记录数）
python "<plugin>/lib/uninstall.py"              # dry-run：看会删什么
python "<plugin>/lib/uninstall.py --all"        # 删 state + memory + config 键 + 反注册
copilot plugin uninstall preference-tracker     # 删插件代码本身
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
