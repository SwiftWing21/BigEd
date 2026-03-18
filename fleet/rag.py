"""
RAG engine — indexes .md files into SQLite FTS5 for retrieval-augmented generation.

No external dependencies — uses Python's built-in sqlite3 with FTS5.
Chunks documents by heading sections, stores metadata, and provides
BM25-ranked search results.

Usage:
    from rag import RAGIndex
    idx = RAGIndex()
    idx.rebuild()                        # full re-index
    idx.update()                         # incremental (changed files only)
    results = idx.search("fleet GPU")    # BM25-ranked chunks
"""
import hashlib
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

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
            conn.execute("DELETE FROM chunks")

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

        total = conn.execute("SELECT COUNT(*) FROM chunks_meta").fetchone()[0] if True else 0
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM chunks_meta").fetchone()[0]

        return {
            "new": new, "updated": updated, "removed": removed,
            "total_chunks": total, "unchanged": len(file_map) - new - updated,
        }

    def _remove_file(self, conn, rel: str):
        """Remove all chunks for a file."""
        rowids = [
            r[0] for r in conn.execute(
                "SELECT rowid FROM chunks_meta WHERE source=?", (rel,)
            ).fetchall()
        ]
        for rid in rowids:
            conn.execute("DELETE FROM chunks WHERE rowid=?", (rid,))
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
