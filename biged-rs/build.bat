@echo off
:: BigEd CC — Build Script
:: Usage:
::   build.bat              — release build (optimized, LTO, stripped)
::   build.bat debug        — debug build (fast compile, symbols)
::   build.bat check        — type-check only (no binary)
::   build.bat test         — run all tests
::   build.bat clean        — clean target directory

cd /d "%~dp0"

:: Find Python for PyO3
for /f "tokens=*" %%P in ('python -c "import sys; print(sys.prefix)" 2^>nul') do (
    set "PYTHON_HOME=%%P"
    set "PATH=%%P;%PATH%"
)

set MODE=%~1
if "%MODE%"=="" set MODE=release

if "%MODE%"=="debug" (
    echo [BUILD] Debug build...
    cargo build
    if errorlevel 1 goto :fail
    echo.
    echo [OK] Debug binary: target\debug\biged.exe
    for %%F in (target\debug\biged.exe) do echo      Size: %%~zF bytes
    goto :done
)

if "%MODE%"=="check" (
    echo [BUILD] Type-check only...
    cargo check
    if errorlevel 1 goto :fail
    echo [OK] All crates check passed
    goto :done
)

if "%MODE%"=="test" (
    echo [BUILD] Running tests...
    cargo test --workspace --exclude biged-wasm -- --nocapture
    if errorlevel 1 goto :fail
    echo [OK] All tests passed
    goto :done
)

if "%MODE%"=="clean" (
    echo [BUILD] Cleaning...
    cargo clean
    echo [OK] Clean
    goto :done
)

:: Default: release build
echo [BUILD] Release build (LTO + strip)...
echo         This may take 1-2 minutes.
echo.
cargo build --release
if errorlevel 1 goto :fail
echo.
echo ========================================
echo   [OK] Release build complete
echo ========================================
for %%F in (target\release\biged.exe) do echo   Size: %%~zF bytes
echo   Binary: target\release\biged.exe
echo   Launch: BigEdCC.bat
echo ========================================
goto :done

:fail
echo.
echo [FAIL] Build failed.
pause
exit /b 1

:done
echo.
pause
