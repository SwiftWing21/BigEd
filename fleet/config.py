import os
import sys
import tomllib
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "fleet.toml"

# Skills allowed in air-gap mode (deny-by-default whitelist)
AIR_GAP_SKILLS = {
    "code_review", "code_discuss", "code_index", "code_quality",
    "summarize", "discuss", "flashcard", "analyze_results",
    "rag_index", "rag_query", "benchmark", "ingest",
    "security_review", "security_audit",
}


def load_config():
    with open(CONFIG_PATH, "rb") as f:
        cfg = tomllib.load(f)
    # air_gap_mode implies offline_mode
    if cfg.get("fleet", {}).get("air_gap_mode", False):
        cfg.setdefault("fleet", {})["offline_mode"] = True
    return cfg


# GitHub owner/repo — configurable via fleet.toml [github]
_cfg = load_config()
GITHUB_OWNER = _cfg.get("github", {}).get("owner", "SwiftWing21")
GITHUB_REPO = _cfg.get("github", {}).get("repo", "BigEds_Agents")


def is_offline(config: dict) -> bool:
    return config.get("fleet", {}).get("offline_mode", False)


def is_air_gap(config: dict) -> bool:
    return config.get("fleet", {}).get("air_gap_mode", False)


def is_native_windows() -> bool:
    """Check if running natively on Windows (not inside WSL).

    Returns True only when:
    - The Python interpreter is running on Windows (sys.platform == "win32")
    - AND we are NOT inside a WSL environment (WSL_DISTRO_NAME not set)
    """
    return sys.platform == "win32" and not os.environ.get("WSL_DISTRO_NAME")


def detect_cli() -> dict:
    """Auto-detect the best local CLI for network + hardware access.

    Returns dict with:
        platform: win32 | linux | darwin | wsl
        shell: powershell | cmd | bash | zsh
        network_tool: nmap | nmap.exe | None
        hw_tool: pynvml | nvidia-smi | system_profiler | None
        bridge: NativeWindowsBridge | WslBridge | DirectBridge
        recommended: str — human-readable recommendation
    """
    import shutil

    result = {
        "platform": sys.platform,
        "shell": None,
        "network_tool": None,
        "hw_tool": None,
        "bridge": None,
        "recommended": "",
    }

    # Detect WSL
    if os.environ.get("WSL_DISTRO_NAME"):
        result["platform"] = "wsl"
        result["shell"] = "bash"
        result["bridge"] = "DirectBridge"
    elif sys.platform == "win32":
        # Prefer PowerShell for richer network/hw access on Windows
        if shutil.which("pwsh") or shutil.which("powershell"):
            result["shell"] = "powershell"
        else:
            result["shell"] = "cmd"
        # Windows defaults to native — WSL is opt-in via BIGED_USE_WSL=1
        if os.environ.get("BIGED_USE_WSL", "").lower() in ("1", "true"):
            result["bridge"] = "WslBridge"
        else:
            result["bridge"] = "NativeWindowsBridge"
    elif sys.platform == "darwin":
        result["shell"] = os.environ.get("SHELL", "/bin/zsh").rsplit("/", 1)[-1]
        result["bridge"] = "DirectBridge"
    else:  # linux
        result["shell"] = os.environ.get("SHELL", "/bin/bash").rsplit("/", 1)[-1]
        result["bridge"] = "DirectBridge"

    # Network tool detection
    for tool in ["nmap", "nmap.exe"]:
        if shutil.which(tool):
            result["network_tool"] = tool
            break

    # Hardware tool detection
    try:
        import pynvml
        result["hw_tool"] = "pynvml"
    except ImportError:
        for tool in ["nvidia-smi", "system_profiler", "lspci"]:
            if shutil.which(tool):
                result["hw_tool"] = tool
                break

    # Build recommendation
    if result["platform"] == "win32" and result["shell"] == "powershell":
        result["recommended"] = "PowerShell — best native network/hw access on Windows"
    elif result["platform"] == "wsl":
        result["recommended"] = "WSL bash — full Linux toolchain (nmap, pgrep, ss)"
    elif result["platform"] == "darwin":
        result["recommended"] = f"{result['shell']} — native macOS (system_profiler for hw)"
    else:
        result["recommended"] = f"{result['shell']} — native Linux (full toolchain)"

    return result
