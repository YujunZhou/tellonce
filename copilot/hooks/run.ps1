# run.ps1 — robust launcher for preference-tracker Python hooks on Windows.
#
# Fixes two Copilot-on-Windows deployment problems found in live Test B:
#   RC1: `pwsh -c "python ...; <python exits 2>"` makes pwsh return exit 1, and
#        Copilot only honors a Stop block on a clean exit (0 or 2) — exit 1 is
#        treated as a hook error and the stdout {"decision":"block"} is discarded.
#        We end with `exit $LASTEXITCODE` so the child's real exit code propagates.
#   RC2: bare `python` can resolve to the Microsoft Store WindowsApps execution-
#        alias stub (which does nothing) when Copilot spawns the hook without the
#        conda-activated PATH. We resolve a REAL python: prefer the path recorded
#        at install time, then search excluding the WindowsApps stub.
#
# Usage (from hooks.json powershell field):
#   & "<plugin>\hooks\run.ps1" <script_name.py> [extra args...]
# stdin (the hook event JSON) is forwarded to the Python process unchanged.

param(
    [Parameter(Mandatory = $true)][string]$Script,
    [Parameter(ValueFromRemainingArguments = $true)]$Rest
)

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pluginRoot = Split-Path -Parent $here
$libScript = Join-Path $pluginRoot (Join-Path 'lib' $Script)

function Resolve-Python {
    # 1. Path recorded by install.ps1 (the python that ran the installer — known good).
    $sidecar = Join-Path $here '.python_path.txt'
    if (Test-Path $sidecar) {
        try {
            $p = (Get-Content $sidecar -Raw -ErrorAction Stop).Trim()
            if ($p -and (Test-Path $p)) { return $p }
        } catch {}
    }
    # 2. Search PATH, skipping the WindowsApps execution-alias stub.
    foreach ($name in @('python3', 'python')) {
        $cmds = Get-Command $name -All -ErrorAction SilentlyContinue
        foreach ($c in $cmds) {
            $src = $c.Source
            if ($src -and ($src -notlike '*\WindowsApps\*')) { return $src }
        }
    }
    # 3. Last resort — bare name (may be the stub, but we tried).
    return 'python'
}

$py = Resolve-Python

# Forward stdin (hook event JSON) to the Python process. Reading to EOF is safe
# because Copilot pipes the event and closes stdin; an empty read is also fine.
$stdinData = ''
try { $stdinData = [Console]::In.ReadToEnd() } catch {}

if ($stdinData) {
    $stdinData | & $py $libScript @Rest
} else {
    & $py $libScript @Rest
}
exit $LASTEXITCODE
