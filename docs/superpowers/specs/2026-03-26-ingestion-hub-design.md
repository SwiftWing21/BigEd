# Ingestion Hub — Design Spec

**Date:** 2026-03-26
**Status:** Approved
**Location:** Web dashboard — new "Ingest" nav item (between Pipeline and Tasks)

## Problem

Fleet agents need real work to train on. Currently tasks are either hand-typed via `lead_client.py task` or generated synthetically by the planner — both produce low-quality, repetitive work. Meanwhile, HuggingFace hosts millions of rows of real code reviews, research papers, security vulnerabilities, and other domain data that map directly to agent skills.

The launcher's tkinter file viewer is rigid and being replaced by the web dashboard. Local file ingestion should move to the dashboard too.

## Solution

A unified ingestion page in the web dashboard that lets operators:
1. Browse and fetch rows from HuggingFace datasets
2. Upload local files via browser file picker (or type a server-side path)
3. Stage selected items with file-type coloring
4. Dispatch to fleet as tasks OR ingest into RAG
5. Control storage with caps and LRU eviction

> **Note:** Web URL scraping (single page, crawl, RSS) is deferred to a future iteration. Phase 1 focuses on HuggingFace datasets and local files.

## Page Layout

Unified vertical scroll page with three zones:

### Zone 1: Source Pills (top bar)

Collapsed source cards as horizontal pill buttons. Each shows an icon, name, and row count. Clicking a pill expands its config panel inline (Zone 2). Only one source expanded at a time.

Pre-configured sources:
| Source | Dataset ID | Rows | Agent | Skill |
|--------|-----------|------|-------|-------|
| github-codereview | `ronantakizawa/github-codereview` | 355K | coder | code_review |
| codereviewer | `fasterinnerlooper/codereviewer` | 317K | coder | code_review |
| arxiv-summarization | `ccdv/arxiv-summarization` | 215K | researcher | summarize |
| scientific-papers | `armanc/scientific_papers` | 300K+ | researcher | summarize, synthesize |
| code-vulnerable | `tranquangtien15092005/code-vulnerable-10000` | 10K+ | security | security_audit |
| code-quality | `happylife365/code-quality-large` | 18K | coder | code_quality |

Special sources:
- **Local Files** — two modes: (1) browser `<input type="file">` upload for remote access, or (2) server-side path input for local operators (validated through `filesystem_guard.py`). Selected files appear in staging.
- **+ Add Source** — opens modal (see below)

### Zone 2: Expanded Source Panel (inline, below active pill)

When a HuggingFace dataset pill is clicked, an inline panel expands showing:

- **Header:** full dataset ID, total rows, total size, last fetch timestamp
- **Config row:**
  - Batch size (default 50, configurable)
  - Destination toggle: Tasks | RAG
  - Content column dropdown (auto-detected from dataset schema)
  - Target skill dropdown (pre-mapped, overridable)
- **Row preview:** paginated list of rows with checkboxes, showing first N characters of content column + token count estimate (heuristic: `len(text) // 4`)
- **Actions:** "Fetch Next N" button, "Stage Selected" button

### Zone 3: Staging Area (bottom, always visible)

Persistent staging area showing all items queued for ingestion. **Backed by `ingest_staging` table in `fleet.db`** so staged items survive dashboard restarts.
- Checkbox list with color-coded dots by source type (amber=HF coder, blue=HF researcher, red=HF security, green=local file, purple=web)
- Each item shows: source indicator, title/filename, token count
- Destination indicator: "→ Tasks" or "→ RAG"
- Action buttons: "Ingest Selected (N)", "Clear"
- Items staged from different sources can coexist

### Storage Bar (bottom edge, always visible)

Horizontal bar showing cache usage: `312 MB / 2 GB`
- Color transitions: green (<60%), amber (60-85%), red (>85%)
- Shows `fleet/knowledge/ingest_cache/` usage

## Add Source Modal

Single form for adding a HuggingFace dataset:

- Input: dataset ID (e.g., `username/dataset-name`)
- Auto-fetch on blur: schema, row count, column names, first 5 rows preview
- Config: skill mapping dropdown, agent role, batch size
- "Add" button saves to source list
- Error states: invalid ID, gated/private dataset, network timeout — shown inline

