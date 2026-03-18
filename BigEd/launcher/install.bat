@echo off
cd /d "%~dp0"

echo.
echo  BigEd CC -- Installer
echo  ────────────────────────────────
echo.

REM Check Python
python --version >/dev/null 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found.
    echo.
    echo  Install Python 3.11 or later:
    echo    winget install Python.Python.3.11
    echo    OR: https://python.org/downloads
    echo.
    pause
    exit /b 1
)

REM Bootstrap minimal GUI deps
echo Installing GUI dependencies...
pip install customtkinter pillow --quiet --disable-pip-version-check
if errorlevel 1 (
    echo Failed to install GUI dependencies.
    pause
    exit /b 1
)

REM Launch installer GUI (pass any args through, e.g. --reinstall)
python installer.py %*
