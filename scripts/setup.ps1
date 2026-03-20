# BigEd CC — First-Time Setup (Windows)
# Run: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#
# This script checks prerequisites and configures BigEd CC for first use.
# It is non-destructive — nothing is installed without your knowledge.

param(
    [switch]$SkipOllama
)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Resolve repo root relative to this script's location
# ---------------------------------------------------------------------------
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$RequirementsFile = Join-Path $RepoRoot "BigEd\launcher\requirements.txt"
$FleetRequirementsFile = Join-Path $RepoRoot "fleet\requirements.txt"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Step   { param([string]$msg) Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK     { param([string]$msg) Write-Host "   [OK] $msg" -ForegroundColor Green }
function Write-Warn   { param([string]$msg) Write-Host "   [WARN] $msg" -ForegroundColor Yellow }
function Write-Err    { param([string]$msg) Write-Host "   [ERROR] $msg" -ForegroundColor Red }
function Write-Info   { param([string]$msg) Write-Host "   $msg" }

function Get-PythonCommand {
    # Returns the first working python command that is 3.11+, or $null.
    foreach ($cmd in @("python", "python3")) {
        try {
            $raw = & $cmd --version 2>&1
            if ($raw -match "Python\s+(\d+)\.(\d+)") {
                $major = [int]$Matches[1]
                $minor = [int]$Matches[2]
                if ($major -ge 3 -and $minor -ge 11) {
                    return @{ Command = $cmd; Version = $raw.ToString().Trim() }
                }
            }
        } catch {
            # command not found — try next
        }
    }
    return $null
}

# ---------------------------------------------------------------------------
# 1. Welcome banner
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host " BigEd CC - First-Time Setup"    -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This script will install prerequisites and configure BigEd CC."
Write-Host "Repo root: $RepoRoot"
if ($SkipOllama) {
    Write-Host "(Ollama steps will be skipped — API-only mode)" -ForegroundColor Yellow
}
Write-Host ""

# ---------------------------------------------------------------------------
# 2. Check Git (needed for source-based updates)
# ---------------------------------------------------------------------------
Write-Step "Checking Git ..."

$gitVersion = $null
try {
    $gitVersion = (& git --version 2>&1).ToString().Trim()
    Write-OK "$gitVersion"
} catch {
    Write-Warn "Git is not installed. It is required for source-based updates."
    Write-Host ""

    $installGit = Read-Host "   Download and install Git now? (Y/n)"
    if ($installGit -eq "n") {
        Write-Warn "Skipping Git. The Updater will fall back to GitHub Release downloads."
        Write-Warn "You can install Git later from https://git-scm.com"
    } else {
        Write-Info "Opening Git download page ..."
        Start-Process "https://git-scm.com/download/win"
        Write-Host ""
        Read-Host "   Press Enter after you have installed Git to continue"

        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("Path", "User")

        try {
            $gitVersion = (& git --version 2>&1).ToString().Trim()
            Write-OK "$gitVersion"
        } catch {
            Write-Warn "Git still not detected. Source-based updates will not work."
            Write-Warn "The Updater will use GitHub Release downloads instead."
        }
    }
}

# ---------------------------------------------------------------------------
# 3. Check Python 3.11+
# ---------------------------------------------------------------------------
Write-Step "Checking Python 3.11+ ..."

$py = Get-PythonCommand

if (-not $py) {
    Write-Err "Python 3.11+ is required but was not found."
    Write-Host ""
    Write-Host "   Please install from: https://python.org/downloads" -ForegroundColor Yellow
    Write-Host "   IMPORTANT: Check 'Add Python to PATH' during installation." -ForegroundColor Yellow
    Write-Host ""

    $openBrowser = Read-Host "   Open the download page in your browser? (Y/n)"
    if ($openBrowser -ne "n") {
        Start-Process "https://python.org/downloads"
    }

    Write-Host ""
    Read-Host "   Press Enter after you have installed Python to continue"

    # Refresh PATH so the new install is visible in this session
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")

    $py = Get-PythonCommand
    if (-not $py) {
        Write-Err "Python 3.11+ still not detected. Please verify your installation and PATH, then re-run this script."
        exit 1
    }
}

$PythonCmd = $py.Command
Write-OK "$($py.Version) found (command: $PythonCmd)"

# ---------------------------------------------------------------------------
# 4. Check pip
# ---------------------------------------------------------------------------
Write-Step "Checking pip ..."

try {
    $pipVer = & $PythonCmd -m pip --version 2>&1
    Write-OK $pipVer.ToString().Trim()
} catch {
    Write-Warn "pip not found — attempting to bootstrap it ..."
    try {
        & $PythonCmd -m ensurepip --upgrade 2>&1 | Out-Null
        $pipVer = & $PythonCmd -m pip --version 2>&1
        Write-OK "pip installed: $($pipVer.ToString().Trim())"
    } catch {
        Write-Err "Could not install pip. Please reinstall Python with pip included."
        exit 1
    }
}

# ---------------------------------------------------------------------------
# 5. Install Python dependencies (launcher + fleet)
# ---------------------------------------------------------------------------
Write-Step "Installing Python dependencies ..."

# Launcher dependencies
if (-not (Test-Path $RequirementsFile)) {
    Write-Err "requirements.txt not found at: $RequirementsFile"
    Write-Err "Make sure you cloned the full repository."
    exit 1
}

Write-Info "Launcher: $RequirementsFile"
try {
    & $PythonCmd -m pip install -r $RequirementsFile 2>&1 | ForEach-Object {
        $line = $_.ToString()
        if ($line -match "^(Requirement already satisfied|Successfully installed|Installing collected)") {
            Write-Info $line
        }
    }
    Write-OK "Launcher dependencies installed."
} catch {
    Write-Err "pip install failed: $_"
    Write-Warn "You can retry manually: $PythonCmd -m pip install -r $RequirementsFile"
}

# Fleet dependencies
if (Test-Path $FleetRequirementsFile) {
    Write-Info "Fleet:    $FleetRequirementsFile"
    try {
        & $PythonCmd -m pip install -r $FleetRequirementsFile 2>&1 | ForEach-Object {
            $line = $_.ToString()
            if ($line -match "^(Requirement already satisfied|Successfully installed|Installing collected)") {
                Write-Info $line
            }
        }
        Write-OK "Fleet dependencies installed."
    } catch {
        Write-Warn "Fleet pip install had issues: $_"
        Write-Warn "Fleet dashboard and some skills may not work until fixed."
    }
} else {
    Write-Warn "fleet/requirements.txt not found — fleet dependencies not installed."
}

# ---------------------------------------------------------------------------
# 6. Check Ollama
# ---------------------------------------------------------------------------
$ollamaVersion = $null

if ($SkipOllama) {
    Write-Step "Skipping Ollama (API-only mode) ..."
    Write-Warn "Local AI inference will not be available. Fleet skills that need Ollama will fall back to API providers."
} else {
    Write-Step "Checking Ollama ..."

    try {
        $ollamaVersion = (& ollama --version 2>&1).ToString().Trim()
        Write-OK "$ollamaVersion"
    } catch {
        Write-Warn "Ollama is not installed. It is required for local AI inference."
        Write-Host ""

        $installOllama = Read-Host "   Download and install Ollama now? (Y/n)"
        if ($installOllama -eq "n") {
            Write-Warn "Skipping Ollama. You can install it later from https://ollama.com/download"
        } else {
            $installerPath = Join-Path $env:TEMP "OllamaSetup.exe"

            Write-Info "Downloading Ollama installer ..."
            try {
                Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" `
                                  -OutFile $installerPath `
                                  -UseBasicParsing
                Write-OK "Downloaded to $installerPath"

                Write-Info "Launching installer — follow the on-screen prompts ..."
                Start-Process $installerPath -Wait

                # Clean up
                if (Test-Path $installerPath) {
                    Remove-Item $installerPath -Force
                    Write-Info "Cleaned up installer file."
                }

                # Refresh PATH
                $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                            [System.Environment]::GetEnvironmentVariable("Path", "User")

                try {
                    $ollamaVersion = (& ollama --version 2>&1).ToString().Trim()
                    Write-OK "$ollamaVersion"
                } catch {
                    Write-Warn "Ollama was installed but is not yet on PATH. You may need to restart your terminal."
                }
            } catch {
                Write-Err "Failed to download Ollama: $_"
                Write-Warn "You can install manually from: https://ollama.com/download"
            }
        }
    }

    # ------------------------------------------------------------------
    # 7. Check / pull default model
    # ------------------------------------------------------------------
    if ($ollamaVersion) {
        Write-Step "Checking default model (qwen3:8b) ..."

        try {
            $modelList = & ollama list 2>&1
            if ($modelList -match "qwen3:8b") {
                Write-OK "qwen3:8b is already available."
            } else {
                Write-Info "Downloading default AI model (qwen3:8b, ~5 GB) ..."
                Write-Info "This may take several minutes depending on your internet speed."
                Write-Host ""
                & ollama pull qwen3:8b
                Write-OK "qwen3:8b downloaded."
            }
        } catch {
            Write-Warn "Could not verify model. You can pull it manually: ollama pull qwen3:8b"
        }
    }
}

# ---------------------------------------------------------------------------
# 8. Verify tkinter
# ---------------------------------------------------------------------------
Write-Step "Checking tkinter ..."

try {
    & $PythonCmd -c "import tkinter" 2>&1 | Out-Null
    Write-OK "tkinter is available."
} catch {
    Write-Warn "tkinter is not available."
    Write-Warn "On Windows, tkinter is normally bundled with the official Python installer."
    Write-Warn "If you installed Python from the Microsoft Store or a minimal build, reinstall"
    Write-Warn "from https://python.org/downloads and ensure 'tcl/tk and IDLE' is checked."
}

# ---------------------------------------------------------------------------
# 9. Final summary
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host " Setup Complete!"                 -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""

# Gather version strings for the summary
$pyDisplay = $py.Version -replace "Python\s+", ""

$ollamaDisplay = if ($SkipOllama) { "skipped (API-only)" }
                 elseif ($ollamaVersion) { ($ollamaVersion -replace "^ollama version\s*", "") + "  [OK]" }
                 else { "not installed  [WARN]" }

$modelDisplay = if ($SkipOllama) { "skipped (API-only)" }
                elseif (-not $ollamaVersion) { "n/a" }
                else {
                    try {
                        $list = & ollama list 2>&1
                        if ($list -match "qwen3:8b") { "qwen3:8b  [OK]" } else { "qwen3:8b  [MISSING]" }
                    } catch { "unknown" }
                }

$tkDisplay = try {
    & $PythonCmd -c "import tkinter" 2>&1 | Out-Null
    "available  [OK]"
} catch {
    "missing  [WARN]"
}

$gitDisplay = if ($gitVersion) { ($gitVersion -replace "^git version\s*", "") + "  [OK]" }
              else { "not installed  [WARN — updates will use Release downloads]" }

Write-Host "   Python:   $pyDisplay  [OK]"       -ForegroundColor Green
Write-Host "   Git:      $gitDisplay"             -ForegroundColor $(if ($gitDisplay -match "OK") { "Green" } else { "Yellow" })
Write-Host "   Ollama:   $ollamaDisplay"          -ForegroundColor $(if ($ollamaDisplay -match "OK|skipped") { "Green" } else { "Yellow" })
Write-Host "   Model:    $modelDisplay"           -ForegroundColor $(if ($modelDisplay -match "OK|skipped") { "Green" } else { "Yellow" })
Write-Host "   Deps:     installed  [OK]"         -ForegroundColor Green
Write-Host "   tkinter:  $tkDisplay"              -ForegroundColor $(if ($tkDisplay -match "OK") { "Green" } else { "Yellow" })
Write-Host ""
Write-Host "   To launch BigEd CC:"               -ForegroundColor White
Write-Host "     $PythonCmd BigEd\launcher\launcher.py" -ForegroundColor White
Write-Host ""
Write-Host "   Or build the .exe:"                -ForegroundColor White
Write-Host "     $PythonCmd BigEd\launcher\build.py"    -ForegroundColor White
Write-Host ""
Write-Host "================================" -ForegroundColor Green
