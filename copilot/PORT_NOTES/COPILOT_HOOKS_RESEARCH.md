> Internal port research notes (not installed; kept for maintainers).

# Porting a Claude Code Skill to a GitHub Copilot CLI Plugin: Hooks Reference

> **TL;DR — Are the two systems the same?** Copilot CLI and Claude Code share the *same `hooks.json` wire format and the same PascalCase event name vocabulary*. The schema, stdin payloads, and `hooks/hooks.json` plugin file layout are byte-for-byte compatible. However, **two semantics differ critically for this port**: (1) `userPromptSubmitted` in Copilot CLI is **fire-and-forget** — stdout is ignored, so `additionalContext` injection does not work; and (2) blocking a turn from `agentStop`/`Stop` requires a stdout JSON `{"decision":"block"}`, not exit code 2. Sections 2 and 3 give the full porting mapping.

---

## 1. The `hooks.json` Schema for Copilot CLI Plugins

### 1.1 File Location Inside the Plugin

How Copilot CLI finds your hook file depends on which plugin format you use:

| Plugin format | Detected by | `hooks.json` path inside plugin |
|---|---|---|
| **Copilot** (default) | `plugin.json` at plugin root | **`hooks.json`** (plugin root) |
| **Claude** | `.claude-plugin/plugin.json` | **`hooks/hooks.json`** |
| **OpenPlugin** | `.plugin/plugin.json` | `hooks/hooks.json` |

Source: `microsoft/vscode:src/vs/platform/agentPlugins/common/pluginParsers.ts:93-124`
```typescript
const COPILOT_FORMAT: IPluginFormatConfig = {
    manifestPath: 'plugin.json',
    hookConfigPath: 'hooks.json',         // ← root-level for Copilot format
    pluginRootToken: undefined,
    pluginRootEnvVar: undefined,
};
const CLAUDE_FORMAT: IPluginFormatConfig = {
    manifestPath: '.claude-plugin/plugin.json',
    hookConfigPath: 'hooks/hooks.json',   // ← subdirectory for Claude format
    pluginRootToken: '${CLAUDE_PLUGIN_ROOT}',
    pluginRootEnvVar: 'CLAUDE_PLUGIN_ROOT',
};
```

> **For porting a Claude Code skill**: your existing `hooks/hooks.json` path is already correct for the Claude plugin format. If you migrate to Copilot-native format, move it to `hooks.json` at the root.

### 1.2 `hooks.json` Top-Level Schema

Source: `github/docs:content/copilot/reference/hooks-reference.md` (SHA `df3b54bb`)

