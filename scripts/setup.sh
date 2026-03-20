#!/usr/bin/env bash
# BigEd CC — First-Time Setup (Linux / macOS)
# Usage: bash scripts/setup.sh
# Options: --skip-ollama  (API-only mode, no local models)
set -euo pipefail

# ── Flag parsing ──────────────────────────────────────────────────────────────
SKIP_OLLAMA=false
for arg in "$@"; do
    case "$arg" in
        --skip-ollama) SKIP_OLLAMA=true ;;
        -h|--help)
            echo "Usage: bash scripts/setup.sh [--skip-ollama]"
            echo ""
            echo "Options:"
            echo "  --skip-ollama  Skip Ollama and local model install (API-only mode)"
            echo "  -h, --help     Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg"
            echo "Usage: bash scripts/setup.sh [--skip-ollama]"
            exit 1
            ;;
    esac
done

# ── Repo root ─────────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Color helpers ─────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    BLUE='\033[1;34m'
    GREEN='\033[1;32m'
    YELLOW='\033[1;33m'
    RED='\033[1;31m'
    RESET='\033[0m'
else
    BLUE='' GREEN='' YELLOW='' RED='' RESET=''
fi

info()  { echo -e "${BLUE}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
fail()  { echo -e "${RED}[FAIL]${RESET}  $*"; }

# ── Summary tracking ─────────────────────────────────────────────────────────
SUMMARY_OS=""
SUMMARY_PYTHON=""
SUMMARY_TKINTER=""
SUMMARY_OLLAMA=""
SUMMARY_MODEL=""
SUMMARY_DEPS=""

# ── 1. Detect OS and package manager ─────────────────────────────────────────
detect_os() {
    OS_TYPE=""
    DISTRO=""
    DISTRO_PRETTY=""
    PKG_MGR=""

    case "$(uname -s)" in
        Linux)
            OS_TYPE="linux"
            if [[ -f /etc/os-release ]]; then
                # shellcheck source=/dev/null
                . /etc/os-release

                DISTRO_PRETTY="${PRETTY_NAME:-Linux}"

                # Normalize distro ID
                local id="${ID:-unknown}"
                local id_like="${ID_LIKE:-}"

                case "$id" in
                    linuxmint|mint)    DISTRO="mint"    ;;
                    ubuntu)            DISTRO="ubuntu"  ;;
                    debian)            DISTRO="debian"  ;;
                    arch)              DISTRO="arch"    ;;
                    steamos)           DISTRO="steamos" ;;
                    fedora)            DISTRO="fedora"  ;;
                    *)
                        # Fall back to ID_LIKE
                        if [[ "$id_like" == *"steamos"* ]]; then
                            DISTRO="steamos"
                        elif [[ "$id_like" == *"arch"* ]]; then
                            DISTRO="arch"
                        elif [[ "$id_like" == *"ubuntu"* ]] || [[ "$id_like" == *"debian"* ]]; then
                            DISTRO="debian"
                        elif [[ "$id_like" == *"fedora"* ]]; then
                            DISTRO="fedora"
                        else
                            DISTRO="$id"
                        fi
                        ;;
                esac

                # Also check NAME/PRETTY_NAME for SteamOS
                if [[ "$DISTRO" != "steamos" ]] && [[ "${NAME:-}" == *"SteamOS"* ]]; then
                    DISTRO="steamos"
                fi
            else
                DISTRO="unknown"
                DISTRO_PRETTY="Linux (unknown distro)"
            fi

            # Set package manager
            case "$DISTRO" in
                mint|ubuntu|debian) PKG_MGR="apt"    ;;
                arch|steamos)       PKG_MGR="pacman" ;;
                fedora)             PKG_MGR="dnf"    ;;
                *)
                    # Auto-detect from available commands
                    if command -v apt &>/dev/null; then
                        PKG_MGR="apt"
                    elif command -v pacman &>/dev/null; then
                        PKG_MGR="pacman"
                    elif command -v dnf &>/dev/null; then
                        PKG_MGR="dnf"
                    else
                        PKG_MGR="unknown"
                    fi
                    ;;
            esac
            ;;
        Darwin)
            OS_TYPE="macos"
            DISTRO="macos"
            local macos_ver
            macos_ver="$(sw_vers -productVersion 2>/dev/null || echo 'unknown')"
            DISTRO_PRETTY="macOS $macos_ver"
            PKG_MGR="brew"
            ;;
        *)
            fail "Unsupported OS: $(uname -s)"
            fail "This script supports Linux (Mint, Ubuntu, Debian, Arch, SteamOS, Fedora) and macOS."
            exit 1
            ;;
    esac

    SUMMARY_OS="$DISTRO_PRETTY"
    info "Detected: $DISTRO_PRETTY (pkg: $PKG_MGR)"
}

# ── Helper: check if sudo is available ────────────────────────────────────────
check_sudo() {
    if command -v sudo &>/dev/null; then
        return 0
    else
        warn "sudo is not available. You may need to run package installs manually as root."
        return 1
    fi
}

