# Ingestion Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Ingest" page to the web dashboard that lets operators browse HuggingFace datasets, upload local files, stage items, and dispatch them as fleet tasks or RAG entries.

**Architecture:** Flask blueprint (`ingest_blueprint.py`) serves `/api/ingest/*` endpoints. Core logic lives in `ingest_manager.py` (fetch, cache, stage, dispatch). Pre-configured sources in `fleet.toml`, user-added sources + staging in `fleet.db`. Dashboard HTML gets a new `section-ingest` with JS functions for the UI.

**Tech Stack:** Flask blueprint, HuggingFace Dataset Viewer REST API, SQLite (fleet.db), SSE events, existing `db.py` + `rag.py` for dispatch.

**Spec:** `docs/superpowers/specs/2026-03-26-ingestion-hub-design.md`

**Security note:** Dashboard JS must use safe DOM methods (createElement/textContent) instead of innerHTML to prevent XSS. All user-supplied strings displayed via textContent, not raw HTML insertion.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `fleet/ingest_manager.py` | Create | Core logic: HF API client, cache management, staging CRUD, dispatch |
| `fleet/ingest_blueprint.py` | Create | Flask blueprint: all `/api/ingest/*` REST endpoints |
| `fleet/db.py` | Modify | Add `ingest_sources` + `ingest_staging` tables to `init_db()` |
| `fleet/dashboard.py` | Modify | Register ingest blueprint |
| `fleet/templates/dashboard.html` | Modify | Add nav item, section-ingest HTML, JS functions (DOM-safe) |
| `fleet/fleet.toml` | Modify | Add `[ingest]` config section + `[[ingest.sources]]` defaults |
| `fleet/smoke_test.py` | Modify | Add smoke tests for ingest module |

---

### Task 1: DB Schema

**Files:** Modify `fleet/db.py`

- [ ] Add `ingest_sources` and `ingest_staging` CREATE TABLE statements to `init_db()`
- [ ] Run `python -c "import db; db.init_db(); print('OK')"` to verify
- [ ] Verify tables exist: query `sqlite_master` for `ingest%` tables
- [ ] Commit: `feat(ingest): add ingest_sources + ingest_staging tables`

Schema in spec: `docs/superpowers/specs/2026-03-26-ingestion-hub-design.md` (DB Tables section)

---

### Task 2: fleet.toml Config

**Files:** Modify `fleet/fleet.toml`

- [ ] Add `[ingest]` section with `max_storage_mb`, `cache_dir`, `batch_size_default`, `eviction_policy`
- [ ] Add 6 `[[ingest.sources]]` entries (github-codereview, codereviewer, arxiv-summarization, scientific-papers, code-vulnerable, code-quality)
- [ ] Verify: `python -c "from config import load_config; cfg = load_config(); print(cfg['ingest']['max_storage_mb'], len(cfg['ingest']['sources']))"`
- [ ] Commit: `feat(ingest): add ingest config and 6 pre-configured HF sources`

Source definitions in spec: Pre-configured Source Definitions section

---

### Task 3: ingest_manager.py — Core Logic

**Files:** Create `fleet/ingest_manager.py`

This is the largest task. Implement these function groups:

**Source management:**
- [ ] `list_sources()` — merge fleet.toml sources + fleet.db `ingest_sources` table
- [ ] `add_source()` — INSERT into `ingest_sources` via `db._retry_write()`
- [ ] `remove_source()` — DELETE from `ingest_sources`

**HuggingFace API (all calls use `timeout=15`):**
- [ ] `fetch_hf_schema(dataset_id)` — GET `/info?dataset={id}`, extract columns/row_count/splits. Handle 404/401/403/429 with specific error messages.
- [ ] `fetch_hf_rows(dataset_id, offset, length, split)` — GET `/rows?dataset={id}&split=train&offset=N&length=N`. Retry 429 with exponential backoff (2s/4s/8s, 3 attempts).
- [ ] `_is_offline()` check — returns early with error message when offline/air-gap

**Cache management:**
- [ ] `_get_cache_dir()` — reads `cache_dir` from config, creates `knowledge/ingest_cache/`
- [ ] `_cache_size_mb()` — sum all files in cache dir
- [ ] `cache_batch(source_id, rows, batch_num)` — write JSONL to `ingest_cache/<source>/batch_NNN.jsonl`, update `source_meta.json` with `dispatched_count` tracking. Check size cap before writing.
- [ ] `evict_processed()` — delete batches where `dispatched_count >= rows`
- [ ] `cache_stats()` — return `{used_mb, max_mb, usage_pct}`

**Staging (all writes via `db._retry_write()`):**
- [ ] `stage_items(items)` — INSERT into `ingest_staging`, return IDs
- [ ] `get_staging()` — SELECT all from `ingest_staging`
- [ ] `remove_staged(item_id)` — DELETE single item
- [ ] `clear_staging()` — DELETE all

**Dispatch:**
- [ ] `dispatch_staged(item_ids)` — loop staged items, call `_dispatch_as_task()` or `_dispatch_to_rag()`, remove from staging on success
- [ ] `_dispatch_as_task(item)` — call `db.post_task()` with `type_=skill`, `payload_json` containing source/content/row_id
- [ ] `_dispatch_to_rag(item)` — call `rag.ingest_text()` with content and source tags

- [ ] Verify: `python -c "import ingest_manager; print(len(ingest_manager.list_sources()), 'sources')"`
- [ ] Commit: `feat(ingest): add ingest_manager with HF fetch, cache, staging, dispatch`

