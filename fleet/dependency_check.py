"""
Dependency checker — validates runtime prerequisites for BigEd CC.

Used by: installer (pre-install gate), launcher walkthrough (first-run),
         smoke_test.py (CI), and CLI `lead_client.py model-check`.

Usage:
    from dependency_check import check_all, check_category
    results = check_all()          # full check
    results = check_category("core")  # just core deps

    # CLI:
    python dependency_check.py           # pretty-print all
    python dependency_check.py --json    # machine-readable
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

FLEET_DIR = Path(__file__).parent


def _which(name: str) -> Optional[str]:
    """Cross-platform which."""
    return shutil.which(name)


def _run(cmd: list[str], timeout: int = 10) -> tuple[bool, str]:
    """Run a command, return (success, output)."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return False, "not found"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def _probe_url(url: str, timeout: int = 3) -> tuple[bool, str]:
    """HTTP probe — returns (reachable, detail)."""
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except Exception as e:
        return False, str(e)


# ── Individual checks ─────────────────────────────────────────────────────────

def check_python() -> dict:
    """Python 3.11+ required."""
    v = sys.version_info
    ok = v >= (3, 11)
    return {
        "name": "python",
        "category": "core",
        "required": True,
        "found": True,
        "version": f"{v.major}.{v.minor}.{v.micro}",
        "ok": ok,
        "path": sys.executable,
        "detail": "" if ok else "Python 3.11+ required",
    }


def check_pip() -> dict:
    """pip for package installation."""
    ok, out = _run([sys.executable, "-m", "pip", "--version"])
    version = out.split()[1] if ok and "pip" in out else ""
    return {
        "name": "pip",
        "category": "core",
        "required": True,
        "found": ok,
        "version": version,
        "ok": ok,
        "detail": "" if ok else "pip not found — install with: python -m ensurepip",
    }


def check_ollama() -> dict:
    """Ollama for local LLM inference."""
    path = _which("ollama")
    version = ""
    if path:
        ok, out = _run(["ollama", "--version"])
        version = out.split()[-1] if ok else ""

    # Check if running (may be installed but not on PATH, e.g. Windows native)
    running, _ = _probe_url("http://localhost:11434/api/tags")

    # OK if either on PATH or reachable on localhost
    found = bool(path) or running

    return {
        "name": "ollama",
        "category": "core",
        "required": True,
        "found": found,
        "version": version,
        "ok": found,
        "running": running,
        "path": path or "",
        "detail": ("running" if running else "installed") if found else "Ollama not found — install from https://ollama.com",
    }


def check_docker() -> dict:
    """Docker for MCP servers and containerized deployment."""
    path = _which("docker")
    if path:
        ok, out = _run(["docker", "--version"])
        version = out.split()[-1].rstrip(",") if ok else ""
    else:
        ok, version = False, ""

    # Check if daemon running
    running = False
    if path:
        r_ok, _ = _run(["docker", "info"], timeout=5)
        running = r_ok

    return {
        "name": "docker",
        "category": "optional",
        "required": False,
        "found": bool(path),
        "version": version,
        "ok": bool(path),
        "running": running,
        "path": path or "",
        "detail": "Optional — needed for MCP servers (playwright) and containerized fleet",
    }


def check_node() -> dict:
    """Node.js / npx for stdio MCP servers."""
    npx_path = _which("npx")
    node_path = _which("node")
    if node_path:
        ok, out = _run(["node", "--version"])
        version = out.strip().lstrip("v") if ok else ""
    else:
        ok, version = False, ""

    return {
        "name": "node",
        "category": "optional",
        "required": False,
        "found": bool(node_path),
        "version": version,
        "ok": bool(node_path),
        "has_npx": bool(npx_path),
        "path": node_path or "",
        "detail": "Optional — needed for stdio MCP servers (filesystem, github, memory, etc.)",
    }


def check_git() -> dict:
    """Git for version control and auto-update."""
    path = _which("git")
    if path:
        ok, out = _run(["git", "--version"])
        version = out.split()[-1] if ok else ""
    else:
        ok, version = False, ""

    return {
        "name": "git",
        "category": "core",
        "required": True,
        "found": bool(path),
        "version": version,
        "ok": bool(path),
        "path": path or "",
        "detail": "" if path else "Git not found — install from https://git-scm.com",
    }


def check_playwright_mcp() -> dict:
    """Playwright MCP server (Docker container)."""
    running, detail = _probe_url("http://localhost:8931")
    return {
        "name": "playwright-mcp",
        "category": "mcp",
        "required": False,
        "found": running,
        "ok": running,
        "running": running,
        "detail": "Playwright MCP at localhost:8931" if running else "Not running — start with: docker compose up -d playwright-mcp",
    }


def check_fleet_db() -> dict:
    """Fleet database."""
    db_path = FLEET_DIR / "fleet.db"
    exists = db_path.exists()
    size_mb = round(db_path.stat().st_size / (1024 * 1024), 2) if exists else 0
    return {
        "name": "fleet.db",
        "category": "data",
        "required": True,
        "found": exists,
        "ok": True,  # auto-created on first run
        "size_mb": size_mb,
        "detail": f"{size_mb}MB" if exists else "Will be created on first fleet boot",
    }