# ── Helper: run with sudo (or warn if unavailable) ───────────────────────────
run_sudo() {
    if command -v sudo &>/dev/null; then
        info "Running: sudo $*"
        sudo "$@"
    else
        fail "Cannot run: sudo $*"
        fail "Please run this command manually as root, then re-run this script."
        exit 1
    fi
}

# ── 2. Check Python 3.11+ ────────────────────────────────────────────────────
check_python() {
    info "Checking Python..."

    local python_cmd=""

    # Find a working python3
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver="$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+\.\d+' | head -1)"
            if [[ -n "$ver" ]]; then
                local major minor
                major="$(echo "$ver" | cut -d. -f1)"
                minor="$(echo "$ver" | cut -d. -f2)"
                if [[ "$major" -eq 3 ]] && [[ "$minor" -ge 11 ]]; then
                    python_cmd="$cmd"
                    SUMMARY_PYTHON="$ver"
                    ok "Python $ver ($cmd)"
                    break
                fi
            fi
        fi
    done

    if [[ -z "$python_cmd" ]]; then
        warn "Python 3.11+ not found. Attempting to install..."
        install_python
        # Re-check
        if command -v python3 &>/dev/null; then
            local ver
            ver="$(python3 --version 2>&1 | grep -oP '\d+\.\d+\.\d+' | head -1)"
            if [[ -n "$ver" ]]; then
                local major minor
                major="$(echo "$ver" | cut -d. -f1)"
                minor="$(echo "$ver" | cut -d. -f2)"
                if [[ "$major" -eq 3 ]] && [[ "$minor" -ge 11 ]]; then
                    SUMMARY_PYTHON="$ver"
                    ok "Python $ver installed"
                    return 0
                fi
            fi
        fi
        fail "Could not install Python 3.11+. Please install it manually and re-run this script."
        exit 1
    fi
}

install_python() {
    case "$PKG_MGR" in
        apt)
            run_sudo apt update
            run_sudo apt install -y python3 python3-pip python3-venv
            ;;
        pacman)
            run_sudo pacman -S --noconfirm python python-pip
            ;;
        dnf)
            run_sudo dnf install -y python3 python3-pip
            ;;
        brew)
            info "Installing Python 3.12 via Homebrew..."
            brew install python@3.12
            ;;
        *)
            fail "Unknown package manager ($PKG_MGR). Please install Python 3.11+ manually."
            exit 1
            ;;
    esac
}

# ── 3. Check tkinter ─────────────────────────────────────────────────────────
check_tkinter() {
    info "Checking tkinter..."

    if python3 -c "import tkinter" &>/dev/null; then
        SUMMARY_TKINTER="available"
        ok "tkinter is available"
        return 0
    fi

    warn "tkinter not found. Attempting to install..."
    install_tkinter

    if python3 -c "import tkinter" &>/dev/null; then
        SUMMARY_TKINTER="available"
        ok "tkinter installed"
    else
        SUMMARY_TKINTER="MISSING"
        fail "Could not install tkinter. Please install it manually:"
        case "$PKG_MGR" in
            apt)    fail "  sudo apt install python3-tk" ;;
            pacman) fail "  sudo pacman -S tk" ;;
            dnf)    fail "  sudo dnf install python3-tkinter" ;;
            brew)   fail "  brew install python-tk@3.12" ;;
        esac
        exit 1
    fi
}

install_tkinter() {
    case "$PKG_MGR" in
        apt)
            run_sudo apt install -y python3-tk
            ;;
        pacman)
            run_sudo pacman -S --noconfirm tk
            ;;
        dnf)
            run_sudo dnf install -y python3-tkinter
            ;;
        brew)
            brew install python-tk@3.12
            ;;
        *)
            fail "Unknown package manager ($PKG_MGR). Please install tkinter manually."
            exit 1
            ;;
    esac
}

# ── 4. Install Python dependencies ───────────────────────────────────────────
install_deps() {
    info "Installing Python dependencies..."

    local req_file="$REPO_ROOT/BigEd/launcher/requirements.txt"
    if [[ ! -f "$req_file" ]]; then
        fail "Requirements file not found: $req_file"
        exit 1
    fi

    if python3 -m pip install -r "$req_file" 2>/dev/null; then
        SUMMARY_DEPS="installed"
        ok "Python dependencies installed"
    elif python3 -m pip install --user -r "$req_file" 2>/dev/null; then
        SUMMARY_DEPS="installed (--user)"
        ok "Python dependencies installed (--user fallback)"
    else
        SUMMARY_DEPS="FAILED"
        fail "Failed to install Python dependencies."
        fail "Try manually: python3 -m pip install -r $req_file"
        exit 1
    fi
}

