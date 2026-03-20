"""
BigEd CC — GitHub Release Update Engine

Git-free update path for installed (PyInstaller) users.
Downloads release assets from the GitHub Releases API and swaps binaries.

Source users continue using git pull (handled by updater.py STEPS).

Usage from updater.py:
    from release_updater import check_release, download_asset, apply_update
"""
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# ── Config defaults ──────────────────────────────────────────────────────

DEFAULT_OWNER = "SwiftWing21"
DEFAULT_REPO = "BigEd"
API_BASE = "https://api.github.com"
CONNECT_TIMEOUT = 15  # seconds


# ── Config loader ────────────────────────────────────────────────────────

def _load_github_config() -> dict:
    """Read [github] section from fleet.toml if available."""
    try:
        # Walk up from this file to find fleet/fleet.toml
        here = Path(__file__).resolve().parent
        candidates = [
            here.parent.parent / "fleet" / "fleet.toml",   # source layout
            here.parent / "fleet" / "fleet.toml",           # installed layout
        ]
        for toml_path in candidates:
            if toml_path.exists():
                try:
                    import tomllib
                except ImportError:
                    try:
                        import tomli as tomllib
                    except ImportError:
                        break
                with open(toml_path, "rb") as f:
                    cfg = tomllib.load(f)
                return cfg.get("github", {})
    except Exception:
        pass
    return {}


def get_repo_info() -> tuple[str, str]:
    """Return (owner, repo) from fleet.toml or defaults."""
    cfg = _load_github_config()
    return cfg.get("owner", DEFAULT_OWNER), cfg.get("repo", DEFAULT_REPO)


# ── API helpers ──────────────────────────────────────────────────────────

def _api_get(url: str) -> dict:
    """GET a GitHub API URL. Returns parsed JSON."""
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "BigEdCC-Updater/1.0",
    })
    with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as resp:
        return json.loads(resp.read())


def check_release(current_version: str = "") -> dict | None:
    """Check for the latest GitHub release.

    Returns a dict with keys: tag, name, published, assets, body, url
    or None if no releases exist or the network is unavailable.

    If current_version is provided (e.g. "v0.40.10a"), returns None
    when the latest release tag matches it (already up to date).
    """
    owner, repo = get_repo_info()
    url = f"{API_BASE}/repos/{owner}/{repo}/releases/latest"
    try:
        data = _api_get(url)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return None

    tag = data.get("tag_name", "")
    if current_version and tag == current_version:
        return None  # already on latest

    assets = []
    for a in data.get("assets", []):
        assets.append({
            "name": a["name"],
            "size": a["size"],
            "url": a["browser_download_url"],
        })

    return {
        "tag": tag,
        "name": data.get("name", tag),
        "published": data.get("published_at", ""),
        "body": data.get("body", ""),
        "url": data.get("html_url", ""),
        "assets": assets,
    }


def list_releases(limit: int = 10) -> list[dict]:
    """List recent releases (for rollback UI)."""
    owner, repo = get_repo_info()
    url = f"{API_BASE}/repos/{owner}/{repo}/releases?per_page={limit}"
    try:
        data = _api_get(url)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return []
    return [{
        "tag": r.get("tag_name", ""),
        "name": r.get("name", ""),
        "published": r.get("published_at", ""),
        "prerelease": r.get("prerelease", False),
        "asset_count": len(r.get("assets", [])),
    } for r in data]


# ── Download ─────────────────────────────────────────────────────────────

