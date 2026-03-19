"""Knowledge file integrity — SHA-256 manifest for tamper detection."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
MANIFEST_PATH = FLEET_DIR / "data" / "integrity_manifest.json"


def compute_hash(file_path: Path) -> str:
    """SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest() -> dict:
    """Build a complete integrity manifest of knowledge/ files."""
    manifest = {"generated_at": datetime.now().isoformat(), "files": {}}
    if not KNOWLEDGE_DIR.exists():
        return manifest
    for f in sorted(KNOWLEDGE_DIR.rglob("*")):
        if f.is_file() and not f.name.startswith("."):
            rel = str(f.relative_to(FLEET_DIR))
            manifest["files"][rel] = {
                "sha256": compute_hash(f),
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
            }
    return manifest


def save_manifest() -> Path:
    """Build and save integrity manifest."""
    manifest = build_manifest()
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return MANIFEST_PATH


def verify_integrity() -> dict:
    """Verify knowledge files against saved manifest."""
    if not MANIFEST_PATH.exists():
        return {"status": "no_manifest", "message": "Run save_manifest() first"}
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    results = {"verified": 0, "modified": [], "missing": [], "new": []}

    # Check manifest files
    for rel_path, info in manifest.get("files", {}).items():
        full = FLEET_DIR / rel_path
        if not full.exists():
            results["missing"].append(rel_path)
        else:
            current_hash = compute_hash(full)
            if current_hash == info["sha256"]:
                results["verified"] += 1
            else:
                results["modified"].append({"file": rel_path, "expected": info["sha256"][:12], "actual": current_hash[:12]})

    # Check for new files not in manifest
    if KNOWLEDGE_DIR.exists():
        for f in KNOWLEDGE_DIR.rglob("*"):
            if f.is_file() and not f.name.startswith("."):
                rel = str(f.relative_to(FLEET_DIR))
                if rel not in manifest.get("files", {}):
                    results["new"].append(rel)

    results["status"] = "clean" if not results["modified"] and not results["missing"] else "tampered"
    return results