# ── 5. Check/install Ollama ───────────────────────────────────────────────────
check_ollama() {
    if [[ "$SKIP_OLLAMA" == true ]]; then
        SUMMARY_OLLAMA="skipped (API-only mode)"
        SUMMARY_MODEL="skipped"
        info "Skipping Ollama install (--skip-ollama)"
        return 0
    fi

    info "Checking Ollama..."

    if ! command -v ollama &>/dev/null; then
        warn "Ollama not found. Installing Ollama (local AI model runtime)..."
        install_ollama
    fi

    if ! command -v ollama &>/dev/null; then
        SUMMARY_OLLAMA="FAILED"
        fail "Ollama installation failed. Install manually from https://ollama.com"
        exit 1
    fi

    # Get version
    local ollama_ver
    ollama_ver="$(ollama --version 2>&1 | grep -oP '[\d.]+' | head -1 || echo 'unknown')"

    # Make sure Ollama is running
    if ! ollama list &>/dev/null 2>&1; then
        info "Starting Ollama server..."
        ollama serve &>/dev/null &
        sleep 3

        if ! ollama list &>/dev/null 2>&1; then
            warn "Ollama server did not start. You may need to start it manually: ollama serve"
        fi
    fi

    SUMMARY_OLLAMA="$ollama_ver"
    ok "Ollama $ollama_ver"
}

install_ollama() {
    case "$OS_TYPE" in
        linux)
            info "Installing Ollama via official install script..."
            curl -fsSL https://ollama.com/install.sh | sh
            ;;
        macos)
            if command -v brew &>/dev/null; then
                info "Installing Ollama via Homebrew..."
                brew install ollama
            else
                fail "Please install Ollama manually from https://ollama.com/download"
                fail "Or install Homebrew first: https://brew.sh"
                exit 1
            fi
            ;;
    esac
}

# ── 6. Pull default model ────────────────────────────────────────────────────
pull_model() {
    if [[ "$SKIP_OLLAMA" == true ]]; then
        return 0
    fi

    info "Checking for default model (qwen3:8b)..."

    if ollama list 2>/dev/null | grep -q "qwen3:8b"; then
        SUMMARY_MODEL="qwen3:8b"
        ok "qwen3:8b already available"
        return 0
    fi

    info "Downloading default AI model (qwen3:8b, ~5GB)..."
    info "This may take several minutes."

    if ollama pull qwen3:8b; then
        SUMMARY_MODEL="qwen3:8b"
        ok "qwen3:8b downloaded"
    else
        SUMMARY_MODEL="FAILED"
        warn "Failed to pull qwen3:8b. You can retry later with: ollama pull qwen3:8b"
    fi
}

# ── 7. SteamOS-specific checks ───────────────────────────────────────────────
steamos_checks() {
    if [[ "$DISTRO" != "steamos" ]]; then
        return 0
    fi

    info "Running SteamOS-specific checks..."

    # Check if likely in Gaming Mode
    if command -v steamos-session-select &>/dev/null; then
        local current_session
        current_session="$(steamos-session-select 2>/dev/null || true)"
        if [[ "$current_session" == *"gamescope"* ]] || [[ "$current_session" == *"steam"* ]]; then
            warn "It looks like you may be in Gaming Mode."
            warn "BigEd CC is designed for Desktop Mode."
            warn "Switch via: steamos-session-select plasma"
        fi
    fi

    # Check for environment indicators of Gaming Mode
    if [[ -n "${GAMESCOPE_WAYLAND_DISPLAY:-}" ]] || [[ -n "${SteamGameId:-}" ]]; then
        warn "Gaming Mode environment detected. Please switch to Desktop Mode."
        warn "Power menu -> Switch to Desktop"
    fi

    warn "Note: On SteamOS, pacman may need --overwrite for some packages"
    warn "due to the immutable filesystem. If installs fail, try:"
    warn "  sudo steamos-readonly disable"
    warn "  <install commands>"
    warn "  sudo steamos-readonly enable"
}

# ── 8. Final summary ─────────────────────────────────────────────────────────
print_summary() {
    echo ""
    echo "================================"
    echo " BigEd CC — Setup Complete"
    echo "================================"
    echo " OS:      $SUMMARY_OS"
    echo " Python:  ${SUMMARY_PYTHON:-unknown}  [${SUMMARY_PYTHON:+OK}${SUMMARY_PYTHON:-FAIL}]"
    echo " tkinter: ${SUMMARY_TKINTER:-unknown} [${SUMMARY_TKINTER:+OK}${SUMMARY_TKINTER:-FAIL}]"
    echo " Ollama:  ${SUMMARY_OLLAMA:-unknown}  [${SUMMARY_OLLAMA:+OK}${SUMMARY_OLLAMA:-FAIL}]"
    echo " Model:   ${SUMMARY_MODEL:-none}      [${SUMMARY_MODEL:+OK}${SUMMARY_MODEL:-FAIL}]"
    echo " Deps:    ${SUMMARY_DEPS:-unknown}    [${SUMMARY_DEPS:+OK}${SUMMARY_DEPS:-FAIL}]"
    echo ""
    echo " To launch BigEd CC:"
    echo "   python3 BigEd/launcher/launcher.py"
    echo ""
    echo " To build AppImage:"
    echo "   python3 BigEd/launcher/package_linux.py"
    echo "================================"
    echo ""
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    echo ""
    echo "================================"
    echo " BigEd CC — First-Time Setup"
    echo "================================"
    echo ""

    detect_os
    steamos_checks
    check_python
    check_tkinter
    install_deps
    check_ollama
    pull_model
    print_summary
}

main
