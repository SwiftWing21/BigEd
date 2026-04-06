@echo off
:: BigEd CC — Production Service Launcher
:: Double-click: starts server + opens GUI automatically
:: BigEdCC serve    — server only (no GUI)
:: BigEdCC gui      — GUI only (connect to running server)
:: BigEdCC supervisor — full supervisor mode

:: Set working directory to the repo root (parent of biged-rs/)
cd /d "%~dp0.."

:: Find Python DLL directory and add to PATH
for /f "tokens=*" %%P in ('python -c "import sys; print(sys.prefix)" 2^>nul') do (
    set "PYTHON_HOME=%%P"
)
if defined PYTHON_HOME (
    set "PATH=%PYTHON_HOME%;%PATH%"
) else (
    echo WARNING: Could not detect Python prefix. python314.dll may not be found.
)

:: Find the binary
if exist "%~dp0target\release\biged.exe" (
    set "BIGED=%~dp0target\release\biged.exe"
) else if exist "%~dp0target\debug\biged.exe" (
    set "BIGED=%~dp0target\debug\biged.exe"
) else (
    echo ERROR: biged.exe not found. Run: cargo build --release
    pause
    exit /b 1
)

echo ========================================
echo   BigEd CC - Rust Service Tier
echo ========================================
echo   Binary:  %BIGED%
echo   Server:  http://localhost:5555
echo   Fleet:   %CD%\fleet
echo   Python:  %PYTHON_HOME%
echo ========================================
echo.

:: If args passed, run that command directly
if not "%~1"=="" (
    "%BIGED%" %*
    pause
    exit /b
)

:: Default (no args): start server in background, then launch GUI
echo Starting server...
start "" /B "%BIGED%" serve
timeout /t 2 /nobreak >nul
echo Starting operator GUI...
"%BIGED%" gui
pause
