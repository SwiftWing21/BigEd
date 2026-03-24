"""
RAG engine — indexes .md files into SQLite FTS5 for retrieval-augmented generation.

No external dependencies — uses Python's built-in sqlite3 with FTS5.
Chunks documents by heading sections, stores metadata, and provides
BM25-ranked search results.  Optionally supports vector search and
reranking when sentence-transformers models are present in fleet/models/.

Usage:
    from rag import RAGIndex
    idx = RAGIndex()
    idx.rebuild()                        # full re-index
    idx.update()                         # incremental (changed files only)
    results = idx.search("fleet GPU")    # BM25-ranked chunks
    results = idx.hybrid_search("GPU")   # BM25 + vector (if model present)
    results = idx.rerank("GPU", results) # cross-encoder rerank (if model present)
"""
import hashlib
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("rag")

FLEET_DIR = Path(__file__).parent
PROJECT_DIR = FLEET_DIR.parent
RAG_DB = FLEET_DIR / "rag.db"

# Directories to index (relative to PROJECT_DIR), with recursive glob
SCAN_PATHS = [
    (".", "*.md"),                              # project root .md files
    ("fleet", "*.md"),                          # fleet root .md files
    ("fleet/knowledge", "**/*.md"),             # all knowledge outputs
    ("BigEd", "*.md"),                      # reference docs
    ("autoresearch", "*.md"),                   # autoresearch docs
]

# Skip patterns
SKIP_PATTERNS = [".git", "node_modules", ".venv", "__pycache__", "dist"]

# Chunk config
MAX_CHUNK_CHARS = 1500   # target chunk size
MIN_CHUNK_CHARS = 80     # skip trivially small chunks
OVERLAP_CHARS = 150      # overlap between chunks for context continuity


def _should_skip(path: Path) -> bool:
    parts = path.parts
    return any(skip in parts for skip in SKIP_PATTERNS)


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _chunk_markdown(text: str, source: str) -> list[dict]:
    """Split markdown by headings into overlapping chunks with metadata."""
    chunks = []
    # Split on headings (##, ###, etc.) keeping the heading with its section
    sections = re.split(r'(?=^#{1,4}\s)', text, flags=re.MULTILINE)

    current_heading = source  # default heading is filename
    buffer = ""

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # Extract heading if present
        heading_match = re.match(r'^(#{1,4})\s+(.+)', section)
        if heading_match:
            current_heading = heading_match.group(2).strip()

        # If adding this section would exceed max, flush buffer
        if len(buffer) + len(section) > MAX_CHUNK_CHARS and buffer:
            if len(buffer) >= MIN_CHUNK_CHARS:
                chunks.append({
                    "text": buffer.strip(),
                    "heading": current_heading,
                    "source": source,
                })
            # Keep overlap from end of buffer
            buffer = buffer[-OVERLAP_CHARS:] + "\n\n" + section
        else:
            buffer += ("\n\n" if buffer else "") + section

    # Flush remaining
    if buffer.strip() and len(buffer.strip()) >= MIN_CHUNK_CHARS:
        chunks.append({
            "text": buffer.strip(),
            "heading": current_heading,
            "source": source,
        })

    # If no chunks were created (file too small), use the whole thing
    if not chunks and text.strip() and len(text.strip()) >= MIN_CHUNK_CHARS:
        chunks.append({
            "text": text.strip(),
            "heading": source,
            "source": source,
        })

    return chunks


