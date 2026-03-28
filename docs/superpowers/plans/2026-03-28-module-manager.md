# Module Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dashboard UI for browsing, installing, and managing BigEd modules with agent-generated recommendations.

**Architecture:** Flask blueprint wraps existing hub.py client. Dashboard gets a new "Modules" nav section with Split Panel (primary) and Card Grid (alternate) views. Agent recommendation skill writes suggestions to DB, displayed as dismissible cards.

**Tech Stack:** Python/Flask, JavaScript (DOM API), SQLite, hub.py (existing)

**Spec:** `docs/superpowers/specs/2026-03-28-module-manager-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `fleet/modules_blueprint.py` | Create | 9 REST endpoints wrapping hub.py |
| `fleet/dashboard.py` | Modify (~4 lines) | Register blueprint, import |
| `fleet/db.py` | Modify (~10 lines) | Add `module_suggestions` table to `init_db()` |
| `fleet/templates/dashboard.html` | Modify | Modules nav item + Split Panel + Card Grid UI |
| `fleet/skills/module_recommend.py` | Create | Agent recommendation skill |
| `fleet/idle_curricula/planner.toml` | Modify (5 lines) | Add module_recommend task |
| `fleet/smoke_test.py` | Modify | Add modules API smoke test |

---

### Task 1: DB Schema — module_suggestions table

**Files:**
- Modify: `fleet/db.py` — inside `init_db()` function

- [ ] **Step 1: Add module_suggestions table to init_db()**

Find the `init_db()` function in `fleet/db.py`. Add this CREATE TABLE after the existing tables:

```python
            conn.execute("""
                CREATE TABLE IF NOT EXISTS module_suggestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    module_name TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    relevance_score REAL NOT NULL,
                    dismissed INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(module_name)
                )
            """)
```

- [ ] **Step 2: Run smoke tests to verify no regression**

Run: `python fleet/smoke_test.py --fast`
Expected: All tests pass (48/48)

- [ ] **Step 3: Commit**

```bash
git add fleet/db.py
git commit -m "feat(db): add module_suggestions table for agent recommendations"
```

---

### Task 2: REST API Blueprint — modules_blueprint.py

**Files:**
- Create: `fleet/modules_blueprint.py`
- Modify: `fleet/dashboard.py` — register the blueprint

- [ ] **Step 1: Create modules_blueprint.py**

```python
"""
BigEd CC — Module Management REST API.

Wraps BigEd/launcher/modules/hub.py for dashboard access.
Provides install, uninstall, enable, disable, available, updates, suggestions.
"""
import json
import logging
import sys
from pathlib import Path
from flask import Blueprint, jsonify, request

log = logging.getLogger("modules_api")

modules_bp = Blueprint("modules", __name__)

# hub.py lives in BigEd/launcher/modules/
_HUB_DIR = Path(__file__).parent.parent / "BigEd" / "launcher" / "modules"


def _get_hub():
    """Lazy-load ModuleHub to avoid import at module level."""
    sys.path.insert(0, str(_HUB_DIR))
    try:
        from hub import ModuleHub
    finally:
        sys.path.pop(0)
    try:
        from config import load_config
        cfg = load_config()
    except Exception:
        cfg = {}
    return ModuleHub(cfg)


@modules_bp.route("/api/modules")
def api_modules_installed():
    """List installed modules with status."""
    try:
        hub = _get_hub()
        installed = hub.list_installed()
        return jsonify({"modules": installed})
    except Exception as e:
        log.warning("modules installed failed: %s", e)
        return jsonify({"modules": [], "error": str(e)}), 500


@modules_bp.route("/api/modules/available")
def api_modules_available():
    """List all available modules from hub registry."""
    try:
        hub = _get_hub()
        available = hub.list_available()
        installed_names = {m["name"] for m in hub.list_installed()}
        for m in available:
            m["installed"] = m["name"] in installed_names
        return jsonify({"modules": available})
    except Exception as e:
        log.warning("modules available failed: %s", e)
        return jsonify({"modules": [], "error": str(e)}), 500


