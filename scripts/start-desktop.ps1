[CmdletBinding()]
param(
    [string]$PythonPath = "",
    [ValidateSet("demo", "video", "live")][string]$Source = "demo",
    [string]$ConfigPath = "",
    [string]$DataDir = "",
    [string]$InputPath = "",
    [string]$Routes = "",
    [string]$DetectorWeights = "",
    [ValidateSet("vram8gb", "existing")][string]$ResourceProfile = "vram8gb",
    [switch]$EnableReview,
    [switch]$Probe
)
$ErrorActionPreference = "Stop"
$TaskRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $TaskRoot "implementation\.venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Prepared Python is missing. Pass -PythonPath with the Python 3.12 executable. See docs/12-demonstration-and-field-run.md."
}
$Arguments = @("-m", "factory_monitor_desktop", "--source", $Source, "--resource-profile", $ResourceProfile)
if ($ConfigPath) { $Arguments += @("--config", $ConfigPath) }
if ($DataDir) { $Arguments += @("--data-dir", $DataDir) }
if ($InputPath) { $Arguments += @("--input", $InputPath) }
if ($Routes) { $Arguments += @("--routes", $Routes) }
if ($DetectorWeights) { $Arguments += @("--detector-weights", $DetectorWeights) }
if ($EnableReview) { $Arguments += "--enable-review" }
if ($Probe) { $Arguments += "--probe" }
$PreviousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = (Join-Path $TaskRoot "desktop\src") + ";" + (Join-Path $TaskRoot "implementation\src")
    & $PythonPath @Arguments
    $TaskExit = $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $PreviousPythonPath
}
exit $TaskExit
