@echo off
setlocal

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-server.ps1" %*
exit /b %ERRORLEVEL%
