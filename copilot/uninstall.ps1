# uninstall.ps1 — ONE-COMMAND uninstaller for tellonce (GitHub Copilot CLI, Windows).
#
#   powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/tellonce/v1.2.3/copilot/uninstall.ps1 | iex"
#
# WHY THIS EXISTS: the hooks keep firing as long as the plugin is REGISTERED in
# ~/.copilot/config.json — deleting the files alone is not enough. This removes
# the registration FIRST (so hooks stop firing) and then the plugin files.
# Your recorded memory/preferences are KEPT. Download + run with -Purge to also
# delete state + memory + the config mode keys.

param([switch]$Purge)
$ErrorActionPreference = 'Stop'
function Note($m){ Write-Host $m }

$copilotHome = Join-Path $env:USERPROFILE '.copilot'
$pluginParent = Join-Path $copilotHome 'installed-plugins\tellonce'
$plugin = Join-Path $pluginParent 'tellonce'

Write-Host "================================================================"
Write-Host "  tellonce — one-command uninstaller (Copilot CLI)"
Write-Host "================================================================"

# Find a real python (skip the Microsoft Store WindowsApps stub).
function Resolve-Python {
    $sidecar = Join-Path $plugin 'hooks\.python_path.txt'
    if (Test-Path $sidecar) {
        $p = (Get-Content $sidecar -Raw).Trim()
        if ($p -and (Test-Path $p) -and ($p -notlike '*\WindowsApps\*')) { return $p }
    }
    foreach ($name in @('python3','python')) {
        foreach ($c in (Get-Command $name -All -ErrorAction SilentlyContinue)) {
            if ($c.Source -and ($c.Source -notlike '*\WindowsApps\*')) {
                try { & $c.Source -c "import sys; assert sys.version_info>=(3,7)" 2>$null; if ($LASTEXITCODE -eq 0) { return $c.Source } } catch {}
            }
        }
    }
    return $null
}
$py = Resolve-Python

# 1. Remove the hook registration (+ optional purge) WHILE the files still exist.
$unregistered = $false
if ($py -and (Test-Path (Join-Path $plugin 'lib\uninstall.py'))) {
    $flags = if ($Purge) { @('--all') } else { @('--unregister','--reset-config') }
    Note "Removing hook registration$(if($Purge){' + state + memory'})..."
    & $py (Join-Path $plugin 'lib\uninstall.py') @flags
    $unregistered = $true
} elseif ($py -and (Test-Path (Join-Path $plugin 'lib\register_plugin.py'))) {
    Note "Removing hook registration..."
    & $py (Join-Path $plugin 'lib\register_plugin.py') '--unregister'
    $unregistered = $true
}
if (-not $unregistered) {
    Write-Host "[i] Could not run the in-plugin uninstaller (python or plugin missing)." -ForegroundColor Yellow
    Write-Host "    Manually remove the 'tellonce' entry from ~\.copilot\config.json installedPlugins." -ForegroundColor Yellow
}

# 2. Remove the plugin files.
if (Test-Path $pluginParent) {
    Remove-Item -Recurse -Force $pluginParent -ErrorAction SilentlyContinue
    Note "Removed plugin files: $pluginParent"
}

Write-Host ""
Write-Host "================================================================"
Write-Host "[OK] tellonce uninstalled." -ForegroundColor Green
Write-Host "  >> RESTART Copilot so the hooks fully unload. <<"
if (-not $Purge) {
    Write-Host "  Your saved memory/preferences were kept. To remove those too,"
    Write-Host "  download this script and run it with -Purge before restarting."
}
Write-Host "================================================================"
