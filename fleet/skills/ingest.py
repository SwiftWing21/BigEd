"""
File/folder ingest skill — imports documents into the fleet RAG index.

Accepts a folder path or zip file, extracts text from supported file types,
chunks the content, and indexes it into the RAG database for agent retrieval.

Supported formats:
  Text:    .md, .txt, .rst, .log, .cfg, .ini, .toml, .yaml, .yml
  Code:    .py, .js, .ts, .go, .rs, .java, .c, .cpp, .h, .cs, .rb, .sh, .bat, .ps1
  Data:    .json, .csv, .tsv, .xml, .html
  Docs:    .pdf (requires pymupdf or pdfplumber)
  Office:  .docx (requires python-docx)
  Archive: .zip (extracted to temp dir, contents processed recursively)

Payload:
  path          str   File or folder path to ingest (required)
  tag           str   Label for this import batch (default: folder/file name)
  max_file_mb   int   Skip files larger than this (default: 50)
  recursive     bool  Recurse into subdirectories (default: true)
  extensions    list  Limit to these extensions (default: all supported)

Limits:
  Single file:   50 MB text content (configurable via max_file_mb)
  Batch total:   ~2 GB text content (RAM-bound, 34 GB system)
  Zip files:     10 GB uncompressed (extracted to temp dir)
  PDF:           Streamed page-by-page, no practical page limit
  SQLite FTS5:   Performant to ~100 GB index size
"""
import csv
import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
INGEST_LOG_DIR = KNOWLEDGE_DIR / "ingests"

# Max uncompressed zip size (10 GB)
MAX_ZIP_BYTES = 10 * 1024 * 1024 * 1024

# File type groups
_TEXT_EXTS = {
    ".md", ".txt", ".rst", ".log", ".cfg", ".ini", ".toml",
    ".yaml", ".yml", ".env.example", ".gitignore", ".dockerignore",
}
_CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".sh", ".bat", ".ps1",
    ".sql", ".r", ".swift", ".kt", ".scala", ".lua", ".pl", ".php",
    ".tf", ".hcl", ".makefile", ".cmake",
}
_DATA_EXTS = {".json", ".csv", ".tsv", ".xml", ".html", ".htm"}
_DOC_EXTS = {".pdf"}
_OFFICE_EXTS = {".docx"}
_ARCHIVE_EXTS = {".zip"}

ALL_SUPPORTED = _TEXT_EXTS | _CODE_EXTS | _DATA_EXTS | _DOC_EXTS | _OFFICE_EXTS | _ARCHIVE_EXTS

# Chunk config (matches rag.py)
MAX_CHUNK_CHARS = 1500
MIN_CHUNK_CHARS = 80
OVERLAP_CHARS = 150


def _extract_text(path: Path, max_bytes: int) -> str | None:
    """Extract text content from a file. Returns None if unsupported or too large."""
    if path.stat().st_size > max_bytes:
        return None

    ext = path.suffix.lower()
    # Also match extensionless files named like Makefile, Dockerfile
    name_lower = path.name.lower()
    if name_lower in ("makefile", "dockerfile", "rakefile", "gemfile", "procfile"):
        ext = ".makefile"

    if ext in _TEXT_EXTS | _CODE_EXTS:
        return _read_text(path)
    elif ext in _DATA_EXTS:
        return _read_data(path, ext)
    elif ext == ".pdf":
        return _read_pdf(path)
    elif ext == ".docx":
        return _read_docx(path)
    return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        try:
            return path.read_text(encoding="latin-1", errors="ignore")
        except Exception:
            return None


def _read_data(path: Path, ext: str) -> str | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    if ext == ".json":
        try:
            data = json.loads(raw)
            return json.dumps(data, indent=2, ensure_ascii=False)[:500_000]
        except Exception:
            return raw

    if ext in (".csv", ".tsv"):
        delimiter = "\t" if ext == ".tsv" else ","
        try:
            reader = csv.reader(io.StringIO(raw), delimiter=delimiter)
            lines = []
            for i, row in enumerate(reader):
                if i > 5000:  # cap at 5000 rows
                    lines.append(f"... ({i}+ rows, truncated)")
                    break
                lines.append(" | ".join(row))
            return "\n".join(lines)
        except Exception:
            return raw

    if ext in (".xml", ".html", ".htm"):
        # Strip tags for plain text
        import re
        text = re.sub(r'<[^>]+>', ' ', raw)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    return raw


