@echo off
setlocal
REM ==============================================================================
REM AlphaVision Unified Launcher
REM Hands off immediately to silent runner (zero persistent console windows)
REM ==============================================================================

pushd "%~dp0"
start "" wscript.exe "%~dp0runner.vbs"
popd
exit /b 0
