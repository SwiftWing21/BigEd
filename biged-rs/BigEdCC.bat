@echo off
:: BigEd CC — Production Service Launcher
:: Double-click this file to start the Rust service tier.
:: Starts the HTTP server on port 5555 with operator GUI available.

:: Set working directory to the repo root (parent of biged-rs/)
:: so find_fleet_dir() discovers fleet/ correctly
cd /d "%~dp0.."

:: Find Python and add to PATH (required for PyO3 skill bridge)
:: Searches: py launcher, PATH, common install locations
where python >nul 2>&1 && goto :python_found
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
    "%LOCALAPPDATA%\Python\pythoncore-3.13-64\python.exe"
    "%PROGRAMFILES%\Python314\python.exe"
    "%PROGRAMFILES%\Python313\python.exe"
) do (
    if exist %%P (
        for %%D in (%%~dpP) do set "PATH=%%~fD;%PATH%"
        goto :python_found
    )
)
echo WARNING: Python not found on PATH. PyO3 skill bridge may fail.
echo Install Python 3.11+ and ensure it is on PATH.

:python_found

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

:: Start the service (default: HTTP server on :5555)
echo ========================================
echo   BigEd CC — Rust Service Tier
echo ========================================
echo   Binary:  %BIGED%
echo   Server:  http://localhost:5555
echo   Fleet:   %CD%\fleet
echo ========================================
echo.
"%BIGED%" %*
pause
