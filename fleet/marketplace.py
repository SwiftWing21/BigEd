"""Marketplace — package catalog, reviews, publisher verification, install/uninstall.

v0.400.00b: Full marketplace with ratings, verified publishers, and per-tenant
package deployment. Dashboard endpoints registered as a Flask Blueprint.

All DB writes use db._retry_write() for WAL busy handling.
"""
import json
import logging
import shutil
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

from security import (
    require_role as _require_role_raw,
    safe_error as _safe_error,
)

FLEET_DIR = Path(__file__).parent
TENANTS_DIR = FLEET_DIR / "tenants"

log = logging.getLogger("marketplace")

marketplace_bp = Blueprint("marketplace", __name__)

PAGE_SIZE = 20

# ── Config ────────────────────────────────────────────────────────────────────


def _load_config():
    """Load fleet.toml config."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return {}
    try:
        with open(FLEET_DIR / "fleet.toml", "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _marketplace_config() -> dict:
    """Return [marketplace] config with defaults."""
    cfg = _load_config()
    mcfg = cfg.get("marketplace", {})
    return {
        "enabled": mcfg.get("enabled", False),
        "require_review_before_publish": mcfg.get("require_review_before_publish", True),
        "allow_unverified_publishers": mcfg.get("allow_unverified_publishers", False),
        "max_package_size_mb": mcfg.get("max_package_size_mb", 25),
    }


def _require_role(role):
    """Convenience wrapper for role-based access."""
    return _require_role_raw(role, _load_config)


# ── DB schema ─────────────────────────────────────────────────────────────────

MARKETPLACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketplace_packages (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT,
    publisher_id TEXT,
    version TEXT,
    skill_names TEXT,
    downloads INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS marketplace_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
    comment TEXT,
    created_at REAL,
    UNIQUE(package_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS marketplace_publishers (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT,
    verified INTEGER DEFAULT 0,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS marketplace_installs (
    package_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    installed_at REAL,
    version TEXT,
    PRIMARY KEY (package_id, tenant_id)
);
"""


def _get_conn():
    """Get a connection to the main fleet DB."""
    import db
    return db.get_conn()


def _ensure_tables():
    """Create marketplace tables if they don't exist."""
    import db

    def _do():
        conn = _get_conn()
        conn.executescript(MARKETPLACE_SCHEMA)

    db._retry_write(_do)


# ── Catalog management ────────────────────────────────────────────────────────


def list_packages(category: str | None = None, search: str | None = None,
                  page: int = 1) -> dict:
    """Paginated package listing with optional category/search filter.

    Returns dict with keys: packages, page, total, pages.
    """
    _ensure_tables()
    conn = _get_conn()

    where_parts = ["status = 'approved'"]
    params: list = []

    if category:
        where_parts.append("category = ?")
        params.append(category)
    if search:
        where_parts.append("(name LIKE ? OR description LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_clause = " AND ".join(where_parts)

    total = conn.execute(
        f"SELECT COUNT(*) FROM marketplace_packages WHERE {where_clause}",
        params,
    ).fetchone()[0]

    offset = (max(1, page) - 1) * PAGE_SIZE
    rows = conn.execute(
        f"SELECT * FROM marketplace_packages WHERE {where_clause} "
        f"ORDER BY downloads DESC, created_at DESC LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, offset],
    ).fetchall()

    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return {
        "packages": [dict(r) for r in rows],
        "page": page,
        "total": total,
        "pages": pages,
    }


def get_package(package_id: str) -> dict | None:
    """Full package details including average rating and review count."""
    _ensure_tables()
    conn = _get_conn()

    row = conn.execute(
        "SELECT * FROM marketplace_packages WHERE id = ?", (package_id,)
    ).fetchone()
    if not row:
        return None

    pkg = dict(row)

    # Attach rating summary
    rating_row = conn.execute(
        "SELECT AVG(rating) as avg_rating, COUNT(*) as review_count "
        "FROM marketplace_reviews WHERE package_id = ?",
        (package_id,),
    ).fetchone()
    pkg["avg_rating"] = round(rating_row["avg_rating"], 2) if rating_row["avg_rating"] else 0.0
    pkg["review_count"] = rating_row["review_count"]

    # Attach publisher info
    if pkg.get("publisher_id"):
        pub_row = conn.execute(
            "SELECT name, verified FROM marketplace_publishers WHERE id = ?",
            (pkg["publisher_id"],),
        ).fetchone()
        if pub_row:
            pkg["publisher_name"] = pub_row["name"]
            pkg["publisher_verified"] = bool(pub_row["verified"])

    return pkg


def publish_package(manifest: dict) -> str:
    """Submit a new package for review. Returns package_id.

    Required manifest keys: name, description, category, publisher_id, version, skill_names.
    """
    import db

    _ensure_tables()
    mcfg = _marketplace_config()

    required = {"name", "description", "category", "publisher_id", "version", "skill_names"}
    missing = required - set(manifest.keys())
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(sorted(missing))}")

    # Validate publisher exists
    conn = _get_conn()
    pub = conn.execute(
        "SELECT verified FROM marketplace_publishers WHERE id = ?",
        (manifest["publisher_id"],),
    ).fetchone()
    if not pub:
        raise ValueError(f"Publisher {manifest['publisher_id']} not found")
    if not mcfg["allow_unverified_publishers"] and not pub["verified"]:
        raise ValueError("Only verified publishers can submit packages")

    package_id = uuid.uuid4().hex[:12]
    now = time.time()
    status = "pending" if mcfg["require_review_before_publish"] else "approved"

    skill_names = manifest["skill_names"]
    if isinstance(skill_names, list):
        skill_names = json.dumps(skill_names)

    def _do():
        c = _get_conn()
        c.execute(
            "INSERT INTO marketplace_packages "
            "(id, name, description, category, publisher_id, version, skill_names, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (package_id, manifest["name"], manifest["description"],
             manifest["category"], manifest["publisher_id"],
             manifest["version"], skill_names, status, now, now),
        )
        c.commit()

    db._retry_write(_do)
    log.info("Package %s published by %s (status=%s)", package_id, manifest["publisher_id"], status)
    return package_id