def check_rag_db() -> dict:
    """RAG vector store database."""
    db_path = FLEET_DIR / "rag.db"
    exists = db_path.exists()
    size_mb = round(db_path.stat().st_size / (1024 * 1024), 2) if exists else 0
    return {
        "name": "rag.db",
        "category": "data",
        "required": False,
        "found": exists,
        "ok": True,  # auto-created on first ingest
        "size_mb": size_mb,
        "detail": f"{size_mb}MB" if exists else "Will be created on first rag_index run",
    }


def check_python_packages() -> dict:
    """Key Python packages."""
    packages = {
        "flask": "fleet dashboard + web launcher",
        "psutil": "process management + memory monitoring",
        "customtkinter": "launcher GUI",
        "tomlkit": "config read/write",
        "anthropic": "Claude API client",
    }
    missing = []
    found = []
    for pkg, purpose in packages.items():
        try:
            __import__(pkg.replace("-", "_"))
            found.append(pkg)
        except ImportError:
            missing.append(f"{pkg} ({purpose})")

    return {
        "name": "python-packages",
        "category": "core",
        "required": True,
        "found": len(missing) == 0,
        "ok": len(missing) == 0,
        "installed": found,
        "missing": missing,
        "detail": "" if not missing else f"Missing: {', '.join(missing)}",
    }


def check_system_ram() -> dict:
    """System RAM check via system_info."""
    try:
        from system_info import get_memory, get_worker_limits
        mem = get_memory()
        limits = get_worker_limits(mem["ram_total_gb"])
        return {
            "name": "system-ram",
            "category": "hardware",
            "required": True,
            "found": True,
            "ok": mem["ram_total_gb"] >= 4,
            "ram_total_gb": mem["ram_total_gb"],
            "ram_available_gb": mem["ram_available_gb"],
            "ram_pct": mem["ram_pct"],
            "tier": limits["tier"],
            "recommended_workers": limits["max_workers"],
            "detail": f'{mem["ram_total_gb"]}GB ({limits["tier"]} tier, {limits["max_workers"]} workers)',
        }
    except Exception:
        return {
            "name": "system-ram",
            "category": "hardware",
            "required": True,
            "found": False,
            "ok": False,
            "detail": "psutil not available — install with: pip install psutil",
        }


# ── Aggregate checks ──────────────────────────────────────────────────────────

ALL_CHECKS = [
    check_python,
    check_pip,
    check_git,
    check_ollama,
    check_python_packages,
    check_system_ram,
    check_fleet_db,
    check_rag_db,
    check_docker,
    check_node,
    check_playwright_mcp,
]

CATEGORIES = {
    "core": "Required — fleet won't start without these",
    "hardware": "System resources",
    "data": "Databases (auto-created if missing)",
    "optional": "Enhances functionality but not required",
    "mcp": "MCP tool servers",
}


def check_all() -> list[dict]:
    """Run all dependency checks."""
    return [fn() for fn in ALL_CHECKS]


def check_category(category: str) -> list[dict]:
    """Run checks for a specific category."""
    results = check_all()
    return [r for r in results if r.get("category") == category]


def summary(results: list[dict] = None) -> dict:
    """Summarize check results."""
    if results is None:
        results = check_all()
    core_ok = all(r["ok"] for r in results if r.get("required"))
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"] and r.get("required")),
        "warnings": sum(1 for r in results if not r["ok"] and not r.get("required")),
        "core_ready": core_ok,
        "results": results,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="BigEd CC Dependency Checker")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--category", choices=list(CATEGORIES.keys()), help="Check specific category")
    args = parser.parse_args()

    if args.category:
        results = [fn() for fn in ALL_CHECKS if fn.__doc__ and True]
        results = [r for r in check_all() if r.get("category") == args.category]
    else:
        results = check_all()

    if args.json:
        import json
        print(json.dumps(summary(results), indent=2))
        return

    s = summary(results)
    print(f"\nBigEd CC — Dependency Check")
    print(f"{'=' * 50}")

    by_cat = {}
    for r in results:
        cat = r.get("category", "other")
        by_cat.setdefault(cat, []).append(r)

    for cat, desc in CATEGORIES.items():
        items = by_cat.get(cat, [])
        if not items:
            continue
        print(f"\n{cat.upper()} — {desc}")
        for r in items:
            icon = "+" if r["ok"] else "!" if not r.get("required") else "X"
            name = r["name"].ljust(20)
            detail = r.get("detail", "")
            version = f'v{r["version"]}' if r.get("version") else ""
            line = f"  [{icon}] {name} {version}"
            if detail:
                line += f"  — {detail}"
            print(line)

    print(f"\n{'=' * 50}")
    status = "READY" if s["core_ready"] else "NOT READY"
    print(f"Status: {status} ({s['passed']}/{s['total']} passed, {s['failed']} failed, {s['warnings']} warnings)")

    if not s["core_ready"]:
        print("\nFix required items before starting the fleet.")
        sys.exit(1)


if __name__ == "__main__":
    main()
