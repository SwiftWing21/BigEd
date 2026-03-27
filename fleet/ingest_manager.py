"""
Ingest Manager — HuggingFace dataset fetching, caching, staging, and dispatch.

Merges config-defined and DB-defined ingest sources, fetches rows from HF
datasets-server API, caches as JSONL, stages items for review, and dispatches
to fleet tasks or RAG index.

v0.900.00b
"""

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("ingest_manager")

FLEET_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_offline():
    """Check if fleet is in offline or air-gap mode."""
    try:
        from config import load_config, is_offline, is_air_gap
        cfg = load_config()
        return is_offline(cfg) or is_air_gap(cfg)
    except Exception:
        log.warning("Could not check offline status, assuming online", exc_info=True)
        return False


def _cfg():
    """Load and return the ingest section of fleet.toml."""
    from config import load_config
    return load_config().get("ingest", {})


# ═══════════════════════════════════════════════════════════════════════════
# Source management
# ═══════════════════════════════════════════════════════════════════════════


def list_sources():
    """Merge fleet.toml sources + DB ingest_sources table.

    Returns list of dicts with keys: id, type, dataset, skill, agent_role,
    content_column, batch_size, enabled, origin ('config' | 'db').
    """
    cfg = _cfg()
    batch_default = cfg.get("batch_size_default", 50)

    # Config sources
    sources = []
    seen_ids = set()
    for s in cfg.get("sources", []):
        sid = s.get("id", "")
        seen_ids.add(sid)
        sources.append({
            "id": sid,
            "type": s.get("type", "huggingface"),
            "dataset": s.get("dataset", ""),
            "skill": s.get("skill", ""),
            "agent_role": s.get("agent_role", ""),
            "content_column": s.get("content_column", ""),
            "batch_size": s.get("batch_size", batch_default),
            "destination": s.get("destination", "tasks"),
            "enabled": bool(s.get("enabled", True)),
            "origin": "config",
        })

    # DB sources
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, type, dataset, skill, agent_role, "
                "content_column, batch_size, enabled FROM ingest_sources"
            ).fetchall()
        for r in rows:
            sid = r[0]
            if sid in seen_ids:
                continue  # config takes precedence
            seen_ids.add(sid)
            sources.append({
                "id": sid,
                "type": r[1] or "huggingface",
                "dataset": r[2] or "",
                "skill": r[3] or "",
                "agent_role": r[4] or "",
                "content_column": r[5] or "",
                "batch_size": r[6] or batch_default,
                "enabled": bool(r[7]),
                "origin": "db",
            })
    except Exception:
        log.warning("Could not read ingest_sources from DB", exc_info=True)

    return sources


def add_source(source_id, dataset, skill, agent_role="",
               content_column="", batch_size=50, source_type="huggingface"):
    """INSERT a new ingest source into fleet.db."""
    import db

    def _do():
        with db.get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ingest_sources "
                "(id, type, dataset, skill, agent_role, content_column, batch_size, enabled) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                (source_id, source_type, dataset, skill, agent_role,
                 content_column, batch_size),
            )
    db._retry_write(_do)
    log.info("Added ingest source %s (%s)", source_id, dataset)


def remove_source(source_id):
    """DELETE an ingest source from fleet.db (config sources unaffected)."""
    import db

    def _do():
        with db.get_conn() as conn:
            conn.execute("DELETE FROM ingest_sources WHERE id = ?", (source_id,))
    db._retry_write(_do)
    log.info("Removed ingest source %s", source_id)


# ═══════════════════════════════════════════════════════════════════════════
# HuggingFace API
# ═══════════════════════════════════════════════════════════════════════════

_HF_INFO_URL = "https://datasets-server.huggingface.co/info?dataset={dataset}"
_HF_ROWS_URL = ("https://datasets-server.huggingface.co/rows"
                "?dataset={dataset}&config={config}&split={split}&offset={offset}&length={length}")


