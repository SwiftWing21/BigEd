# Outputs Module — Web Dashboard Integration

**Date:** 2026-03-31
**Status:** Approved
**Replaces:** `BigEd/launcher/modules/mod_outputs.py` (tkinter, lost in transition)

## Problem

The outputs module (`mod_outputs.py`) was a tkinter knowledge browser with HITL feedback. It survived in the old launcher but never got a web dashboard equivalent. The Modules page shows it as "Enabled v0.23" but `fleet/modules/outputs/` only has a stub `manifest.json` — no implementation.

## Design

Split the old module's functionality across two existing dashboard pages:

- **Docs page** — knowledge file browser (categories, file list, markdown preview)
- **HITL page** — unreviewed outputs queue with approve/reject/notes

Both pages share a common REST backend via `fleet/outputs_blueprint.py`.

## Backend: `fleet/outputs_blueprint.py`

Place file at `fleet/outputs_blueprint.py`. Import shared helpers from `dashboard_utils` (`_require_role`, `_check_rate_limit`, `_load_config`, `FLEET_DIR`, `get_conn`, `query`).

Flask blueprint registered as `outputs_bp`, prefix `/api/outputs`.

### Categories

Knowledge subdirectories that contain reviewable agent outputs:

```python
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
```

**Excluded intentionally:** `code_discussion`, `discussion`, `code_writes`, `ingests`, `leads`, `plans`, `ingest_cache`, `marathon`, `prompt_optimization` — these are internal artifacts or raw data, not reviewable outputs.

**Missing dirs:** Categories whose subdirectory doesn't exist on disk return `count: 0` — no crash. Skills may create these dirs in the future.

### Path contract

- **API responses** use paths relative to `knowledge/` (e.g. `"security/reviews/file.md"`)
- **DB storage** uses absolute paths (what `db.submit_feedback()` stores)
- **Conversion:** the blueprint resolves relative → absolute for DB calls, and strips the `knowledge/` prefix for API responses. The DB is the source of truth for feedback lookups.

### Endpoints

#### `GET /api/outputs/categories`

Returns category names with file counts.

```json
[
  {"name": "All", "count": 342},
  {"name": "Code Reviews", "dir": "code_reviews", "count": 47},
  ...
]
```

Implementation: `os.listdir` + count `.md` files in each subdir. Cache for 30s.

#### `GET /api/outputs/files?category=Security&limit=50&offset=0`

Returns file list sorted by mtime descending, with feedback badge status.

```json
{
  "files": [
    {
      "name": "security_review_2026-03-30.md",
      "path": "security/reviews/security_review_2026-03-30.md",
      "category": "Security",
      "size": 4521,
      "mtime": "2026-03-30T14:22:00",
      "verdict": "approved"
    },
    ...
  ],
  "total": 47
}
```

Implementation: glob `knowledge/<subdir>/**/*.md`, stat each, join with `db.get_feedback_bulk()` using absolute paths for lookup. Path returned in response is relative to `knowledge/` — never expose absolute paths.

#### `GET /api/outputs/file?path=security/reviews/security_review_2026-03-30.md`

Returns file content (raw text, max 32KB).

```json
{
  "path": "security/reviews/security_review_2026-03-30.md",
  "content": "# Security Review...",
  "size": 4521,
  "verdict": "approved",
  "feedback_notes": "Looks good, no issues"
}
```

**Security:** Path-traversal guard — resolve the path, verify it's under `knowledge/`, reject `..` or absolute paths. Use `pathlib.Path.resolve()` and check `.is_relative_to(knowledge_dir)`.

#### `POST /api/outputs/feedback`

Submit approve/reject with optional notes.

```json
{"path": "security/reviews/security_review_2026-03-30.md", "verdict": "approved", "notes": "Looks good"}
```

Response: `{"ok": true}`

Implementation: validate verdict is one of `("approved", "rejected", "neutral")` to match `db.submit_feedback()`. Convert relative path to absolute before calling `db.submit_feedback(abs_path, verdict, feedback_text=notes)`. Requires `operator` role minimum.

#### `GET /api/outputs/unreviewed?limit=20`

Files with no feedback, newest first. For the HITL page.

```json
{
  "files": [
    {
      "name": "quality_review_2026-03-31.md",
      "path": "quality/reviews/quality_review_2026-03-31.md",
      "category": "Quality",
      "size": 3200,
      "mtime": "2026-03-31T09:15:00",
      "snippet": "## Summary\nFound 3 issues..."
    },
    ...
  ],
  "total": 15
}
```

Implementation: glob all `.md` files, get bulk feedback, filter to those with no verdict, return newest N with first 200 chars as snippet.

### Blueprint registration

In `dashboard.py` (or `app_factory.py` if Phase 5 created it):

```python
from outputs_blueprint import outputs_bp
app.register_blueprint(outputs_bp)
```

## Frontend Integration

### Docs Page

Add a "Knowledge Browser" section. Uses the existing dashboard CSS framework.

**Layout:** Category dropdown (left) + file list (left column, scrollable) + preview pane (right column, 2/3 width).

**Behavior:**
- On load: fetch `/api/outputs/categories` to populate dropdown with counts
- On category change: fetch `/api/outputs/files?category=X`
- On file click: fetch `/api/outputs/file?path=X`, render markdown in preview pane
- Badge indicators: green checkmark for approved, red X for rejected, no badge for unreviewed
- Pagination: "Load more" button if `total > limit`

### HITL Page

Add an "Unreviewed Outputs" card below existing HITL content.

**Layout:** Vertical list of unreviewed files, each with: filename, category tag, snippet, approve/reject buttons, notes input.

**Behavior:**
- On load: fetch `/api/outputs/unreviewed?limit=20`
- On approve/reject: POST to `/api/outputs/feedback`, remove item from list with fade animation
- Empty state: "All outputs reviewed" message
- Count badge on card header showing total unreviewed

## DB Dependencies

Uses existing `db.submit_feedback()` and `db.get_feedback()` / `db.get_feedback_bulk()` — no schema changes needed. These functions are already used by the tkinter module.

## Security

- Path traversal: all file paths validated as relative to `knowledge/` via `Path.resolve().is_relative_to()`
- Role check: file browsing requires `viewer` role, feedback submission requires `operator` role
- Rate limiting: `_check_rate_limit` on all endpoints (10/min for writes, 30/min for reads)
- Content size: file content capped at 32KB to prevent memory issues
- No file writes or deletes — read-only browser + feedback metadata only

## Not in scope

- File export/CSV download
- File editing or deletion
- Real-time SSE updates for new outputs (separate SSE audit will determine this)
- Search within file contents (future — can use RAG)

## Testing

- `tests/test_outputs_blueprint.py`: endpoint tests with mock filesystem + mock db
- Path traversal rejection tests
- Category counting tests
- Feedback submission + retrieval round-trip
- Role-based access tests