def update_package(package_id: str, updates: dict) -> bool:
    """Update an existing package. Returns True on success."""
    import db

    _ensure_tables()

    allowed = {"name", "description", "category", "version", "skill_names", "status"}
    set_parts = []
    params: list = []

    for key, val in updates.items():
        if key not in allowed:
            continue
        if key == "skill_names" and isinstance(val, list):
            val = json.dumps(val)
        set_parts.append(f"{key} = ?")
        params.append(val)

    if not set_parts:
        return False

    set_parts.append("updated_at = ?")
    params.append(time.time())
    params.append(package_id)

    def _do():
        c = _get_conn()
        c.execute(
            f"UPDATE marketplace_packages SET {', '.join(set_parts)} WHERE id = ?",
            params,
        )
        c.commit()

    db._retry_write(_do)
    log.info("Package %s updated: %s", package_id, list(updates.keys()))
    return True


# ── Reviews and ratings ───────────────────────────────────────────────────────


def submit_review(package_id: str, tenant_id: str, rating: int, comment: str = "") -> str:
    """Submit or update a review (1-5 stars). One review per tenant per package.

    Returns review id (as string).
    """
    import db

    _ensure_tables()

    if not (1 <= rating <= 5):
        raise ValueError("Rating must be between 1 and 5")

    # Verify package exists
    conn = _get_conn()
    pkg = conn.execute(
        "SELECT id FROM marketplace_packages WHERE id = ?", (package_id,)
    ).fetchone()
    if not pkg:
        raise ValueError(f"Package {package_id} not found")

    now = time.time()
    review_id = None

    def _do():
        nonlocal review_id
        c = _get_conn()
        # Upsert: replace existing review for this tenant+package
        c.execute(
            "INSERT INTO marketplace_reviews (package_id, tenant_id, rating, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(package_id, tenant_id) DO UPDATE SET rating=excluded.rating, "
            "comment=excluded.comment, created_at=excluded.created_at",
            (package_id, tenant_id, rating, comment, now),
        )
        c.commit()
        row = c.execute(
            "SELECT id FROM marketplace_reviews WHERE package_id = ? AND tenant_id = ?",
            (package_id, tenant_id),
        ).fetchone()
        review_id = str(row["id"]) if row else "unknown"

    db._retry_write(_do)
    log.info("Review submitted for package %s by tenant %s: %d stars", package_id, tenant_id, rating)
    return review_id


def get_reviews(package_id: str, page: int = 1) -> dict:
    """Paginated reviews for a package."""
    _ensure_tables()
    conn = _get_conn()

    total = conn.execute(
        "SELECT COUNT(*) FROM marketplace_reviews WHERE package_id = ?",
        (package_id,),
    ).fetchone()[0]

    offset = (max(1, page) - 1) * PAGE_SIZE
    rows = conn.execute(
        "SELECT * FROM marketplace_reviews WHERE package_id = ? "
        "ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (package_id, PAGE_SIZE, offset),
    ).fetchall()

    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return {
        "reviews": [dict(r) for r in rows],
        "page": page,
        "total": total,
        "pages": pages,
    }