**Pre-configured sources** defined in `fleet.toml` (read-only from dashboard).
**User-added sources** stored in `fleet.db` `ingest_sources` table (avoids write contention on fleet.toml).

> **Web URL sources** deferred to Phase 2 (requires scraping library selection, robots.txt compliance, content extraction, rate limiting — significant additional scope).

## Storage Management

### Config (`fleet.toml`)
```toml
[ingest]
max_storage_mb = 2048        # global cache cap (default 2GB)
cache_dir = "knowledge/ingest_cache"
batch_size_default = 50      # default rows per fetch
eviction_policy = "lru"      # lru | fifo | manual
```

### Cache Behavior
- Downloaded dataset rows stored as JSONL in `knowledge/ingest_cache/<source_id>/`
- Each batch is a separate file: `batch_001.jsonl`, `batch_002.jsonl`
- Metadata file: `source_meta.json` (schema, last offset, total fetched, per-batch `dispatched_count` tracking)
- **LRU eviction:** when cache exceeds `max_storage_mb`, evict batches where `dispatched_count == row_count` (all rows dispatched as tasks or ingested to RAG). Batch files renamed to `.evicted` before deletion for audit trail.
- **Hard cap:** refuse to fetch if adding a batch would exceed cap and nothing is evictable — show warning in UI

### HuggingFace API Integration
- Use HuggingFace Dataset Viewer API (no auth required for public datasets)
- Endpoint: `GET https://datasets-server.huggingface.co/rows?dataset={id}&split=train&offset={n}&length={batch}`
- Schema detection: `GET https://datasets-server.huggingface.co/info?dataset={id}`
- No full dataset download — stream batches on demand
- All HTTP calls use explicit `timeout=15` per project conventions
- **Error handling:**
  - Invalid dataset ID → 404 from API → show "Dataset not found" in UI
  - Gated/private dataset → 401/403 → show "Dataset requires authentication (not supported)"
  - Rate limited → 429 → exponential backoff (3 retries, 2s/4s/8s), then show warning
  - Network timeout → show "HuggingFace API unreachable" with retry button
  - Schema detection failure → fall back to showing raw column names, let user pick

### Offline / Air-Gap Mode
- `is_offline()` or `is_air_gap()` → HuggingFace sources disabled (pills greyed out with tooltip "Requires network")
- Local file upload/path input still works
- Previously cached batches remain accessible for staging/dispatch

## Task Dispatch Flow

When destination is "Tasks":
1. For each staged row, create a fleet task with:
   - `type`: mapped skill name (e.g., `code_review`)
   - `payload_json`: `{"source": "hf:<dataset_id>", "row_id": N, "content": "<column value>", ...}`
   - `status`: PENDING
   - `assigned_to`: null (normal dispatch routing picks the right agent)
2. Tasks appear in the normal fleet queue and get assigned by the supervisor
3. Results stored in `knowledge/` per the skill's output contract

When destination is "RAG":
1. Content column extracted and chunked via existing `rag.py` ingest pipeline
2. Tagged with source metadata: `source=hf:<dataset_id>`, `row_id=N`
3. Searchable via `rag_query` skill

## Dashboard API Endpoints

New endpoints under `/api/ingest/`:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/ingest/sources` | List configured sources with metadata |
| POST | `/api/ingest/sources` | Add a new source (HF dataset ID or URL) |
| DELETE | `/api/ingest/sources/<id>` | Remove a source |
| GET | `/api/ingest/sources/<id>/schema` | Get dataset schema/columns |
| GET | `/api/ingest/sources/<id>/rows` | Fetch rows (params: offset, limit) |
| POST | `/api/ingest/stage` | Add items to staging area |
| GET | `/api/ingest/staging` | Get current staging contents |
| DELETE | `/api/ingest/staging/<id>` | Remove from staging |
| POST | `/api/ingest/dispatch` | Dispatch staged items to Tasks or RAG |
| GET | `/api/ingest/cache/stats` | Cache usage stats |
| POST | `/api/ingest/cache/evict` | Manual cache eviction |

## File Structure

```
fleet/
  ingest_blueprint.py          # Flask blueprint — all /api/ingest/ endpoints
  ingest_manager.py            # Core logic — fetch, cache, stage, dispatch
  knowledge/
    ingest_cache/              # Downloaded dataset batches (JSONL)
      github-codereview/
        source_meta.json
        batch_001.jsonl
        batch_002.jsonl
      arxiv-summarization/
        ...
  fleet.toml                   # [ingest] config section + [[ingest.sources]] defaults
  fleet.db                     # ingest_sources + ingest_staging tables (user-added)
  templates/
    dashboard.html             # New "Ingest" page section in the SPA
