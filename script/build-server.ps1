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
$AdminDistDir = Join-Path $Root "packaging\dist\server-admin"
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

Write-Host "[1/4] Checking server build dependencies and release config..." -ForegroundColor Yellow
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

# A reused dist directory may still contain files from the former external-INI
# deployment. Remove them so they cannot override the newly embedded config.
foreach ($ObsoleteConfig in @("flight_analyzer.ini", "flight_analyzer.ini.example")) {
    Remove-Item `
        -LiteralPath (Join-Path $DistDir $ObsoleteConfig) `
        -Force `
        -ErrorAction SilentlyContinue
}
Remove-Item `
    -LiteralPath (Join-Path $DistDir "initialize-mysql.ps1") `
    -Force `
    -ErrorAction SilentlyContinue

Write-Host "[3/4] Staging deployment helpers..." -ForegroundColor Yellow
$Initializer = Join-Path $AdminDistDir "initialize-mysql.ps1"
Copy-Item `
    -LiteralPath (Join-Path $Root "script\initialize-mysql.ps1") `
    -Destination $Initializer `
    -Force
$parseTokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    $Initializer,
    [ref]$parseTokens,
    [ref]$parseErrors
) > $null
if ($parseErrors.Count -gt 0) {
    throw "Generated MySQL initializer is not valid PowerShell: $($parseErrors[0].Message)"
}
Copy-Item `
    -LiteralPath (Join-Path $Root "script\start-server.ps1") `
    -Destination (Join-Path $DistDir "start-server.ps1") `
    -Force

$Exe = Join-Path $DistDir "FlightAnalyzerServer.exe"
$Hash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash
Set-Content `
    -LiteralPath (Join-Path $DistDir "SHA256.txt") `
    -Value "$Hash *FlightAnalyzerServer.exe" `
    -Encoding ascii
$InitializerHash = (Get-FileHash -LiteralPath $Initializer -Algorithm SHA256).Hash
Set-Content `
    -LiteralPath (Join-Path $AdminDistDir "SHA256.txt") `
    -Value "$InitializerHash *initialize-mysql.ps1" `
    -Encoding ascii

Write-Host "[4/4] Checking packaged server runtime..." -ForegroundColor Yellow
Push-Location $DistDir
try {
    & ".\FlightAnalyzerServer.exe" --check-runtime
    $RuntimeExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($RuntimeExitCode -ne 0) {
    throw "Packaged server runtime check failed with exit code $RuntimeExitCode"
}

Write-Host "Runtime package: $DistDir" -ForegroundColor Green
Write-Host "Administrator package: $AdminDistDir" -ForegroundColor Green