@modules_bp.route("/api/modules/updates")
def api_modules_updates():
    """List modules with available updates."""
    try:
        hub = _get_hub()
        updates = hub.get_update_available()
        return jsonify({"updates": updates})
    except Exception as e:
        log.warning("modules updates failed: %s", e)
        return jsonify({"updates": [], "error": str(e)}), 500


@modules_bp.route("/api/modules/install", methods=["POST"])
def api_modules_install():
    """Install a module by name."""
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify({"error": "Missing 'name' field"}), 400
    try:
        hub = _get_hub()
        result = hub.install_module(name)
        if "error" in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        log.warning("module install failed: %s", e)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/<name>/enable", methods=["POST"])
def api_modules_enable(name):
    """Enable an installed module."""
    try:
        hub = _get_hub()
        result = hub.enable_module(name)
        if "error" in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        log.warning("module enable failed: %s", e)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/<name>/disable", methods=["POST"])
def api_modules_disable(name):
    """Disable an installed module."""
    try:
        hub = _get_hub()
        result = hub.disable_module(name)
        if "error" in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        log.warning("module disable failed: %s", e)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/<name>/uninstall", methods=["DELETE"])
def api_modules_uninstall(name):
    """Uninstall a module."""
    try:
        hub = _get_hub()
        result = hub.uninstall_module(name)
        if "error" in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as e:
        log.warning("module uninstall failed: %s", e)
        return jsonify({"error": str(e)}), 500


@modules_bp.route("/api/modules/suggestions")
def api_modules_suggestions():
    """Get agent-generated module suggestions."""
    try:
        import db
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, module_name, reason, relevance_score, created_at "
                "FROM module_suggestions WHERE dismissed = 0 "
                "ORDER BY relevance_score DESC LIMIT 5"
            ).fetchall()
        return jsonify({"suggestions": [dict(r) for r in rows]})
    except Exception as e:
        log.warning("module suggestions failed: %s", e)
        return jsonify({"suggestions": []}), 500


@modules_bp.route("/api/modules/suggestions/<int:sid>/dismiss", methods=["POST"])
def api_modules_dismiss_suggestion(sid):
    """Dismiss a module suggestion."""
    try:
        import db
        def _do():
            with db.get_conn() as conn:
                conn.execute("UPDATE module_suggestions SET dismissed = 1 WHERE id = ?", (sid,))
        db._retry_write(_do)
        return jsonify({"ok": True})
    except Exception as e:
        log.warning("dismiss suggestion failed: %s", e)
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 2: Register blueprint in dashboard.py**

Find the blueprint registration section in `fleet/dashboard.py` (search for `register_blueprint`). Add after the last one:

```python
# ── Module Manager API (v1.0) ──────────────────────────────────────────────
from modules_blueprint import modules_bp
app.register_blueprint(modules_bp)
```

- [ ] **Step 3: Commit**

```bash
git add fleet/modules_blueprint.py fleet/dashboard.py
git commit -m "feat: modules REST API blueprint — 9 endpoints wrapping hub.py"
```

---

### Task 3: Dashboard UI — Modules Nav + Split Panel

**Files:**
- Modify: `fleet/templates/dashboard.html`

- [ ] **Step 1: Add Modules nav item**

Find the Views nav button (search for `data-section="views"`). Add a new nav button AFTER it, BEFORE the Settings button:

```html
    <button class="nav-item" data-section="modules" onclick="showSection('modules')">
      <span class="nav-icon">&#128230;</span> Modules
    </button>
```

- [ ] **Step 2: Add Modules section HTML**

Find `section-settings` div. Add BEFORE it:

