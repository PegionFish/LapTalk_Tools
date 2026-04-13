[CmdletBinding()]
param(
    [string]$Name = "HWiNFO-CSV-Plotter",
    [switch]$Onedir,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectDir

$pythonCommand = $null
foreach ($candidate in @("python", "py")) {
    try {
        & $candidate --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonCommand = $candidate
            break
        }
    } catch {
    }
}

if (-not $pythonCommand) {
    throw "Python was not found. Install Python 3.10+ and retry."
}

$arguments = @(".\build_exe.py", "--name", $Name)
if ($Onedir) {
    $arguments += "--onedir"
}
if ($DryRun) {
    $arguments += "--dry-run"
}

& $pythonCommand @arguments
exit $LASTEXITCODE
