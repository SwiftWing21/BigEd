"""
Remote Fleet Deployment — push deployment packages to federation peers.

Builds on lead_client.py export/import for local backup, adding remote push,
approval gates, status tracking, and rollback support.

v0.100.00b: Initial implementation.
"""
import hashlib
import io
import json
import logging
import os
import re
import tarfile
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("remote_deploy")
FLEET_DIR = Path(__file__).parent


# ── Deployment state (in-memory, persisted to DB for history) ─────────────

_deployments: dict[str, dict] = {}  # deploy_id -> status dict
_pending_inbound: dict[str, dict] = {}  # deploy_id -> received package info


def _generate_deploy_id() -> str:
    """Generate a unique deployment ID: deploy-<timestamp>-<hash>."""
    ts = time.strftime("%Y%m%d%H%M%S")
    h = hashlib.sha256(f"{ts}-{os.getpid()}-{time.time_ns()}".encode()).hexdigest()[:8]
    return f"deploy-{ts}-{h}"


def _sanitize_toml(content: str) -> str:
    """Strip tokens and API keys from fleet.toml content."""
    return re.sub(
        r'^((?:dashboard_token|admin_token|operator_token)\s*=\s*)".+"',
        r'\1""  # REDACTED — set after deploy',
        content, flags=re.MULTILINE
    )