```html
<!-- ═══════════════════════════════════════════════════════════════════════
     MODULES
     ═══════════════════════════════════════════════════════════════════ -->
<div id="section-modules" class="section">
  <div class="section-header">
    <div class="section-title">Modules</div>
    <div class="section-desc">Browse, install, and manage BigEd modules</div>
    <div style="margin-left:auto;display:flex;gap:8px;">
      <button class="btn btn-sm btn-outline" id="module-view-toggle" onclick="toggleModuleView()" title="Switch view">&#9638;</button>
      <button class="btn btn-sm btn-outline" onclick="loadModules()">&#8635; Refresh</button>
    </div>
  </div>
  <!-- Suggestion cards -->
  <div id="module-suggestions" style="margin-bottom:12px;"></div>
  <!-- Split Panel view (default) -->
  <div id="module-split-view" class="glass-panel" style="display:flex;min-height:400px;">
    <div id="module-list-panel" style="width:30%;border-right:1px solid var(--border);overflow-y:auto;max-height:500px;">
      <div style="padding:8px;border-bottom:1px solid var(--border);">
        <input type="text" id="module-search" placeholder="Search modules..." oninput="filterModules()" style="width:100%;padding:6px 10px;border-radius:6px;border:1px solid var(--border);background:var(--card);color:var(--foreground);font-size:12px;">
      </div>
      <div id="module-list" style="padding:4px;">
        <div class="spinner"></div>
      </div>
    </div>
    <div id="module-detail-panel" style="flex:1;padding:20px;overflow-y:auto;max-height:500px;">
      <div style="color:var(--muted-foreground);text-align:center;padding:40px;font-size:13px;">Select a module to view details</div>
    </div>
  </div>
  <!-- Card Grid view (alternate) -->
  <div id="module-card-view" class="glass-panel" style="display:none;">
    <div id="module-cards" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;padding:16px;">
      <div class="spinner"></div>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add loadSectionData case**

Find `function loadSectionData(name)` and its switch statement. Add:

```javascript
    case 'modules': loadModules(); break;
```

- [ ] **Step 4: Add Modules JavaScript**

Add at the end of the file (before the closing `</script>` or after the Ingest code):

```javascript
/* ── Module Manager ──────────────────────────────────────────────────────── */
var _moduleData = { installed: [], available: [], selected: null };
var _moduleView = localStorage.getItem('biged_module_view') || 'split';

function loadModules() {
  Promise.all([
    apiFetch('/api/modules'),
    apiFetch('/api/modules/available'),
    apiFetch('/api/modules/suggestions')
  ]).then(function(results) {
    _moduleData.installed = results[0].modules || [];
    _moduleData.available = results[1].modules || [];
    var suggestions = results[2].suggestions || [];
    _renderModuleSuggestions(suggestions);
    _renderModuleList();
    _renderModuleCards();
    if (_moduleView === 'cards') {
      document.getElementById('module-split-view').style.display = 'none';
      document.getElementById('module-card-view').style.display = 'block';
    } else {
      document.getElementById('module-split-view').style.display = 'flex';
      document.getElementById('module-card-view').style.display = 'none';
    }
  }).catch(function(e) {
    console.warn('loadModules failed', e);
  });
}

function toggleModuleView() {
  _moduleView = _moduleView === 'split' ? 'cards' : 'split';
  localStorage.setItem('biged_module_view', _moduleView);
  loadModules();
}

function filterModules() {
  var q = (document.getElementById('module-search').value || '').toLowerCase();
  document.querySelectorAll('.module-list-item').forEach(function(el) {
    el.style.display = el.dataset.name.indexOf(q) >= 0 ? '' : 'none';
  });
}

