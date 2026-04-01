"""Outputs module — knowledge file browser + HITL feedback.

Provides REST endpoints for browsing knowledge/ outputs and submitting
human feedback (approve/reject/neutral). Spec: docs/superpowers/specs/2026-03-31-outputs-module-design.md
"""
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

from dashboard_utils import (
    _require_role,
    _check_rate_limit,
    get_conn,
    FLEET_DIR,
    KNOWLEDGE_DIR,
)

log = logging.getLogger("dashboard.outputs")

outputs_bp = Blueprint("outputs", __name__)

CATEGORIES = {
    "All": None,
    "Code Reviews": "code_reviews",
    "Security": "security",
    "Quality": "quality",
    "Drafts": "code_drafts",
    "Reports": "reports",
    "Evaluations": "evaluations",
    "Summaries": "summaries",
    "FMA Reviews": "fma_reviews",
    "Evolution": "evolution",
}

MAX_FILE_SIZE = 32 * 1024  # 32 KB

# ── Category cache ──────────────────────────────────────────────────────────

_cat_cache = {"data": None, "ts": 0}
_CAT_CACHE_TTL = 30


def _count_md_files(directory: Path) -> int:
    if not directory.exists():
        return 0
    count = 0
    try:
        for _root, _dirs, files in os.walk(directory):
            for f in files:
                if f.endswith(".md"):
                    count += 1
    except Exception:
        log.warning("Failed to count files in %s", directory, exc_info=True)
    return count


def _resolve_safe(rel_path: str) -> Path | None:
    """Resolve a relative path under knowledge/, returning None if unsafe."""
    if not rel_path or ".." in rel_path or rel_path.startswith(("/", "\\")):
        return None
    try:
        candidate = (KNOWLEDGE_DIR / rel_path).resolve()
        if not candidate.is_relative_to(KNOWLEDGE_DIR.resolve()):
            return None
        return candidate
    except Exception:
        return None


def _rel_path(abs_path: Path) -> str:
    try:
        return str(abs_path.relative_to(KNOWLEDGE_DIR.resolve())).replace("\\", "/")
    except ValueError:
        return str(abs_path).replace("\\", "/")


def _category_for_path(abs_path: Path) -> str:
    try:
        rel = abs_path.relative_to(KNOWLEDGE_DIR.resolve())
        top_dir = rel.parts[0] if rel.parts else ""
    except ValueError:
        return "All"
    for name, subdir in CATEGORIES.items():
        if subdir and subdir == top_dir:
            return name
    return "All"


def _glob_md_files(subdir: str | None) -> list[Path]:
    results = []
    if subdir is None:
        for _name, cat_dir in CATEGORIES.items():
            if cat_dir is None:
                continue
            cat_path = KNOWLEDGE_DIR / cat_dir
            if cat_path.exists():
                results.extend(cat_path.rglob("*.md"))
    else:
        cat_path = KNOWLEDGE_DIR / subdir
        if cat_path.exists():
            results.extend(cat_path.rglob("*.md"))
    return results


def _list_files(subdir: str | None) -> list[dict]:
    """Glob .md files, stat them, and return sorted file_infos dicts (no feedback)."""
    files = _glob_md_files(subdir)
    file_infos = []
    for fp in files:
        try:
            st = fp.stat()
            resolved = fp.resolve()
            file_infos.append({
                "_abs": str(resolved),
                "name": fp.name,
                "path": _rel_path(resolved),
                "category": _category_for_path(resolved),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
            })
        except Exception:
            continue
    file_infos.sort(key=lambda x: x["mtime"], reverse=True)
    return file_infos


# ── Endpoints ───────────────────────────────────────────────────────────────

@outputs_bp.route("/api/outputs/categories")
@_require_role("viewer")
def api_outputs_categories():
    if not _check_rate_limit("outputs_categories", max_per_min=30):
        return jsonify({"error": "Rate limit exceeded"}), 429

    now = time.time()
    if _cat_cache["data"] is not None and now - _cat_cache["ts"] < _CAT_CACHE_TTL:
        return jsonify(_cat_cache["data"])

    result = []
    total = 0
    for name, subdir in CATEGORIES.items():
        if subdir is None:
            continue
        count = _count_md_files(KNOWLEDGE_DIR / subdir)
        total += count
        result.append({"name": name, "dir": subdir, "count": count})

    result.insert(0, {"name": "All", "count": total})
    _cat_cache["data"] = result
    _cat_cache["ts"] = now
    return jsonify(result)


