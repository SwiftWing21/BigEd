@echo off
cd /d "%~dp0"

echo.
echo  Fleet Manager App -- Uninstaller
echo  ──────────────────────────────────
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found.
    pause
    exit /b 1
)

REM Bootstrap minimal GUI deps
echo Installing GUI dependencies...
pip install customtkinter pillow --quiet --disable-pip-version-check

REM Launch setup GUI (auto-detects install state)
python installer.py %*
