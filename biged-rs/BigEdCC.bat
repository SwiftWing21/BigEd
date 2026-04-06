@echo off
:: BigEd CC — Production Service Launcher
:: Double-click this file to start the Rust service tier.
:: Starts the HTTP server on port 5555 with operator GUI available.

:: Add Python to PATH (required for PyO3 skill bridge)
set PATH=C:\Users\max\AppData\Local\Python\pythoncore-3.14-64;%PATH%

:: Find the binary
if exist "%~dp0target\release\biged.exe" (
    set BIGED="%~dp0target\release\biged.exe"
) else if exist "%~dp0target\debug\biged.exe" (
    set BIGED="%~dp0target\debug\biged.exe"
) else (
    echo ERROR: biged.exe not found. Run: cargo build --release
    pause
    exit /b 1
)

:: Start the service (default: HTTP server on :5555)
echo Starting BigEd CC service...
echo Server: http://localhost:5555
echo.
%BIGED% %*
pause