function _renderModuleSuggestions(suggestions) {
  var wrap = document.getElementById('module-suggestions');
  if (!wrap) return;
  wrap.textContent = '';
  if (!suggestions.length) return;
  suggestions.forEach(function(s) {
    var card = document.createElement('div');
    card.style.cssText = 'display:flex;align-items:center;gap:12px;padding:10px 16px;border-radius:8px;border:1px solid #3b82f644;background:#3b82f60a;margin-bottom:6px;';
    var bulb = document.createElement('span');
    bulb.textContent = '\uD83D\uDCA1';
    bulb.style.fontSize = '16px';
    card.appendChild(bulb);
    var text = document.createElement('div');
    text.style.cssText = 'flex:1;';
    var title = document.createElement('div');
    title.style.cssText = 'font-size:13px;font-weight:600;color:var(--foreground);';
    title.textContent = 'Recommended: ' + s.module_name;
    text.appendChild(title);
    var reason = document.createElement('div');
    reason.style.cssText = 'font-size:11px;color:var(--muted-foreground);';
    reason.textContent = s.reason;
    text.appendChild(reason);
    card.appendChild(text);
    var installBtn = document.createElement('button');
    installBtn.className = 'btn btn-sm btn-primary';
    installBtn.textContent = 'Install';
    installBtn.onclick = function() { _installModule(s.module_name); };
    card.appendChild(installBtn);
    var dismissBtn = document.createElement('button');
    dismissBtn.className = 'btn btn-sm btn-outline';
    dismissBtn.textContent = '\u2715';
    dismissBtn.title = 'Dismiss';
    dismissBtn.onclick = function() {
      apiFetch('/api/modules/suggestions/' + s.id + '/dismiss', { method: 'POST' }).then(function() { card.remove(); });
    };
    card.appendChild(dismissBtn);
    wrap.appendChild(card);
  });
}

var _statusDots = { enabled: '#10b981', disabled: '#64748b', available: '#3b82f6' };

function _renderModuleList() {
  var list = document.getElementById('module-list');
  if (!list) return;
  list.textContent = '';

  var installedNames = {};
  _moduleData.installed.forEach(function(m) { installedNames[m.name] = true; });

  // Installed first
  _moduleData.installed.forEach(function(m) {
    var enabled = m.default_enabled !== false && m.enabled !== false;
    _addModuleRow(list, m.name, m.version || '?', enabled ? 'enabled' : 'disabled', m);
  });

  // Available (not installed)
  _moduleData.available.forEach(function(m) {
    if (installedNames[m.name]) return;
    _addModuleRow(list, m.name, m.version || '?', 'available', m);
  });
}

function _addModuleRow(container, name, version, status, data) {
  var row = document.createElement('div');
  row.className = 'module-list-item';
  row.dataset.name = name.toLowerCase();
  row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 10px;cursor:pointer;border-radius:6px;transition:background 0.15s;';
  row.onmouseenter = function() { row.style.background = 'var(--glass-hover, #333)'; };
  row.onmouseleave = function() { row.style.background = ''; };
  row.onclick = function() { _selectModule(name, status, data); };

  var dot = document.createElement('span');
  dot.style.cssText = 'width:8px;height:8px;border-radius:50%;flex-shrink:0;background:' + (_statusDots[status] || '#666') + ';';
  row.appendChild(dot);

  var label = document.createElement('span');
  label.textContent = name;
  label.style.cssText = 'flex:1;font-size:12px;color:var(--foreground);';
  row.appendChild(label);

  var ver = document.createElement('span');
  ver.textContent = 'v' + version;
  ver.style.cssText = 'font-size:10px;color:var(--muted-foreground);font-family:monospace;';
  row.appendChild(ver);

  container.appendChild(row);
}

