# Preference-Tracker (GitHub Copilot CLI)

**English** · [中文](README.zh.md)

Your AI coding agent records the preferences, pitfalls, and workflow rules you
teach it — and stops repeating mistakes you already corrected. Safe by default:
it only **records and reminds**; it never blocks you or sends your conversation
anywhere until you opt in.

For the project overview and the other platforms, see the
[repository landing page](../README.md).

---

## One-command install (copy one line; your environment doesn't matter)

> Prerequisites: GitHub Copilot CLI and Python 3.7+. Everything else is
> automatic. **Restart Copilot after install.**
> The command is pinned to the immutable release tag `v1.1.0` (it won't change
> when `main` does), which is safer.

### Windows (PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/preference-tracker/v1.1.0/copilot/bootstrap.ps1 | iex"
```

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/YujunZhou/preference-tracker/v1.1.0/copilot/bootstrap.sh | bash
```

This command automatically: downloads the plugin → copies it into Copilot's
plugin directory → installs the optional dependency → registers it with Copilot
(so the hooks load) → sets the safe `observe` mode → records your Python path.
**Restart Copilot when it's done.**

> Default mode is **observe** (records and reminds only — no blocking, no LLM).

### Verify integrity

If you'd rather not pipe a script straight into a shell, download it first, read
it, and check its SHA256 against the value published for `v1.1.0`:

```bash
# Windows: irm ".../v1.1.0/copilot/bootstrap.ps1" -OutFile bootstrap.ps1; Get-FileHash bootstrap.ps1 -Algorithm SHA256
# macOS/Linux: curl -fsSL ".../v1.1.0/copilot/bootstrap.sh" -o bootstrap.sh; sha256sum bootstrap.sh
```

| File | SHA256 (v1.1.0) |
|------|------------------|
| `bootstrap.ps1` | `dbca25a9d7ab67cc8f85f8506d0e408104eadda71cc225e10e05507c6a12c9bb` |
| `bootstrap.sh`  | `0d9e6c4cbcf9eccc21ba09be6631666b6b3e943b480ebb153ad1fb5d7075e83d` |

---

## Three modes + one-command switch

```bash
python "<plugin>/lib/pt_mode.py" enforce     # turn on hard blocking
python "<plugin>/lib/pt_mode.py" full        # hard blocking + LLM judge
python "<plugin>/lib/pt_mode.py" observe     # back to the safe default
python "<plugin>/lib/pt_mode.py" status      # show the current mode
```

`<plugin>` is `~/.copilot/installed-plugins/preference-tracker/preference-tracker`;
the full path is printed at the end of install.

| Mode | Hard block | LLM judge | Description |
|------|------------|-----------|-------------|
| **observe** (default) | off | off | Records preferences and reminds you; never interrupts. |
| **enforce** | on | off | Deterministic hard-block layer — ships with **no built-in rules** (opt-in extension point). |
| **full** | on | on | `enforce` plus a small-model LLM judge that checks each reply against your recorded preferences (costs time / credit). |

**Privacy:** `observe` / `enforce` stay entirely on your machine. Only `full`
sends the last message and reply (redacted) to `copilot -p`.

---

## Self-check / uninstall

**One-command uninstall** (removes the hook registration so hooks stop firing,
then the plugin files; your saved memory is kept):

Windows (PowerShell):
```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/preference-tracker/v1.1.0/copilot/uninstall.ps1 | iex"
```
macOS / Linux:
```bash
curl -fsSL https://raw.githubusercontent.com/YujunZhou/preference-tracker/v1.1.0/copilot/uninstall.sh | bash
```
**Restart Copilot afterward.** To also wipe your saved memory/state, download the
script and run it with `-Purge` (PowerShell) / `--purge` (bash).

> Deleting the plugin files alone is NOT enough — the hooks keep firing while the
> plugin is still registered in `~/.copilot/config.json`. The uninstaller removes
> that registration first.

Manual / granular alternative:
```bash
python "<plugin>/lib/doctor.py"                 # self-check (python / registration / mode / hooks)
python "<plugin>/lib/dashboard.py"              # status at a glance (mode / registration / rule count / record count)
python "<plugin>/lib/uninstall.py"              # dry-run: show what would be removed
python "<plugin>/lib/uninstall.py --all"        # remove state + memory + config keys + unregister
copilot plugin uninstall preference-tracker     # remove the plugin code itself
```

---

## Installed but nothing happens?

Hooks only load when the plugin is **registered in Copilot's
`~/.copilot/config.json`**. The one-command script registers it automatically; if
a manual install missed that step:

```bash
python "<plugin>/lib/register_plugin.py"        # register (idempotent + backup)
python "<plugin>/lib/register_plugin.py --status"
```

After registering you **must restart Copilot**. Still stuck? Run `doctor.py` to
see which check FAILs.

## Note: preferences recorded mid-session

On the GitHub Copilot CLI, recorded preferences are injected into the agent's
context at **session start**. A preference you record **mid-session** is saved to
memory immediately, but won't be re-surfaced to the agent until your **next
session** — Copilot's per-prompt hooks (`UserPromptSubmit` / `PreToolUse`) can't
inject context (a platform limitation, not a bug in this tool). To apply a new
preference right away, start a new session. (The Claude Code and Codex variants
re-inject every turn, so they don't have this limitation.)