```

### New DB Tables (fleet.db)

```sql
CREATE TABLE IF NOT EXISTS ingest_sources (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL DEFAULT 'huggingface',  -- huggingface | local
    dataset TEXT,                               -- HF dataset ID
    skill TEXT NOT NULL,
    agent_role TEXT,
    content_column TEXT,
    batch_size INTEGER DEFAULT 50,
    enabled INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ingest_staging (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    row_id INTEGER,                            -- HF row offset, NULL for local files
    title TEXT,                                 -- display name
    content_preview TEXT,                       -- first 200 chars
    token_estimate INTEGER,
    destination TEXT DEFAULT 'tasks',           -- tasks | rag
    skill TEXT,
    file_path TEXT,                             -- for local file uploads
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Pre-configured Source Definitions (fleet.toml)

```toml
[ingest]
max_storage_mb = 2048
cache_dir = "knowledge/ingest_cache"
batch_size_default = 50
eviction_policy = "lru"

[[ingest.sources]]
id = "github-codereview"
type = "huggingface"
dataset = "ronantakizawa/github-codereview"
skill = "code_review"
agent_role = "coder"
content_column = "diff"
enabled = true

[[ingest.sources]]
id = "codereviewer"
type = "huggingface"
dataset = "fasterinnerlooper/codereviewer"
skill = "code_review"
agent_role = "coder"
content_column = "review"
enabled = true

[[ingest.sources]]
id = "arxiv-summarization"
type = "huggingface"
dataset = "ccdv/arxiv-summarization"
skill = "summarize"
agent_role = "researcher"
content_column = "article"
enabled = true

[[ingest.sources]]
id = "scientific-papers"
type = "huggingface"
dataset = "armanc/scientific_papers"
skill = "summarize"
agent_role = "researcher"
content_column = "article"
enabled = true

[[ingest.sources]]
id = "code-vulnerable"
type = "huggingface"
dataset = "tranquangtien15092005/code-vulnerable-10000"
skill = "security_audit"
agent_role = "security"
content_column = "code"
enabled = true

[[ingest.sources]]
id = "code-quality"
type = "huggingface"
dataset = "happylife365/code-quality-large"
skill = "code_quality"
agent_role = "coder"
content_column = "code"
enabled = true
```

## SSE Events

Ingestion operations push real-time updates via the existing SSE stream:

| Event Type | Payload | When |
|------------|---------|------|
| `ingest_fetch_start` | `{source_id, batch_size}` | Batch fetch begins |
| `ingest_fetch_complete` | `{source_id, rows_fetched, cache_mb}` | Batch downloaded |
| `ingest_dispatch_progress` | `{total, completed, failed}` | During bulk dispatch |
| `ingest_dispatch_complete` | `{destination, count, source_id}` | All staged items dispatched |
| `ingest_cache_warning` | `{usage_pct, cache_mb, max_mb}` | Cache >85% full |

## Out of Scope (Phase 2+)

- Web URL scraping (single page, crawl, RSS — requires scraping library, robots.txt, rate limiting)
- Custom API endpoint sources with field mapping
- Scheduled auto-fetch (cron-based batch ingestion)
- Dataset quality scoring / filtering before staging
- Cross-source deduplication
- Streaming ingestion (process rows as they download)
- Authentication for private HF datasets
- Multi-skill dispatch per source (e.g., codereviewer → code_review + code_refactor)
