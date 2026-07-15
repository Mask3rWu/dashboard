@echo off
echo ========================================
echo   Flight Analyzer - Package
echo ========================================
echo.

echo [1/2] Building frontend...
cd /d "%~dp0..\frontend"
call npm run build
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Frontend build failed!
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [2/2] Running PyInstaller...
cd /d "%~dp0.."
.venv\Scripts\pyinstaller.exe --distpath packaging\dist --workpath packaging\build --noconfirm packaging\FlightAnalyzer.spec
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: PyInstaller failed!
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo Done: packaging\dist\FlightAnalyzer.exe
pause
