@echo off
:: BigEd CC — Production Service Launcher
:: Double-click this file to start the Rust service tier.

:: Set working directory to the repo root (parent of biged-rs/)
cd /d "%~dp0.."

:: Find Python DLL directory and add to PATH
:: PyO3 needs python314.dll (or similar) on PATH at process start.
:: We ask Python itself where its prefix is — that's where the DLL lives.
for /f "tokens=*" %%P in ('python -c "import sys; print(sys.prefix)" 2^>nul') do (
    set "PYTHON_HOME=%%P"
)
if defined PYTHON_HOME (
    set "PATH=%PYTHON_HOME%;%PATH%"
) else (
    echo WARNING: Could not detect Python prefix. python314.dll may not be found.
    echo Install Python 3.11+ and ensure 'python' is on PATH.
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

:: Start the service (default: HTTP server on :5555)
echo ========================================
echo   BigEd CC - Rust Service Tier
echo ========================================
echo   Binary:  %BIGED%
echo   Server:  http://localhost:5555
echo   Fleet:   %CD%\fleet
echo   Python:  %PYTHON_HOME%
echo ========================================
echo.
"%BIGED%" %*
pause
