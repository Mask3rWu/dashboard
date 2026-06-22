Set-Location $PSScriptRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Flight Analyzer - Build & Run" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# [1/2] Build frontend
Write-Host "[1/2] Building frontend..." -ForegroundColor Yellow
Set-Location "$PSScriptRoot\frontend"
npm run build
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Frontend build failed!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit $LASTEXITCODE
}

# [2/2] Start backend
Write-Host ""
Write-Host "[2/2] Starting backend..." -ForegroundColor Yellow
Set-Location $PSScriptRoot
.\.venv\Scripts\Activate.ps1
python main.py
