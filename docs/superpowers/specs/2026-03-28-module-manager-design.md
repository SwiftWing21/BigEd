# Module Manager — Design Spec

**Date:** 2026-03-28
**Status:** Approved
**Goal:** Dashboard UI for browsing, installing, and managing BigEd modules, plus agent-generated module recommendations.

---

## Problem

The module system backend is complete (hub.py, loader, manifest, checksums, registry, enterprise hub) but has no web UI. Module management requires CLI or the tkinter launcher settings. Agent-generated recommendations are deferred with no implementation.

## Solution

### 1. REST API (modules_blueprint.py)

New Flask blueprint wrapping hub.py operations:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/modules` | GET | List installed modules with status, version, lifecycle |
| `/api/modules/available` | GET | List available modules from hub registry |
| `/api/modules/updates` | GET | List modules with available updates |
| `/api/modules/install` | POST | Install module by name `{name: "analytics_pro"}` |
| `/api/modules/<name>/enable` | POST | Enable installed module |
| `/api/modules/<name>/disable` | POST | Disable installed module |
| `/api/modules/<name>/uninstall` | DELETE | Uninstall module |
| `/api/modules/suggestions` | GET | Get agent-generated recommendations |
| `/api/modules/suggestions/<id>/dismiss` | POST | Dismiss a suggestion |

All mutating endpoints require the module to exist in the manifest or registry. Install verifies SHA-256 checksum. Enable/disable updates fleet.toml `[launcher.tabs]`.

### 2. Dashboard UI — Split Panel (Primary)

New "Modules" nav item between Views and Settings.

**Left Panel (30% width):**
- Search/filter input at top
- Scrollable module list
- Each row: status dot (green=enabled, gray=disabled, blue=available) + name + version badge
- Installed modules grouped first, then available from hub
- Click to select → loads detail in right panel

**Right Panel (70% width):**
- Module name, icon, version, author
- Description (full text)
- Status badge (Enabled/Disabled/Available/Deprecated)
- Dependencies list (with status: installed or missing)
- Data schema info (table name, field count)
- Metrics: file size, data record count
- Action buttons:
  - Installed+Enabled: Disable | Uninstall | Export Data
  - Installed+Disabled: Enable | Uninstall
  - Available: Install (shows dependency check first)
  - Deprecated: Warning banner with sunset version

**View Toggle:**
- Small icon button in page header to switch Split Panel ↔ Card Grid
- Preference saved to localStorage (`biged_module_view`)

### 3. Dashboard UI — Card Grid (Alternate View)

Toggled via view switch button. Same data, different presentation:

- 2-3 column grid of module cards
- Each card: icon, name, version, description preview, tags, status
- Installed modules: solid border, action menu
- Available modules: dashed border, Install button
- Click card to open detail modal or inline expand

### 4. Agent Recommendations

**Skill:** `fleet/skills/module_recommend.py`

```python
SKILL_NAME = "module_recommend"
DESCRIPTION = "Analyze fleet activity and suggest useful modules"
REQUIRES_NETWORK = True  # fetches hub registry

def run(task, context):
    # 1. Query task type distribution (last 7 days)
    # 2. Query skill usage patterns
    # 3. Fetch hub registry for available modules
    # 4. Score each uninstalled module by relevance to fleet activity
    # 5. Write top 3 suggestions to module_suggestions table
    return {"status": "ok", "suggestions": [...]}
```

**Scoring logic:**
- Match module tags against most-used skill types
- Boost score if module addresses frequent errors or missing capabilities
- Penalize if module's dependencies aren't met
- Threshold: only suggest if relevance score > 0.5

**DB table:** `module_suggestions`
```sql
CREATE TABLE IF NOT EXISTS module_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name TEXT NOT NULL,
    reason TEXT NOT NULL,
    relevance_score REAL NOT NULL,
    dismissed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(module_name)
)
```

**Dashboard display:** Suggestion cards at the top of the Modules page (above the module list). Each card shows module name, reason, and an Install button. Dismissible (X button calls `/api/modules/suggestions/<id>/dismiss`).

**Idle curriculum:** Add to planner.toml, runs once per day:
```toml
[[tasks]]
type = "module_recommend"
[tasks.payload]
lookback_days = 7
max_suggestions = 3
```

### 5. Hub Repo Structure

**Private (dev):** `SwiftWing21/BigEd-ModuleHub`
- All modules including experimental/untested
- registry.json with full module list
- fleet.toml `[modules] hub_url` points here

**Public (upstream):** `SwiftWing21/BigEd-ModuleHub-public`
- Curated, tested modules only
- Separate registry.json
- Manual git push to sync (cherry-pick what goes public)
- Public users configure `hub_url` to point here

No automation — developer manually pushes tested modules from private to public.

## Files Changed

| File | Action | Purpose |
|------|--------|---------|
| `fleet/modules_blueprint.py` | Create (~200 lines) | REST API for module operations |
| `fleet/templates/dashboard.html` | Modify | Add Modules nav + Split Panel + Card Grid pages |
| `fleet/dashboard.py` | Modify (2 lines) | Register modules_blueprint |
| `fleet/skills/module_recommend.py` | Create (~100 lines) | Agent recommendation skill |
| `fleet/db.py` | Modify (10 lines) | Add module_suggestions table to init_db() |
| `fleet/idle_curricula/planner.toml` | Modify (5 lines) | Add module_recommend to planner rotation |
| `fleet/smoke_test.py` | Modify | Add module API test |

## Out of Scope

- Auto-install / autonomous module management (future Phase 2-3)
- Module dependency resolution with auto-install of missing deps
- Module versioning / rollback
- Public hub CI automation
- Module submission workflow (community contributions)
- Per-module settings UI (modules handle their own settings via `get_settings()`)
