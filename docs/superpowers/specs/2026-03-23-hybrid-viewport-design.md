# Hybrid ViewPort — Unified View Platform + Launcher Decomposition

**Date:** 2026-03-23
**Status:** Draft
**Approach:** C — Hybrid ViewPort (Native Shell + Embedded Views)

## Problem

BigEd has two full-featured UIs (CustomTkinter launcher at ~10,400 lines across launcher.py + ui/ modules, Flask web dashboard at 4,100+ lines backend + 3,000-line SPA frontend) that duplicate effort, diverge in design language, and can't share visualization components. The launcher's main file (`launcher.py`) is a 6,700-line god-object with ~230 methods in a single class. There is no extensible way for new modules to surface data in the UI — each integration is hand-wired.

## Goals

1. **Extensible data platform** — any module registers as a data source; any view can consume it
2. **Dynamic swimlane/flow graphs** — progressive detail visualization with drag-and-drop builder
3. **Shared view engine** — web-based graph renderer consumed by both dashboard and launcher (via embedded webview)
4. **Unified design tokens** — bidirectional Figma sync across both UIs
5. **Launcher decomposition** — break the 6,700-line god-object into focused modules

## Non-Goals

- Replacing the launcher entirely (native shell stays for boot, tray, Ollama, chat consoles)
- Rebuilding chat consoles in the browser
- Real-time collaborative editing of graph views (single-operator tool)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  design-tokens.json                  │
│              (W3C DTCG, bidirectional)               │
└──────────┬──────────────────────┬───────────────────┘
           │                      │
    token_bridge.py          token_bridge.py
           │                      │
           ▼                      ▼
    tokens.css (web)        theme.py (native)
           │                      │
