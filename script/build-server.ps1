param(
    [string]$BuildVenv = ".venv-server"
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not [System.IO.Path]::IsPathRooted($BuildVenv)) {
    $BuildVenv = Join-Path $Root $BuildVenv
}
$Python = Join-Path $BuildVenv "Scripts\python.exe"
$PyInstaller = Join-Path $BuildVenv "Scripts\pyinstaller.exe"
$DistDir = Join-Path $Root "packaging\dist\server"
$WorkDir = Join-Path $Root "packaging\build\server"
$Spec = Join-Path $Root "packaging\FlightAnalyzerServer.spec"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Minimal server build environment was not found at $BuildVenv. Run: py -3.12 -m venv .venv-server; .\.venv-server\Scripts\pip.exe install -r requirements-server-build.txt"
}
if (-not (Test-Path -LiteralPath $PyInstaller)) {
    throw "PyInstaller was not found at $PyInstaller. Install requirements-server-build.txt in the selected build environment."
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Flight Analyzer Server - Package" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

Write-Host "[1/4] Checking server build dependencies..." -ForegroundColor Yellow
& $Python -c "import fastapi, uvicorn, sqlalchemy, pymysql, multipart"
if ($LASTEXITCODE -ne 0) {
    throw "Server dependencies are incomplete. Install requirements-server-build.txt in $BuildVenv"
}

Write-Host "[2/4] Running PyInstaller..." -ForegroundColor Yellow
& $PyInstaller `
    --distpath $DistDir `
    --workpath $WorkDir `
    --clean `
    --noconfirm `
    $Spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Write-Host "[3/4] Staging deployment helpers..." -ForegroundColor Yellow
Copy-Item `
    -LiteralPath (Join-Path $Root "packaging\flight_analyzer.server.ini.example") `
    -Destination (Join-Path $DistDir "flight_analyzer.ini.example") `
    -Force
Copy-Item `
    -LiteralPath (Join-Path $Root "script\start-packaged-server.ps1") `
    -Destination (Join-Path $DistDir "start-server.ps1") `
    -Force

$Exe = Join-Path $DistDir "FlightAnalyzerServer.exe"
$Hash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash
Set-Content `
    -LiteralPath (Join-Path $DistDir "SHA256.txt") `
    -Value "$Hash *FlightAnalyzerServer.exe" `
    -Encoding ascii

Write-Host "[4/4] Checking packaged server runtime..." -ForegroundColor Yellow
& $Exe `
    --config (Join-Path $DistDir "flight_analyzer.ini.example") `
    --check-runtime
if ($LASTEXITCODE -ne 0) {
    throw "Packaged server runtime check failed with exit code $LASTEXITCODE"
}

Write-Host "Done: $DistDir" -ForegroundColor Green
