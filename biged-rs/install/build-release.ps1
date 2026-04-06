# BigEd CC — Production Release Build Script
# Run: powershell -ExecutionPolicy Bypass -File install/build-release.ps1
#
# Produces:
#   target/release/biged.exe    — optimized binary (LTO + strip)
#   install/dist/               — deployment bundle ready to ship
#
# Requirements: Rust toolchain, Python 3.11+ (for PyO3 skill execution)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$DistDir  = Join-Path $PSScriptRoot "dist"
$FleetDir = Join-Path (Resolve-Path "$RepoRoot\..").Path "fleet"

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  BigEd CC — Production Release Build"   -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# 1. Build release binary
Write-Host "[1/5] Building release binary..." -ForegroundColor Yellow
Push-Location $RepoRoot
cargo build --release
if ($LASTEXITCODE -ne 0) { Write-Error "Cargo build failed"; exit 1 }
Pop-Location
$ExePath = Join-Path $RepoRoot "target\release\biged.exe"
$ExeSize = [math]::Round((Get-Item $ExePath).Length / 1MB, 1)
Write-Host "  Binary: $ExePath ($ExeSize MB)" -ForegroundColor Green

# 2. Create distribution directory
Write-Host "[2/5] Creating distribution bundle..." -ForegroundColor Yellow
if (Test-Path $DistDir) { Remove-Item $DistDir -Recurse -Force }
New-Item -ItemType Directory -Path $DistDir | Out-Null
New-Item -ItemType Directory -Path "$DistDir\fleet\skills" | Out-Null
New-Item -ItemType Directory -Path "$DistDir\fleet\logs" | Out-Null

# 3. Copy artifacts
Write-Host "[3/5] Copying artifacts..." -ForegroundColor Yellow
Copy-Item $ExePath "$DistDir\biged.exe"

# Copy fleet.toml as default config
if (Test-Path "$FleetDir\fleet.toml") {
    Copy-Item "$FleetDir\fleet.toml" "$DistDir\fleet\fleet.toml"
    Write-Host "  Copied fleet.toml" -ForegroundColor DarkGray
}

# Copy skills directory (required for PyO3 execution)
if (Test-Path "$FleetDir\skills") {
    Copy-Item "$FleetDir\skills\*" "$DistDir\fleet\skills\" -Recurse
    $SkillCount = (Get-ChildItem "$DistDir\fleet\skills\*.py" | Measure-Object).Count
    Write-Host "  Copied $SkillCount Python skills" -ForegroundColor DarkGray
}

# Copy key Python modules needed by skills (db.py, config.py, providers.py, comms.py)
foreach ($PyFile in @("db.py", "db_tasks.py", "config.py", "providers.py", "comms.py", "rag.py")) {
    $Src = Join-Path $FleetDir $PyFile
    if (Test-Path $Src) {
        Copy-Item $Src "$DistDir\fleet\"
        Write-Host "  Copied $PyFile" -ForegroundColor DarkGray
    }
}

# 4. Compute SHA-256 of binary
Write-Host "[4/5] Computing binary hash..." -ForegroundColor Yellow
$Hash = (Get-FileHash "$DistDir\biged.exe" -Algorithm SHA256).Hash
$Version = & "$DistDir\biged.exe" --version 2>&1 | Select-String -Pattern "\d+\.\d+\.\d+" | ForEach-Object { $_.Matches[0].Value }
if (-not $Version) { $Version = "0.9.0" }

$Manifest = @{
    version  = $Version
    track    = "rust"
    binary   = "biged.exe"
    sha256   = $Hash
    built_at = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    platform = "windows-x86_64"
} | ConvertTo-Json -Depth 2
$Manifest | Out-File "$DistDir\manifest.json" -Encoding UTF8
Write-Host "  SHA-256: $($Hash.Substring(0,16))..." -ForegroundColor DarkGray

# 5. Summary
Write-Host "`n[5/5] Distribution bundle ready:" -ForegroundColor Yellow
$BundleSize = [math]::Round((Get-ChildItem $DistDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
Write-Host "  Location: $DistDir" -ForegroundColor Green
Write-Host "  Size:     $BundleSize MB" -ForegroundColor Green
Write-Host "  Binary:   $ExeSize MB" -ForegroundColor Green
Write-Host "  Hash:     $($Hash.Substring(0,16))..." -ForegroundColor Green
Write-Host ""
Write-Host "To deploy: copy dist/ to target machine and run biged.exe" -ForegroundColor Cyan
Write-Host ""