---

### Task 4: ingest_blueprint.py — REST API

**Files:** Create `fleet/ingest_blueprint.py`, Modify `fleet/dashboard.py`

**Endpoints (all return JSON, all catch Exception with log.warning):**
- [ ] `GET /api/ingest/sources` — call `ingest_manager.list_sources()`
- [ ] `POST /api/ingest/sources` — call `ingest_manager.add_source()`, validate required field `id`
- [ ] `DELETE /api/ingest/sources/<id>` — call `ingest_manager.remove_source()`
- [ ] `GET /api/ingest/sources/<id>/schema` — find source, call `fetch_hf_schema()`, return 502 on HF error
- [ ] `GET /api/ingest/sources/<id>/rows` — find source, call `fetch_hf_rows()` with query params offset/limit/split, auto-cache batch
- [ ] `POST /api/ingest/stage` — accept `{items: [...]}`, call `stage_items()`
- [ ] `GET /api/ingest/staging` — call `get_staging()`
- [ ] `DELETE /api/ingest/staging/<id>` — call `remove_staged()`
- [ ] `POST /api/ingest/dispatch` — accept optional `{item_ids: [...]}`, call `dispatch_staged()`
- [ ] `GET /api/ingest/cache/stats` — call `cache_stats()`
- [ ] `POST /api/ingest/cache/evict` — call `evict_processed()`
- [ ] `POST /api/ingest/upload` — accept multipart file, save to `_uploads/`, return metadata

**Register in dashboard.py:**
- [ ] Add `from ingest_blueprint import ingest_bp; app.register_blueprint(ingest_bp)` (wrapped in try/except)

- [ ] Verify: `curl -s http://localhost:5555/api/ingest/sources | python -m json.tool`
- [ ] Commit: `feat(ingest): add REST API blueprint with 12 endpoints`

---

### Task 5: Dashboard HTML — Ingest Page UI

**Files:** Modify `fleet/templates/dashboard.html`

**IMPORTANT:** All JS must use safe DOM construction (createElement + textContent). No innerHTML with user data. Use the same patterns as `loadFleet()` and `loadTasks()` in the existing codebase.

**Navigation:**
- [ ] Add nav button `data-section="ingest"` with icon `📥` after Pipeline
- [ ] Add `case 'ingest': loadIngest(); break;` to `loadSectionData()` switch

**HTML section:**
- [ ] Add `<section class="section" id="section-ingest">` with:
  - Zone 1: `div#ingest-source-pills` (source pill container)
  - Zone 2: `div#ingest-expanded-panel` (inline expand area, hidden by default)
  - Zone 3: `div#ingest-staging-list` with Ingest Selected / Clear buttons
  - Storage bar: `div#ingest-cache-bar` + `span#ingest-cache-label`

**JS functions (all using safe DOM methods):**
- [ ] `loadIngest()` — fetch sources, render pills, load staging + cache
- [ ] `renderSourcePills()` — createElement for each source pill + Local Files button + Add Source button + hidden file input
- [ ] `toggleSourcePanel(sourceId)` — expand/collapse inline config panel for a source
- [ ] `fetchSourceRows(sourceId, offset)` — fetch rows API, render preview with checkboxes and pagination
- [ ] `stageSelectedRows()` — collect checked rows, POST to /api/ingest/stage
- [ ] `loadIngestStaging()` — fetch staging, render item list with color dots and remove buttons
- [ ] `ingestDispatchAll()` — collect checked staging items, POST to /api/ingest/dispatch
- [ ] `ingestClearStaging()` — remove all staged items
- [ ] `unstageItem(id)` — DELETE single staging item
- [ ] `loadIngestCache()` — fetch cache stats, update bar width and color
- [ ] `handleLocalFileUpload()` — FormData upload, auto-stage uploaded files
- [ ] `showAddSourceModal()` — create modal overlay with form fields (createElement, not innerHTML)
- [ ] `submitAddSource()` — POST new source, reload

Source pill colors by agent_role: `{coder: '#f59e0b', researcher: '#3b82f6', security: '#ef4444', archivist: '#8b5cf6', analyst: '#06b6d4', local: '#10b981'}`

- [ ] Verify: reload dashboard, click Ingest, see 6 source pills + Local + Add Source
- [ ] Commit: `feat(ingest): add Ingest page to dashboard with source pills, staging, cache bar`

---

### Task 6: Smoke Tests

**Files:** Modify `fleet/smoke_test.py`

- [ ] `test_ingest_module()` — verify `ingest_manager.list_sources()` returns 5+ sources
- [ ] `test_ingest_cache_stats()` — verify `cache_stats()` returns valid structure with `used_mb`, `max_mb`
- [ ] `test_ingest_staging_empty()` — verify `get_staging()` returns a list
- [ ] Register in smoke test runner
- [ ] Run: `python smoke_test.py --fast` — all pass
- [ ] Commit: `test(ingest): add 3 smoke tests for ingest module`

---

### Task 7: Integration Test

- [ ] List sources via curl, verify 6+ returned
- [ ] Fetch 3 rows from `code-quality` source via API
- [ ] Stage 1 row via POST
- [ ] Verify staging shows 1 item
- [ ] Dispatch via POST
- [ ] Verify task created in fleet.db with `type='code_quality'`
- [ ] Verify cache stats updated
- [ ] Test dashboard UI end-to-end: click source pill, fetch, stage, dispatch
- [ ] Final commit: `feat(ingest): Ingestion Hub complete`
