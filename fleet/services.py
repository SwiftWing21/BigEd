"""Auto-boot service management — install/uninstall fleet as a system service."""
import subprocess
import sys
from pathlib import Path


def install_service(fleet_dir: Path, python: str = None):
    """Install fleet as system service (auto-start on login)."""
    if python is None:
        python = sys.executable
    supervisor_path = fleet_dir / "supervisor.py"

    if sys.platform == "win32":
        # Windows: Task Scheduler
        task_name = "BigEdFleet"
        cmd_line = f'"{python}" "{supervisor_path}"'
        try:
            # Remove existing task if any
            subprocess.run(
                ["schtasks", "/delete", "/tn", task_name, "/f"],
                capture_output=True, timeout=10
            )
            # Create new task: run on user logon
            result = subprocess.run(
                ["schtasks", "/create", "/tn", task_name, "/tr", cmd_line,
                 "/sc", "onlogon", "/rl", "limited", "/f"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                print(f"Service installed: {task_name} (runs on login)")
                print(f"Command: {cmd_line}")
            else:
                print(f"Failed: {result.stderr.strip()}")
        except Exception as e:
            print(f"Error: {e}")

    elif sys.platform == "darwin":
        # macOS: launchd
        plist_name = "com.biged.fleet"
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{plist_name}.plist"
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{plist_name}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{supervisor_path}</string>
    </array>
    <key>WorkingDirectory</key><string>{fleet_dir}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><false/>
    <key>StandardOutPath</key><string>{fleet_dir}/logs/supervisor.log</string>
    <key>StandardErrorPath</key><string>{fleet_dir}/logs/supervisor.log</string>
</dict>
</plist>"""
        try:
            plist_path.parent.mkdir(parents=True, exist_ok=True)
            plist_path.write_text(plist_content)
            subprocess.run(["launchctl", "load", str(plist_path)], timeout=10)
            print(f"Service installed: {plist_path}")
        except Exception as e:
            print(f"Error: {e}")

    else:
        # Linux: systemd --user
        service_name = "biged-fleet"
        service_dir = Path.home() / ".config" / "systemd" / "user"
        service_path = service_dir / f"{service_name}.service"
        service_content = f"""[Unit]
Description=BigEd Fleet Supervisor
After=network.target

[Service]
Type=simple
WorkingDirectory={fleet_dir}
ExecStart={python} {supervisor_path}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""
        try:
            service_dir.mkdir(parents=True, exist_ok=True)
            service_path.write_text(service_content)
            subprocess.run(["systemctl", "--user", "daemon-reload"], timeout=10)
            subprocess.run(["systemctl", "--user", "enable", service_name], timeout=10)
            print(f"Service installed and enabled: {service_path}")
            print(f"Start now: systemctl --user start {service_name}")
        except Exception as e:
            print(f"Error: {e}")


def uninstall_service():
    """Remove fleet system service."""
    if sys.platform == "win32":
        # Windows: Task Scheduler
        task_name = "BigEdFleet"
        try:
            result = subprocess.run(
                ["schtasks", "/delete", "/tn", task_name, "/f"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                print(f"Service removed: {task_name}")
            else:
                print(f"Failed (may not exist): {result.stderr.strip()}")
        except Exception as e:
            print(f"Error: {e}")

    elif sys.platform == "darwin":
        # macOS: launchd
        plist_name = "com.biged.fleet"
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{plist_name}.plist"
        try:
            if plist_path.exists():
                subprocess.run(["launchctl", "unload", str(plist_path)],
                               capture_output=True, timeout=10)
                plist_path.unlink()
                print(f"Service removed: {plist_path}")
            else:
                print(f"Service not installed (no plist at {plist_path})")
        except Exception as e:
            print(f"Error: {e}")

    else:
        # Linux: systemd --user
        service_name = "biged-fleet"
        service_dir = Path.home() / ".config" / "systemd" / "user"
        service_path = service_dir / f"{service_name}.service"
        try:
            subprocess.run(["systemctl", "--user", "stop", service_name],
                           capture_output=True, timeout=10)
            subprocess.run(["systemctl", "--user", "disable", service_name],
                           capture_output=True, timeout=10)
            if service_path.exists():
                service_path.unlink()
            subprocess.run(["systemctl", "--user", "daemon-reload"], timeout=10)
            print(f"Service stopped, disabled, and removed: {service_path}")
        except Exception as e:
            print(f"Error: {e}")
