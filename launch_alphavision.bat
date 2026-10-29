@echo off
cd /d "%~dp0"
if exist alpha_env\Scripts\activate.bat (
    call alpha_env\Scripts\activate.bat
    start "" alpha_env\Scripts\pythonw.exe AnimeVision.py
) else if exist Scripts\activate.bat (
    call Scripts\activate.bat
    start "" Scripts\pythonw.exe AnimeVision.py
) else (
    call alpha_env\Scripts\activate.bat
    start "" alpha_env\Scripts\pythonw.exe AnimeVision.py
)
exit
