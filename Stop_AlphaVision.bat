@echo off
setlocal enabledelayedexpansion
REM ==============================================================================
REM AlphaVision Unified Kill Switch
REM Instantly terminates MateEngine, Python Vision daemon, SillyTavern, and Supervisor
REM ==============================================================================

echo ========================================================
echo   Stopping AlphaVision Ecosystem...
echo ========================================================

REM 1. Terminate Supervisor watchdog process
echo [1/5] Terminating Supervisor watchdog...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name = 'powershell.exe'\" | Where-Object { $_.CommandLine -like '*supervisor.ps1*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

REM 2. Terminate MateEngine foreground app
echo [2/5] Terminating MateEngine...
taskkill /F /IM MateEngineX.exe /T >nul 2>&1

REM 3. Terminate AlphaVision pythonw/python daemons
echo [3/5] Terminating AlphaVision daemons...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name = 'pythonw.exe' or Name = 'python.exe'\" | Where-Object { $_.CommandLine -like '*AnimeVision*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

REM 4. Terminate SillyTavern node instance
echo [4/5] Terminating SillyTavern background server...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name = 'node.exe'\" | Where-Object { $_.CommandLine -like '*server.js*' -or $_.CommandLine -like '*SillyTavern*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

REM 5. Ensure ports 5001, 8787, 8000 listeners are cleared
echo [5/5] Freeing bound network ports (5001, 8787, 8000)...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-NetTCPConnection -LocalPort 5001, 8787, 8000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"

echo ========================================================
echo   All AlphaVision processes terminated cleanly.
echo ========================================================
timeout /t 2 /nobreak >nul
exit /b 0
