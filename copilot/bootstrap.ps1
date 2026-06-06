# bootstrap.ps1 — ONE-COMMAND installer for preference-tracker (GitHub Copilot CLI, Windows).
#
# Users run a single copy-paste line (no environment fiddling required):
#
#   powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/preference-tracker/main/copilot/bootstrap.ps1 | iex"
#
# It downloads the plugin, drops it into Copilot's plugin folder, installs the
# optional PyYAML dep, runs post-install (state dirs, seed rules, observe mode,
# plugin registration, python path), and tells you to restart Copilot.
# Safe to re-run. Default mode = observe (records + reminds, never blocks).

$ErrorActionPreference = 'Stop'
$REPO   = 'https://github.com/YujunZhou/preference-tracker'
$BRANCH = 'main'

function Fail($msg) { Write-Host "[X] $msg" -ForegroundColor Red; exit 1 }

Write-Host "================================================================"
Write-Host "  preference-tracker — one-command installer (Copilot CLI)"
Write-Host "================================================================"

# 1. Copilot home must exist.
$copilotHome = Join-Path $env:USERPROFILE '.copilot'
if (-not (Test-Path $copilotHome)) {
    Fail "Copilot CLI home (~\.copilot) not found. Install GitHub Copilot CLI first, run it once, then re-run this."
}

# 2. Find a REAL python (skip the Microsoft Store WindowsApps stub).
function Resolve-Python {
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
if (-not $py) { Fail "Python 3.7+ not found. Install it from https://www.python.org/downloads/ (check 'Add to PATH'), then re-run." }
Write-Host "[OK] Python: $py" -ForegroundColor Green

# 3. Download the repo (git clone if available, else zip).
$work = Join-Path $env:TEMP ("pt-bootstrap-" + [System.Guid]::NewGuid().ToString('N').Substring(0,8))
$srcCopilot = $null
try {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Host "Downloading (git)..."
        git clone --depth 1 --branch $BRANCH "$REPO.git" $work 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
        $srcCopilot = Join-Path $work 'copilot'
    } else { throw "no git" }
} catch {
    Write-Host "Downloading (zip)..."
    $zip = "$work.zip"
    Invoke-WebRequest -UseBasicParsing -Uri "$REPO/archive/refs/heads/$BRANCH.zip" -OutFile $zip
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    Expand-Archive -Path $zip -DestinationPath $work -Force
    $inner = Get-ChildItem $work -Directory | Select-Object -First 1
    $srcCopilot = Join-Path $inner.FullName 'copilot'
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
}
if (-not (Test-Path $srcCopilot)) { Fail "Download succeeded but copilot/ folder missing — repo layout changed?" }

# 4. Copy into Copilot's installed-plugins (overwrite code; user memory/state live elsewhere).
$dest = Join-Path $copilotHome 'installed-plugins\preference-tracker\preference-tracker'
New-Item -ItemType Directory -Force -Path $dest | Out-Null
robocopy $srcCopilot $dest /E /XD __pycache__ .pytest_cache PORT_NOTES /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
Write-Host "[OK] Plugin files installed to $dest" -ForegroundColor Green

# 5. Optional dependency (best-effort; the flagship block works without it).
try { & $py -m pip install --quiet --disable-pip-version-check pyyaml 2>$null; Write-Host "[OK] PyYAML ready" -ForegroundColor Green } catch { Write-Host "[i] PyYAML not installed (fingerprint retrieval will degrade; core blocking still works)" -ForegroundColor Yellow }

# 6. Run post-install from the installed copy (state, seed, observe mode, register, python path).
Write-Host "Running post-install..."
& powershell -ExecutionPolicy Bypass -File (Join-Path $dest 'install.ps1') -Mode observe

# 7. Cleanup.
Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "================================================================"
Write-Host "[OK] preference-tracker installed." -ForegroundColor Green
Write-Host "  >> RESTART Copilot for the hooks to load. <<"
Write-Host ""
Write-Host "  Default mode = observe (records your preferences, never blocks)."
Write-Host "  Turn on hard blocking later with:"
Write-Host "    python `"$dest\lib\pt_mode.py`" enforce"
Write-Host "  Check status anytime:"
Write-Host "    python `"$dest\lib\doctor.py`""
Write-Host "================================================================"
