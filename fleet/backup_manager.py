"""
BigEd CC — Auto-Save & Backup System.

Configurable via fleet.toml [backup] section.
Default: every 5 minutes, keep last 10, ~/BigEd-backups/
"""
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent
DEFAULT_LOCATION = Path.home() / "BigEd-backups"


class BackupManager:
    def __init__(self, config: dict = None):
        cfg = (config or {}).get("backup", {})
        self.enabled = cfg.get("enabled", True)
        self.interval = cfg.get("interval_secs", 300)
        self.location = Path(os.path.expanduser(cfg.get("location", str(DEFAULT_LOCATION))))
        self.depth = cfg.get("depth", 10)
        self.prune_enabled = cfg.get("prune_enabled", True)
        self.warn_pct = cfg.get("warn_disk_usage_pct", 80)
        self.verify = cfg.get("safety", {}).get("verify_integrity", True)

        targets = cfg.get("targets", {})
        self.backup_fleet_db = targets.get("fleet_db", True)
        self.backup_rag_db = targets.get("rag_db", True)
        self.backup_config = targets.get("config", True)
        self.backup_knowledge = targets.get("knowledge", True)

        self._running = False
        self._thread = None

    def perform_backup(self, trigger: str = "manual") -> dict:
        """Execute a single backup. Returns manifest dict."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.location / ts
        backup_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "id": ts,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "trigger": trigger,
            "location": str(backup_dir),
            "files": {},
            "integrity": {},
            "total_size_bytes": 0,
        }

        # Checkpoint WAL before backup
        self._checkpoint_wal()

        # Backup targets
        if self.backup_fleet_db:
            self._backup_file(FLEET_DIR / "fleet.db", backup_dir, manifest)
        if self.backup_rag_db:
            self._backup_file(FLEET_DIR / "rag.db", backup_dir, manifest)
        if self.backup_config:
            self._backup_file(FLEET_DIR / "fleet.toml", backup_dir, manifest)
        if self.backup_knowledge:
            self._backup_dir(FLEET_DIR / "knowledge", backup_dir / "knowledge", manifest)

        # Integrity check
        if self.verify:
            for db_name in ["fleet.db", "rag.db"]:
                db_path = backup_dir / db_name
                if db_path.exists():
                    ok = self._verify_db(db_path)
                    manifest["integrity"][db_name] = "ok" if ok else "FAILED"

        # Write manifest
        manifest_path = backup_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # Prune old backups
        if self.prune_enabled:
            self._prune()

        return manifest

    def _backup_file(self, src: Path, dest_dir: Path, manifest: dict):
        if not src.exists():
            return
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        size = dest.stat().st_size
        manifest["files"][src.name] = {
            "size_bytes": size,
            "sha256": self._file_hash(dest),
        }
        manifest["total_size_bytes"] += size

    def _backup_dir(self, src: Path, dest: Path, manifest: dict):
        if not src.exists():
            return
        total = 0
        count = 0
        shutil.copytree(src, dest, dirs_exist_ok=True)
        for f in dest.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
                count += 1
        manifest["files"]["knowledge/"] = {"size_bytes": total, "file_count": count}
        manifest["total_size_bytes"] += total

    def _checkpoint_wal(self):
        for db_name in ["fleet.db", "rag.db"]:
            db_path = FLEET_DIR / db_name
            if db_path.exists():
                try:
                    conn = sqlite3.connect(str(db_path), timeout=5)
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    conn.close()
                except Exception:
                    pass

    def _verify_db(self, db_path: Path) -> bool:
        try:
            conn = sqlite3.connect(str(db_path), timeout=10)
            result = conn.execute("PRAGMA integrity_check").fetchone()
            conn.close()
            return result[0] == "ok"
        except Exception:
            return False

    def _file_hash(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]

    def _prune(self):
        if not self.location.exists():
            return
        backups = sorted(
            [d for d in self.location.iterdir() if d.is_dir() and (d / "manifest.json").exists()],
            key=lambda d: d.name,
            reverse=True,
        )
        for old in backups[self.depth:]:
            shutil.rmtree(old, ignore_errors=True)

    def list_backups(self) -> list:
        if not self.location.exists():
            return []
        backups = []
        for d in sorted(self.location.iterdir(), reverse=True):
            manifest_path = d / "manifest.json"
            if manifest_path.exists():
                try:
                    m = json.loads(manifest_path.read_text())
                    m["_dir"] = str(d)
                    backups.append(m)
                except Exception:
                    pass
        return backups

    def get_disk_warning(self) -> str | None:
        if not self.location.exists():
            return None
        total, used, free = shutil.disk_usage(str(self.location))
        backup_size = sum(
            f.stat().st_size for f in self.location.rglob("*") if f.is_file()
        )
        if backup_size > 0 and (backup_size / free) > (self.warn_pct / 100):
            return f"Backups using {backup_size/1024/1024:.0f}MB, {free/1024/1024:.0f}MB free"
        return None

    # Auto-save thread
    def start_auto_save(self):
        if not self.enabled or self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._auto_save_loop, daemon=True)
        self._thread.start()

    def stop_auto_save(self):
        self._running = False

    def _auto_save_loop(self):
        while self._running:
            try:
                self.perform_backup(trigger="auto_save")
            except Exception:
                pass
            # Sleep in small increments so we can stop quickly
            for _ in range(self.interval):
                if not self._running:
                    break
                time.sleep(1)