function _selectModule(name, status, data) {
  _moduleData.selected = { name: name, status: status, data: data };
  var panel = document.getElementById('module-detail-panel');
  if (!panel) return;
  panel.textContent = '';

  var header = document.createElement('div');
  header.style.cssText = 'margin-bottom:16px;';
  var h2 = document.createElement('h3');
  h2.textContent = data.label || data.name || name;
  h2.style.cssText = 'font-size:18px;font-weight:700;color:var(--foreground);margin-bottom:4px;';
  header.appendChild(h2);

  var badge = document.createElement('span');
  badge.textContent = status.charAt(0).toUpperCase() + status.slice(1);
  badge.style.cssText = 'font-size:10px;padding:2px 8px;border-radius:10px;background:' + (_statusDots[status] || '#666') + '22;color:' + (_statusDots[status] || '#666') + ';font-weight:600;';
  header.appendChild(badge);

  if (data.version) {
    var verSpan = document.createElement('span');
    verSpan.textContent = 'v' + data.version;
    verSpan.style.cssText = 'font-size:11px;color:var(--muted-foreground);margin-left:8px;';
    header.appendChild(verSpan);
  }
  panel.appendChild(header);

  var desc = document.createElement('p');
  desc.textContent = data.description || 'No description available.';
  desc.style.cssText = 'font-size:13px;color:var(--muted-foreground);line-height:1.6;margin-bottom:16px;';
  panel.appendChild(desc);

  // Metadata
  var meta = document.createElement('div');
  meta.style.cssText = 'display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:20px;font-size:11px;';
  var fields = [
    ['Author', data.author || 'Unknown'],
    ['License', data.license || 'N/A'],
    ['File', data.file || 'mod_' + name + '.py'],
    ['Dependencies', (data.depends_on || []).join(', ') || 'None'],
  ];
  fields.forEach(function(f) {
    var item = document.createElement('div');
    var lbl = document.createElement('div');
    lbl.textContent = f[0];
    lbl.style.cssText = 'color:var(--muted-foreground);font-size:10px;text-transform:uppercase;letter-spacing:0.5px;';
    item.appendChild(lbl);
    var val = document.createElement('div');
    val.textContent = f[1];
    val.style.color = 'var(--foreground)';
    item.appendChild(val);
    meta.appendChild(item);
  });
  panel.appendChild(meta);

  // Actions
  var actions = document.createElement('div');
  actions.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;';

  if (status === 'available') {
    var installBtn = document.createElement('button');
    installBtn.className = 'btn btn-sm btn-primary';
    installBtn.textContent = 'Install';
    installBtn.onclick = function() { _installModule(name); };
    actions.appendChild(installBtn);
  } else if (status === 'enabled') {
    var disableBtn = document.createElement('button');
    disableBtn.className = 'btn btn-sm btn-outline';
    disableBtn.textContent = 'Disable';
    disableBtn.onclick = function() { _toggleModule(name, 'disable'); };
    actions.appendChild(disableBtn);
    var uninstallBtn = document.createElement('button');
    uninstallBtn.className = 'btn btn-sm';
    uninstallBtn.textContent = 'Uninstall';
    uninstallBtn.style.color = 'var(--destructive, #ef4444)';
    uninstallBtn.onclick = function() { _uninstallModule(name); };
    actions.appendChild(uninstallBtn);
  } else if (status === 'disabled') {
    var enableBtn = document.createElement('button');
    enableBtn.className = 'btn btn-sm btn-primary';
    enableBtn.textContent = 'Enable';
    enableBtn.onclick = function() { _toggleModule(name, 'enable'); };
    actions.appendChild(enableBtn);
    var uninstBtn2 = document.createElement('button');
    uninstBtn2.className = 'btn btn-sm';
    uninstBtn2.textContent = 'Uninstall';
    uninstBtn2.style.color = 'var(--destructive, #ef4444)';
    uninstBtn2.onclick = function() { _uninstallModule(name); };
    actions.appendChild(uninstBtn2);
  }
  panel.appendChild(actions);
}

function _installModule(name) {
  apiFetch('/api/modules/install', { method: 'POST', body: { name: name } }).then(function(r) {
    if (r.error) { alert('Install failed: ' + r.error); return; }
    loadModules();
  }).catch(function(e) { alert('Install error: ' + e.message); });
}

function _toggleModule(name, action) {
  apiFetch('/api/modules/' + name + '/' + action, { method: 'POST' }).then(function(r) {
    if (r.error) { alert(action + ' failed: ' + r.error); return; }
    loadModules();
  }).catch(function(e) { alert(action + ' error: ' + e.message); });
}

