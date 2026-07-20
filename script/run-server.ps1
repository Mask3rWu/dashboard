param(
    [string]$ServerVenv = ".venv-server",
    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not [System.IO.Path]::IsPathRooted($ServerVenv)) {
    $ServerVenv = Join-Path $Root $ServerVenv
}
$Python = Join-Path $ServerVenv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Server environment was not found at $ServerVenv. Create it and install requirements-server.txt."
}

$arguments = @((Join-Path $Root "server_main.py"))
if ($ConfigPath) {
    $arguments += @("--config", $ConfigPath)
}

Push-Location $Root
try {
    & $Python @arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