```json
{
  "version": 1,
  "disableAllHooks": false,
  "hooks": {
    "<EVENT_NAME>": [
      {
        "type": "command",
        "bash":       "string — Unix shell command",
        "powershell": "string — Windows PowerShell command",
        "command":    "string — cross-platform fallback (used if neither bash/powershell matches platform)",
        "cwd":        "optional/working/directory",
        "env":        { "KEY": "value" },
        "timeoutSec": 30,
        "matcher":    "optional-regex"
      }
    ]
  }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `version` | number | **Yes** | Must be `1` |
| `disableAllHooks` | boolean | No | `true` disables every hook in this file without deleting it |
| `hooks` | object | **Yes** | Map of event name → array of hook entries |
| `type` | `"command"` | **Yes** | Only `"command"` is supported for shell-based hooks |
| `bash` | string | Yes on Unix | Shell command string executed on macOS/Linux |
| `powershell` | string | Yes on Windows | PowerShell command string executed on Windows |
| `command` | string | Fallback | Cross-platform command used when neither `bash`/`powershell` match the current OS |
| `cwd` | string | No | Working directory relative to repo root, or absolute |
| `env` | object | No | Extra environment variables merged into the process environment |
| `timeoutSec` | number | No | Default `30`; hook is killed after this many seconds |
| `matcher` | string | No | Optional regex; anchored as `^(?:pattern)$` against an event-specific field |

> **Compatibility note**: VS Code's `pluginParsers.ts` normalises `bash` → `linux`/`osx` and `powershell` → `windows` internally, and accepts `timeout` as a synonym for `timeoutSec`.

### 1.3 Full Set of Supported Event Names

Two naming conventions are accepted for every event. The **camelCase** key is the Copilot CLI native name; the **PascalCase** key is the VS Code / Claude Code compatible alias. They produce **identical** behaviour except that the JSON stdin payload uses `camelCase` field names for the former and `snake_case` field names for the latter.

Source: `microsoft/vscode:src/vs/platform/agentPlugins/common/pluginParsers.ts:390-411`

```typescript
const HOOK_TYPE_MAP: Record<string, string> = {
    // PascalCase (VS Code / Claude Code)
    'SessionStart':    'SessionStart',
    'SessionEnd':      'SessionEnd',
    'UserPromptSubmit':'UserPromptSubmit',
    'PreToolUse':      'PreToolUse',
    'PostToolUse':     'PostToolUse',
    'PreCompact':      'PreCompact',
    'SubagentStart':   'SubagentStart',
    'SubagentStop':    'SubagentStop',
    'Stop':            'Stop',
    'ErrorOccurred':   'ErrorOccurred',
    // camelCase (GitHub Copilot CLI)
    'sessionStart':       'SessionStart',
    'sessionEnd':         'SessionEnd',
    'userPromptSubmitted':'UserPromptSubmit',  // ← different spelling!
    'preToolUse':         'PreToolUse',
    'postToolUse':        'PostToolUse',
    'agentStop':          'Stop',              // ← different name!
    'subagentStop':       'SubagentStop',
    'errorOccurred':      'ErrorOccurred',
};
```

> **Note**: `preCompact` and `subagentStart` do not have camelCase aliases in the current map.

All 10 events supported by Copilot CLI (with their output-processing behaviour):

| Event (camelCase / PascalCase) | Fires when | Output processed | Cloud Agent |
|---|---|---|---|
| `sessionStart` / `SessionStart` | New or resumed session begins | ✅ `additionalContext` injected | Once per job |
| `sessionEnd` / `SessionEnd` | Session terminates | ❌ Fire-and-forget | Once per job |
| `userPromptSubmitted` / `UserPromptSubmit` | User submits a prompt | ❌ **Fire-and-forget** | Once per job |
| `preToolUse` / `PreToolUse` | Before any tool executes | ✅ Can allow/deny/modify | Fires |
| `postToolUse` / `PostToolUse` | After tool completes successfully | ❌ Fire-and-forget | Fires |
| `postToolUseFailure` / `PostToolUseFailure` | After tool fails | ✅ `additionalContext` via exit 2 | Fires |
| `agentStop` / `Stop` | Main agent finishes a turn | ✅ Can block and force continuation | Fires |
| `subagentStop` / `SubagentStop` | Subagent completes | ✅ Can block and force continuation | Fires |
| `subagentStart` / `SubagentStart` | Subagent is spawned | ✅ `additionalContext` prepended to subagent | Fires |
| `errorOccurred` / `ErrorOccurred` | Error during execution | ❌ Fire-and-forget | Fires |
| `preCompact` / `PreCompact` | Context compaction about to begin | ❌ Notification only | Only `auto` trigger |
| `notification` *(CLI-only)* | CLI emits any system notification | ✅ `additionalContext` injected | **Does not fire** |
| `permissionRequest` *(CLI-only)* | Before permission service runs | ✅ Can allow/deny | **Does not fire** |

Source: `github/docs:content/copilot/reference/hooks-reference.md:163-177`

### 1.4 stdin JSON Payloads (Per-Event)

Each hook receives a JSON object piped to stdin. The schema depends on whether you register the event using camelCase or PascalCase key:

#### `userPromptSubmitted` (camelCase)
```typescript
{
    sessionId: string;
    timestamp: number;   // Unix ms
    cwd: string;
    prompt: string;      // the user's prompt text
}
```

#### `UserPromptSubmit` (PascalCase — VS Code compatible)
```typescript
{
    hook_event_name: "UserPromptSubmit";
    session_id: string;
    timestamp: string;   // ISO 8601
    cwd: string;
    prompt: string;
}
```

#### `agentStop` (camelCase)
```typescript
{
    sessionId: string;
    timestamp: number;
    cwd: string;
    transcriptPath: string;  // path to session transcript
    stopReason: "end_turn";
}
```

#### `Stop` (PascalCase — direct Claude Code name, VS Code compatible)
```typescript
{
    hook_event_name: "Stop";
    session_id: string;
    timestamp: string;       // ISO 8601
    cwd: string;
    transcript_path: string;
    stop_reason: "end_turn";
}
```

Source: `github/docs:content/copilot/reference/hooks-reference.md:238-381`

### 1.5 stdout / Exit Code Interpretation

Source: `github/docs:content/copilot/reference/hooks-reference.md:607-613`

| Exit code | Meaning |
|---|---|
| `0` | Success. `stdout` is parsed as JSON hook output (if non-empty). |
| `2` | **Warning by default** — `stderr` is surfaced to user; run continues. Special cases: for `permissionRequest`, treated as `{"behavior":"deny"}`; for `postToolUseFailure`, stdout is appended as `additionalContext`. |
| Any other non-zero | Logged as hook failure; run continues (fail-open). |

**stdout JSON for `agentStop`/`Stop`:**
```typescript
{
    decision?: "block" | "allow";  // "block" forces another agent turn
    reason?: string;               // prompt for the next turn
}
```

**stdout JSON for `preToolUse`/`PreToolUse`:**
```typescript
{
    permissionDecision?: "allow" | "deny" | "ask";
    permissionDecisionReason?: string;  // Required if denying
    modifiedArgs?: object;              // Substitute tool arguments
}
```

**stdout JSON for `sessionStart` / `notification` (additionalContext injection):**
```typescript
{
    additionalContext?: string;  // Injected into session as a user message
}
```

---

## 2. Closest Analogs to Claude Code Hook Events

### 2.1 Mapping Table

| Claude Code event | Behaviour in Claude Code | Copilot CLI equivalent | Critical differences |
|---|---|---|---|
| `UserPromptSubmit` | Fires on every prompt; stdout `additionalContext` injected into model input; exit 2 blocks with reason | `userPromptSubmitted` (camelCase) or `UserPromptSubmit` (PascalCase) | ⚠️ **Output NOT processed** in Copilot CLI. Stdout is ignored; additionalContext injection does NOT work. |
| `Stop` | Fires after model response; exit 2 deterministically blocks; exit 0 allows; stdout JSON can inject follow-up | `agentStop` (camelCase) or `Stop` (PascalCase) | ⚠️ Exit 2 is a *warning*, not a block. **Use stdout JSON `{"decision":"block","reason":"..."}` to block.** Exit 0 is allow. |

### 2.2 The `UserPromptSubmit` → `userPromptSubmitted` Gap

**Claude Code** treats `UserPromptSubmit` as an *output-bearing* hook: a script returning `{"additionalContext":"..."}` has that text prepended to the model's input on every turn.

**Copilot CLI** marks `userPromptSubmitted` as "No" in its *Output processed* column — stdout is ignored. The event exists for auditing and logging only.

**Migration options:**

| Need | Recommended Copilot hook |
|---|---|
| Inject context once at session start | `sessionStart` — supports `additionalContext` in stdout |
| Inject context on every prompt | **No direct equivalent.** Use `preToolUse` on the first tool call, or use `sessionStart` to inject standing instructions |
| Audit / log every prompt | `userPromptSubmitted` — stdout ignored, but the prompt text is in stdin, so you can write to a log file |
| Block a prompt (pre-validation) | Use `preToolUse` on a specific tool if the prompt reliably triggers one early tool call |

Source: `github/docs:content/copilot/reference/hooks-reference.md:163-177`; `microsoft/vscode:src/vs/platform/agentPlugins/common/pluginParsers.ts:390-411`

### 2.3 The `Stop` → `agentStop` Difference in Exit Code Semantics

**Claude Code `Stop`**: exit 2 blocks the response, surfacing `stderr` as a block reason.

**Copilot CLI `agentStop`**: exit 2 is a *warning* — `stderr` is surfaced but the run continues. To deterministically block, return JSON via stdout:

```bash
#!/usr/bin/env bash
INPUT=$(cat)
# ... compliance check ...
if [ "$FAILED" = "true" ]; then
  # Copilot CLI: must use stdout JSON to block
  echo '{"decision":"block","reason":"Compliance check failed: output contains PII."}'
  exit 0   # exit 0 so the JSON is parsed; exit 2 would just warn