@outputs_bp.route("/api/outputs/files")
@_require_role("viewer")
def api_outputs_files():
    if not _check_rate_limit("outputs_files", max_per_min=30):
        return jsonify({"error": "Rate limit exceeded"}), 429

    category = request.args.get("category", "All")
    limit = min(request.args.get("limit", 50, type=int), 200)
    offset = max(request.args.get("offset", 0, type=int), 0)

    if category != "All" and category not in CATEGORIES:
        return jsonify({"error": "Unknown category"}), 400

    subdir = CATEGORIES.get(category)
    file_infos = _list_files(subdir)
    total = len(file_infos)
    page = file_infos[offset:offset + limit]

    # Bulk feedback lookup
    if page:
        import db as _db
        abs_paths = [f["_abs"] for f in page]
        feedback = _db.get_feedback_bulk(abs_paths)
        for f in page:
            f["verdict"] = feedback.get(f["_abs"])
            del f["_abs"]
    else:
        page = []

    return jsonify({"files": page, "total": total})


@outputs_bp.route("/api/outputs/file")
@_require_role("viewer")
def api_outputs_file():
    if not _check_rate_limit("outputs_file", max_per_min=120):
        return jsonify({"error": "Rate limit exceeded"}), 429

    rel = request.args.get("path", "")
    abs_path = _resolve_safe(rel)
    if abs_path is None:
        return jsonify({"error": "Invalid path"}), 400

    if not abs_path.exists() or not abs_path.is_file():
        return jsonify({"error": "File not found"}), 404

    try:
        size = abs_path.stat().st_size
        if size > MAX_FILE_SIZE:
            content = abs_path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_SIZE]
            content += "\n\n[Truncated — file exceeds 32KB]"
        else:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        log.warning("Failed to read file %s", abs_path, exc_info=True)
        return jsonify({"error": "Failed to read file"}), 500

    import db as _db
    fb = _db.get_feedback(str(abs_path))
    verdict = fb["verdict"] if fb else None
    notes = fb.get("feedback_text", "") if fb else ""

    return jsonify({
        "path": rel,
        "content": content,
        "size": size,
        "verdict": verdict,
        "feedback_notes": notes,
    })


@outputs_bp.route("/api/outputs/feedback", methods=["POST"])
@_require_role("viewer")
def api_outputs_feedback():
    if not _check_rate_limit("outputs_feedback", max_per_min=120):
        return jsonify({"error": "Rate limit exceeded"}), 429

    data = request.get_json(silent=True) or {}
    rel = data.get("path", "")
    verdict = data.get("verdict", "")
    notes = data.get("notes", "")

    if verdict not in ("approved", "rejected", "neutral"):
        return jsonify({"error": "verdict must be approved, rejected, or neutral"}), 400

    abs_path = _resolve_safe(rel)
    if abs_path is None:
        return jsonify({"error": "Invalid path"}), 400

    if not abs_path.exists():
        return jsonify({"error": "File not found"}), 404

    import db as _db

    # Get current user from session for audit trail
    reviewer = ""
    session_token = request.cookies.get("biged_session", "")
    if session_token:
        try:
            conn = get_conn()
            row = conn.execute(
                "SELECT u.display_name FROM user_sessions s JOIN user_profiles u "
                "ON s.user_id = u.id WHERE s.token = ?",
                (session_token,),
            ).fetchone()
            if row:
                reviewer = row["display_name"]
        except Exception:
            pass

    try:
        _db.submit_feedback(str(abs_path), verdict, feedback_text=notes, reviewer=reviewer)
    except Exception:
        log.warning("Failed to submit feedback for %s", abs_path, exc_info=True)
        return jsonify({"error": "Failed to save feedback"}), 500

    return jsonify({"ok": True})


@outputs_bp.route("/api/outputs/unreviewed")
@_require_role("viewer")
def api_outputs_unreviewed():
    if not _check_rate_limit("outputs_unreviewed", max_per_min=30):
        return jsonify({"error": "Rate limit exceeded"}), 429

    limit = min(request.args.get("limit", 20, type=int), 100)
    file_infos = _list_files(None)

    if file_infos:
        import db as _db
        abs_paths = [f["_abs"] for f in file_infos]
        feedback = _db.get_feedback_bulk(abs_paths)
        unreviewed = [f for f in file_infos if f["_abs"] not in feedback]
    else:
        unreviewed = []

    total = len(unreviewed)
    page = unreviewed[:limit]

    for f in page:
        try:
            text = Path(f["_abs"]).read_text(encoding="utf-8", errors="replace")[:200]
            f["snippet"] = text
        except Exception:
            f["snippet"] = ""
        del f["_abs"]

    return jsonify({"files": page, "total": total})