def get_average_rating(package_id: str) -> float:
    """Average rating for a package (0.0 if no reviews)."""
    _ensure_tables()
    conn = _get_conn()
    row = conn.execute(
        "SELECT AVG(rating) as avg_rating FROM marketplace_reviews WHERE package_id = ?",
        (package_id,),
    ).fetchone()
    return round(row["avg_rating"], 2) if row and row["avg_rating"] else 0.0


# ── Publisher verification ────────────────────────────────────────────────────


def register_publisher(tenant_id: str, name: str, url: str = "") -> dict:
    """Register a new publisher. Returns publisher info dict."""
    import db

    _ensure_tables()

    publisher_id = uuid.uuid4().hex[:12]
    now = time.time()

    def _do():
        c = _get_conn()
        # Check for duplicate tenant_id publisher
        existing = c.execute(
            "SELECT id FROM marketplace_publishers WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if existing:
            raise ValueError(f"Tenant {tenant_id} already has a publisher profile: {existing['id']}")
        c.execute(
            "INSERT INTO marketplace_publishers (id, tenant_id, name, url, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (publisher_id, tenant_id, name, url, now),
        )
        c.commit()

    db._retry_write(_do)
    log.info("Publisher %s registered for tenant %s", publisher_id, tenant_id)

    return {
        "id": publisher_id,
        "tenant_id": tenant_id,
        "name": name,
        "url": url,
        "verified": False,
        "created_at": now,
    }


def verify_publisher(publisher_id: str) -> bool:
    """Admin action: mark publisher as verified. Returns True on success."""
    import db

    _ensure_tables()

    def _do():
        c = _get_conn()
        result = c.execute(
            "UPDATE marketplace_publishers SET verified = 1 WHERE id = ?",
            (publisher_id,),
        )
        c.commit()
        return result.rowcount > 0

    success = db._retry_write(_do)
    if success:
        log.info("Publisher %s verified", publisher_id)
    return success


def get_publisher(publisher_id: str) -> dict | None:
    """Publisher profile with their packages."""
    _ensure_tables()
    conn = _get_conn()

    row = conn.execute(
        "SELECT * FROM marketplace_publishers WHERE id = ?", (publisher_id,)
    ).fetchone()
    if not row:
        return None

    pub = dict(row)
    pub["verified"] = bool(pub["verified"])

    # Attach packages
    packages = conn.execute(
        "SELECT id, name, version, status, downloads, category FROM marketplace_packages "
        "WHERE publisher_id = ? ORDER BY created_at DESC",
        (publisher_id,),
    ).fetchall()
    pub["packages"] = [dict(p) for p in packages]

    return pub


# ── Install / uninstall ──────────────────────────────────────────────────────


def install_package(tenant_id: str, package_id: str) -> dict:
    """Install a package for a tenant. Returns install info.

    Increments download counter and records the install in marketplace_installs.
    Copies package skills to the tenant's skill directory.
    """
    import db

    _ensure_tables()

    # Verify package exists and is approved
    conn = _get_conn()
    pkg = conn.execute(
        "SELECT * FROM marketplace_packages WHERE id = ?", (package_id,)
    ).fetchone()
    if not pkg:
        raise ValueError(f"Package {package_id} not found")
    if pkg["status"] != "approved":
        raise ValueError(f"Package {package_id} is not approved (status={pkg['status']})")

    # Check if already installed
    existing = conn.execute(
        "SELECT 1 FROM marketplace_installs WHERE package_id = ? AND tenant_id = ?",
        (package_id, tenant_id),
    ).fetchone()
    if existing:
        raise ValueError(f"Package {package_id} already installed for tenant {tenant_id}")

    now = time.time()
    version = pkg["version"]

    def _do():
        c = _get_conn()
        c.execute(
            "INSERT INTO marketplace_installs (package_id, tenant_id, installed_at, version) "
            "VALUES (?, ?, ?, ?)",
            (package_id, tenant_id, now, version),
        )
        c.execute(
            "UPDATE marketplace_packages SET downloads = downloads + 1 WHERE id = ?",
            (package_id,),
        )
        c.commit()

    db._retry_write(_do)

    # Deploy skills to tenant directory
    skill_names_raw = pkg["skill_names"]
    try:
        skill_list = json.loads(skill_names_raw) if skill_names_raw else []
    except (json.JSONDecodeError, TypeError):
        skill_list = [s.strip() for s in (skill_names_raw or "").split(",") if s.strip()]

    deployed = []
    tenant_skills_dir = TENANTS_DIR / tenant_id / "skills"
    tenant_skills_dir.mkdir(parents=True, exist_ok=True)

    for skill_name in skill_list:
        src = FLEET_DIR / "skills" / f"{skill_name}.py"
        if src.exists():
            dst = tenant_skills_dir / f"{skill_name}.py"
            try:
                shutil.copy2(str(src), str(dst))
                deployed.append(skill_name)
            except Exception:
                log.warning("Failed to deploy skill %s to tenant %s", skill_name, tenant_id, exc_info=True)

    log.info("Package %s installed for tenant %s (skills: %s)", package_id, tenant_id, deployed)
    return {
        "package_id": package_id,
        "tenant_id": tenant_id,
        "version": version,
        "installed_at": now,
        "skills_deployed": deployed,
    }


def uninstall_package(tenant_id: str, package_id: str) -> bool:
    """Remove a package from a tenant. Returns True on success."""
    import db

    _ensure_tables()

    # Get package info for skill cleanup
    conn = _get_conn()
    pkg = conn.execute(
        "SELECT skill_names FROM marketplace_packages WHERE id = ?", (package_id,)
    ).fetchone()

    def _do():
        c = _get_conn()
        result = c.execute(
            "DELETE FROM marketplace_installs WHERE package_id = ? AND tenant_id = ?",
            (package_id, tenant_id),
        )
        c.commit()
        return result.rowcount > 0

    removed = db._retry_write(_do)

    # Clean up skill files from tenant directory
    if removed and pkg and pkg["skill_names"]:
        try:
            skill_list = json.loads(pkg["skill_names"])
        except (json.JSONDecodeError, TypeError):
            skill_list = [s.strip() for s in (pkg["skill_names"] or "").split(",") if s.strip()]

        tenant_skills_dir = TENANTS_DIR / tenant_id / "skills"
        for skill_name in skill_list:
            skill_path = tenant_skills_dir / f"{skill_name}.py"
            try:
                if skill_path.exists():
                    skill_path.unlink()
            except Exception:
                log.warning("Failed to remove skill %s from tenant %s", skill_name, tenant_id, exc_info=True)

    if removed:
        log.info("Package %s uninstalled from tenant %s", package_id, tenant_id)
    return removed


def get_installed(tenant_id: str) -> list:
    """List installed packages for a tenant, with package details."""
    _ensure_tables()
    conn = _get_conn()

    rows = conn.execute(
        "SELECT mi.package_id, mi.installed_at, mi.version, "
        "mp.name, mp.description, mp.category, mp.publisher_id "
        "FROM marketplace_installs mi "
        "JOIN marketplace_packages mp ON mi.package_id = mp.id "
        "WHERE mi.tenant_id = ? "
        "ORDER BY mi.installed_at DESC",
        (tenant_id,),
    ).fetchall()

    return [dict(r) for r in rows]


# ── Dashboard endpoints ───────────────────────────────────────────────────────


@marketplace_bp.route("/api/marketplace/packages")
def api_marketplace_packages():
    """List packages — supports ?category=, ?search=, ?page= query params."""
    try:
        category = request.args.get("category")
        search = request.args.get("search")
        page = int(request.args.get("page", 1))
        return jsonify(list_packages(category=category, search=search, page=page))
    except Exception as e:
        log.warning("list_packages failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@marketplace_bp.route("/api/marketplace/packages/<package_id>")
def api_marketplace_package_detail(package_id):
    """Full package details with rating summary."""
    try:
        pkg = get_package(package_id)
        if not pkg:
            return jsonify({"error": "Package not found"}), 404
        return jsonify(pkg)
    except Exception as e:
        log.warning("get_package failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@marketplace_bp.route("/api/marketplace/packages", methods=["POST"])
def api_marketplace_publish():
    """Publish a new package. Requires JSON body with manifest."""
    try:
        err = _require_role("operator")
        if err:
            return err
        data = request.get_json()
        if not data:
            return jsonify({"error": "JSON body required"}), 400
        mcfg = _marketplace_config()
        if not mcfg["enabled"]:
            return jsonify({"error": "Marketplace is disabled"}), 403
        package_id = publish_package(data)
        return jsonify({"status": "published", "package_id": package_id}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("publish_package failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@marketplace_bp.route("/api/marketplace/packages/<package_id>", methods=["PUT"])
def api_marketplace_update(package_id):
    """Update an existing package (operator+)."""
    try:
        err = _require_role("operator")
        if err:
            return err
        data = request.get_json()
        if not data:
            return jsonify({"error": "JSON body required"}), 400
        if update_package(package_id, data):
            return jsonify({"status": "updated", "package_id": package_id})
        return jsonify({"error": "No valid fields to update"}), 400
    except Exception as e:
        log.warning("update_package failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@marketplace_bp.route("/api/marketplace/packages/<package_id>/reviews")
def api_marketplace_reviews(package_id):
    """Paginated reviews for a package."""
    try:
        page = int(request.args.get("page", 1))
        return jsonify(get_reviews(package_id, page=page))
    except Exception as e:
        log.warning("get_reviews failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@marketplace_bp.route("/api/marketplace/packages/<package_id>/reviews", methods=["POST"])
def api_marketplace_submit_review(package_id):
    """Submit a review (1-5 stars + comment)."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "JSON body required"}), 400
        tenant_id = data.get("tenant_id")
        rating = data.get("rating")
        comment = data.get("comment", "")
        if not tenant_id or rating is None:
            return jsonify({"error": "tenant_id and rating are required"}), 400
        review_id = submit_review(package_id, tenant_id, int(rating), comment)
        return jsonify({"status": "submitted", "review_id": review_id}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("submit_review failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@marketplace_bp.route("/api/marketplace/packages/<package_id>/install", methods=["POST"])
def api_marketplace_install(package_id):
    """Install a package for a tenant."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "JSON body required"}), 400
        tenant_id = data.get("tenant_id")
        if not tenant_id:
            return jsonify({"error": "tenant_id is required"}), 400
        mcfg = _marketplace_config()
        if not mcfg["enabled"]:
            return jsonify({"error": "Marketplace is disabled"}), 403
        result = install_package(tenant_id, package_id)
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("install_package failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@marketplace_bp.route("/api/marketplace/packages/<package_id>/install", methods=["DELETE"])
def api_marketplace_uninstall(package_id):
    """Uninstall a package from a tenant."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "JSON body required"}), 400
        tenant_id = data.get("tenant_id")
        if not tenant_id:
            return jsonify({"error": "tenant_id is required"}), 400
        if uninstall_package(tenant_id, package_id):
            return jsonify({"status": "uninstalled", "package_id": package_id, "tenant_id": tenant_id})
        return jsonify({"error": "Package not installed for this tenant"}), 404
    except Exception as e:
        log.warning("uninstall_package failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@marketplace_bp.route("/api/marketplace/installed")
def api_marketplace_installed():
    """List installed packages for a tenant. Requires ?tenant_id= query param."""
    try:
        tenant_id = request.args.get("tenant_id")
        if not tenant_id:
            return jsonify({"error": "tenant_id query param required"}), 400
        return jsonify({"packages": get_installed(tenant_id)})
    except Exception as e:
        log.warning("get_installed failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@marketplace_bp.route("/api/marketplace/publishers", methods=["POST"])
def api_marketplace_register_publisher():
    """Register as a publisher."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "JSON body required"}), 400
        tenant_id = data.get("tenant_id")
        name = data.get("name")
        if not tenant_id or not name:
            return jsonify({"error": "tenant_id and name are required"}), 400
        mcfg = _marketplace_config()
        if not mcfg["enabled"]:
            return jsonify({"error": "Marketplace is disabled"}), 403
        pub = register_publisher(tenant_id, name, data.get("url", ""))
        return jsonify(pub), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        log.warning("register_publisher failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@marketplace_bp.route("/api/marketplace/publishers/<publisher_id>")
def api_marketplace_publisher_detail(publisher_id):
    """Publisher profile with packages."""
    try:
        pub = get_publisher(publisher_id)
        if not pub:
            return jsonify({"error": "Publisher not found"}), 404
        return jsonify(pub)
    except Exception as e:
        log.warning("get_publisher failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500


@marketplace_bp.route("/api/marketplace/publishers/<publisher_id>/verify", methods=["POST"])
def api_marketplace_verify_publisher(publisher_id):
    """Admin action: verify a publisher."""
    try:
        err = _require_role("admin")
        if err:
            return err
        if verify_publisher(publisher_id):
            return jsonify({"status": "verified", "publisher_id": publisher_id})
        return jsonify({"error": "Publisher not found"}), 404
    except Exception as e:
        log.warning("verify_publisher failed: %s", e, exc_info=True)
        return jsonify({"error": _safe_error(e)}), 500
