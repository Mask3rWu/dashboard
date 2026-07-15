Set-Location "$PSScriptRoot\.."

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Flight Analyzer - Package" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# [1/2] Build frontend
Write-Host "[1/2] Building frontend..." -ForegroundColor Yellow
Set-Location "$PSScriptRoot\..\frontend"
npm run build
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Frontend build failed!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit $LASTEXITCODE
}

# [2/2] Run PyInstaller
Set-Location "$PSScriptRoot\.."
Write-Host ""
Write-Host "[2/2] Running PyInstaller..." -ForegroundColor Yellow
.\.venv\Scripts\pyinstaller.exe --distpath packaging\dist --workpath packaging\build --noconfirm packaging\FlightAnalyzer.spec
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: PyInstaller failed!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Done: packaging\dist\FlightAnalyzer.exe" -ForegroundColor Green