def download_asset(asset_url: str, dest_path: Path,
                   progress_cb=None) -> Path:
    """Download a release asset to dest_path.

    progress_cb(bytes_so_far, total_bytes) is called periodically.
    Returns the dest_path on success, raises on failure.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(asset_url, headers={
        "User-Agent": "BigEdCC-Updater/1.0",
    })

    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 64 * 1024  # 64 KB

        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)

    return dest_path


# ── Apply update (binary swap) ───────────────────────────────────────────

def apply_update(download_path: Path, install_dir: Path,
                 log_cb=None) -> list[str]:
    """Apply a downloaded release to the install directory.

    Supports two asset formats:
      1. A .zip containing executables (BigEdCC.exe, Updater.exe, etc.)
      2. A single .exe file

    Returns list of filenames that were updated.
    log_cb(message) is called with status updates.

    The running Updater.exe cannot be overwritten directly on Windows,
    so it's staged as Updater_new.exe (same as git-based updater).
    """
    def log(msg):
        if log_cb:
            log_cb(msg)

    updated = []

    if download_path.suffix == ".zip":
        # Extract zip to temp dir, then copy files
        tmp_dir = Path(tempfile.mkdtemp(prefix="bigedcc_update_"))
        try:
            log(f"Extracting {download_path.name}...")
            with zipfile.ZipFile(download_path, "r") as zf:
                zf.extractall(tmp_dir)

            # Find executables in the extracted content
            for exe in tmp_dir.rglob("*.exe"):
                dest_name = exe.name
                # Stage updater as _new to avoid locking
                if dest_name.lower() == "updater.exe":
                    dest = install_dir / "Updater_new.exe"
                else:
                    dest = install_dir / dest_name

                log(f"  {dest_name} → {dest.name}")
                shutil.copy2(exe, dest)
                updated.append(dest_name)

            # Also copy non-exe assets (icons, etc.)
            for asset in tmp_dir.rglob("*"):
                if asset.is_file() and asset.suffix in (".ico", ".png", ".dll"):
                    dest = install_dir / asset.name
                    shutil.copy2(asset, dest)
                    updated.append(asset.name)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    elif download_path.suffix == ".exe":
        dest_name = download_path.name
        if dest_name.lower() == "updater.exe":
            dest = install_dir / "Updater_new.exe"
        else:
            dest = install_dir / dest_name
        log(f"  {dest_name} → {dest.name}")
        shutil.copy2(download_path, dest)
        updated.append(dest_name)
    else:
        raise ValueError(f"Unsupported asset format: {download_path.suffix}")

    return updated


# ── Version file ─────────────────────────────────────────────────────────

VERSION_FILE = ".bigedcc_version"


def read_installed_version(install_dir: Path) -> str:
    """Read the installed version tag from the version file."""
    vf = install_dir / VERSION_FILE
    if vf.exists():
        return vf.read_text(encoding="utf-8").strip()
    return ""


def write_installed_version(install_dir: Path, version: str):
    """Write the current version tag after a successful update."""
    vf = install_dir / VERSION_FILE
    vf.write_text(version.strip(), encoding="utf-8")


# ── Convenience: full update flow ────────────────────────────────────────

def check_and_update(install_dir: Path, log_cb=None,
                     progress_cb=None) -> dict:
    """One-call update: check → download → apply.

    Returns a result dict:
      {"status": "up_to_date"} — no new release
      {"status": "updated", "version": "v0.41.00", "files": [...]}
      {"status": "error", "message": "..."}
      {"status": "no_assets", "version": "v0.41.00"} — release has no downloadable assets
    """
    def log(msg):
        if log_cb:
            log_cb(msg)

    current = read_installed_version(install_dir)
    log(f"Current version: {current or '(unknown)'}")

    release = check_release(current)
    if release is None:
        log("Already up to date.")
        return {"status": "up_to_date"}

    log(f"New release available: {release['tag']} — {release['name']}")

    # Find the best asset to download
    # Prefer: .zip > platform-specific .exe
    assets = release["assets"]
    if not assets:
        log("Release has no downloadable assets.")
        return {"status": "no_assets", "version": release["tag"]}

    # Prefer zip, then exe
    chosen = None
    for a in assets:
        name_lower = a["name"].lower()
        if name_lower.endswith(".zip"):
            chosen = a
            break
    if chosen is None:
        # Pick the first exe or the first asset
        for a in assets:
            if a["name"].lower().endswith(".exe"):
                chosen = a
                break
    if chosen is None:
        chosen = assets[0]  # fallback

    size_mb = chosen["size"] / (1024 * 1024)
    log(f"Downloading {chosen['name']} ({size_mb:.1f} MB)...")

    tmp = Path(tempfile.gettempdir()) / "bigedcc_update" / chosen["name"]
    try:
        download_asset(chosen["url"], tmp, progress_cb=progress_cb)
    except Exception as e:
        return {"status": "error", "message": f"Download failed: {e}"}

    log("Applying update...")
    try:
        files = apply_update(tmp, install_dir, log_cb=log_cb)
    except Exception as e:
        return {"status": "error", "message": f"Apply failed: {e}"}
    finally:
        # Clean up download
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    write_installed_version(install_dir, release["tag"])
    log(f"Updated to {release['tag']} — {len(files)} file(s) replaced.")
    return {"status": "updated", "version": release["tag"], "files": files}


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="BigEd CC Release Updater")
    parser.add_argument("--check", action="store_true", help="Check for updates only")
    parser.add_argument("--update", action="store_true", help="Download and apply update")
    parser.add_argument("--list", action="store_true", help="List recent releases")
    parser.add_argument("--install-dir", type=Path, default=Path("."),
                        help="Install directory (default: current dir)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.list:
        releases = list_releases()
        if args.json:
            print(json.dumps(releases, indent=2))
        else:
            for r in releases:
                pre = " [pre]" if r["prerelease"] else ""
                print(f"  {r['tag']:16s} {r['name']}{pre}  ({r['asset_count']} assets)")
        sys.exit(0)

    if args.check:
        current = read_installed_version(args.install_dir)
        release = check_release(current)
        if args.json:
            print(json.dumps(release or {"status": "up_to_date"}, indent=2))
        elif release:
            print(f"Update available: {release['tag']} — {release['name']}")
            for a in release["assets"]:
                print(f"  {a['name']} ({a['size'] / 1024 / 1024:.1f} MB)")
        else:
            print("Up to date.")
        sys.exit(0)

    if args.update:
        result = check_and_update(
            args.install_dir,
            log_cb=lambda m: print(f"  {m}"),
            progress_cb=lambda done, total: print(
                f"\r  {done / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB", end="", flush=True
            ) if total else None,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"\n  Result: {result['status']}")
        sys.exit(0 if result.get("status") != "error" else 1)

    parser.print_help()