def fetch_hf_schema(dataset_id):
    """Fetch column/split metadata from HuggingFace datasets-server.

    Returns dict: {columns, row_count, splits, default_split, error}.
    """
    if _is_offline():
        return {"columns": [], "row_count": 0, "splits": [],
                "default_split": None, "error": "Fleet is in offline/air-gap mode"}

    import urllib.request
    import urllib.error

    url = _HF_INFO_URL.format(dataset=dataset_id)
    try:
        resp = urllib.request.urlopen(url, timeout=15)
        data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"columns": [], "row_count": 0, "splits": [],
                    "default_split": None, "error": f"Dataset '{dataset_id}' not found"}
        if e.code in (401, 403):
            return {"columns": [], "row_count": 0, "splits": [],
                    "default_split": None, "error": f"Dataset '{dataset_id}' requires authentication"}
        if e.code == 429:
            return {"columns": [], "row_count": 0, "splits": [],
                    "default_split": None, "error": "Rate limited by HuggingFace — try again later"}
        return {"columns": [], "row_count": 0, "splits": [],
                "default_split": None, "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as exc:
        return {"columns": [], "row_count": 0, "splits": [],
                "default_split": None, "error": str(exc)}

    # Parse the info response — structure: {dataset_info: {<config>: {splits, features, ...}}}
    dataset_info = data.get("dataset_info", {})
    # Take the first config available
    default_config = next(iter(dataset_info.keys()), "default") if dataset_info else "default"
    first_config = next(iter(dataset_info.values()), {}) if dataset_info else {}

    # Extract columns from features
    features = first_config.get("features", {})
    if isinstance(features, dict):
        columns = list(features.keys())
    elif isinstance(features, list):
        columns = [f.get("name", "") for f in features if isinstance(f, dict)]
    else:
        columns = []

    # Splits
    splits_info = first_config.get("splits", {})
    if isinstance(splits_info, dict):
        splits = list(splits_info.keys())
        row_count = sum(s.get("num_examples", 0) if isinstance(s, dict) else 0
                        for s in splits_info.values())
    elif isinstance(splits_info, list):
        splits = [s.get("name", "") for s in splits_info if isinstance(s, dict)]
        row_count = sum(s.get("num_examples", 0) for s in splits_info if isinstance(s, dict))
    else:
        splits = []
        row_count = 0

    default_split = "train" if "train" in splits else (splits[0] if splits else None)

    return {
        "columns": columns,
        "row_count": row_count,
        "splits": splits,
        "default_split": default_split,
        "default_config": default_config,
        "error": None,
    }


def fetch_hf_rows(dataset_id, offset=0, length=50, split="train", config="default"):
    """Fetch rows from HuggingFace datasets-server with retry on 429.

    Returns dict: {rows, total, offset, length, error}.
    """
    if _is_offline():
        return {"rows": [], "total": 0, "offset": offset,
                "length": length, "error": "Fleet is in offline/air-gap mode"}

    import urllib.request
    import urllib.error

    url = _HF_ROWS_URL.format(
        dataset=dataset_id, config=config, split=split, offset=offset, length=length
    )

    delays = [2, 4, 8]  # exponential backoff for 429
    last_err = None

    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(url, timeout=15)
            data = json.loads(resp.read().decode())

            rows = []
            for item in data.get("rows", []):
                row = item.get("row", item)
                rows.append(row)

            total = data.get("num_rows_total", data.get("num_rows", len(rows)))
            return {
                "rows": rows,
                "total": total,
                "offset": offset,
                "length": length,
                "error": None,
            }
        except urllib.error.HTTPError as e:
            if e.code == 429:
                last_err = "Rate limited by HuggingFace"
                if attempt < 2:
                    time.sleep(delays[attempt])
                    continue
            elif e.code == 404:
                return {"rows": [], "total": 0, "offset": offset,
                        "length": length, "error": f"Dataset '{dataset_id}' not found"}
            elif e.code in (401, 403):
                return {"rows": [], "total": 0, "offset": offset,
                        "length": length, "error": f"Dataset '{dataset_id}' requires authentication"}
            else:
                last_err = f"HTTP {e.code}: {e.reason}"
                break
        except Exception as exc:
            last_err = str(exc)
            break

    return {"rows": [], "total": 0, "offset": offset,
            "length": length, "error": last_err}


# ═══════════════════════════════════════════════════════════════════════════
# Cache management
# ═══════════════════════════════════════════════════════════════════════════


def _get_cache_dir():
    """Return the cache directory Path, creating it if needed."""
    cfg = _cfg()
    rel = cfg.get("cache_dir", "knowledge/ingest_cache")
    cache_dir = FLEET_DIR / rel
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _cache_size_mb():
    """Sum all files in cache dir, return size in MB."""
    cache_dir = _get_cache_dir()
    total = sum(f.stat().st_size for f in cache_dir.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def cache_batch(source_id, rows, batch_num=None):
    """Write rows as JSONL to ingest_cache/<source>/batch_NNN.jsonl.

    Auto-detects batch_num from existing files if None.
    Checks size cap before writing.
    Updates source_meta.json with dispatched_count tracking.

    Returns dict: {path, batch_num, rows_written, error}.
    """
    cfg = _cfg()
    max_mb = cfg.get("max_storage_mb", 2048)
    current_mb = _cache_size_mb()
    if current_mb >= max_mb:
        return {"path": None, "batch_num": None, "rows_written": 0,
                "error": f"Cache cap reached ({current_mb:.1f}/{max_mb} MB)"}

    cache_dir = _get_cache_dir()
    source_dir = cache_dir / source_id
    source_dir.mkdir(parents=True, exist_ok=True)

    # Auto-detect batch number
    if batch_num is None:
        existing = sorted(source_dir.glob("batch_*.jsonl"))
        if existing:
            last_name = existing[-1].stem  # e.g. batch_003
            try:
                batch_num = int(last_name.split("_")[1]) + 1
            except (IndexError, ValueError):
                batch_num = len(existing)
        else:
            batch_num = 0

    filename = f"batch_{batch_num:03d}.jsonl"
    filepath = source_dir / filename

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("Failed to write cache batch %s", filepath, exc_info=True)
        return {"path": None, "batch_num": batch_num, "rows_written": 0,
                "error": str(exc)}

    # Update source_meta.json
    meta_path = source_dir / "source_meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    meta.setdefault("dispatched_count", 0)
    meta["total_cached_rows"] = meta.get("total_cached_rows", 0) + len(rows)
    meta["last_batch"] = batch_num
    meta["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    try:
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except Exception:
        log.warning("Failed to update source_meta.json for %s", source_id, exc_info=True)

    return {"path": str(filepath), "batch_num": batch_num,
            "rows_written": len(rows), "error": None}


def evict_processed():
    """Delete batches where dispatched_count >= total cached rows.

    Returns count of files evicted.
    """
    cache_dir = _get_cache_dir()
    evicted = 0

    for source_dir in cache_dir.iterdir():
        if not source_dir.is_dir():
            continue
        meta_path = source_dir / "source_meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        dispatched = meta.get("dispatched_count", 0)
        total = meta.get("total_cached_rows", 0)

        if dispatched > 0 and dispatched >= total:
            # Evict all batch files for this source
            for batch_file in source_dir.glob("batch_*.jsonl"):
                try:
                    batch_file.unlink()
                    evicted += 1
                except Exception:
                    log.warning("Failed to evict %s", batch_file, exc_info=True)

            # Reset meta
            meta["total_cached_rows"] = 0
            meta["dispatched_count"] = 0
            try:
                meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            except Exception:
                pass

    # Also run orphan cleanup as a secondary pass
    cleanup_orphans()

    return evicted


# Track last orphan cleanup time (module-level)
_last_orphan_cleanup: float = 0.0
_ORPHAN_CLEANUP_INTERVAL = 6 * 3600  # 6 hours


def cleanup_orphans(max_age_hours: int = 48) -> int:
    """Remove cache .jsonl files not referenced by staging and older than max_age_hours.

    Scans cache directory for all .jsonl batch files, checks if each is
    referenced by an active staging entry, and removes unreferenced files
    older than the threshold.
    """
    cache_dir = _get_cache_dir()
    if not cache_dir.exists():
        return 0

    max_age_secs = max_age_hours * 3600
    now = time.time()
    removed = 0

    # Get all file paths currently referenced in staging
    referenced_paths = set()
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute("SELECT file_path FROM ingest_staging WHERE file_path IS NOT NULL AND file_path != ''").fetchall()
        for r in rows:
            referenced_paths.add(r[0])
    except Exception:
        log.warning("Could not query staging for orphan cleanup", exc_info=True)
        return 0  # fail-safe: don't delete if we can't check references

    for f in cache_dir.rglob("*.jsonl"):
        if str(f) in referenced_paths:
            continue
        try:
            if now - f.stat().st_mtime > max_age_secs:
                f.unlink()
                removed += 1
        except Exception as e:
            log.warning("Failed to remove orphan cache file %s: %s", f, e)

    if removed:
        log.info("Cleaned up %d orphan cache files (>%dh old)", removed, max_age_hours)
    return removed


def cache_stats():
    """Return cache usage statistics."""
    cfg = _cfg()
    max_mb = cfg.get("max_storage_mb", 2048)
    used_mb = _cache_size_mb()
    cache_dir = _get_cache_dir()
    return {
        "used_mb": round(used_mb, 2),
        "max_mb": max_mb,
        "usage_pct": round((used_mb / max_mb) * 100, 1) if max_mb > 0 else 0,
        "cache_dir": str(cache_dir),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Staging
# ═══════════════════════════════════════════════════════════════════════════


def stage_items(items):
    """INSERT items into ingest_staging table.

    Each item is a dict with keys: source_id, row_id, title, content_preview,
    token_estimate, destination ('tasks' | 'rag'), skill, file_path.

    Returns list of inserted IDs.
    """
    import db
    ids = []

    def _do():
        with db.get_conn() as conn:
            for item in items:
                conn.execute(
                    "INSERT INTO ingest_staging "
                    "(source_id, row_id, title, content_preview, token_estimate, "
                    "destination, skill, file_path) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item.get("source_id", ""),
                        item.get("row_id"),
                        item.get("title", ""),
                        item.get("content_preview", ""),
                        item.get("token_estimate", 0),
                        item.get("destination", "tasks"),
                        item.get("skill", ""),
                        item.get("file_path", ""),
                    ),
                )
                row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                ids.append(row_id)

    db._retry_write(_do)
    log.info("Staged %d items", len(ids))
    return ids


def get_staging():
    """SELECT all staged items, ordered by created_at DESC."""
    import db
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, source_id, row_id, title, content_preview, "
            "token_estimate, destination, skill, file_path, created_at, "
            "COALESCE(dispatch_failures, 0) "
            "FROM ingest_staging ORDER BY created_at DESC"
        ).fetchall()

    return [
        {
            "id": r[0], "source_id": r[1], "row_id": r[2], "title": r[3],
            "content_preview": r[4], "token_estimate": r[5],
            "destination": r[6], "skill": r[7], "file_path": r[8],
            "created_at": r[9], "dispatch_failures": r[10],
        }
        for r in rows
    ]


def remove_staged(item_id):
    """DELETE a single staged item."""
    import db

    def _do():
        with db.get_conn() as conn:
            conn.execute("DELETE FROM ingest_staging WHERE id = ?", (item_id,))
    db._retry_write(_do)


def clear_staging():
    """DELETE all staged items."""
    import db

    def _do():
        with db.get_conn() as conn:
            conn.execute("DELETE FROM ingest_staging")
    db._retry_write(_do)
    log.info("Cleared all staging items")


# ═══════════════════════════════════════════════════════════════════════════
# Dispatch
# ═══════════════════════════════════════════════════════════════════════════


def _dispatch_as_task(item):
    """Create a fleet task from a staged item."""
    import db
    payload = json.dumps({
        "source": item.get("source_id", ""),
        "row_id": item.get("row_id"),
        "content": item.get("content_preview", ""),
        "title": item.get("title", ""),
    })
    skill = item.get("skill", "ingest")
    db.post_task(
        type_=skill,
        payload_json=payload,
        priority=5,
        classification="ingest",
    )


def _dispatch_to_rag(item):
    """Ingest a staged item into the RAG knowledge base."""
    from rag import RAGIndex

    content = item.get("content_preview", "")
    source_id = item.get("source_id", "unknown")
    skill = item.get("skill", "")
    source_label = f"ingest:{source_id}"

    idx = RAGIndex()
    # Insert directly into RAG FTS5 via its internal connection
    with idx._get_conn() as conn:
        conn.execute(
            "INSERT INTO chunks_meta (source, heading, text) VALUES (?, ?, ?)",
            (source_label, skill, content),
        )
        rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO chunks (rowid, text, heading, source) VALUES (?, ?, ?, ?)",
            (rowid, content, skill, source_label),
        )
    log.info("Ingested item into RAG: source=%s", source_label)


def dispatch_staged(item_ids=None):
    """Dispatch staged items to tasks or RAG based on destination.

    Args:
        item_ids: optional list of staging IDs. If None, dispatches all.

    Returns dict: {tasks, rag, errors, skipped}.
    """
    import db

    staged = get_staging()

    if item_ids is not None:
        id_set = set(item_ids)
        staged = [s for s in staged if s["id"] in id_set]

    counts = {"tasks": 0, "rag": 0, "errors": 0, "skipped": 0}

    for item in staged:
        # Skip items with too many dispatch failures
        failures = item.get("dispatch_failures", 0) or 0
        if failures >= 3:
            counts["skipped"] += 1
            continue

        try:
            dest = item.get("destination", "tasks")
            if dest == "rag":
                _dispatch_to_rag(item)
                counts["rag"] += 1
            else:
                _dispatch_as_task(item)
                counts["tasks"] += 1
            # Remove from staging on success
            remove_staged(item["id"])
        except Exception:
            log.warning("Failed to dispatch staged item %s", item.get("id"), exc_info=True)
            counts["errors"] += 1
            # Increment dispatch_failures counter
            item_id = item.get("id")
            try:
                def _inc():
                    with db.get_conn() as conn:
                        conn.execute(
                            "UPDATE ingest_staging SET dispatch_failures = dispatch_failures + 1 WHERE id = ?",
                            (item_id,),
                        )
                db._retry_write(_inc)
            except Exception:
                log.warning("Failed to increment dispatch_failures for item %s", item_id, exc_info=True)

    # Auto-remove items with >= 3 failures AND older than 24h
    try:
        def _cleanup_failed():
            with db.get_conn() as conn:
                removed = conn.execute(
                    "DELETE FROM ingest_staging WHERE dispatch_failures >= 3 "
                    "AND created_at < datetime('now', '-24 hours')"
                ).rowcount
                if removed:
                    log.warning("Auto-removed %d staging items with 3+ dispatch failures (>24h old)", removed)
        db._retry_write(_cleanup_failed)
    except Exception:
        log.warning("Failed to cleanup failed staging items", exc_info=True)

    # Periodic orphan cleanup (every 6 hours)
    global _last_orphan_cleanup
    now = time.time()
    if now - _last_orphan_cleanup > _ORPHAN_CLEANUP_INTERVAL:
        _last_orphan_cleanup = now
        try:
            cleanup_orphans()
        except Exception:
            log.warning("Orphan cleanup failed during dispatch", exc_info=True)

    log.info("Dispatch complete: %s", counts)
    return counts
