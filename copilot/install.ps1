# install.ps1 — Post-install setup for preference-tracker Copilot CLI plugin (Windows).
#
# Run after `copilot plugin install YujunZhou/preference-tracker:copilot`
# to initialize state directories and seed memory if not already present.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File <plugin_root>\install.ps1 [-ProjectRoot C:\path\to\project]
#
# Idempotent — safe to re-run.

param(
    [string]$ProjectRoot = (Get-Location).Path,
    [ValidateSet('observe','enforce','full')]
    [string]$Mode = 'observe',
    [string]$Python = ''
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PTLib = Join-Path $ScriptDir "lib"

Write-Host "================================================================"
Write-Host "  preference-tracker - Copilot CLI plugin post-install"
Write-Host "================================================================"
Write-Host ""
Write-Host "Plugin root:  $ScriptDir"
Write-Host "Project root: $ProjectRoot"
Write-Host ""

# 1. Resolve a REAL python (3.7+). Prefer the path bootstrap passed in (-Python);
# otherwise search PATH skipping the Microsoft Store WindowsApps stub and trying
# python3 before python. Using bare `python` is unsafe (can be the Store stub).
function Resolve-RealPython {
    param([string]$Hint)
    if ($Hint -and (Test-Path $Hint)) {
        try { & $Hint -c "import sys; raise SystemExit(0 if sys.version_info>=(3,7) else 1)" 2>$null; if ($LASTEXITCODE -eq 0) { return $Hint } } catch {}
    }
    foreach ($name in @('python3','python')) {
        foreach ($c in (Get-Command $name -All -ErrorAction SilentlyContinue)) {
            if ($c.Source -and ($c.Source -notlike '*\WindowsApps\*')) {
                try { & $c.Source -c "import sys; raise SystemExit(0 if sys.version_info>=(3,7) else 1)" 2>$null; if ($LASTEXITCODE -eq 0) { return $c.Source } } catch {}
            }
        }
    }
    return $null
}
$Py = Resolve-RealPython -Hint $Python
if (-not $Py) {
    Write-Host "[ERROR] Python 3.7+ not found (the Microsoft Store stub does not count)." -ForegroundColor Red
    Write-Host "        Install from https://www.python.org/downloads/ (check 'Add to PATH')." -ForegroundColor Red
    exit 1
}
$pythonVer = & $Py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "[OK] Python $pythonVer ($Py)" -ForegroundColor Green

# 1b. Record the REAL python executable for the hook launcher (run.ps1). When
# Copilot spawns hooks it may not have the conda PATH, so bare `python` can
# resolve to the WindowsApps stub and the hook won't run.
$realPython = & $Py -c "import sys; print(sys.executable)"
if ($realPython) {
    $sidecar = Join-Path $ScriptDir "hooks\.python_path.txt"
    [System.IO.File]::WriteAllText($sidecar, $realPython.Trim(), (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "[OK] Recorded python path for hooks: $realPython" -ForegroundColor Green
}

# 2. Create state directories
Write-Host ""
Write-Host "Creating state directories..."
$env:B5_PROJECT_ROOT = $ProjectRoot
$env:PT_LIB = $PTLib
& $Py "$PTLib\path_config.py"
& $Py "$ScriptDir\_install_helper.py" ensure-dirs
if ($LASTEXITCODE -eq 0) { Write-Host "[OK] State directories created" -ForegroundColor Green }

# 3. Seed memory
$memoryDir = & $Py "$ScriptDir\_install_helper.py" get-memory-dir

if ((Test-Path $memoryDir) -and (Get-ChildItem $memoryDir -Filter "*.md" -ErrorAction SilentlyContinue)) {
    Write-Host "[OK] Memory directory already has rules ($memoryDir)" -ForegroundColor Green
} else {
    Write-Host "Seeding memory with starter rules..."
    New-Item -ItemType Directory -Path $memoryDir -Force | Out-Null
    $seedDir = Join-Path $ScriptDir "seed_memory"
    if (Test-Path $seedDir) {
        Copy-Item "$seedDir\*.md" $memoryDir -Force -ErrorAction SilentlyContinue
        $count = (Get-ChildItem $memoryDir -Filter "*.md" | Measure-Object).Count
        Write-Host "[OK] Seeded $count rules" -ForegroundColor Green
    }
}

# 4. Write config file (retrieve defaults) + set the mode switch automatically.
$configPath = Join-Path $env:USERPROFILE ".preference-tracker.config.json"
if (-not (Test-Path $configPath)) {
    Write-Host ""
    Write-Host "Writing default config to $configPath..."
    $config = @{
        retrieve_cli = "copilot"
        retrieve_backend = "cli"
        retrieve_model = "claude-haiku-4-5"
    } | ConvertTo-Json
    # Write UTF-8 WITHOUT BOM. PowerShell 5.1 `Set-Content -Encoding UTF8`
    # prepends a BOM, which makes json.load choke on the reader side.
    [System.IO.File]::WriteAllText($configPath, $config, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "[OK] Config written" -ForegroundColor Green
} else {
    Write-Host "[OK] Config already exists at $configPath" -ForegroundColor Green
    # Migration: older installs pinned `project_root` into the config, which
    # overrides the per-cwd path resolution and silently sends every project to
    # one shared state/memory dir. Strip it so runtime falls back to cwd.
    $migrate = @'
import json, io, os
p = os.path.expanduser("~/.preference-tracker.config.json")
try:
    c = json.load(io.open(p, encoding="utf-8-sig"))
except Exception:
    raise SystemExit(0)
if "project_root" in c:
    c.pop("project_root", None)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print("[OK] Migrated config: removed stale project_root")
'@
    & $Py -c $migrate 2>$null
}

# Set the on/off switch for the user automatically — no hand-editing needed.
# pt_mode merges into the config and preserves all other keys.
Write-Host ""
Write-Host "Setting mode = $Mode ..."
& $Py "$PTLib\pt_mode.py" $Mode | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Host "[OK] Mode set to $Mode" -ForegroundColor Green }

# Register the plugin in Copilot's config so its hooks actually load. Required
# for SIDE-LOAD installs (files copied into installed-plugins without going
# through `copilot plugin install`). Idempotent + backs up config.json. If you
# installed via `copilot plugin install`, this is already done and is a no-op.
$scriptUnderInstalled = $ScriptDir.ToLower().Replace('\','/') -like '*installed-plugins*'
if ($scriptUnderInstalled) {
    Write-Host ""
    Write-Host "Registering plugin with Copilot (so hooks load)..."
    & $Py "$PTLib\register_plugin.py"
    Write-Host "  (restart Copilot to load the hooks)"
} else {
    Write-Host ""
    Write-Host "[NOTE] Running from a non-installed location; skipping auto-registration." -ForegroundColor Yellow
    Write-Host "       For hooks to load, install via 'copilot plugin install' OR run this"
    Write-Host "       installer from the copied plugin dir under ~/.copilot/installed-plugins."
}

Write-Host ""
Write-Host "================================================================"
Write-Host "[OK] Installation complete!" -ForegroundColor Green
Write-Host ""
Write-Host "The plugin hooks are now active for any Copilot CLI session."
Write-Host "Current mode = $Mode"
Write-Host ""
Write-Host "observe = only records preferences + reminds you (safe default;"
Write-Host "          never hard-blocks, never calls an LLM)."
Write-Host "enforce = also hard-blocks replies that violate your saved rules."
Write-Host "full    = enforce + an LLM 'shadow judge' (sends the conversation"
Write-Host "          to copilot -p; redacts secrets first)."
Write-Host ""
Write-Host "Change mode anytime with ONE command (copy-paste):"
Write-Host "  python `"$PTLib\pt_mode.py`" enforce     # turn on hard blocking"
Write-Host "  python `"$PTLib\pt_mode.py`" full        # blocking + AI judge"
Write-Host "  python `"$PTLib\pt_mode.py`" observe     # back to safe default"
Write-Host "  python `"$PTLib\pt_mode.py`" status      # show current mode"
Write-Host ""
Write-Host "Tip: re-run this installer with -Mode enforce (or -Mode full) to"
Write-Host "     turn it on at install time."
Write-Host ""
Write-Host "State: $ProjectRoot\.copilot\preference-tracker-state\"
Write-Host "Memory: $memoryDir"
Write-Host "================================================================"
