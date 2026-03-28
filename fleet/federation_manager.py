#!/usr/bin/env python3
"""Federation manager — cross-fleet peer communication.

Extracted from supervisor.py during restructure. Handles heartbeat
broadcast, rejoin announcement, mesh discovery, and mTLS setup.
"""

import json
import logging
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("supervisor")

FLEET_DIR = Path(__file__).parent


class FederationManager:
    """Cross-fleet peer communication."""

    def __init__(self, config: dict, pm):
        self.config = config
        self.pm = pm  # ProcessManager
        self._last_heartbeat: float = 0

    def update_config(self, config: dict) -> None:
        self.config = config

    def tick(self, now: float) -> None:
        """Broadcast status to peers (every 60s)."""
        try:
            self._broadcast_heartbeat(now)
        except Exception:
            log.warning("Federation heartbeat failed", exc_info=True)

    def announce_rejoin(self, roles: list) -> None:
        """Announce rejoin to peers on startup (crash recovery)."""
        from config import is_offline
        federation_cfg = self.config.get("federation", {})
        if not federation_cfg.get("enabled") or is_offline(self.config):
            return

        device_name = self.config.get("naming", {}).get("device_name", "unknown")
        peers = federation_cfg.get("peers", [])

        ssl_ctx = self._get_ssl_context()
        for peer_url in peers:
            try:
                rejoin_data = json.dumps({
                    "fleet_id": device_name,
                    "agents": len(roles),
                    "pending": self._count_pending(),
                    "event": "rejoin",
                    "timestamp": time.time(),
                }).encode()
                req = urllib.request.Request(
                    f"{peer_url}/api/federation/heartbeat",
                    data=rejoin_data, method="POST",
                    headers={"Content-Type": "application/json"})
                if ssl_ctx:
                    urllib.request.urlopen(req, timeout=5, context=ssl_ctx)
                else:
                    urllib.request.urlopen(req, timeout=5)
                log.info(f"Federation: rejoined peer {peer_url}")
            except Exception:
                log.debug(f"Federation: peer {peer_url} unreachable (will retry in heartbeat loop)")

    def start_discovery(self) -> None:
        """Start mesh auto-discovery (UDP broadcast + mDNS)."""
        federation_cfg = self.config.get("federation", {})
        if not federation_cfg.get("discovery_enabled", True):
            return
        try:
            import discovery
            dashboard_port = self.config.get("dashboard", {}).get("port", 5555)
            discovery.start_discovery(port=dashboard_port)
            log.info("Federation: mesh auto-discovery started")
        except Exception:
            log.warning("Federation: auto-discovery failed to start", exc_info=True)

    def setup_tls(self) -> None:
        """Deferred mTLS auto-setup."""
        try:
            from fleet_tls import auto_setup as _tls_auto_setup
            _tls_auto_setup()
        except Exception:
            pass

    # ── Internal ────────────────────────────────────────────────────

    def _get_ssl_context(self):
        try:
            from fleet_tls import is_tls_enabled, get_ssl_context
            if is_tls_enabled():
                return get_ssl_context("client")
        except Exception:
            pass
        return None

    def _count_pending(self) -> int:
        try:
            from db import get_conn
            with get_conn() as conn:
                row = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='PENDING'").fetchone()
                return row[0] if row else 0
        except Exception:
            return 0

    def _broadcast_heartbeat(self, now: float) -> None:
        if now - self._last_heartbeat < 60:
            return
        self._last_heartbeat = now

        federation_cfg = self.config.get("federation", {})
        if not federation_cfg.get("enabled"):
            return

        # GPU capacity info
        gpu_count = 0
        total_vram = 0.0
        try:
            from hw_supervisor import detect_gpu_config
            gpu_info = detect_gpu_config()
            gpu_count = gpu_info.get("gpu_count", 0)
            total_vram = gpu_info.get("total_vram_gb", 0.0)
        except Exception:
            pass

        # Peer list: auto-discovered + manual
        try:
            import discovery
            all_peers = discovery.get_all_peers()
            peer_urls = [p["url"] for p in all_peers]
        except Exception:
            peer_urls = federation_cfg.get("peers", [])

        ssl_ctx = self._get_ssl_context()
        for peer_url in peer_urls:
            try:
                status = {
                    "fleet_id": self.config.get("naming", {}).get("device_name", ""),
                    "agents": len(self.pm.get_running_workers()),
                    "pending": self._count_pending(),
                    "gpu_count": gpu_count,
                    "total_vram_gb": total_vram,
                    "timestamp": time.time(),
                }
                body = json.dumps(status).encode()
                req = urllib.request.Request(
                    f"{peer_url}/api/federation/heartbeat",
                    data=body, method="POST",
                    headers={"Content-Type": "application/json"})
                if ssl_ctx:
                    urllib.request.urlopen(req, timeout=3, context=ssl_ctx)
                else:
                    urllib.request.urlopen(req, timeout=3)
            except Exception:
                pass
