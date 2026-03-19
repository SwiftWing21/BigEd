#!/usr/bin/env python3
"""
PT-3: Cross-platform installer abstraction.
Replaces Windows-only installer.py with platform-conditional logic.

Usage:
    python installer_cross.py install     # Install for current platform
    python installer_cross.py uninstall   # Uninstall for current platform
    python installer_cross.py status      # Check installation status
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
APP_NAME = "BigEd CC"
APP_ID = "com.biged.cc"
VERSION = "0.43"


class WindowsInstaller:
    """Windows: Registry entries for Add/Remove Programs."""

    def install(self):
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\BigEdCC"
            key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path)
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "BigEd")
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(HERE))
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ,
                            f'"{sys.executable}" "{HERE / "installer_cross.py"}" uninstall')
            winreg.CloseKey(key)
            print(f"Windows: Registered in Add/Remove Programs")
        except Exception as e:
            print(f"Windows install error: {e}")

    def uninstall(self):
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\BigEdCC"
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
            print("Windows: Removed from Add/Remove Programs")
        except Exception as e:
            print(f"Windows uninstall: {e}")

    def status(self) -> dict:
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\BigEdCC"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)
            version, _ = winreg.QueryValueEx(key, "DisplayVersion")
            location, _ = winreg.QueryValueEx(key, "InstallLocation")
            winreg.CloseKey(key)
            return {"installed": True, "version": version, "location": location}
        except Exception:
            return {"installed": False}


class LinuxInstaller:
    """Linux: .desktop file + optional symlink."""

    def __init__(self):
        self.desktop_dir = Path.home() / ".local" / "share" / "applications"
        self.desktop_file = self.desktop_dir / f"{APP_ID}.desktop"
        self.bin_link = Path.home() / ".local" / "bin" / "biged"

    def install(self):
        self.desktop_dir.mkdir(parents=True, exist_ok=True)
        desktop_content = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=AI Agent Fleet Manager
Exec={sys.executable} {HERE / 'launcher.py'}
Terminal=false
Categories=Development;Utility;
"""
        self.desktop_file.write_text(desktop_content)
        print(f"Linux: Desktop file installed at {self.desktop_file}")

        # Optional: symlink in PATH
        bin_dir = self.bin_link.parent
        bin_dir.mkdir(parents=True, exist_ok=True)
        if self.bin_link.exists():
            self.bin_link.unlink()
        self.bin_link.symlink_to(HERE / "launcher.py")
        print(f"Linux: Symlink created at {self.bin_link}")

    def uninstall(self):
        if self.desktop_file.exists():
            self.desktop_file.unlink()
            print(f"Linux: Removed {self.desktop_file}")
        if self.bin_link.exists():
            self.bin_link.unlink()
            print(f"Linux: Removed {self.bin_link}")

    def status(self) -> dict:
        return {
            "installed": self.desktop_file.exists(),
            "desktop_file": str(self.desktop_file) if self.desktop_file.exists() else None,
            "bin_link": str(self.bin_link) if self.bin_link.exists() else None,
        }


class MacOSInstaller:
    """macOS: /Applications copy + optional launchd service."""

    def __init__(self):
        self.app_dir = Path("/Applications")
        self.app_path = self.app_dir / f"{APP_NAME}.app"
        self.dist_app = HERE / "dist" / f"{APP_NAME}.app"

    def install(self):
        if not self.dist_app.exists():
            print(f"macOS: No .app bundle found at {self.dist_app}")
            print("Run package_macos.py first to build the .app bundle.")
            return
        if self.app_path.exists():
            shutil.rmtree(self.app_path)
        shutil.copytree(self.dist_app, self.app_path)
        print(f"macOS: Installed to {self.app_path}")

    def uninstall(self):
        if self.app_path.exists():
            shutil.rmtree(self.app_path)
            print(f"macOS: Removed {self.app_path}")
        else:
            print(f"macOS: Not installed at {self.app_path}")

    def status(self) -> dict:
        return {
            "installed": self.app_path.exists(),
            "location": str(self.app_path) if self.app_path.exists() else None,
        }


def get_installer():
    """Return the appropriate installer for the current platform."""
    if sys.platform == "win32":
        return WindowsInstaller()
    elif sys.platform == "darwin":
        return MacOSInstaller()
    else:
        return LinuxInstaller()


def main():
    parser = argparse.ArgumentParser(description=f"{APP_NAME} Cross-Platform Installer")
    parser.add_argument("action", choices=["install", "uninstall", "status"],
                       help="Action to perform")
    args = parser.parse_args()

    installer = get_installer()
    print(f"Platform: {sys.platform}")

    if args.action == "install":
        installer.install()
    elif args.action == "uninstall":
        installer.uninstall()
    elif args.action == "status":
        import json
        status = installer.status()
        print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
