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
    [string]$ProjectRoot = (Get-Location).Path
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

# 1. Verify Python
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "[ERROR] python not found in PATH. Please install Python 3.7+." -ForegroundColor Red
    exit 1
}
$pythonVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Host "[OK] Python $pythonVer" -ForegroundColor Green

# 2. Create state directories
Write-Host ""
Write-Host "Creating state directories..."
$env:B5_PROJECT_ROOT = $ProjectRoot
$env:PT_LIB = $PTLib
python "$PTLib\path_config.py"
python "$ScriptDir\_install_helper.py" ensure-dirs
if ($LASTEXITCODE -eq 0) { Write-Host "[OK] State directories created" -ForegroundColor Green }

# 3. Seed memory
$memoryDir = python "$ScriptDir\_install_helper.py" get-memory-dir

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

# 4. Write config file
$configPath = Join-Path $env:USERPROFILE ".preference-tracker.config.json"
if (-not (Test-Path $configPath)) {
    Write-Host ""
    Write-Host "Writing default config to $configPath..."
    $config = @{
        project_root = $ProjectRoot
        retrieve_cli = "copilot"
        retrieve_backend = "cli"
        retrieve_model = "claude-haiku-4-5"
    } | ConvertTo-Json
    Set-Content $configPath -Value $config -Encoding UTF8
    Write-Host "[OK] Config written" -ForegroundColor Green
} else {
    Write-Host "[OK] Config already exists at $configPath" -ForegroundColor Green
}

Write-Host ""
Write-Host "================================================================"
Write-Host "[OK] Installation complete!" -ForegroundColor Green
Write-Host ""
Write-Host "The plugin hooks are now active for any Copilot CLI session."
Write-Host "State: $ProjectRoot\.copilot\preference-tracker-state\"
Write-Host "Memory: $memoryDir"
Write-Host ""
Write-Host "To verify: run 'copilot' in your project and check that the"
Write-Host "Gate Function (SCAN/RECORD/CONFIRM) fires on Stop events."
Write-Host "================================================================"
