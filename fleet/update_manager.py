"""update_manager.py — Core update logic for BigEd CC.

Dual-mode updater:
  - **Git mode** (source users): git fetch/pull + pip install
  - **Release mode** (installed .exe users): GitHub Releases API + download/stage

All subprocess calls use CREATE_NO_WINDOW on Windows.
All HTTP calls have explicit timeouts.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
_FLEET_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _FLEET_DIR.parent
_LAUNCHER_DIR = _PROJECT_ROOT / "BigEd" / "launcher"
_DIST_DIR = _LAUNCHER_DIR / "dist"
_VERSION_FILE = _DIST_DIR / ".bigedcc_version"
_MANIFEST_FILE = _DIST_DIR / ".update_manifest.json"
_SEARCH_ROOTS: list[Path] = [_LAUNCHER_DIR, _LAUNCHER_DIR.parent, _PROJECT_ROOT]

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------
_update_status: dict[str, Any] = {}
_update_lock = threading.Lock()
_apply_proc: subprocess.Popen | None = None
_apply_cancel = threading.Event()

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def _load_fleet_config() -> dict:
    """Load fleet.toml via config.py if available, else empty dict."""
    try:
        from config import load_config
        return load_config()
    except Exception:
        log.warning("Could not load fleet config", exc_info=True)
        return {}


def _is_offline() -> bool:
    """Check fleet config for offline/air-gap mode."""
    cfg = _load_fleet_config()
    fleet = cfg.get("fleet", {})
    return fleet.get("offline_mode", False) or fleet.get("air_gap_mode", False)


# ---------------------------------------------------------------------------
# 1. detect_mode
# ---------------------------------------------------------------------------
def detect_mode() -> str:
    """Return 'git' if any _SEARCH_ROOTS contains .git, else 'release'."""
    for root in _SEARCH_ROOTS:
        if (root / ".git").exists():
            return "git"
    return "release"


# ---------------------------------------------------------------------------
# 2. _git_describe
# ---------------------------------------------------------------------------
def _git_describe() -> str:
    """Run ``git describe --tags --always`` and return stdout or ''."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=10,
            cwd=str(_PROJECT_ROOT),
            creationflags=_NO_WINDOW,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        log.warning("git describe failed", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# 3. current_version
# ---------------------------------------------------------------------------
def current_version() -> str:
    """Read _VERSION_FILE, fallback to git describe, fallback to 'unknown'."""
    try:
        if _VERSION_FILE.exists():
            text = _VERSION_FILE.read_text().strip()
            if text:
                return text
    except Exception:
        log.warning("Failed to read version file", exc_info=True)

    desc = _git_describe()
    if desc:
        return desc

    return "unknown"


# ---------------------------------------------------------------------------
# 4. Manifest read / write
# ---------------------------------------------------------------------------
def get_manifest() -> dict:
    """Read JSON manifest from _MANIFEST_FILE, or empty dict."""
    try:
        if _MANIFEST_FILE.exists():
            return json.loads(_MANIFEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        log.warning("Failed to read update manifest", exc_info=True)
    return {}


def save_manifest(data: dict) -> None:
    """Write JSON manifest to _MANIFEST_FILE. Creates parent dirs."""
    try:
        _DIST_DIR.mkdir(parents=True, exist_ok=True)
        _MANIFEST_FILE.write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8"
        )
    except Exception:
        log.warning("Failed to write update manifest", exc_info=True)


# ---------------------------------------------------------------------------
# 5. file_hash
# ---------------------------------------------------------------------------
def file_hash(path: Path) -> str:
    """Return MD5 hex digest of a file."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 7. _git_check_behind
# ---------------------------------------------------------------------------
def _git_check_behind() -> tuple[int, list[str]]:
    """git fetch + rev-list count. Returns (behind_count, commit_summaries)."""
    cwd = str(_PROJECT_ROOT)
    try:
        subprocess.run(
            ["git", "fetch", "--quiet"],
            capture_output=True, text=True, timeout=30,
            cwd=cwd, creationflags=_NO_WINDOW,
        )
    except Exception:
        log.warning("git fetch failed", exc_info=True)
        return 0, []

    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD..@{u}"],
            capture_output=True, text=True, timeout=10,
            cwd=cwd, creationflags=_NO_WINDOW,
        )
        count = int(result.stdout.strip()) if result.returncode == 0 else 0
    except Exception:
        log.warning("git rev-list count failed", exc_info=True)
        count = 0

    commits: list[str] = []
    if count > 0:
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "HEAD..@{u}"],
                capture_output=True, text=True, timeout=10,
                cwd=cwd, creationflags=_NO_WINDOW,
            )
            if result.returncode == 0:
                commits = [ln.strip() for ln in result.stdout.strip().splitlines() if ln.strip()]
        except Exception:
            log.warning("git log failed", exc_info=True)

    return count, commits


# ---------------------------------------------------------------------------
# 8. _release_check
# ---------------------------------------------------------------------------
def _release_check() -> dict:
    """Query GitHub Releases API for the latest release."""
    owner, repo = _get_repo_info()
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return {
            "tag": data.get("tag_name", ""),
            "body": data.get("body", ""),
            "assets": [
                {"name": a["name"], "url": a["browser_download_url"], "size": a["size"]}
                for a in data.get("assets", [])
            ],
            "published_at": data.get("published_at", ""),
        }
    except urllib.error.HTTPError as exc:
        log.warning("GitHub release check HTTP error: %s", exc.code)
        return {"error": f"HTTP {exc.code}"}
    except Exception:
        log.warning("GitHub release check failed", exc_info=True)
        return {"error": "request_failed"}


# ---------------------------------------------------------------------------
# 9. _get_repo_info
# ---------------------------------------------------------------------------
def _get_repo_info() -> tuple[str, str]:
    """Read [github] owner/repo from fleet.toml; default SwiftWing21/BigEd."""
    cfg = _load_fleet_config()
    gh = cfg.get("github", {})
    return gh.get("owner", "SwiftWing21"), gh.get("repo", "BigEd")


# ---------------------------------------------------------------------------
# 10. check_for_update
# ---------------------------------------------------------------------------
def check_for_update() -> dict:
    """Unified update check. Returns dict with available, mode, etc."""
    if _is_offline():
        return {"available": False, "mode": detect_mode(), "error": "offline"}

    mode = detect_mode()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    result: dict[str, Any] = {
        "available": False,
        "mode": mode,
        "behind_count": 0,
        "commits": [],
        "tag": "",
        "body": "",
        "last_check": now,
        "error": None,
    }

    try:
        if mode == "git":
            count, commits = _git_check_behind()
            result["behind_count"] = count
            result["commits"] = commits
            result["available"] = count > 0
        else:
            release = _release_check()
            if "error" in release:
                result["error"] = release["error"]
            else:
                result["tag"] = release.get("tag", "")
                result["body"] = release.get("body", "")
                cur = current_version()
                result["available"] = bool(result["tag"] and result["tag"] != cur)
                result["_assets"] = release.get("assets", [])
    except Exception:
        log.warning("check_for_update failed", exc_info=True)
        result["error"] = "check_failed"

    with _update_lock:
        _update_status.update(result)

    return result


# ---------------------------------------------------------------------------
# 11. get_status
# ---------------------------------------------------------------------------
def get_status() -> dict:
    """Return cached status + version + mode."""
    with _update_lock:
        status = dict(_update_status)
    status["version"] = current_version()
    status["mode"] = detect_mode()
    return status


# ---------------------------------------------------------------------------
# 12. apply_update
# ---------------------------------------------------------------------------
def apply_update(progress_cb: Callable[[str, float], None] | None = None) -> dict:
    """Run update steps. Returns {success, restart_required, error}."""
    _apply_cancel.clear()
    mode = detect_mode()

    try:
        if mode == "git":
            _apply_git_update(progress_cb)
        else:
            _apply_release_update(progress_cb)
        _record_history(mode, tag=_update_status.get("tag", ""))
        return {"success": True, "restart_required": True, "error": None}
    except Exception as exc:
        log.warning("apply_update failed", exc_info=True)
        return {"success": False, "restart_required": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# 13. cancel_update
# ---------------------------------------------------------------------------
def cancel_update() -> None:
    """Set cancel event and terminate any running subprocess."""
    _apply_cancel.set()
    global _apply_proc
    if _apply_proc is not None:
        try:
            _apply_proc.terminate()
        except Exception:
            log.warning("Failed to terminate update subprocess", exc_info=True)
        _apply_proc = None


# ---------------------------------------------------------------------------
# 14. _apply_git_update
# ---------------------------------------------------------------------------
def _apply_git_update(progress_cb: Callable[[str, float], None] | None = None) -> None:
    """git pull + pip install -r requirements.txt."""
    global _apply_proc
    cwd = str(_PROJECT_ROOT)

    def _report(msg: str, pct: float) -> None:
        if progress_cb:
            progress_cb(msg, pct)

    _report("Pulling latest changes...", 0.1)
    if _apply_cancel.is_set():
        raise RuntimeError("Update cancelled")

    proc = subprocess.run(
        ["git", "pull", "--ff-only"],
        capture_output=True, text=True, timeout=60,
        cwd=cwd, creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git pull failed: {proc.stderr.strip()}")

    _report("Pull complete. Checking dependencies...", 0.5)
    if _apply_cancel.is_set():
        raise RuntimeError("Update cancelled")

    req_file = _PROJECT_ROOT / "requirements.txt"
    if req_file.exists():
        _report("Installing dependencies...", 0.6)
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_file), "--quiet"],
            capture_output=True, text=True, timeout=120,
            cwd=cwd, creationflags=_NO_WINDOW,
        )
        if proc.returncode != 0:
            log.warning("pip install returned non-zero: %s", proc.stderr.strip())

    _report("Update applied.", 1.0)


# ---------------------------------------------------------------------------
# 15. _apply_release_update
# ---------------------------------------------------------------------------
def _apply_release_update(progress_cb: Callable[[str, float], None] | None = None) -> None:
    """Download release asset and stage into _DIST_DIR."""
    global _apply_proc

    def _report(msg: str, pct: float) -> None:
        if progress_cb:
            progress_cb(msg, pct)

    with _update_lock:
        assets = _update_status.get("_assets", [])
        tag = _update_status.get("tag", "")

    if not assets:
        raise RuntimeError("No release assets available")

    # Pick first .exe or .zip asset, fallback to first
    asset = assets[0]
    for a in assets:
        if a["name"].endswith((".exe", ".zip")):
            asset = a
            break

    url = asset["url"]
    dest = _DIST_DIR / asset["name"]
    _DIST_DIR.mkdir(parents=True, exist_ok=True)

    _report(f"Downloading {asset['name']}...", 0.1)
    if _apply_cancel.is_set():
        raise RuntimeError("Update cancelled")

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                while True:
                    if _apply_cancel.is_set():
                        raise RuntimeError("Update cancelled")
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        _report(f"Downloading... {downloaded * 100 // total}%", 0.1 + 0.7 * downloaded / total)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Download failed: {exc}") from exc

    _report("Download complete. Staged for swap.", 0.9)

    # Write staged tag for swap helper
    manifest = get_manifest()
    manifest["staged_tag"] = tag
    manifest["staged_asset"] = str(dest)
    save_manifest(manifest)

    _report("Ready to apply. Restart required.", 1.0)


# ---------------------------------------------------------------------------
# 16. spawn_swap_helper
# ---------------------------------------------------------------------------
def spawn_swap_helper(source_dir: str, target_dir: str, relaunch_exe: str = "") -> None:
    """Spawn update_helper.py to swap files after main process exits."""
    global _apply_proc
    helper = _FLEET_DIR / "update_helper.py"
    if not helper.exists():
        raise FileNotFoundError(f"update_helper.py not found at {helper}")

    cmd = [sys.executable, str(helper), source_dir, target_dir]
    if relaunch_exe:
        cmd.append(relaunch_exe)

    _apply_proc = subprocess.Popen(
        cmd,
        creationflags=_NO_WINDOW,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    log.info("Spawned swap helper PID %d", _apply_proc.pid)


# ---------------------------------------------------------------------------
# 17. _record_history
# ---------------------------------------------------------------------------
def _record_history(mode: str, tag: str = "") -> None:
    """Append to manifest history list."""
    manifest = get_manifest()
    history = manifest.get("history", [])
    history.append({
        "mode": mode,
        "tag": tag,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    # Keep last 50 entries
    manifest["history"] = history[-50:]
    save_manifest(manifest)


# ---------------------------------------------------------------------------
# 18. get_history
# ---------------------------------------------------------------------------
def get_history() -> list:
    """Read history from manifest."""
    return get_manifest().get("history", [])


# ---------------------------------------------------------------------------
# 19. start_background_checker
# ---------------------------------------------------------------------------
def start_background_checker(
    interval_hours: float = 24,
    sse_broadcast: Callable[[str, dict], None] | None = None,
) -> threading.Thread:
    """Start a daemon thread that checks for updates periodically."""
    def _loop() -> None:
        while True:
            try:
                result = check_for_update()
                if result.get("available") and sse_broadcast:
                    sse_broadcast("update_available", result)
            except Exception:
                log.warning("Background update check failed", exc_info=True)
            time.sleep(interval_hours * 3600)

    t = threading.Thread(target=_loop, daemon=True, name="update-checker")
    t.start()
    log.info("Background update checker started (interval=%sh)", interval_hours)
    return t
