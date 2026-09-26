[CmdletBinding()]
param(
    [string]$OllamaPath = "",
    [string]$ModelsPath = ""
)
$ErrorActionPreference = "Stop"
if (-not $OllamaPath) {
    $OllamaPath = (Get-Command ollama.exe -ErrorAction Stop).Source
}
if (-not (Test-Path -LiteralPath $OllamaPath -PathType Leaf)) {
    throw "Installed Ollama executable was not found. No download was attempted."
}
if ($ModelsPath -and -not (Test-Path -LiteralPath $ModelsPath -PathType Container)) {
    throw "ModelsPath must be an existing verified offline Ollama models directory."
}
# Fail before changing process environment; never terminate another listener.
$Listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 11435)
try { $Listener.Start() } catch { throw "Port 11435 is occupied. Stop the intended service yourself before retrying." }
finally { $Listener.Stop() }
$Settings = @{
    OLLAMA_HOST = "127.0.0.1:11435"
    OLLAMA_MAX_LOADED_MODELS = "1"
    OLLAMA_NUM_PARALLEL = "1"
    OLLAMA_CONTEXT_LENGTH = "4096"
    OLLAMA_NO_CLOUD = "1"
}
if ($ModelsPath) { $Settings["OLLAMA_MODELS"] = (Resolve-Path -LiteralPath $ModelsPath).Path }
$Previous = @{}
try {
    foreach ($Key in $Settings.Keys) {
        $Previous[$Key] = [Environment]::GetEnvironmentVariable($Key, "Process")
        [Environment]::SetEnvironmentVariable($Key, $Settings[$Key], "Process")
    }
    Write-Host "Starting local Ollama on 127.0.0.1:11435. One model, one request. Leave this window open."
    Write-Host "This starts the service only. Run prepare_8gb_model.py separately to verify and warm the pinned offline model."
    & $OllamaPath serve
    $TaskExit = $LASTEXITCODE
} finally {
    foreach ($Key in $Previous.Keys) {
        [Environment]::SetEnvironmentVariable($Key, $Previous[$Key], "Process")
    }
}
exit $TaskExit
