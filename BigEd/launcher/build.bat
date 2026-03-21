@echo off
REM BigEd CC — build launcher + updater
REM Run from Windows cmd in this directory (not WSL)
REM Requires Python: winget install Python.Python.3.11

cd /d "%~dp0"

echo == Installing dependencies ==
pip install -r requirements.txt
if errorlevel 1 ( echo FAILED: pip install && pause && exit /b 1 )

echo == Checking icons ==
if exist brick.ico if exist icon_1024.png (
    echo   brick.ico + icon_1024.png present — locked assets
) else (
    echo   ERROR: Missing brick.ico or icon_1024.png — cannot build
    pause && exit /b 1
)

echo.
echo == Closing running BigEdCC.exe (if open) ==
taskkill /f /im BigEdCC.exe >nul 2>&1

echo == Building BigEdCC.exe ==
python -m PyInstaller --onefile --windowed --name "BigEdCC" --icon "brick.ico" --add-data "brick.ico;." --add-data "icon_1024.png;." --collect-all customtkinter --hidden-import psutil --hidden-import pynvml launcher.py
if errorlevel 1 ( echo FAILED: BigEdCC build && pause && exit /b 1 )

echo.
echo == Closing running Updater.exe (if open) ==
taskkill /f /im Updater.exe >nul 2>&1

echo == Building Updater.exe ==
python -m PyInstaller --onefile --windowed --name "Updater" --icon "brick.ico" --add-data "brick.ico;." --collect-all customtkinter updater.py
if errorlevel 1 ( echo FAILED: Updater build && pause && exit /b 1 )

echo.
echo == Closing running Setup.exe (if open) ==
taskkill /f /im Setup.exe >nul 2>&1

echo.
echo == Building Setup.exe ==
python -m PyInstaller --onefile --windowed --name "Setup" --icon "brick.ico" --add-data "brick.ico;." --add-data "icon_1024.png;." --collect-all customtkinter installer.py
if errorlevel 1 ( echo FAILED: Setup build && pause && exit /b 1 )

echo.
echo == Done ==
echo   BigEdCC.exe  -^>  dist\BigEdCC.exe
echo   Updater.exe       -^>  dist\Updater.exe
echo   Setup.exe         -^>  dist\Setup.exe
echo.
echo Run Updater.exe any time to upgrade packages and rebuild BigEdCC.exe
pause