function _uninstallModule(name) {
  apiFetch('/api/modules/' + name + '/uninstall', { method: 'DELETE' }).then(function(r) {
    if (r.error) { alert('Uninstall failed: ' + r.error); return; }
    loadModules();
  }).catch(function(e) { alert('Uninstall error: ' + e.message); });
}

function _renderModuleCards() {
  var container = document.getElementById('module-cards');
  if (!container) return;
  container.textContent = '';
  var installedNames = {};
  _moduleData.installed.forEach(function(m) { installedNames[m.name] = true; });

  var all = _moduleData.installed.map(function(m) { return { data: m, status: m.default_enabled !== false ? 'enabled' : 'disabled' }; });
  _moduleData.available.forEach(function(m) {
    if (!installedNames[m.name]) all.push({ data: m, status: 'available' });
  });

  all.forEach(function(item) {
    var card = document.createElement('div');
    card.style.cssText = 'border:1px ' + (item.status === 'available' ? 'dashed' : 'solid') + ' var(--border);border-radius:10px;padding:16px;cursor:pointer;transition:background 0.15s;';
    card.onmouseenter = function() { card.style.background = 'var(--glass-hover, #333)'; };
    card.onmouseleave = function() { card.style.background = ''; };
    card.onclick = function() {
      _moduleView = 'split';
      localStorage.setItem('biged_module_view', 'split');
      loadModules();
      setTimeout(function() { _selectModule(item.data.name, item.status, item.data); }, 200);
    };

    var top = document.createElement('div');
    top.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:8px;';
    var dot = document.createElement('span');
    dot.style.cssText = 'width:10px;height:10px;border-radius:50%;background:' + (_statusDots[item.status] || '#666') + ';';
    top.appendChild(dot);
    var name = document.createElement('span');
    name.textContent = item.data.label || item.data.name;
    name.style.cssText = 'font-weight:600;font-size:13px;color:var(--foreground);flex:1;';
    top.appendChild(name);
    var ver = document.createElement('span');
    ver.textContent = 'v' + (item.data.version || '?');
    ver.style.cssText = 'font-size:10px;background:' + (_statusDots[item.status] || '#666') + '22;color:' + (_statusDots[item.status] || '#666') + ';padding:2px 6px;border-radius:10px;';
    top.appendChild(ver);
    card.appendChild(top);

    var desc = document.createElement('div');
    desc.textContent = (item.data.description || '').substring(0, 80) + ((item.data.description || '').length > 80 ? '...' : '');
    desc.style.cssText = 'font-size:11px;color:var(--muted-foreground);line-height:1.4;';
    card.appendChild(desc);

    container.appendChild(card);
  });
}
```

- [ ] **Step 5: Commit**

```bash
git add fleet/templates/dashboard.html
git commit -m "feat: Module Manager dashboard UI — Split Panel + Card Grid views"
```

---

### Task 4: Agent Recommendation Skill

**Files:**
- Create: `fleet/skills/module_recommend.py`
- Modify: `fleet/idle_curricula/planner.toml`

- [ ] **Step 1: Create module_recommend.py**

```python
"""Analyze fleet activity and suggest useful modules."""
SKILL_NAME = "module_recommend"
DESCRIPTION = "Analyze fleet task patterns and recommend useful modules from the hub"
REQUIRES_NETWORK = True

# Module tags → task type mapping for relevance scoring
_MODULE_RELEVANCE = {
    "analytics_pro": ["data_analysis", "autoresearch_trial", "evaluate"],
    "webhooks": ["api_call", "web_search", "monitor"],
    "crm": ["lead_research", "outreach", "account_review"],
    "onboarding": ["onboarding", "setup", "walkthrough"],
    "customers": ["account_review", "client_onboarding"],
}