┌──────────┴──────────┐  ┌───────┴────────────────────┐
│   Web Dashboard      │  │   Launcher (Native Shell)   │
│   (Flask + JS SPA)   │  │   (CustomTkinter)           │
│                      │  │                             │
│  ┌────────────────┐  │  │  ┌───────────────────────┐ │
│  │  View Engine    │  │  │  │ Embedded Webview       │ │
│  │  (Cytoscape.js) │◄─┼──┼──│ /view/embed/<name>    │ │
│  └────────────────┘  │  │  └───────────────────────┘ │
│                      │  │                             │
│  ┌────────────────┐  │  │  Native-only:              │
│  │  View Builder   │  │  │  - Boot sequence           │
│  │  /view/builder  │  │  │  - System tray             │
│  └────────────────┘  │  │  - Ollama lifecycle         │
│                      │  │  - Chat consoles            │
│  ┌────────────────┐  │  │  - Local settings           │
│  │  Data Sources   │  │  │  - Hardware monitoring      │
│  │  /api/views/*   │  │  └────────────────────────────┘
│  └───────┬────────┘  │
│          │           │
│  ┌───────┴────────┐  │
│  │ View Registry   │  │
│  │ view_registry.py│  │
│  └───────┬────────┘  │
└──────────┴───────────┘
           │
    ┌──────┴──────────────────────────┐
    │     Registered Modules          │
    │  supervisor, agents, skills,    │
    │  autoresearch, RAG, federation, │
    │  billing, marketplace, ...      │
    └─────────────────────────────────┘
```

---

## 1. Launcher Decomposition

### Current State

`launcher.py` is 6,706 lines with ~230 methods in a single `BigEdCC` class plus ~45 module-level utility functions (~700 lines, before the class definition). Existing extractions use the mixin pattern (BootManagerMixin, TrayManagerMixin).

### Line Budget

| Extraction | Type | Lines Removed |
|-----------|------|---------------|
| `ui/comm_tab.py` (CommTabMixin) | Class methods → mixin | ~1,400 |
| `ui/ollama_manager.py` (OllamaManagerMixin) | Class methods → mixin | ~400 |
| `ui/dispatch.py` (DispatchMixin) | Class methods → mixin | ~400 |
| `ui/fleet_status.py` | Module-level functions | ~400 |
| `ui/utils.py` | Module-level functions + Tooltip class | ~300 |
| **Total removed** | | **~2,900** |
| **Remaining launcher.py** | | **~3,800** |

Note: mixin extraction reduces the BigEdCC class directly. Module-level utility extraction reduces the file size but not the class — these are already separate functions defined before the class.

### New Mixin Extractions (class methods)

| File | Mixin Class | Methods | Lines |
|------|-------------|---------|-------|
| `ui/comm_tab.py` | `CommTabMixin` | 34 | ~1,400 |
| `ui/ollama_manager.py` | `OllamaManagerMixin` | 18 | ~400 |
| `ui/dispatch.py` | `DispatchMixin` | 16 | ~400 |

### Utility Extractions (module-level functions)

| File | Functions | Purpose |
|------|-----------|---------|
| `ui/fleet_status.py` | `parse_status()`, `_check_supervisor_liveness()`, `_zombie_sweep()`, `_graceful_save_tasks()`, `_unload_all_ollama_models()`, `read_log_tail()`, `_read_combined_logs()`, `get_hw_stats()` | Fleet monitoring utilities |
| `ui/utils.py` | `_relative_time()`, `themed_name()`, `_shell_safe()`, `wsl()`, `wsl_bg()`, `_ctx_preview_confirm()`, `Tooltip`, settings load/save | Shared UI helpers |

### Result

```python
class BigEdCC(
    BootManagerMixin,       # ui/boot.py (existing)
    TrayManagerMixin,       # ui/tray.py (existing)
    CommTabMixin,           # ui/comm_tab.py (NEW)
    OllamaManagerMixin,     # ui/ollama_manager.py (NEW)
    DispatchMixin,          # ui/dispatch.py (NEW)
    ctk.CTk,
):
```

launcher.py: 6,706 → ~3,800 lines. Pure refactor — no behavior change.

### CommTabMixin Detail (34 methods)

Covers the entire Fleet Comm tab:

- **Tab builder**: `_build_tab_comm`, `_toggle_comm_requests`, `_expand_comm_requests`, `_toggle_comm_pin`, `_update_comm_request_view`, `_refresh_comm`
- **Chat providers**: `_select_provider`, `_update_model_swapper`, `_on_chat_enter`, `_send_manual_chat`, `_append_tagged_message`, `_append_chat_response`, `_set_streaming`, `_unified_local_chat`, `_unified_claude_chat`, `_unified_gemini_chat`, `_check_provider_connections`
- **Usage**: `_update_usage_bar`, `_show_usage_popover`, `_show_quarantine_controls`
- **VS Code**: `_reply_via_vscode`, `_find_vscode`
- **Voice**: `_voice_input`, `_voice_into_task`, `_voice_into_active_entry`
- **OAuth**: `_launch_oauth_session`
- **HITL**: `_send_human_response`, `_load_hitl_to_chat`, `_draft_comm_response`, `_draft_via_claude`, `_draft_via_gemini`, `_draft_via_local`
- **Advisories**: `_approve_advisory`, `_dismiss_advisory`

### OllamaManagerMixin Detail (18 methods)

Covers Ollama lifecycle and model management:

- **Status**: `_poll_ollama`, `_is_ollama_running`, `_apply_ollama_status`, `_ollama_status`
- **Control**: `_run_ollama_start`, `_start_ollama`, `_stop_ollama`
- **Models**: `_populate_model_dropdown`, `_quick_model_switch`, `_ollama_script`
- **Health**: `_on_ollama_recovered`, `_recover_offline_agents`, `_schedule_ollama_watch`, `_send_keepalive`
- **Strategy**: `_apply_strategy`, `_get_complex_provider`, `_toggle_claude_research`, `_is_eco_mode`, `_is_training_active`

### DispatchMixin Detail (16 methods)

Covers prompt queue, task dispatch, and marathon:

- **Queue**: `_pq_toggle_add_row`, `_pq_add_item`, `_pq_remove_item`, `_pq_refresh_list`, `_pq_start`, `_pq_stop`, `_pq_run`
- **Dispatch**: `_dispatch_task`, `_dispatch_raw`
- **Marathon**: `_start_marathon`, `_show_marathon_log`, `_stop_marathon`
- **Idle**: `_enable_idle`, `_disable_idle`
- **Search**: `_open_search_dialog`, `_show_results`

---

## 2. Data Source Registry

### File: `fleet/view_registry.py`

Central registry where modules declare their graph-renderable data.

### Registration Contract

```python
# Required fields — minimum to appear on any graph
register_source(
    name="autoresearch",
    category="training",
    node_types=["trainer", "evaluator", "dataset"],
    edge_types=["trains_on", "produces", "evaluates"],
    data_endpoint="/api/autoresearch/graph",
)

# Optional progressive enhancement
register_source(
    name="supervisor",
    category="fleet",
    node_types=["agent", "worker", "conductor"],
    edge_types=["dispatches", "reports_to", "heartbeat"],
    data_endpoint="/api/fleet/graph",
    icon="cpu",
    color="#4caf50",
    layout_hint="radial",
    animation_rules={"dispatches": "pulse", "heartbeat": "fade"},
    metrics=["tok_s", "queue_depth", "latency_ms"],
)
```

### Category Defaults

Categories provide default icon, color, and layout so modules don't repeat themselves:

| Category | Icon | Color | Layout |
|----------|------|-------|--------|
| `fleet` | cpu | `#4caf50` | radial |
| `training` | flask | `#ff9800` | swimlane |
| `storage` | database | `#4fc3f7` | cluster |
| `external` | globe | `#9c7cfc` | tree |
| `security` | shield | `#f44336` | cluster |

Modules only override what differs from their category default.

### Data Endpoint Contract

Each registered source's `data_endpoint` must return:

**Success response:**
```json
{
  "source": "supervisor",
  "timestamp": "2026-03-23T12:00:00Z",
  "nodes": [
    {
      "id": "researcher",
      "type": "agent",
      "status": "IDLE",
      "metrics": {"tok_s": 12.4, "queue_depth": 0}
    }
  ],
  "edges": [
    {
      "source": "supervisor",
      "target": "researcher",
      "type": "dispatches",
      "weight": 3
    }
  ],
  "truncated": false
}
```

**Error response** (source unavailable or unhealthy):
```json
{
  "source": "autoresearch",
  "error": "source_unavailable",
  "message": "Training pipeline not running"
}
```

The view engine renders error sources as grayed-out nodes with the error message as a tooltip. The `truncated` field indicates when the source has more nodes than the default limit (200 nodes). The builder can override this via a `?limit=` query parameter.

### Discovery

`GET /api/views/sources` returns all registered sources with their metadata. The builder uses this to populate its source palette.

### Registration Timing

Dashboard startup calls `view_registry.discover_and_register()` which iterates known modules, imports each one, and calls its `_register_views()` function. Import errors are caught per-module and logged as warnings — a failing module does not prevent other sources from registering. The health panel reports registration status (e.g., "5 of 7 sources registered"). No central manifest file — adding a module is self-contained (just implement `_register_views()`).

---

## 3. View Engine + Graph Renderer

### Endpoints

| Endpoint | Purpose | Consumers |
|----------|---------|-----------|
| `/view/graph/<name>` | Full graph view with chrome | Dashboard |
| `/view/embed/<name>` | Minimal chrome (no sidebar/header) | Launcher webview |
| `/view/builder` | Drag-and-drop view builder | Both |

### Rendering Stack

- **Cytoscape.js** (already in dashboard) — node/edge layout, interaction, zoom
- **Canvas overlay** — animated particles for data flow
- **CSS custom properties** — reads from generated tokens.css

### Progressive Zoom

| Zoom Level | Renders | Performance |
|------------|---------|-------------|
| < 0.5 (overview) | Color-coded dots + edges. Heat map coloring by throughput | Lightweight — handles hundreds of nodes |
| 0.5 – 1.0 (mid) | Node labels, edge thickness by weight, metric badges on hover | Moderate — DOM overlays for badges |
| > 1.0 (detail) | Animated particles along edges, full metric panels, sparkline histories | Heavy — viewport-limited rendering |

### Layout Engines

Mapped from `layout_hint` in the source registry:

| Hint | Cytoscape Layout | Use Case |
|------|-----------------|----------|
| `radial` | `concentric` | Hub-and-spoke (supervisor → agents) |
| `cluster` | `cose` | Grouped modules |
| `swimlane` | Custom positioned | Cross-module data flow with named lanes |
| `tree` | `dagre` | DAG task dependencies |

### Real-Time Updates

The view engine subscribes to SSE at `/api/stream`. When node status or edge weights change, the graph patches in-place via Cytoscape's data API — no full reload.

### Launcher Embedding

The launcher uses `pywebview` to open graph views in a managed companion window. `pywebview` uses the system browser engine (WebView2 on Windows, WebKit on macOS, WebKitGTK on Linux) — full React/Cytoscape.js/Chart.js support with ~500KB overhead and no bundled Chromium. The companion window opens alongside the launcher (not embedded inside it) since no library can reliably embed a modern browser engine inside a CustomTkinter frame cross-platform. The launcher manages the webview window lifecycle (open/close/focus) and communicates via pywebview's built-in Python-JS bridge.

A JS bridge enables native actions from graph interactions:

```javascript
// Web side — notify native shell of clicks
window.bigEdBridge = {
  onNodeClick: (nodeId, nodeType) => { /* native handler */ },
  onEdgeClick: (edgeId, edgeType) => { /* native handler */ },
};
```

```python
# Launcher side — register native handlers
def _on_graph_node_click(self, node_id, node_type):
    if node_type == "agent":
        self._agents_edit_dialog({"name": node_id})
