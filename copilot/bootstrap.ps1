# bootstrap.ps1 — ONE-COMMAND installer for tellonce (GitHub Copilot CLI, Windows).
#
# Users run a single copy-paste line (no environment fiddling required):
#
#   powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/YujunZhou/tellonce/v1.2.0/copilot/bootstrap.ps1 | iex"
#
# It downloads the plugin, drops it into Copilot's plugin folder, installs the
# optional PyYAML dep, runs post-install (state dirs, seed rules, observe mode,
# plugin registration, python path), and tells you to restart Copilot.
# Safe to re-run. Default mode = observe (records + reminds, never blocks).

$ErrorActionPreference = 'Stop'
$REPO   = 'https://github.com/YujunZhou/tellonce'
# Pinned to a release tag (immutable) for integrity. git clone --branch accepts
# a tag; archive uses refs/tags for a tag (refs/heads for a branch).
$REF    = 'v1.2.0'
$REFKIND = 'tags'

function Fail($msg) { Write-Host "[X] $msg" -ForegroundColor Red; exit 1 }

Write-Host "================================================================"
Write-Host "  tellonce — one-command installer (Copilot CLI)"
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
        git clone --depth 1 --branch $REF "$REPO.git" $work 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
        $srcCopilot = Join-Path $work 'copilot'
    } else { throw "no git" }
} catch {
    Write-Host "Downloading (zip)..."
    try {
        $zip = "$work.zip"
        Invoke-WebRequest -UseBasicParsing -Uri "$REPO/archive/refs/$REFKIND/$REF.zip" -OutFile $zip
        New-Item -ItemType Directory -Force -Path $work | Out-Null
        Expand-Archive -Path $zip -DestinationPath $work -Force
        $inner = Get-ChildItem $work -Directory | Select-Object -First 1
        $srcCopilot = Join-Path $inner.FullName 'copilot'
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
    } catch {
        Fail "Download failed (no internet / proxy / private repo?). Check your connection and retry. Details: $($_.Exception.Message)"
    }
}
if (-not (Test-Path $srcCopilot)) { Fail "Download succeeded but copilot/ folder missing — repo layout changed?" }

# 4. Stage the new code tree, then swap it in: a failed copy leaves the
# previous working install untouched; the swap drops files deleted in newer
# releases. User memory/state live elsewhere, EXCEPT personal rule overlays
# lib\*.user.yaml — carried over into the staged tree before the swap.
$dest = Join-Path $copilotHome 'installed-plugins\tellonce\tellonce'
$stage = "$dest.new-$PID"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
# robocopy exit codes 0-7 mean success/partial-success; >=8 means failure.
robocopy $srcCopilot $stage /E /XD __pycache__ .pytest_cache PORT_NOTES /XF *.pyc PORT_DESIGN.md /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Fail "Plugin file copy failed (robocopy exit $LASTEXITCODE) — previous install left untouched. Re-run the installer." }
$oldLib = Join-Path $dest 'lib'
if (Test-Path $oldLib) {
    $kept = Get-ChildItem -Path $oldLib -Filter '*.user.yaml' -ErrorAction SilentlyContinue
    if ($kept) {
        $kept | Copy-Item -Destination (Join-Path $stage 'lib') -Force
        Write-Host "[OK] Preserved your personal rule overlay(s): $($kept.Name -join ', ')" -ForegroundColor Green
    }
}
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
Move-Item -Path $stage -Destination $dest
Write-Host "[OK] Plugin files installed to $dest" -ForegroundColor Green

# 5. Optional dependency (best-effort; deterministic blocking works without it,
# but session-start rule injection needs PyYAML). Native commands don't throw, so
# check $LASTEXITCODE rather than try/catch.
& $py -m pip install --quiet --disable-pip-version-check "pyyaml>=6.0" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] PyYAML ready" -ForegroundColor Green
} else {
    Write-Host "[i] PyYAML not installed — session-start rule injection will be OFF (deterministic blocking still works). Install later: $py -m pip install pyyaml" -ForegroundColor Yellow
}

# 6. Run post-install from the installed copy. Pass the resolved python so the
# installer doesn't re-discover (and risk picking the WindowsApps stub).
Write-Host "Running post-install..."
& powershell -ExecutionPolicy Bypass -File (Join-Path $dest 'install.ps1') -Mode observe -Python $py

# 7. Cleanup.
Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "================================================================"
Write-Host "[OK] tellonce installed." -ForegroundColor Green
Write-Host "  >> RESTART Copilot for the hooks to load. <<"
Write-Host ""
Write-Host "  Default mode = observe (records your preferences, never blocks)."
Write-Host "  Turn on hard blocking later with:"
Write-Host "    python `"$dest\lib\pt_mode.py`" enforce"
Write-Host "  Check status anytime:"
Write-Host "    python `"$dest\lib\doctor.py`""
Write-Host "  Uninstall:"
Write-Host "    python `"$dest\lib\uninstall.py`" --all"
Write-Host "================================================================"
