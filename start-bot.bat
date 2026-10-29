@echo off
rem Zapusk bota na Windows dvoynym shchelchkom. Vsya logika - v launcher.py.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -X utf8 launcher.py
    goto :eof
)
python -X utf8 launcher.py
if %errorlevel%==9009 (
    echo.
    echo Python not found. Install it from https://www.python.org/downloads/
    echo During install, check the box "Add python.exe to PATH". Then run this file again.
    pause
)
