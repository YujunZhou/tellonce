# preference-tracker (Copilot CLI)

Your AI coding agent records the preferences, pitfalls, and workflow rules you
teach it — and stops repeating mistakes you already corrected. Safe by default:
it only **records and reminds**; it never blocks you or sends your conversation
anywhere until you opt in.

---

## 一键安装（复制一条命令，不用管你的环境）

> 前提：已装好 GitHub Copilot CLI 和 Python 3.7+。其余全自动。装完**重启 Copilot**即可。

### Windows (PowerShell)
```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/preference-tracker/main/copilot/bootstrap.ps1 | iex"
```

### macOS / Linux
```bash
curl -fsSL https://raw.githubusercontent.com/YujunZhou/preference-tracker/main/copilot/bootstrap.sh | bash
```

这条命令会自动：下载插件 → 放进 Copilot 的插件目录 → 装好可选依赖 → 注册进 Copilot（hook 才会加载）→ 设成安全的 observe 模式 → 记录你的 python 路径。**装完重启 Copilot。**

> 默认 **observe**（只记录+提醒，不拦截、不调用 LLM）。

---

## 三种模式 + 一键切换

```
python "<plugin>\lib\pt_mode.py" enforce     # 开硬拦截
python "<plugin>\lib\pt_mode.py" full        # 硬拦截 + AI 判官
python "<plugin>\lib\pt_mode.py" observe     # 回到安全默认
python "<plugin>\lib\pt_mode.py" status      # 看当前模式
```
（`<plugin>` = `~/.copilot/installed-plugins/preference-tracker/preference-tracker`；装完提示里会打印完整路径。）

| 模式 | 硬拦截 | AI 判官 | 说明 |
|------|--------|---------|------|
| **observe**（默认） | 关 | 关 | 只记录偏好并提醒，绝不打断 |
| **enforce** | 开 | 关 | 违反保存规则的回复被拦下强制改 |
| **full** | 开 | 开 | enforce + 小模型语义判分（多花时间/额度） |

隐私：observe / enforce 全程**只在本机**；只有 full 才把「最后一条消息 + 回复」（已脱敏）发给 `copilot -p`。

---

## 自检 / 卸载

```
python "<plugin>\lib\doctor.py"                 # 自检（python / 注册 / 模式 / 钩子）
python "<plugin>\lib\uninstall.py"              # 看会删什么（dry-run）
python "<plugin>\lib\uninstall.py --all"        # 删 state+memory+config 键+反注册
copilot plugin uninstall preference-tracker     # 删插件代码本身
```

---

## 装完没反应？

hook 只有在插件**注册进 Copilot 的 `~/.copilot/config.json`** 时才加载。一键脚本会自动注册；若手动装漏了：
```
python "<plugin>\lib\register_plugin.py"        # 注册（幂等+备份）
python "<plugin>\lib\register_plugin.py --status"
```
注册后**必须重启 Copilot**。还不行就跑 `doctor.py` 看哪一项 FAIL。

---

详细设计 / 已知限制见仓库根目录 `README.md`、`AUDIT_REPORT_2026-06-05.md`、`SESSION_HANDOFF_v2.md`。