```

**Graceful degradation**: if the dashboard isn't running, the webview shows a static "Dashboard offline" placeholder. Native-only controls remain functional.

---

## 4. Swimlane Builder

### Location: `/view/builder`

### UI Layout

**Left panel — Source palette:**
- All registered data sources from `/api/views/sources`
- Grouped by category (fleet, training, storage, external)
- Each source expandable to show node types as draggable chips
- Search/filter bar

**Center — Canvas:**
- Drop zone for sources
- Sources appear as expandable groups with node types
- Click source → target to draw edges; edge type dropdown from registered edge_types
- Horizontal swimlane dividers (draggable) to create named lanes

**Right panel — Properties:**
- Selected node/edge configuration
- Metric selection from available metrics list
- Animation toggle per edge type
- Color override (or "use category default")
- Zoom breakpoint configuration

### View Config Format

Saved as JSON in `fleet/views/<name>.json`:

```json
{
  "schema_version": 1,
  "name": "training-pipeline",
  "description": "Data flow from ingestion to model evaluation",
  "layout": "swimlane",
  "lanes": [
    {"name": "Ingestion", "sources": ["rag", "arxiv_fetch"]},
    {"name": "Training", "sources": ["autoresearch"]},
    {"name": "Evaluation", "sources": ["reinforcement", "ab_testing"]}
  ],
  "edges": [
    {"from": "rag:chunk", "to": "autoresearch:dataset", "type": "feeds"},
    {"from": "autoresearch:trainer", "to": "reinforcement:scorer", "type": "produces"}
  ],
  "metrics_overlay": ["tok_s", "queue_depth"],
  "animation": {"feeds": "flow", "produces": "pulse"},
  "zoom_breakpoints": {"labels": 0.5, "metrics": 0.8, "animation": 1.2}
}
```

### Round-Trip

Builder generates JSON → power users edit JSON directly → builder loads edited JSON. Both paths are first-class.

### Pre-Built Views

Ship with BigEd:

| View | Layout | Shows |
|------|--------|-------|
| `fleet-overview` | radial | Supervisor → agents → skills hub-and-spoke |
| `training-pipeline` | swimlane | Data ingestion → training → evaluation flow |
| `data-flow` | cluster | All registered sources, all connections |
| `bottleneck-detector` | cluster | Same as data-flow + animation + latency metrics enabled |

---

## 5. Design Token Bridge

### File: `fleet/token_bridge.py`

### Token Flow

```
design-tokens.json (W3C DTCG)
       │
       ├──► fleet/static/tokens.css        (CSS custom properties)
       ├──► BigEd/launcher/ui/theme.py      (Python constants)
       └──► figma-export/tokens-diff.json   (changelog for Figma re-import)