fi
exit 0
```

The `decision: "block"` value forces another agent turn, with `reason` used as the prompt for that turn. This is slightly more powerful than Claude Code's exit 2 (which just suppresses the response): it can redirect the agent to fix its own output.

---

## 3. Hook Execution Environment

### 3.1 Working Directory (`$PWD`)

The `cwd` field in each hook entry controls the working directory. If omitted, it defaults to the **repository root** (where the CLI was launched).

- For cloud agent, the working directory is `/workspace` when a repo is cloned, `/root` otherwise.
- Plugin hooks that reference scripts *inside the plugin directory* must use the `${PLUGIN_ROOT}` / `${CLAUDE_PLUGIN_ROOT}` token (see §3.4).

Source: `github/docs:content/copilot/reference/hooks-reference.md:52` and `microsoft/vscode:docs/copilot/customization/agent-plugins.md:182-184`

### 3.2 Environment Variables Set by Copilot CLI

For local CLI sessions, hooks inherit the full user shell environment. Additionally:

| Variable | Set by | Value |
|---|---|---|
| `COPILOT_HOME` | User (optional) | Overrides `~/.copilot/` if set |
| `COPILOT_CACHE_HOME` | User (optional) | Overrides the platform cache dir |
| Hook `env` field | You (per hook entry) | Merged with existing env |

For **Cloud Agent** sandbox only:

| Variable | Value |
|---|---|
| `GITHUB_COPILOT_API_TOKEN` | API token for making Copilot requests |
| `GITHUB_COPILOT_GIT_TOKEN` | Git token for the job's repo |
| `COPILOT_AGENT_PROMPT` | The prompt the job was invoked with |
| `HOME` | `/root` (ephemeral) |

Source: `github/docs:content/copilot/reference/hooks-reference.md:55`

For Claude-format plugins specifically, the `CLAUDE_PLUGIN_ROOT` env var is set to the plugin's absolute install path at runtime, both in the hook process and in MCP server processes.

Source: `microsoft/vscode:src/vs/platform/agentPlugins/common/pluginParsers.ts:104-112`

### 3.3 Windows Shell Execution

On Windows, the `powershell` field is used. On macOS/Linux, the `bash` field is used. The `command` field is a cross-platform fallback when neither platform-specific key is set.

Source: `microsoft/vscode:src/vs/platform/agentPlugins/common/pluginParsers.ts:417-450`

```typescript
const windows = hasWindows ? raw.windows : (hasPowerShell ? raw.powershell : undefined);
const linux   = hasLinux   ? raw.linux   : (hasBash       ? raw.bash       : undefined);
const osx     = hasOsx     ? raw.osx     : (hasBash       ? raw.bash       : undefined);
```

**Can the command be a Python script?** Yes. Use the `bash` / `powershell` / `command` field to invoke the interpreter:

```json
{
  "type": "command",
  "bash": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/my_hook.py",
  "powershell": "python ${CLAUDE_PLUGIN_ROOT}\\hooks\\my_hook.py",
  "timeoutSec": 15
}
```

The Python script reads stdin for the JSON payload and writes JSON to stdout. Real-world example from `rullerzhou-afk/clawd-on-desk:hooks/copilot-install.js:49-60` shows Node.js called the same way with both `bash` and `powershell` entries in the same hook object.

**There is no native Git Bash invocation** — Copilot CLI delegates to the system shell; on Windows it runs PowerShell for the `powershell` field, *not* Git Bash. If you need bash on Windows, explicitly invoke `C:\Program Files\Git\bin\bash.exe ...`.

---

## 4. Plugin Install Lifecycle

### 4.1 Where plugins land after `copilot plugin install`

Source: `https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference` (official docs, last fetched 2026-05-20)

| Installation source | Disk path |
|---|---|
| Installed from a marketplace | `~/.copilot/installed-plugins/<MARKETPLACE>/<PLUGIN-NAME>/` |
| Installed directly (git URL, local path, `OWNER/REPO`) | `~/.copilot/installed-plugins/_direct/<SOURCE-ID>/` |
| Marketplace cache | `~/.cache/copilot/marketplaces/` (Linux), `~/Library/Caches/copilot/marketplaces/` (macOS) |
| Windows equivalent of `~/.copilot` | `%USERPROFILE%\.copilot\` |

VS Code discovers CLI-installed plugins from `~/.copilot/installed-plugins/` automatically:
> "Plugins from `~/.copilot/installed-plugins/` appear in the **Agent Plugins - Installed** view alongside plugins you installed from a marketplace or from source."

Source: `microsoft/vscode-docs:docs/copilot/customization/agent-plugins.md:307-309`

### 4.2 Is There an "On Install" Hook?

**No.** There is no `onInstall` or `postInstall` lifecycle hook in the Copilot CLI plugin system. After `copilot plugin install`, the plugin directory is cloned/copied to disk and immediately active — no setup script runs automatically.

**Consequences for porting:**

If your Claude Code skill registers hooks by mutating `~/.claude/settings.json` at install time, that mechanism **does not exist** in Copilot CLI. Instead:

1. Declare your hooks inside `hooks.json` (or `hooks/hooks.json`) in the plugin directory — they are automatically discovered and activated.
2. If the plugin needs a post-install step (e.g., `npm install`, environment setup), document it in your `README.md` as a manual step and optionally ship a `setup.sh` the user runs once.

**Global user-level hooks** (outside any plugin) are read from `~/.copilot/hooks/*.json`. You can ask users to place a file there manually, or use a bootstrap script (like `rullerzhou-afk/clawd-on-desk:hooks/copilot-install.js` — a standalone Node.js script that merges hook entries idempotently into `~/.copilot/hooks/hooks.json`).

---

## 5. Real-World Plugin `hooks.json` Examples

### 5.1 `eugenejahn/oh-my-openagent-copilot` — 5 `preToolUse` guards + `postToolUse` + `sessionEnd`

Source: [`https://github.com/eugenejahn/oh-my-openagent-copilot/blob/812630a4/hooks.json`](https://github.com/eugenejahn/oh-my-openagent-copilot/blob/812630a43022aaac85c3cad9a801e81e89b18e7b/hooks.json)

```json
{
  "version": 1,
  "hooks": {
    "preToolUse": [
      { "type": "command", "bash": "hooks/guard-write-existing.sh",   "cwd": ".", "timeoutSec": 10 },
      { "type": "command", "bash": "hooks/guard-prometheus-md.sh",    "cwd": ".", "timeoutSec":  5 },
      { "type": "command", "bash": "hooks/guard-webfetch-redirect.sh","cwd": ".", "timeoutSec": 15 }
    ],
    "postToolUse": [
      { "type": "command", "bash": "hooks/detect-json-error.sh",      "cwd": ".", "timeoutSec":  5 }
    ],
    "sessionEnd": [
      { "type": "command", "bash": "hooks/notify-session.sh",         "cwd": ".", "timeoutSec": 10 }
    ]
  }
}
```

The `preToolUse` guard scripts receive `toolName` + `toolArgs` on stdin and emit `{"permissionDecision":"deny","permissionDecisionReason":"..."}` to stdout to block a write:

```bash
# hooks/guard-write-existing.sh (excerpt)
INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.toolName // empty')
# ...
echo '{"permissionDecision":"deny","permissionDecisionReason":"File already exists and has not been Read first."}'
```

Source: [`https://github.com/eugenejahn/oh-my-openagent-copilot/blob/812630a4/hooks/guard-write-existing.sh`](https://github.com/eugenejahn/oh-my-openagent-copilot/blob/812630a43022aaac85c3cad9a801e81e89b18e7b/hooks/guard-write-existing.sh)

This plugin is Copilot-format (has `plugin.json` at root, not `.claude-plugin/`). The hooks.json lives at the plugin root, consistent with `COPILOT_FORMAT.hookConfigPath = 'hooks.json'`.

The `plugin.json` shows that hooks are NOT listed under `hooks:` in the manifest — the CLI discovers them by convention (the default path `hooks.json`):

```json
{
  "name": "oh-my-openagent-copilot",
  "version": "1.0.0",
  "agents": ["./agents"],
  "skills": ["skills/"]
}
```

Source: [`https://github.com/eugenejahn/oh-my-openagent-copilot/blob/812630a4/plugin.json`](https://github.com/eugenejahn/oh-my-openagent-copilot/blob/812630a43022aaac85c3cad9a801e81e89b18e7b/plugin.json)

### 5.2 `github/copilot-plugins` Official Collection

The official plugin repository does **not** ship `hooks.json` files in its current plugins (`advanced-security`, `spark`, `workiq`). These plugins focus on skills and MCP servers. The repo uses a `.claude-plugin/marketplace.json` for Claude Code marketplace compatibility:

```json
{ "name": "copilot-plugins", "owner": { "name": "GitHub", "email": "copilot@github.com" }, "plugins": [...] }
```

Source: [`https://github.com/github/copilot-plugins/blob/3d8a5bff/.claude-plugin/marketplace.json`](https://github.com/github/copilot-plugins/blob/3d8a5bffb4c5850c984388ca5f40e20543c6aad1/.claude-plugin/marketplace.json)

### 5.3 VS Code `agent-plugins.md` Quick-Start Example (flat format)

Source: `microsoft/vscode-docs:docs/copilot/customization/agent-plugins.md:145-158`

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "type": "command",
        "command": "${CLAUDE_PLUGIN_ROOT}/scripts/format.sh"
      }
    ]
  }
}
```

This is the Claude-format style (`hooks/hooks.json`), using `${CLAUDE_PLUGIN_ROOT}` token and PascalCase event names.

---

## 6. Key Documentation URLs

| Resource | URL | Notes |
|---|---|---|
| **Official hooks reference** (CLI + Cloud Agent) | `https://docs.github.com/en/copilot/reference/hooks-reference` | Canonical reference for all events, payloads, exit codes |
| **hooks-reference.md source** | `github/docs:content/copilot/reference/hooks-reference.md` (SHA `df3b54bb`) | PascalCase/camelCase dual format, full payload schemas |
| **Copilot CLI plugin reference** (`plugin.json` + `marketplace.json`) | `https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference` | Canonical `plugin.json` schema, file locations table |
| **CLI config directory reference** | `https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference` | `~/.copilot/` layout, `installed-plugins/` paths |
| **About hooks (concepts)** | `https://docs.github.com/en/copilot/concepts/agents/hooks` | Hook types, config format, performance/security notes |
| **VS Code hooks guide** | `microsoft/vscode-docs:docs/copilot/customization/hooks.md` (SHA `edb32577`) | VS Code-specific; 8 events, `chat.hookFilesLocations` setting |
| **VS Code agent plugins guide** | `microsoft/vscode-docs:docs/copilot/customization/agent-plugins.md` (SHA `a0feb5e3`) | Plugin format detection, hooks-in-plugins, install lifecycle |
| **`pluginParsers.ts` (VS Code source)** | `microsoft/vscode:src/vs/platform/agentPlugins/common/pluginParsers.ts` (SHA `1d6d8b44`) | Ground truth for format detection, HOOK_TYPE_MAP, field normalisation |

---

## 7. Complete Port Mapping: Claude Code Skill → Copilot CLI Plugin

### 7.1 Directory Layout

```
my-plugin/
  plugin.json                   # Copilot-format manifest
  hooks.json                    # Hook configuration (Copilot format)
  hooks/
    audit-prompt.sh             # Was: UserPromptSubmit script
    compliance-check.sh         # Was: Stop script
```

Or, to keep the Claude Code layout exactly (if registering with `.claude-plugin/plugin.json`):
```
my-plugin/
  .claude-plugin/
    plugin.json                 # Claude-format manifest
  hooks/
    hooks.json                  # hook config (Claude format, discovered automatically)
    audit-prompt.sh
    compliance-check.sh
```

### 7.2 `plugin.json` (Copilot format)

```json
{
  "name": "my-compliance-plugin",
  "description": "Audit logging and compliance enforcement for Copilot sessions",
  "version": "1.0.0",
  "author": { "name": "Your Name" },
  "hooks": "hooks.json"
}
```

Source schema: `https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference`

### 7.3 `hooks.json` Port

```jsonc
{
  "version": 1,
  "hooks": {

    // ── Claude Code: UserPromptSubmit ──────────────────────────────────────
    // CAUTION: Output is NOT processed in Copilot CLI. additionalContext
    // injection does NOT work here. Use this only for audit logging.
    // To inject context, use sessionStart instead.
    "userPromptSubmitted": [
      {
        "type": "command",
        "bash":       "hooks/audit-prompt.sh",
        "powershell": "hooks/audit-prompt.ps1",
        "cwd": ".",
        "timeoutSec": 5
      }
    ],

    // ── additionalContext injection alternative (fires once per session) ───
    "sessionStart": [
      {
        "type": "command",
        "bash":       "hooks/inject-context.sh",
        "powershell": "hooks/inject-context.ps1",
        "cwd": ".",
        "timeoutSec": 10
      }
    ],

    // ── Claude Code: Stop ─────────────────────────────────────────────────
    // CAUTION: exit 2 does NOT block in Copilot CLI (it's just a warning).
    // Return {"decision":"block","reason":"..."} via stdout to block a turn.
    "agentStop": [
      {
        "type": "command",
        "bash":       "hooks/compliance-check.sh",
        "powershell": "hooks/compliance-check.ps1",
        "cwd": ".",
        "timeoutSec": 30
      }
    ]
  }
}
```

### 7.4 Porting the `UserPromptSubmit` Script

**Claude Code original** (injects context via stdout `additionalContext`):
```bash
#!/usr/bin/env bash
INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt')
echo '{"additionalContext":"Always respond in bullet points."}'
```

**Copilot CLI port** (stdout ignored — logging only):
```bash
#!/usr/bin/env bash
INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt')
SESSION=$(echo "$INPUT" | jq -r '.sessionId')
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [$SESSION] PROMPT: $PROMPT" >> ~/.copilot/audit.log
# stdout JSON is silently discarded by Copilot CLI for this event
exit 0
```

**To inject context on every prompt** — use `sessionStart` and put standing instructions there, OR use `preToolUse` on the first tool the agent reliably calls (e.g., `bash`) and inject via `additionalContext` in that hook's output.

> ⚠️ **Confirmed gap**: `additionalContext` injection from `userPromptSubmitted` is explicitly marked as unsupported in the Copilot CLI hooks reference. Source: `github/docs:content/copilot/reference/hooks-reference.md:177`

### 7.5 Porting the `Stop` Script

**Claude Code original** (exit 2 blocks):
```bash
#!/usr/bin/env bash
INPUT=$(cat)
# ... run compliance scan on transcript ...
if grep -q "SECRET_KEY" transcript.txt; then
  echo "Compliance failure: credential detected" >&2
  exit 2   # blocks in Claude Code
fi
exit 0
```

**Copilot CLI port** (stdout JSON to block):
```bash
#!/usr/bin/env bash
INPUT=$(cat)
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcriptPath')
if grep -q "SECRET_KEY" "$TRANSCRIPT" 2>/dev/null; then
  # Must use stdout JSON; exit 2 would only warn
  echo '{"decision":"block","reason":"Compliance failure: credential detected in output. Please remove the secret and restate your answer."}'
  exit 0
fi
# Log compliance data (exit 0 is allow)
SESSION=$(echo "$INPUT" | jq -r '.sessionId')
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [$SESSION] PASS" >> ~/.copilot/compliance.log
exit 0
```

---

## 8. Verified Cross-Tool Compatibility Claim

**Finding: CONFIRMED.** The `hooks.json` wire format is shared across Copilot CLI, VS Code, and Claude Code.

Evidence:
1. `microsoft/vscode:src/vs/platform/agentPlugins/common/pluginParsers.ts:390-411` — The VS Code engine maps both camelCase (Copilot CLI) and PascalCase (Claude Code) event names to the same canonical identifiers.
2. `microsoft/vscode-docs:docs/copilot/customization/agent-plugins.md:406-425` — Explicit statement: *"The plugin format is shared between VS Code, GitHub Copilot CLI, and Claude Code. A single plugin repository can work across all three tools."*
3. `github/docs:content/copilot/concepts/agents/hooks.md` — Cross-tool config: Copilot CLI reads both `.github/copilot/settings.json` and `.claude/settings.json` (Claude Code's own format).
4. `github/copilot-plugins:.claude-plugin/marketplace.json` — GitHub's official plugin collection uses the `.claude-plugin/` directory naming, signalling deliberate Claude Code format compatibility.

**The key semantic differences** (not schema differences) between systems are exactly the two issues called out in §2: `userPromptSubmitted` output processing and `Stop` exit-code semantics.

---

## 9. Gaps and Uncertainties

| Question | Status |
|---|---|
| Does Copilot CLI's `userPromptSubmitted` hook plan to support `additionalContext` in future? | **Cannot determine from public sources.** The `"No"` in the output-processed column in `hooks-reference.md` is the current documented state. File an issue at `github/docs` or `cli/copilot` to track. |
| `notification` hook (CLI-only) as alternative for per-prompt injection? | Partially viable: `agent_completed` and `agent_idle` notification types fire after each turn, and `additionalContext` injected there is inserted as a user message and can trigger further processing. But it is async, not synchronous like Claude Code's `UserPromptSubmit`. |
| Does exit 2 from `agentStop` ever function as a block in any Copilot CLI version? | **Not documented.** The reference is explicit that exit 2 is a warning for most events. The only events where exit 2 has special meaning are `permissionRequest` (deny) and `postToolUseFailure` (additionalContext). |
| Windows: is there any way to run a bash script on `bash` field when Git Bash is available? | Not directly; the `bash` field invokes `/bin/bash` which doesn't exist on Windows. Use `command: "bash hooks/my-script.sh"` as a cross-platform field to rely on whatever `bash` is in PATH, or use `powershell: "& 'C:\\Program Files\\Git\\bin\\bash.exe' hooks/my-script.sh"`. |
| Is there a `preCompact` camelCase alias? | **Not in the current HOOK_TYPE_MAP.** Only PascalCase `PreCompact` is mapped. |
| `github/awesome-copilot` marketplace — any hooks.json examples? | The `awesome-copilot` repo was not publicly searchable with sufficient depth during this research; `sciagent-plugin` (registered for it) contains no `hooks.json`. |

---

*All citations verified against public GitHub repositories and `docs.github.com`. Last fetched: 2026-05-20.*