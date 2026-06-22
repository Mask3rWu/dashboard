@echo off
echo ========================================
echo   CR500A Flight Analyzer - Build ^& Run
echo ========================================
echo.

echo [1/2] Building frontend...
cd /d "%~dp0frontend"
call npm run build
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Frontend build failed!
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [2/2] Starting backend...
cd /d "%~dp0"
call .venv\Scripts\activate && python main.py

pause