class RAGIndex:
    def __init__(self, db_path: Path = RAG_DB):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS files (
                    path     TEXT PRIMARY KEY,
                    hash     TEXT NOT NULL,
                    indexed  TEXT NOT NULL,
                    chunks   INTEGER DEFAULT 0
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                    text, heading, source,
                    content='',
                    tokenize='porter unicode61'
                );

                CREATE TABLE IF NOT EXISTS chunks_meta (
                    rowid    INTEGER PRIMARY KEY AUTOINCREMENT,
                    source   TEXT NOT NULL,
                    heading  TEXT NOT NULL,
                    text     TEXT NOT NULL
                );
            """)

    def _scan_files(self) -> list[Path]:
        """Find all .md files to index."""
        files = []
        seen = set()
        for base, pattern in SCAN_PATHS:
            search_dir = PROJECT_DIR / base
            if not search_dir.exists():
                continue
            for path in search_dir.glob(pattern):
                if path.is_file() and not _should_skip(path) and path not in seen:
                    seen.add(path)
                    files.append(path)
        return sorted(files)

    def _relative_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(PROJECT_DIR))
        except ValueError:
            return str(path)

    def rebuild(self) -> dict:
        """Full re-index — drops everything and rebuilds."""
        files = self._scan_files()
        total_chunks = 0

        with self._get_conn() as conn:
            conn.execute("DELETE FROM files")
            conn.execute("DELETE FROM chunks_meta")
            conn.execute("DROP TABLE IF EXISTS chunks")
            conn.execute("""
                CREATE VIRTUAL TABLE chunks USING fts5(
                    text, heading, source,
                    content='',
                    tokenize='porter unicode61'
                )
            """)

            for path in files:
                rel = self._relative_path(path)
                try:
                    text = path.read_text(errors="ignore")
                except Exception:
                    continue

                chunks = _chunk_markdown(text, rel)
                for chunk in chunks:
                    conn.execute(
                        "INSERT INTO chunks_meta (source, heading, text) VALUES (?, ?, ?)",
                        (chunk["source"], chunk["heading"], chunk["text"]),
                    )
                    rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    conn.execute(
                        "INSERT INTO chunks (rowid, text, heading, source) VALUES (?, ?, ?, ?)",
                        (rowid, chunk["text"], chunk["heading"], chunk["source"]),
                    )

                conn.execute(
                    "INSERT OR REPLACE INTO files (path, hash, indexed, chunks) VALUES (?, ?, ?, ?)",
                    (rel, _file_hash(path), datetime.utcnow().isoformat(), len(chunks)),
                )
                total_chunks += len(chunks)

        return {
            "files_indexed": len(files),
            "total_chunks": total_chunks,
            "db_path": str(self.db_path),
        }

    def update(self) -> dict:
        """Incremental update — only re-index changed/new files, remove deleted."""
        files = self._scan_files()
        file_map = {self._relative_path(p): p for p in files}
        updated = 0
        removed = 0
        new = 0

        with self._get_conn() as conn:
            # Get existing index state
            existing = {
                row["path"]: row["hash"]
                for row in conn.execute("SELECT path, hash FROM files").fetchall()
            }

            # Remove files that no longer exist
            for rel in existing:
                if rel not in file_map:
                    self._remove_file(conn, rel)
                    removed += 1

            # Add/update changed files
            for rel, path in file_map.items():
                current_hash = _file_hash(path)
                if rel in existing and existing[rel] == current_hash:
                    continue  # unchanged

                if rel in existing:
                    self._remove_file(conn, rel)
                    updated += 1
                else:
                    new += 1

                try:
                    text = path.read_text(errors="ignore")
                except Exception:
                    continue

                chunks = _chunk_markdown(text, rel)
                for chunk in chunks:
                    conn.execute(
                        "INSERT INTO chunks_meta (source, heading, text) VALUES (?, ?, ?)",
                        (chunk["source"], chunk["heading"], chunk["text"]),
                    )
                    rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    conn.execute(
                        "INSERT INTO chunks (rowid, text, heading, source) VALUES (?, ?, ?, ?)",
                        (rowid, chunk["text"], chunk["heading"], chunk["source"]),
                    )

                conn.execute(
                    "INSERT OR REPLACE INTO files (path, hash, indexed, chunks) VALUES (?, ?, ?, ?)",
                    (rel, current_hash, datetime.utcnow().isoformat(), len(chunks)),
                )

        # Clean up entries whose source files no longer exist on disk
        stale_result = self.cleanup_stale()
        stale_removed = stale_result["stale_removed"]

        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM chunks_meta").fetchone()[0]

        return {
            "new": new, "updated": updated, "removed": removed,
            "stale_removed": stale_removed,
            "total_chunks": total, "unchanged": len(file_map) - new - updated,
        }

    def _remove_file(self, conn, rel: str):
        """Remove all chunks for a file (contentless FTS5 requires special delete)."""
        rows = conn.execute(
            "SELECT rowid, text, heading, source FROM chunks_meta WHERE source=?", (rel,)
        ).fetchall()
        for r in rows:
            conn.execute(
                "INSERT INTO chunks(chunks, rowid, text, heading, source) VALUES('delete', ?, ?, ?, ?)",
                (r[0], r[1], r[2], r[3]),
            )
        conn.execute("DELETE FROM chunks_meta WHERE source=?", (rel,))
        conn.execute("DELETE FROM files WHERE path=?", (rel,))

    def search(self, query: str, limit: int = 8) -> list[dict]:
        """BM25-ranked search across all indexed chunks."""
        if not query.strip():
            return []

        with self._get_conn() as conn:
            # FTS5 search with BM25 ranking
            rows = conn.execute("""
                SELECT cm.source, cm.heading, cm.text, rank
                FROM chunks c
                JOIN chunks_meta cm ON c.rowid = cm.rowid
                WHERE chunks MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (query, limit)).fetchall()

        return [
            {
                "source": row["source"],
                "heading": row["heading"],
                "text": row["text"],
                "score": round(row["rank"], 3),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Pluggable vector search + reranker hooks (optional dependencies)
    # ------------------------------------------------------------------

    def _load_embedding_model(self):
        """Load a SentenceTransformer embedding model from fleet/models/embeddings/.

        Caches the model on ``self._embed_model`` so it is loaded at most once.
        Returns the model, or None if sentence-transformers is not installed or
        no .pt model file exists in the expected directory.
        """
        if hasattr(self, "_embed_model"):
            return self._embed_model

        self._embed_model = None
        model_dir = FLEET_DIR / "models" / "embeddings"

        if not model_dir.exists():
            log.debug("Embedding model directory not found: %s", model_dir)
            return None

        # Find the most recent .pt file
        pt_files = sorted(model_dir.glob("*.pt"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not pt_files:
            log.debug("No .pt embedding model found in %s", model_dir)
            return None

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            log.debug("sentence-transformers not installed — vector search unavailable")
            return None

        try:
            self._embed_model = SentenceTransformer(str(pt_files[0]))
            log.debug("Loaded embedding model: %s", pt_files[0].name)
        except Exception:
            log.warning("Failed to load embedding model from %s", pt_files[0], exc_info=True)
            self._embed_model = None

        return self._embed_model

    def vector_search(self, query: str, limit: int = 8) -> list[dict]:
        """Semantic vector search across all indexed chunks.

        Encodes the query and all stored chunks using a SentenceTransformer
        model, then ranks by cosine similarity.  Falls back to BM25
        (``self.search``) if no embedding model is available or on any error.
        """
        try:
            model = self._load_embedding_model()
            if model is None:
                return self.search(query, limit)

            import numpy as np

            # Fetch all chunks from the metadata table
            with self._get_conn() as conn:
                rows = conn.execute(
                    "SELECT rowid, source, heading, text FROM chunks_meta"
                ).fetchall()

            if not rows:
                return []

            texts = [row["text"] for row in rows]

            # Encode query and corpus (normalize for cosine via dot product)
            query_vec = model.encode(query, normalize_embeddings=True)
            corpus_vecs = model.encode(texts, normalize_embeddings=True)

            # Cosine similarity via dot product on normalized vectors
            scores = np.dot(corpus_vecs, query_vec)

            # Top-k indices (descending score)
            top_indices = np.argsort(scores)[::-1][:limit]

            results = []
            for idx in top_indices:
                row = rows[idx]
                results.append({
                    "source": row["source"],
                    "heading": row["heading"],
                    "text": row["text"],
                    "score": round(float(scores[idx]), 4),
                })
            return results

        except Exception:
            log.warning("vector_search failed — falling back to BM25", exc_info=True)
            return self.search(query, limit)

    def hybrid_search(self, query: str, limit: int = 8, bm25_weight: float = 0.4) -> list[dict]:
        """Hybrid BM25 + vector search with reciprocal rank fusion (RRF).

        Runs both BM25 and vector search (each returning ``2 * limit``
        candidates), then fuses rankings using RRF with ``k=60``::

            rrf_score = weight / (rank + 60)

        Falls back gracefully: if vector search is unavailable the result
        is equivalent to a plain BM25 search.
        """
        k = 60
        fetch = limit * 2
        vector_weight = 1.0 - bm25_weight

        bm25_results = self.search(query, fetch)
        vector_results = self.vector_search(query, fetch)

        # Build a dict keyed by (source, heading, text) → cumulative RRF score
        fused: dict[tuple, dict] = {}

        def _key(r: dict) -> tuple:
            return (r["source"], r["heading"], r["text"])

        for rank, r in enumerate(bm25_results, start=1):
            ky = _key(r)
            entry = fused.setdefault(ky, {"source": r["source"], "heading": r["heading"],
                                          "text": r["text"], "score": 0.0})
            entry["score"] += bm25_weight / (rank + k)

        for rank, r in enumerate(vector_results, start=1):
            ky = _key(r)
            entry = fused.setdefault(ky, {"source": r["source"], "heading": r["heading"],
                                          "text": r["text"], "score": 0.0})
            entry["score"] += vector_weight / (rank + k)

        # Sort descending by fused score, return top results
        ranked = sorted(fused.values(), key=lambda x: x["score"], reverse=True)[:limit]
        for r in ranked:
            r["score"] = round(r["score"], 4)
        return ranked

    def rerank(self, query: str, results: list[dict], limit: int = 8) -> list[dict]:
        """Rerank search results using a CrossEncoder model.

        Looks for the most recent model in ``fleet/models/reranker/``.
        If no model is found or sentence-transformers is not installed,
        returns the original results truncated to *limit*.
        """
        if not results:
            return results

        reranker_dir = FLEET_DIR / "models" / "reranker"

        if not reranker_dir.exists():
            log.debug("Reranker model directory not found: %s", reranker_dir)
            return results[:limit]

        # Accept .pt files or directories (HuggingFace-style saved models)
        model_candidates = sorted(
            [p for p in reranker_dir.iterdir() if p.is_file() or p.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        # Filter out hidden/meta files
        model_candidates = [p for p in model_candidates if not p.name.startswith(".")]

        if not model_candidates:
            log.debug("No reranker model found in %s", reranker_dir)
            return results[:limit]

        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            log.debug("sentence-transformers not installed — reranking unavailable")
            return results[:limit]

        try:
            cross_encoder = CrossEncoder(str(model_candidates[0]))
            pairs = [(query, r["text"]) for r in results]
            scores = cross_encoder.predict(pairs)

            # Pair each result with its cross-encoder score, sort descending
            scored = sorted(zip(results, scores), key=lambda x: x[1], reverse=True)
            reranked = []
            for r, s in scored[:limit]:
                reranked.append({
                    "source": r["source"],
                    "heading": r["heading"],
                    "text": r["text"],
                    "score": round(float(s), 4),
                })
            return reranked

        except Exception:
            log.warning("Reranking failed — returning original order", exc_info=True)
            return results[:limit]

    def search_by_source(self, source_pattern: str, limit: int = 20) -> list[dict]:
        """List chunks from a specific source file pattern."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT source, heading, text FROM chunks_meta
                WHERE source LIKE ?
                ORDER BY rowid
                LIMIT ?
            """, (f"%{source_pattern}%", limit)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        """Index statistics."""
        with self._get_conn() as conn:
            files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM chunks_meta").fetchone()[0]
            sources = conn.execute(
                "SELECT path, chunks, indexed FROM files ORDER BY indexed DESC"
            ).fetchall()
        return {
            "files": files,
            "chunks": chunks,
            "sources": [dict(r) for r in sources],
        }

    def cleanup_stale(self) -> dict:
        """Remove index entries for files that no longer exist on disk.

        Walks every distinct source path in the index, resolves it against
        PROJECT_DIR, and deletes all chunks + metadata for missing files.
        Returns a summary with the count and list of cleaned paths.
        """
        cleaned = []

        with self._get_conn() as conn:
            indexed_paths = conn.execute(
                "SELECT path FROM files"
            ).fetchall()

            for row in indexed_paths:
                rel = row["path"]
                abs_path = PROJECT_DIR / rel
                if not abs_path.exists():
                    self._remove_file(conn, rel)
                    cleaned.append(rel)

        return {
            "stale_removed": len(cleaned),
            "cleaned_paths": cleaned,
        }

    def get_index_stats(self) -> dict:
        """Extended index statistics including staleness and disk usage.

        Returns:
            total_entries: number of chunks in the index
            unique_files: number of distinct indexed files
            stale_entries: number of indexed files missing from disk
            stale_paths: list of missing source paths
            index_size_bytes: rag.db file size on disk
            last_indexed: ISO timestamp of the most recently indexed file
        """
        with self._get_conn() as conn:
            total_entries = conn.execute(
                "SELECT COUNT(*) FROM chunks_meta"
            ).fetchone()[0]
            unique_files = conn.execute(
                "SELECT COUNT(*) FROM files"
            ).fetchone()[0]
            indexed_paths = conn.execute(
                "SELECT path FROM files"
            ).fetchall()
            last_row = conn.execute(
                "SELECT indexed FROM files ORDER BY indexed DESC LIMIT 1"
            ).fetchone()

        # Check each indexed path against disk
        stale_paths = []
        for row in indexed_paths:
            rel = row["path"]
            if not (PROJECT_DIR / rel).exists():
                stale_paths.append(rel)

        # DB file size
        try:
            index_size_bytes = os.path.getsize(self.db_path)
        except OSError:
            index_size_bytes = 0

        return {
            "total_entries": total_entries,
            "unique_files": unique_files,
            "stale_entries": len(stale_paths),
            "stale_paths": stale_paths,
            "index_size_bytes": index_size_bytes,
            "last_indexed": last_row["indexed"] if last_row else None,
        }


def _register_views():
    """Register RAG data source for Hybrid ViewPort."""
    import view_registry
    view_registry.register_source(
        name="rag",
        category="storage",
        node_types=["index", "chunk", "query"],
        edge_types=["indexes", "retrieves", "ranks"],
        data_endpoint="/api/rag/graph",
        icon="database",
        layout_hint="cluster",
        metrics=["chunk_count", "query_latency_ms"],
    )