def run(task: dict, context: dict) -> dict:
    import db
    import json
    import sys
    from pathlib import Path

    payload = task.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    lookback_days = payload.get("lookback_days", 7)
    max_suggestions = payload.get("max_suggestions", 3)

    # 1. Query task type distribution
    with db.get_conn() as conn:
        task_types = conn.execute(
            "SELECT type, COUNT(*) as cnt FROM tasks "
            "WHERE created_at >= datetime('now', ? || ' days') "
            "AND classification != 'synthetic_prefix' AND type IS NOT NULL "
            "GROUP BY type ORDER BY cnt DESC",
            (str(-lookback_days),)
        ).fetchall()

    type_counts = {r["type"]: r["cnt"] for r in task_types}

    # 2. Get installed modules
    hub_dir = Path(__file__).parent.parent.parent / "BigEd" / "launcher" / "modules"
    sys.path.insert(0, str(hub_dir))
    try:
        from hub import ModuleHub
    finally:
        sys.path.pop(0)

    try:
        from config import load_config
        cfg = load_config()
    except Exception:
        cfg = {}

    hub = ModuleHub(cfg)
    installed_names = {m["name"] for m in hub.list_installed()}

    # 3. Score uninstalled modules by relevance
    suggestions = []
    for mod_name, relevant_tasks in _MODULE_RELEVANCE.items():
        if mod_name in installed_names:
            continue
        score = 0.0
        matched_tasks = []
        for task_type in relevant_tasks:
            if task_type in type_counts:
                score += min(1.0, type_counts[task_type] / 50.0)
                matched_tasks.append(f"{task_type} ({type_counts[task_type]})")

        if score > 0.5:
            reason = f"Your fleet ran {', '.join(matched_tasks)} tasks in the last {lookback_days} days"
            suggestions.append({
                "module_name": mod_name,
                "reason": reason,
                "relevance_score": round(score, 2),
            })

    suggestions.sort(key=lambda x: x["relevance_score"], reverse=True)
    suggestions = suggestions[:max_suggestions]

    # 4. Write to DB
    if suggestions:
        def _write():
            with db.get_conn() as conn:
                for s in suggestions:
                    conn.execute(
                        "INSERT OR REPLACE INTO module_suggestions "
                        "(module_name, reason, relevance_score, dismissed) "
                        "VALUES (?, ?, ?, 0)",
                        (s["module_name"], s["reason"], s["relevance_score"])
                    )
        db._retry_write(_write)

    return {"status": "ok", "suggestions": suggestions, "task_types_analyzed": len(type_counts)}
```

- [ ] **Step 2: Add to planner idle curriculum**

Append to `fleet/idle_curricula/planner.toml`:

```toml

# ── Module recommendations (daily) ───────────────────────────────────────────
[[tasks]]
type = "module_recommend"
[tasks.payload]
lookback_days = 7
max_suggestions = 3
```

- [ ] **Step 3: Commit**

```bash
git add fleet/skills/module_recommend.py fleet/idle_curricula/planner.toml
git commit -m "feat: module_recommend skill — agent-generated module suggestions"
```

---

### Task 5: Smoke Test

**Files:**
- Modify: `fleet/smoke_test.py`

- [ ] **Step 1: Add modules API smoke test**

```python
def test_modules_api():
    """Module management API endpoints respond correctly."""
    import urllib.request
    import json
    base = "http://localhost:5555"
    # Test installed modules endpoint
    try:
        r = urllib.request.urlopen(base + "/api/modules", timeout=5)
        data = json.loads(r.read())
        assert "modules" in data, "Missing 'modules' key"
        print(f"  Modules API: {len(data['modules'])} installed")
    except urllib.error.URLError:
        # Dashboard not running — test the blueprint import instead
        from modules_blueprint import modules_bp
        assert modules_bp.name == "modules"
        print("  Modules API: blueprint importable (dashboard not running)")
```

Register in the tests list in `main()`.

- [ ] **Step 2: Run smoke tests**

Run: `python fleet/smoke_test.py --fast`
Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add fleet/smoke_test.py
git commit -m "test: add modules API smoke test"
```