def _read_pdf(path: Path) -> str | None:
    """Extract text from PDF — tries pymupdf first, falls back to pdfplumber."""
    try:
        import fitz  # pymupdf
        doc = fitz.open(str(path))
        pages = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                pages.append(f"--- Page {page.number + 1} ---\n{text}")
        doc.close()
        return "\n\n".join(pages) if pages else None
    except ImportError:
        pass

    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text and text.strip():
                    pages.append(f"--- Page {i + 1} ---\n{text}")
        return "\n\n".join(pages) if pages else None
    except ImportError:
        return f"[PDF: {path.name} — install pymupdf or pdfplumber to extract text]"
    except Exception as e:
        return f"[PDF error: {e}]"


def _read_docx(path: Path) -> str | None:
    try:
        from docx import Document
        doc = Document(str(path))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs) if paragraphs else None
    except ImportError:
        return f"[DOCX: {path.name} — install python-docx to extract text]"
    except Exception as e:
        return f"[DOCX error: {e}]"


def _chunk_text(text: str, source: str, heading: str = "") -> list[dict]:
    """Split text into overlapping chunks for RAG indexing."""
    if not text or len(text.strip()) < MIN_CHUNK_CHARS:
        return []

    chunks = []
    # For code files, chunk by logical blocks (functions/classes) if possible
    # For everything else, chunk by size with overlap
    pos = 0
    while pos < len(text):
        end = pos + MAX_CHUNK_CHARS
        chunk_text = text[pos:end]

        # Try to break at a paragraph/line boundary
        if end < len(text):
            last_break = chunk_text.rfind("\n\n")
            if last_break < MAX_CHUNK_CHARS // 3:
                last_break = chunk_text.rfind("\n")
            if last_break > MAX_CHUNK_CHARS // 3:
                chunk_text = chunk_text[:last_break]
                end = pos + last_break

        chunk_text = chunk_text.strip()
        if len(chunk_text) >= MIN_CHUNK_CHARS:
            chunks.append({
                "text": chunk_text,
                "heading": heading or source,
                "source": source,
            })

        pos = end - OVERLAP_CHARS if end < len(text) else len(text)

    return chunks


def _extract_zip(zip_path: Path, max_bytes: int) -> tuple[Path | None, str | None]:
    """Extract zip to temp dir. Returns (temp_dir, error)."""
    if not zipfile.is_zipfile(zip_path):
        return None, f"Not a valid zip file: {zip_path.name}"

    # Check uncompressed size
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            total = sum(info.file_size for info in zf.infolist())
            if total > MAX_ZIP_BYTES:
                gb = total / 1e9
                return None, f"Zip too large: {gb:.1f} GB uncompressed (limit: 10 GB)"

            # Security: check for zip slip
            tmp = Path(tempfile.mkdtemp(prefix="biged_ingest_"))
            for info in zf.infolist():
                target = tmp / info.filename
                if not str(target.resolve()).startswith(str(tmp.resolve())):
                    shutil.rmtree(tmp, ignore_errors=True)
                    return None, "Zip contains path traversal — rejected"

            zf.extractall(tmp)
            return tmp, None
    except Exception as e:
        return None, f"Zip extraction failed: {e}"


def _walk_files(root: Path, recursive: bool, extensions: set | None,
                max_file_bytes: int) -> list[tuple[Path, str]]:
    """Walk directory and return (path, relative_path) pairs for supported files."""
    results = []
    skip_dirs = {".git", "node_modules", ".venv", "__pycache__", "dist", "build", ".tox"}

    if root.is_file():
        ext = root.suffix.lower()
        if extensions is None or ext in extensions:
            if ext in ALL_SUPPORTED:
                return [(root, root.name)]
        return []

    pattern = "**/*" if recursive else "*"
    for path in root.glob(pattern):
        if not path.is_file():
            continue
        if any(skip in path.parts for skip in skip_dirs):
            continue
        ext = path.suffix.lower()
        if extensions and ext not in extensions:
            continue
        if ext not in ALL_SUPPORTED:
            continue
        if path.stat().st_size > max_file_bytes:
            continue
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = path.name
        results.append((path, rel))

    return sorted(results, key=lambda x: x[1])