def _config_hash(content: str) -> str:
    """SHA-256 of sanitized config for integrity check."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _skill_list() -> list[str]:
    """List all skill .py files in fleet/skills/."""
    skills_dir = FLEET_DIR / "skills"
    if not skills_dir.exists():
        return []
    return sorted(f.stem for f in skills_dir.glob("*.py") if f.stem != "__init__")


# ── Package creation ──────────────────────────────────────────────────────

def prepare_deployment(
    include_skills: bool = True,
    include_config: bool = True,
    include_models: bool = False,
) -> Path:
    """Create a deployment package (tarball) for pushing to peers.

    Returns the Path to the created .tar.gz file.
    """
    deploy_id = _generate_deploy_id()
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    out_dir = FLEET_DIR / "deployments"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"deployment-{timestamp}.tar.gz"

    manifest = {
        "deploy_id": deploy_id,
        "version": "1.0",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "BigEd CC Fleet",
        "include_skills": include_skills,
        "include_config": include_config,
        "include_models": include_models,
        "skills": [],
        "config_hash": "",
        "contents": [],
    }

    with tarfile.open(str(out_path), "w:gz") as tar:
        # 1. fleet.toml (sanitized)
        if include_config:
            toml_path = FLEET_DIR / "fleet.toml"
            if toml_path.exists():
                content = toml_path.read_text(encoding="utf-8")
                sanitized = _sanitize_toml(content)
                manifest["config_hash"] = _config_hash(sanitized)
                data = sanitized.encode("utf-8")
                info = tarfile.TarInfo(name="fleet.toml")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
                manifest["contents"].append("fleet.toml")

        # 2. Skills
        if include_skills:
            skills_dir = FLEET_DIR / "skills"
            if skills_dir.exists():
                for f in sorted(skills_dir.glob("*.py")):
                    arcname = f"skills/{f.name}"
                    tar.add(str(f), arcname=arcname)
                    manifest["contents"].append(arcname)
                    manifest["skills"].append(f.stem)

        # 3. Curricula
        curricula_dir = FLEET_DIR / "idle_curricula"
        if curricula_dir.exists():
            for f in sorted(curricula_dir.rglob("*")):
                if f.is_file():
                    arcname = f"curricula/{f.relative_to(curricula_dir)}"
                    tar.add(str(f), arcname=arcname)
                    manifest["contents"].append(arcname)

        # 4. Write manifest
        mdata = json.dumps(manifest, indent=2).encode("utf-8")
        minfo = tarfile.TarInfo(name="manifest.json")
        minfo.size = len(mdata)
        tar.addfile(minfo, io.BytesIO(mdata))

    _deployments[deploy_id] = {
        "deploy_id": deploy_id,
        "status": "prepared",
        "package_path": str(out_path),
        "manifest": manifest,
        "created_at": time.time(),
        "targets": [],
    }

    log.info("Deployment package prepared: %s (%d items)", deploy_id, len(manifest["contents"]))
    return out_path


# ── Push to peer ──────────────────────────────────────────────────────────

def push_to_peer(peer_url: str, package_path: Path, timeout: int = 60) -> dict:
    """Upload a deployment package to a peer fleet's /api/deploy/receive endpoint.

    Returns a dict with deploy_id and peer response.
    """
    package_path = Path(package_path)
    if not package_path.exists():
        return {"ok": False, "error": f"Package not found: {package_path}"}

    # Check max package size from config
    try:
        from config import load_config
        cfg = load_config()
        max_mb = cfg.get("federation", {}).get("deploy", {}).get("max_package_mb", 50)
    except Exception:
        max_mb = 50

    size_mb = package_path.stat().st_size / (1024 * 1024)
    if size_mb > max_mb:
        return {"ok": False, "error": f"Package too large: {size_mb:.1f} MB > {max_mb} MB limit"}

    # Read package data
    with open(package_path, "rb") as f:
        pkg_data = f.read()

    url = f"{peer_url.rstrip('/')}/api/deploy/receive"
    try:
        req = urllib.request.Request(
            url,
            data=pkg_data,
            method="POST",
            headers={
                "Content-Type": "application/gzip",
                "X-Deploy-Source": "BigEd CC Fleet",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
            # Track which peers we pushed to
            for d in _deployments.values():
                if d.get("package_path") == str(package_path):
                    d["targets"].append({"peer_url": peer_url, "result": result})
                    break
            return {"ok": True, "peer_url": peer_url, "response": result}
    except Exception as e:
        log.warning("Push to peer %s failed: %s", peer_url, e)
        return {"ok": False, "peer_url": peer_url, "error": str(e)}


def deploy_status(peer_url: str, deploy_id: str) -> dict:
    """Check deployment progress on a peer."""
    url = f"{peer_url.rstrip('/')}/api/deploy/status/{deploy_id}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.warning("Status check on %s failed: %s", peer_url, e)
        return {"ok": False, "error": str(e)}


def rollback_peer(peer_url: str, deploy_id: str) -> dict:
    """Request rollback of a deployment on a peer."""
    url = f"{peer_url.rstrip('/')}/api/deploy/rollback/{deploy_id}"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"deploy_id": deploy_id}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.warning("Rollback on %s failed: %s", peer_url, e)
        return {"ok": False, "error": str(e)}


# ── Receiving side — called by dashboard endpoints ────────────────────────

def receive_deployment(package_data: bytes) -> dict:
    """Receive and stage a deployment package (does NOT apply — needs approval).

    Returns deploy info with ID for approval/rejection.
    """
    deploy_id = _generate_deploy_id()
    staging_dir = FLEET_DIR / "deployments" / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    pkg_path = staging_dir / f"{deploy_id}.tar.gz"

    # Check max package size
    try:
        from config import load_config
        cfg = load_config()
        max_mb = cfg.get("federation", {}).get("deploy", {}).get("max_package_mb", 50)
    except Exception:
        max_mb = 50

    size_mb = len(package_data) / (1024 * 1024)
    if size_mb > max_mb:
        return {"ok": False, "error": f"Package too large: {size_mb:.1f} MB > {max_mb} MB limit"}

    # Save to staging
    with open(pkg_path, "wb") as f:
        f.write(package_data)

    # Read manifest
    try:
        with tarfile.open(str(pkg_path), "r:gz") as tar:
            # Security: path traversal check
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in member.name:
                    pkg_path.unlink(missing_ok=True)
                    return {"ok": False, "error": f"Unsafe path in archive: {member.name}"}
            mf = tar.extractfile("manifest.json")
            if mf is None:
                pkg_path.unlink(missing_ok=True)
                return {"ok": False, "error": "No manifest.json in package"}
            manifest = json.loads(mf.read())
    except Exception as e:
        pkg_path.unlink(missing_ok=True)
        return {"ok": False, "error": f"Invalid package: {e}"}

    # Check auto-approve setting
    try:
        from config import load_config
        cfg = load_config()
        auto_approve = cfg.get("federation", {}).get("deploy", {}).get("auto_approve", False)
    except Exception:
        auto_approve = False

    info = {
        "deploy_id": deploy_id,
        "status": "pending_approval" if not auto_approve else "approved",
        "received_at": time.time(),
        "package_path": str(pkg_path),
        "manifest": manifest,
        "size_mb": round(size_mb, 2),
    }
    _pending_inbound[deploy_id] = info

    if auto_approve:
        return _apply_deployment(deploy_id)

    log.info("Deployment %s staged for approval (%d items, %.1f MB)",
             deploy_id, len(manifest.get("contents", [])), size_mb)
    return {"ok": True, "deploy_id": deploy_id, "status": "pending_approval",
            "items": len(manifest.get("contents", []))}


def approve_deployment(deploy_id: str) -> dict:
    """Approve and apply a pending deployment."""
    if deploy_id not in _pending_inbound:
        return {"ok": False, "error": f"Unknown deployment: {deploy_id}"}
    info = _pending_inbound[deploy_id]
    if info["status"] not in ("pending_approval",):
        return {"ok": False, "error": f"Deployment {deploy_id} is not pending (status: {info['status']})"}
    return _apply_deployment(deploy_id)


def reject_deployment(deploy_id: str) -> dict:
    """Reject and clean up a pending deployment."""
    if deploy_id not in _pending_inbound:
        return {"ok": False, "error": f"Unknown deployment: {deploy_id}"}
    info = _pending_inbound[deploy_id]
    pkg_path = Path(info["package_path"])
    pkg_path.unlink(missing_ok=True)
    info["status"] = "rejected"
    log.info("Deployment %s rejected", deploy_id)
    return {"ok": True, "deploy_id": deploy_id, "status": "rejected"}


def _apply_deployment(deploy_id: str) -> dict:
    """Apply a staged deployment package to this fleet instance."""
    if deploy_id not in _pending_inbound:
        return {"ok": False, "error": f"Unknown deployment: {deploy_id}"}

    info = _pending_inbound[deploy_id]
    pkg_path = Path(info["package_path"])

    if not pkg_path.exists():
        return {"ok": False, "error": "Package file missing"}

    # Auto-backup before deploy (if configured)
    try:
        from config import load_config
        cfg = load_config()
        if cfg.get("federation", {}).get("deploy", {}).get("backup_before_deploy", True):
            from backup_manager import BackupManager
            bm = BackupManager(cfg)
            bm.perform_backup(trigger="pre-deploy")
            log.info("Pre-deploy backup created")
    except Exception:
        log.warning("Pre-deploy backup failed (continuing anyway)", exc_info=True)

    applied = 0
    try:
        with tarfile.open(str(pkg_path), "r:gz") as tar:
            for member in tar.getmembers():
                if member.name == "manifest.json":
                    continue
                # Security re-check
                if member.name.startswith("/") or ".." in member.name:
                    continue

                dest = FLEET_DIR / member.name
                dest.parent.mkdir(parents=True, exist_ok=True)

                # Handle curricula/ -> idle_curricula/ mapping
                if member.name.startswith("curricula/"):
                    real_name = member.name.replace("curricula/", "idle_curricula/", 1)
                    dest = FLEET_DIR / real_name
                    dest.parent.mkdir(parents=True, exist_ok=True)

                src = tar.extractfile(member)
                if src is not None:
                    dest.write_bytes(src.read())
                    applied += 1
    except Exception as e:
        info["status"] = "failed"
        info["error"] = str(e)
        log.warning("Deployment %s failed: %s", deploy_id, e)
        return {"ok": False, "deploy_id": deploy_id, "error": str(e)}

    info["status"] = "applied"
    info["applied_at"] = time.time()
    info["applied_count"] = applied
    log.info("Deployment %s applied: %d files", deploy_id, applied)

    # Persist to DB for history
    _record_deployment(deploy_id, info)

    return {"ok": True, "deploy_id": deploy_id, "status": "applied", "files_applied": applied}


def _record_deployment(deploy_id: str, info: dict):
    """Record deployment in fleet.db for history tracking."""
    try:
        import db
        def _do():
            with db.get_conn() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO deployments
                    (deploy_id, status, manifest_json, received_at, applied_at, size_mb)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    deploy_id,
                    info.get("status", "unknown"),
                    json.dumps(info.get("manifest", {})),
                    info.get("received_at", time.time()),
                    info.get("applied_at"),
                    info.get("size_mb", 0),
                ))
        db._retry_write(_do)
    except Exception:
        log.warning("Failed to record deployment %s to DB", deploy_id, exc_info=True)


# ── History / queries ─────────────────────────────────────────────────────

def get_deployment_history(limit: int = 20) -> list[dict]:
    """Return recent deployments from DB."""
    try:
        import db
        conn = db.get_conn()
        rows = conn.execute(
            "SELECT * FROM deployments ORDER BY received_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        # Table may not exist yet — return in-memory state
        combined = list(_deployments.values()) + list(_pending_inbound.values())
        combined.sort(key=lambda d: d.get("created_at", d.get("received_at", 0)), reverse=True)
        return combined[:limit]


def get_local_deploy_status(deploy_id: str) -> dict:
    """Get status of a deployment (outbound or inbound)."""
    if deploy_id in _deployments:
        return _deployments[deploy_id]
    if deploy_id in _pending_inbound:
        return _pending_inbound[deploy_id]
    return {"ok": False, "error": f"Unknown deployment: {deploy_id}"}


def get_pending_deployments() -> list[dict]:
    """List pending inbound deployments awaiting approval."""
    return [
        info for info in _pending_inbound.values()
        if info.get("status") == "pending_approval"
    ]
