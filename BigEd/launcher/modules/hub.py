"""
BigEd CC — Module Hub client.

Downloads, verifies, and installs modules from the BigEd-ModuleHub GitHub repo.
Enterprise users can configure a private hub URL.

v0.053.00b:
  - Auto-register installed modules in fleet.toml [launcher.tabs]
  - Enterprise-only module gating (requires enterprise_hub_url)
  - Federation: enterprise hub auto-selected when configured
  - Enable/disable module support (updates manifest + fleet.toml)
"""
import hashlib
import json
import logging
import os
import re
import urllib.request
from pathlib import Path

log = logging.getLogger("modules.hub")

MODULES_DIR = Path(__file__).parent
DEFAULT_HUB = "https://github.com/SwiftWing21/BigEd-ModuleHub"

# fleet.toml lives at fleet/fleet.toml
_FLEET_TOML = MODULES_DIR.parent.parent.parent / "fleet" / "fleet.toml"


def _parse_version(v: str) -> tuple:
    """Parse a version string like '0.22' or '1.2.3' into a comparable tuple."""
    import re
    parts = re.split(r"[.\-]", str(v or "0"))
    result = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            result.append(0)
    return tuple(result) if result else (0,)


class ModuleHub:
    def __init__(self, config: dict = None):
        cfg = (config or {}).get("modules", {})
        self.hub_url = cfg.get("hub_url", DEFAULT_HUB)
        self.enterprise_url = cfg.get("enterprise_hub_url", "")
        self.verify_checksums = cfg.get("verify_checksums", True)
        self._registry = None
        self._enterprise_registry = None

    def _fetch_registry_from(self, hub_url: str) -> dict:
        """Fetch registry.json from a specific hub URL."""
        raw_base = hub_url.replace("github.com", "raw.githubusercontent.com") + "/main"
        url = f"{raw_base}/registry.json"
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())

    def get_registry(self, force_refresh=False) -> dict:
        """Fetch registry.json from the hub.

        Federation: when enterprise_hub_url is configured, fetch from enterprise
        first and merge with public hub. Enterprise modules take priority.
        """
        if self._registry and not force_refresh:
            return self._registry

        # Try enterprise hub first (federation auto-select)
        enterprise_modules = {}
        if self.enterprise_url:
            try:
                ent_reg = self._fetch_registry_from(self.enterprise_url)
                self._enterprise_registry = ent_reg
                for m in ent_reg.get("modules", []):
                    enterprise_modules[m["name"]] = m
                log.info("Fetched %d modules from enterprise hub", len(enterprise_modules))
            except Exception as e:
                log.warning("Enterprise hub fetch failed: %s — falling back to public", e)

        # Fetch public hub
        try:
            public_reg = self._fetch_registry_from(self.hub_url)
        except Exception as e:
            if enterprise_modules:
                # Enterprise-only mode: return enterprise registry
                self._registry = self._enterprise_registry
                return self._registry
            return {"version": "0", "modules": [], "error": str(e)}

        # Merge: enterprise modules override public modules with same name
        merged = {}
        for m in public_reg.get("modules", []):
            merged[m["name"]] = m
        merged.update(enterprise_modules)  # enterprise wins on conflict

        self._registry = {
            "version": public_reg.get("version", "0"),
            "modules": list(merged.values()),
        }
        return self._registry

    def is_enterprise(self) -> bool:
        """True when an enterprise hub URL is configured."""
        return bool(self.enterprise_url)

    def list_available(self, include_enterprise_only=None) -> list:
        """List modules available in the hub.

        Enterprise-only modules are only shown when the enterprise hub is
        configured (or include_enterprise_only is explicitly True).
        """
        reg = self.get_registry()
        modules = reg.get("modules", [])

        show_enterprise = include_enterprise_only if include_enterprise_only is not None else self.is_enterprise()
        if not show_enterprise:
            modules = [m for m in modules if not m.get("enterprise_only", False)]

        return modules

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
        """Download and install a module from the hub.

        v0.053.00b: Also registers the module in fleet.toml [launcher.tabs]
        and enforces enterprise-only gating.
        """
        reg = self.get_registry()
        module = None
        for m in reg.get("modules", []):
            if m["name"] == name:
                module = m
                break

        if not module:
            return {"error": f"Module '{name}' not found in hub"}

        # Enterprise-only gating: block install if enterprise_only and no enterprise hub
        if module.get("enterprise_only", False) and not self.is_enterprise():
            return {"error": f"Module '{name}' requires enterprise hub — configure enterprise_hub_url in fleet.toml"}

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

        # Register in fleet.toml [launcher.tabs]
        tab_enabled = module.get("default_enabled", False)
        self._register_in_fleet_toml(name, tab_enabled)

        return {
            "name": name,
            "version": module.get("version", "unknown"),
            "file": str(dest),
            "installed": True,
            "tab_registered": True,
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

    def _register_in_fleet_toml(self, name: str, enabled: bool = False):
        """Add or update a module entry in fleet.toml [launcher.tabs].

        v0.053.00b: Modules installed from the hub are auto-registered so
        the launcher knows about them without manual config editing.
        """
        toml_path = _FLEET_TOML
        if not toml_path.exists():
            log.warning("fleet.toml not found at %s — skipping tab registration", toml_path)
            return

        try:
            text = toml_path.read_text(encoding="utf-8")
            enabled_str = "true" if enabled else "false"
            tab_line = f"{name} = {enabled_str}"

            # Check if [launcher.tabs] section exists
            tabs_match = re.search(r'^\[launcher\.tabs\]', text, re.M)
            if not tabs_match:
                # Append the section
                text = text.rstrip() + f"\n\n[launcher.tabs]\n{tab_line}\n"
            else:
                # Check if this module already has a line
                existing = re.search(rf'^{re.escape(name)}\s*=\s*(true|false)', text, re.M)
                if existing:
                    # Already registered — do not overwrite user's choice
                    return
                else:
                    # Find the end of the [launcher.tabs] block and insert
                    # Insert after the last key= line before the next [section]
                    block_start = tabs_match.end()
                    next_section = re.search(r'^\[', text[block_start:], re.M)
                    if next_section:
                        insert_pos = block_start + next_section.start()
                    else:
                        insert_pos = len(text)
                    # Insert before the next section (with newline)
                    text = text[:insert_pos].rstrip() + f"\n{tab_line}\n" + text[insert_pos:]

            toml_path.write_text(text, encoding="utf-8")
            log.info("Registered module '%s' in fleet.toml [launcher.tabs] (enabled=%s)", name, enabled)
        except Exception as e:
            log.warning("Failed to register module '%s' in fleet.toml: %s", name, e)

    def enable_module(self, name: str) -> dict:
        """Enable a module by updating fleet.toml [launcher.tabs]."""
        return self._set_module_enabled(name, True)

    def disable_module(self, name: str) -> dict:
        """Disable a module by updating fleet.toml [launcher.tabs]."""
        return self._set_module_enabled(name, False)

    def _set_module_enabled(self, name: str, enabled: bool) -> dict:
        """Update module enabled state in fleet.toml [launcher.tabs]."""
        toml_path = _FLEET_TOML
        if not toml_path.exists():
            return {"error": "fleet.toml not found"}
        try:
            text = toml_path.read_text(encoding="utf-8")
            enabled_str = "true" if enabled else "false"
            pattern = rf'^({re.escape(name)}\s*=\s*)(true|false)'
            if re.search(pattern, text, re.M):
                text = re.sub(pattern, rf'\g<1>{enabled_str}', text, flags=re.M)
            else:
                # Module not in tabs — add it
                self._register_in_fleet_toml(name, enabled)
                return {"name": name, "enabled": enabled, "added": True}
            toml_path.write_text(text, encoding="utf-8")
            return {"name": name, "enabled": enabled}
        except Exception as e:
            return {"error": f"Failed to update fleet.toml: {e}"}

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