def run(payload, config):
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    from rag import RAGIndex

    path_str = payload.get("path", "")
    if not path_str:
        return {"error": "No path provided"}

    source_path = Path(path_str)
    if not source_path.exists():
        return {"error": f"Path not found: {path_str}"}

    tag = payload.get("tag", source_path.stem)
    max_file_mb = int(payload.get("max_file_mb", 50))
    max_file_bytes = max_file_mb * 1024 * 1024
    recursive = payload.get("recursive", True)
    ext_filter = payload.get("extensions", None)
    if ext_filter:
        ext_filter = {e if e.startswith(".") else f".{e}" for e in ext_filter}

    # Track results
    stats = {
        "files_found": 0,
        "files_ingested": 0,
        "files_skipped": 0,
        "chunks_indexed": 0,
        "zips_extracted": 0,
        "errors": [],
        "tag": tag,
        "source": str(source_path),
    }

    temp_dirs = []  # track for cleanup
    all_files = []  # (path, display_name) pairs

    # Handle zip files at the top level
    if source_path.is_file() and source_path.suffix.lower() == ".zip":
        tmp, err = _extract_zip(source_path, max_file_bytes)
        if err:
            return {"error": err}
        temp_dirs.append(tmp)
        stats["zips_extracted"] += 1
        all_files = _walk_files(tmp, recursive, ext_filter, max_file_bytes)
    else:
        all_files = _walk_files(source_path, recursive, ext_filter, max_file_bytes)

    stats["files_found"] = len(all_files)

    # Process nested zips found during walk
    nested_zips = [(p, r) for p, r in all_files if p.suffix.lower() == ".zip"]
    non_zips = [(p, r) for p, r in all_files if p.suffix.lower() != ".zip"]

    for zip_path, zip_rel in nested_zips:
        tmp, err = _extract_zip(zip_path, max_file_bytes)
        if err:
            stats["errors"].append(f"{zip_rel}: {err}")
            continue
        temp_dirs.append(tmp)
        stats["zips_extracted"] += 1
        extracted = _walk_files(tmp, recursive, ext_filter, max_file_bytes)
        for p, r in extracted:
            non_zips.append((p, f"{zip_rel}/{r}"))

    stats["files_found"] = len(non_zips)

    # Index into RAG
    idx = RAGIndex()
    conn = idx._get_conn()
    now = datetime.now().isoformat()

    try:
        for file_path, rel_name in non_zips:
            display = f"[ingest:{tag}] {rel_name}"

            text = _extract_text(file_path, max_file_bytes)
            if text is None:
                stats["files_skipped"] += 1
                continue

            chunks = _chunk_text(text, display)
            if not chunks:
                stats["files_skipped"] += 1
                continue

            # Remove old chunks for this source (re-ingest support)
            conn.execute("DELETE FROM chunks_meta WHERE source = ?", (display,))
            conn.execute("DELETE FROM files WHERE path = ?", (display,))

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
                (display, "", now, len(chunks)),
            )

            stats["files_ingested"] += 1
            stats["chunks_indexed"] += len(chunks)

        conn.commit()
    except Exception as e:
        stats["errors"].append(f"Index error: {e}")
    finally:
        conn.close()
        # Clean up temp dirs
        for tmp in temp_dirs:
            shutil.rmtree(tmp, ignore_errors=True)

    # Write ingest log
    INGEST_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = INGEST_LOG_DIR / f"ingest_{tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    log_lines = [
        f"# Ingest: {tag}",
        f"**Source:** `{source_path}`",
        f"**Date:** {now}",
        f"**Files found:** {stats['files_found']}",
        f"**Files ingested:** {stats['files_ingested']}",
        f"**Files skipped:** {stats['files_skipped']}",
        f"**Chunks indexed:** {stats['chunks_indexed']}",
        f"**Zips extracted:** {stats['zips_extracted']}",
    ]
    if stats["errors"]:
        log_lines.append(f"\n## Errors ({len(stats['errors'])})")
        for err in stats["errors"][:20]:
            log_lines.append(f"- {err}")
    log_file.write_text("\n".join(log_lines), encoding="utf-8")
    stats["log"] = str(log_file)

    return stats
