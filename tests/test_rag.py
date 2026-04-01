"""Unit tests for fleet/rag.py."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "fleet"))


def _make_rag(tmp_path):
    from rag import RAGIndex
    idx = RAGIndex(db_path=Path(tmp_path))
    return idx


def _close_rag(rag):
    """Force-close all SQLite connections held by RAGIndex (Windows WAL lock fix)."""
    import gc, sqlite3
    del rag
    gc.collect()


class TestChunkMarkdown(unittest.TestCase):
    def test_empty_returns_empty(self):
        from rag import _chunk_markdown
        result = _chunk_markdown("", "test.md")
        self.assertEqual(result, [])

    def test_small_doc_single_chunk(self):
        from rag import _chunk_markdown, MIN_CHUNK_CHARS
        text = "# Title\n" + ("word " * 20)  # well above MIN_CHUNK_CHARS
        chunks = _chunk_markdown(text, "test.md")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["source"], "test.md")

    def test_chunk_has_required_keys(self):
        from rag import _chunk_markdown
        text = "# Section\n" + ("content " * 30)
        chunks = _chunk_markdown(text, "doc.md")
        self.assertGreaterEqual(len(chunks), 1)
        for chunk in chunks:
            self.assertIn("text", chunk)
            self.assertIn("heading", chunk)
            self.assertIn("source", chunk)

    def test_heading_extracted(self):
        from rag import _chunk_markdown
        text = "## My Section\n" + ("data " * 30)
        chunks = _chunk_markdown(text, "doc.md")
        self.assertTrue(any("My Section" in c["heading"] for c in chunks))

    def test_large_doc_multiple_chunks(self):
        from rag import _chunk_markdown, MAX_CHUNK_CHARS
        text = "# H1\n" + ("word " * 500) + "\n# H2\n" + ("word " * 500)
        chunks = _chunk_markdown(text, "big.md")
        self.assertGreater(len(chunks), 1)

    def test_too_small_skipped(self):
        from rag import _chunk_markdown, MIN_CHUNK_CHARS
        text = "tiny"  # below MIN_CHUNK_CHARS
        chunks = _chunk_markdown(text, "small.md")
        self.assertEqual(chunks, [])


class TestShouldSkip(unittest.TestCase):
    def test_skips_git(self):
        from rag import _should_skip
        p = Path(".git/config")
        self.assertTrue(_should_skip(p))

    def test_skips_node_modules(self):
        from rag import _should_skip
        p = Path("node_modules/some/lib.js")
        self.assertTrue(_should_skip(p))

    def test_normal_file_not_skipped(self):
        from rag import _should_skip
        p = Path("fleet/knowledge/notes.md")
        self.assertFalse(_should_skip(p))


class TestRAGIndexInit(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil, gc
        gc.collect()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_creates_db(self):
        db_path = Path(self.tmp_dir) / "rag_test.db"
        rag = _make_rag(str(db_path))
        exists = db_path.exists()
        del rag
        self.assertTrue(exists)

    def test_tables_created(self):
        import sqlite3, gc
        db_path = Path(self.tmp_dir) / "rag_test2.db"
        rag = _make_rag(str(db_path))
        del rag
        gc.collect()
        with sqlite3.connect(str(db_path)) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        self.assertIn("files", tables)
        self.assertIn("chunks_meta", tables)


class TestRAGSearch(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.tmp_dir) / "rag_search_test.db"
        self.rag = _make_rag(str(self.db_path))

        # Index a simple in-memory document
        self._index_text("## GPU Memory\nThe fleet uses Ollama for local GPU inference with VRAM management.", "test.md")

    def tearDown(self):
        import shutil, gc
        del self.rag
        gc.collect()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _index_text(self, text, source):
        from rag import _chunk_markdown
        chunks = _chunk_markdown(text, source)
        import sqlite3
        with sqlite3.connect(str(self.db_path), check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            for chunk in chunks:
                conn.execute(
                    "INSERT INTO chunks_meta (source, heading, text) VALUES (?, ?, ?)",
                    (chunk["source"], chunk["heading"], chunk["text"])
                )
                rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO chunks (rowid, text, heading, source) VALUES (?, ?, ?, ?)",
                    (rowid, chunk["text"], chunk["heading"], chunk["source"])
                )
            conn.execute(
                "INSERT OR REPLACE INTO files (path, hash, indexed, chunks) VALUES (?, ?, datetime('now'), ?)",
                (source, "abc123", len(chunks))
            )

    def test_search_returns_results(self):
        results = self.rag.search("GPU VRAM Ollama")
        self.assertGreater(len(results), 0)

    def test_search_empty_query_returns_empty(self):
        results = self.rag.search("")
        self.assertEqual(results, [])

    def test_search_result_has_expected_keys(self):
        results = self.rag.search("GPU")
        for r in results:
            self.assertIn("source", r)
            self.assertIn("heading", r)
            self.assertIn("text", r)
            self.assertIn("score", r)

    def test_search_limit_respected(self):
        results = self.rag.search("GPU", limit=1)
        self.assertLessEqual(len(results), 1)

    def test_whitespace_only_query_returns_empty(self):
        results = self.rag.search("   ")
        self.assertEqual(results, [])


class TestRAGRebuild(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil, gc
        gc.collect()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_rebuild_with_no_files(self):
        import gc
        db_path = Path(self.tmp_dir) / "rag_rebuild.db"
        rag = _make_rag(str(db_path))
        with patch.object(rag, "_scan_files", return_value=[]):
            result = rag.rebuild()
        del rag
        gc.collect()
        self.assertEqual(result["files_indexed"], 0)
        self.assertEqual(result["total_chunks"], 0)

    def test_rebuild_indexes_files(self):
        import gc
        db_path = Path(self.tmp_dir) / "rag_idx.db"
        md_file = Path(self.tmp_dir) / "doc.md"
        md_file.write_text("## Test Section\n" + "fleet management system " * 30)

        rag = _make_rag(str(db_path))
        with patch.object(rag, "_scan_files", return_value=[md_file]):
            result = rag.rebuild()
        del rag
        gc.collect()
        self.assertEqual(result["files_indexed"], 1)
        self.assertGreater(result["total_chunks"], 0)


class TestRAGHybridSearch(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil, gc
        gc.collect()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_hybrid_falls_back_to_bm25_without_vector_model(self):
        import gc
        db_path = Path(self.tmp_dir) / "rag_hybrid.db"
        rag = _make_rag(str(db_path))
        # No embedding model → should fall back to BM25 (returns list)
        with patch.object(rag, "_load_embedding_model", return_value=None):
            results = rag.hybrid_search("test query")
        del rag
        gc.collect()
        self.assertIsInstance(results, list)


class TestFileHash(unittest.TestCase):
    def test_hash_is_hex_string(self):
        from rag import _file_hash
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello world")
            tmp = f.name
        result = _file_hash(Path(tmp))
        os.unlink(tmp)
        self.assertIsInstance(result, str)
        self.assertRegex(result, r"^[0-9a-f]+$")

    def test_different_content_different_hash(self):
        from rag import _file_hash
        with tempfile.NamedTemporaryFile(delete=False) as f1, \
             tempfile.NamedTemporaryFile(delete=False) as f2:
            f1.write(b"content_a")
            f2.write(b"content_b")
        h1 = _file_hash(Path(f1.name))
        h2 = _file_hash(Path(f2.name))
        os.unlink(f1.name)
        os.unlink(f2.name)
        self.assertNotEqual(h1, h2)


if __name__ == "__main__":
    unittest.main()
