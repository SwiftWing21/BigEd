"""
BigEd CC — Module Hub client.

Downloads, verifies, and installs modules from the BigEd-ModuleHub GitHub repo.
Enterprise users can configure a private hub URL.
"""
import hashlib
import json
import os
import urllib.request
from pathlib import Path

MODULES_DIR = Path(__file__).parent
DEFAULT_HUB = "https://github.com/SwiftWing21/BigEd-ModuleHub"


def _parse_version(v: str):
    """Parse a version string for correct ordering comparison.

    Tries packaging.version.Version first (handles semver edge cases like
    0.10 > 0.9). Falls back to tuple-of-ints split on '.' if packaging is
    unavailable.
    """
    try:
        from packaging.version import Version
        return Version(str(v))
    except Exception:
        pass
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except Exception:
        return (0,)


class ModuleHub:
    def __init__(self, config: dict = None):
        cfg = (config or {}).get("modules", {})
        self.hub_url = cfg.get("hub_url", DEFAULT_HUB)
        self.enterprise_url = cfg.get("enterprise_hub_url", "")
        self.verify_checksums = cfg.get("verify_checksums", True)
        self._registry = None

    def get_registry(self, force_refresh=False) -> dict:
        """Fetch registry.json from the hub."""
        if self._registry and not force_refresh:
            return self._registry

        hub = self.enterprise_url or self.hub_url
        # Convert GitHub repo URL to raw content URL
        raw_base = hub.replace("github.com", "raw.githubusercontent.com") + "/main"
        url = f"{raw_base}/registry.json"

        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                self._registry = json.loads(r.read())
            return self._registry
        except Exception as e:
            return {"version": "0", "modules": [], "error": str(e)}

    def list_available(self) -> list:
        """List modules available in the hub."""
        reg = self.get_registry()
        return reg.get("modules", [])

    def list_installed(self) -> list:
        """List locally installed modules."""
        manifest_path = MODULES_DIR / "manifest.json"
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                return data.get("modules", [])
            except Exception:
                pass
        return []

    def get_update_available(self) -> list:
        """Compare installed vs hub versions, return modules with updates."""
        installed = {m["name"]: m.get("version", "0") for m in self.list_installed()}
        available = self.list_available()
        updates = []
        for mod in available:
            name = mod["name"]
            if name in installed:
                if _parse_version(mod.get("version", "0")) > _parse_version(installed[name]):
                    updates.append(mod)
            else:
                updates.append(mod)  # Not installed = available
        return updates

    def install_module(self, name: str) -> dict:
        """Download and install a module from the hub."""
        reg = self.get_registry()
        module = None
        for m in reg.get("modules", []):
            if m["name"] == name:
                module = m
                break

        if not module:
            return {"error": f"Module '{name}' not found in hub"}

        # Download module file
        hub = self.enterprise_url or self.hub_url
        raw_base = hub.replace("github.com", "raw.githubusercontent.com") + "/main"
        download_url = f"{raw_base}/{module['download_url']}"

        try:
            with urllib.request.urlopen(download_url, timeout=30) as r:
                content = r.read()
        except Exception as e:
            return {"error": f"Download failed: {e}"}

        # Verify checksum
        if self.verify_checksums and module.get("checksum_sha256"):
            actual = hashlib.sha256(content).hexdigest()
            expected = module["checksum_sha256"]
            if not actual.startswith(expected) and not expected.startswith(actual):
                return {"error": f"Checksum mismatch: expected {expected[:16]}, got {actual[:16]}"}

        # Write module file
        dest = MODULES_DIR / module.get("file", f"mod_{name}.py")
        dest.write_bytes(content)

        # Update local manifest
        self._update_local_manifest(module)

        return {
            "name": name,
            "version": module.get("version", "unknown"),
            "file": str(dest),
            "installed": True,
        }

    def _update_local_manifest(self, module: dict):
        """Add or update module in local manifest.json."""
        manifest_path = MODULES_DIR / "manifest.json"
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            data = {"modules": []}

        # Update or add
        found = False
        for i, m in enumerate(data.get("modules", [])):
            if m["name"] == module["name"]:
                data["modules"][i] = {
                    "name": module["name"],
                    "file": module.get("file", f"mod_{module['name']}.py"),
                    "version": module.get("version", "0"),
                    "default_enabled": module.get("default_enabled", False),
                    "deprecated": False,
                }
                found = True
                break

        if not found:
            data["modules"].append({
                "name": module["name"],
                "file": module.get("file", f"mod_{module['name']}.py"),
                "version": module.get("version", "0"),
                "default_enabled": module.get("default_enabled", False),
                "deprecated": False,
            })

        manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def uninstall_module(self, name: str) -> dict:
        """Remove a module (delete file + remove from manifest)."""
        manifest_path = MODULES_DIR / "manifest.json"
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {"error": "Cannot read manifest"}

        # Find and remove
        module = None
        for m in data.get("modules", []):
            if m["name"] == name:
                module = m
                break

        if not module:
            return {"error": f"Module '{name}' not in manifest"}

        # Delete file
        mod_file = MODULES_DIR / module.get("file", f"mod_{name}.py")
        if mod_file.exists():
            mod_file.unlink()

        # Update manifest
        data["modules"] = [m for m in data["modules"] if m["name"] != name]
        manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        return {"name": name, "uninstalled": True}