```

### Sync Directions

| Direction | Trigger | Flow |
|-----------|---------|------|
| Figma → code | `/figma-import` command in launcher | JSON → design-tokens.json → bridge regenerates CSS + theme.py |
| Code → Figma | Developer edits design-tokens.json | Bridge regenerates CSS + theme.py; tokens-diff.json shows changes for manual Figma import |
| Code → code | Developer edits `THEME_PRESETS` dict in theme.py | Reverse parser reads only the `THEME_PRESETS` dictionary (strict format required) → updates design-tokens.json → bridge regenerates CSS |

### Conflict Resolution

The token file includes a `version` timestamp. The bridge warns when generated outputs are older than the token file. No automatic merge — human picks which side wins.

### Scope

**Synced:** colors, typography, spacing, component tokens (button variants, badges, alerts)

**Platform-specific (not synced):**
- CustomTkinter widget constructors (theme.py only)
- CSS Grid/Flexbox layout rules (tokens.css only)
- Cytoscape graph styling (reads CSS custom properties at render time)

---

## 6. Migration Plan

### Phase 1: Launcher Decomposition

Pure refactor. No new features, no behavior change.

- Extract CommTabMixin → `ui/comm_tab.py`
- Extract OllamaManagerMixin → `ui/ollama_manager.py`
- Extract DispatchMixin → `ui/dispatch.py`
- Extract utilities → `ui/fleet_status.py` + `ui/utils.py`
- launcher.py: 6,700 → ~4,000 lines

### Phase 2: Data Source Registry + Token Bridge

Foundation for the view platform.

- Build `fleet/view_registry.py`
- Register existing fleet modules (supervisor, agents, skills, training, RAG)
- Build `fleet/token_bridge.py`
- Wire dashboard CSS to generated tokens.css
- Add `/api/views/sources` discovery endpoint

### Phase 3: View Engine

The graph renderer.

- Add `/view/graph/<name>` and `/view/embed/<name>` endpoints
- Build progressive zoom renderer on Cytoscape.js
- Ship 4 pre-built views
- Migrate dashboard Pipeline tab to new view engine

### Phase 4: Builder

The drag-and-drop tool.

- Add `/view/builder` endpoint
- Source palette from registry, Cytoscape editable canvas
- Config save/load from `fleet/views/*.json`
- Add to dashboard sidebar

### Phase 5: Launcher Webview Integration

Connect the two shells.

- Add webview widget to launcher
- Embed `/view/embed/fleet-overview` in Command Center
- JS bridge for node click → native action
- "Open in Browser" button for full dashboard
- Graceful degradation when dashboard offline

### Testing Per Phase

- **Phase 1**: Run `smoke_test.py --fast` (22/22). Manual launch test — verify all tabs render, boot sequence completes, chat consoles open. No new tests needed; this is a pure refactor.
- **Phase 2**: Add smoke test for `/api/views/sources` endpoint (returns registered sources). Verify token bridge generates valid CSS and valid theme.py.
- **Phase 3**: Add smoke test for `/view/graph/fleet-overview` (returns 200). Visual QA for progressive zoom levels.
- **Phase 4**: Manual QA — create a view in builder, save, reload, verify round-trip.
- **Phase 5**: Manual QA — verify webview loads in launcher, node click triggers native action, graceful degradation when dashboard offline.

### Phase Independence

Each phase is independently shippable. Phases 4 and 5 deliver maximum value after Phase 3 (they need the view engine endpoints), but each phase works on its own.

### What Doesn't Change

- Boot sequence (native — must work without dashboard)
- System tray (native)
- Ollama lifecycle (native — hardware control)
- Chat consoles (native — API key management, OAuth)
- Local settings (native)

---

## File Inventory

### New Files

| File | Phase | Purpose |
|------|-------|---------|
| `BigEd/launcher/ui/comm_tab.py` | 1 | CommTabMixin (34 methods) |
| `BigEd/launcher/ui/ollama_manager.py` | 1 | OllamaManagerMixin (18 methods) |
| `BigEd/launcher/ui/dispatch.py` | 1 | DispatchMixin (16 methods) |
| `BigEd/launcher/ui/fleet_status.py` | 1 | Fleet monitoring utilities |
| `BigEd/launcher/ui/utils.py` | 1 | Shared UI helpers |
| `fleet/view_registry.py` | 2 | Data source registry |
| `fleet/views_blueprint.py` | 2 | Flask blueprint for all `/view/*` and `/api/views/*` endpoints |
| `fleet/token_bridge.py` | 2 | Design token sync |
| `fleet/static/tokens.css` | 2 | Generated CSS custom properties |
| `fleet/views/*.json` | 3 | Pre-built view configs |
| `fleet/templates/view_graph.html` | 3 | Graph renderer template |
| `fleet/templates/view_builder.html` | 4 | Builder template |

### Modified Files

| File | Phase | Change |
|------|-------|--------|
| `BigEd/launcher/launcher.py` | 1 | Reduce from 6,700 to ~4,000 lines |
| `BigEd/launcher/ui/theme.py` | 2 | Add generated-from-tokens marker |
| `fleet/dashboard.py` | 2-4 | Add view endpoints + source discovery |
| `fleet/templates/dashboard.html` | 3 | Pipeline tab uses view engine |
| `figma-export/design-tokens.json` | 2 | Add version timestamp field |
