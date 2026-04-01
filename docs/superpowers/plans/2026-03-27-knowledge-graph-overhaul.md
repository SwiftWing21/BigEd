# Knowledge Graph Overhaul — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the knowledge graph from concentric rings with 370 nodes into organic community clusters with ~80 nodes.

**Architecture:** Backend aggregates tasks and assigns community parents. Frontend uses fCoSE layout with compound node support for organic clustering. Edge dedup and community coloring complete the visual transformation.

**Tech Stack:** Python (Flask), Cytoscape.js (fCoSE layout), SQLite

**Spec:** `docs/superpowers/specs/2026-03-27-knowledge-graph-overhaul-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `fleet/views_blueprint.py` | Modify lines 1026-1327 | Task aggregation, compound parents, edge dedup |
| `fleet/templates/dashboard.html` | Modify lines 3225-3460 | Compound styles, fCoSE config, community coloring |
| `fleet/templates/view_graph.html` | Modify lines 650-860 | Same Cytoscape changes for standalone page |
| `fleet/static/view_engine.js` | Modify lines 25-93 | LAYOUT_MAP update |
| `fleet/smoke_test.py` | Modify (add test) | Smoke test for graph node count reduction |

---

### Task 1: Backend — Task Aggregation

**Files:**
- Modify: `fleet/views_blueprint.py:1102-1123`

- [ ] **Step 1: Replace individual task query with GROUP BY aggregation**

In `_graph_universe()`, replace the task section (section 3, lines 1102-1123) with:

```python
        # ── 3. TASK AGGREGATES (grouped by skill+agent+status, last 24h) ──
        with db.get_conn() as conn:
            task_groups = conn.execute("""
                SELECT type, status, assigned_to, COUNT(*) as cnt,
                       MIN(id) as first_id, MAX(id) as last_id
                FROM tasks
                WHERE created_at >= datetime('now', '-24 hours')
                  AND type IS NOT NULL
                GROUP BY type, assigned_to, status
                ORDER BY cnt DESC
            """).fetchall()

        for tg in task_groups:
            skill_type = tg["type"] or "unknown"
            agent_name = tg["assigned_to"] or "unassigned"
            status = tg["status"] or "PENDING"
            count = tg["cnt"]
            tg_id = f"task_group:{agent_name}:{skill_type}:{status}"

            label = f"{skill_type} ({count})"
            _add_node(tg_id, type="task_group", source="universe",
                      label=label, status=status,
                      metrics={"count": count, "skill": skill_type,
                               "agent": agent_name,
                               "range": f"#{tg['first_id']}-#{tg['last_id']}"})

            # Task group → agent (assigned)
            if agent_name != "unassigned":
                agent_id = f"agent:{agent_name}"
                _add_edge(agent_id, tg_id, "assigned", max(1, count // 5))
            # Task group → skill
            skill_id = f"skill:{skill_type}"
            _add_edge(tg_id, skill_id, "runs", max(1, count // 10))
```

- [ ] **Step 2: Verify the graph endpoint returns fewer nodes**

Run: `python -c "import sys; sys.path.insert(0,'fleet'); import db; db.init_db(); from views_blueprint import _graph_universe; n,e = _graph_universe(db); print(f'Nodes: {len(n)}, Edges: {len(e)}'); print('Task groups:', sum(1 for x in n if x.get('type')=='task_group'))"` from the project root.

Expected: Nodes < 200 (was ~370), task_group count ~10-20.

- [ ] **Step 3: Commit**

```bash
git add fleet/views_blueprint.py
git commit -m "feat(graph): aggregate tasks by skill+agent+status — 200 nodes → ~15"
```

---

### Task 2: Backend — Compound Parent Nodes

**Files:**
- Modify: `fleet/views_blueprint.py:1026-1045` (add community builder after all nodes/edges are collected)

- [ ] **Step 1: Add community assignment after all nodes are built**

Insert this block just before the `return nodes, edges` at line 1327, inside the try block:

```python
        # ── 10. COMPOUND PARENT NODES (agent communities) ──────────────
        communities = {}  # agent_name → list of child node IDs

        # Assign nodes to communities based on edges
        for e in edges:
            src, tgt = e["source"], e["target"]
            # If an agent is the source, its target joins its community
            if src.startswith("agent:"):
                agent_name = src.split(":", 1)[1]
                communities.setdefault(agent_name, set()).add(src)
                communities[agent_name].add(tgt)
            # If an agent is the target, its source joins its community
            elif tgt.startswith("agent:"):
                agent_name = tgt.split(":", 1)[1]
                communities.setdefault(agent_name, set()).add(tgt)
                communities[agent_name].add(src)

        # Nodes claimed by multiple communities → assign to largest community
        node_to_community = {}
        for agent_name, members in sorted(communities.items(),
                                           key=lambda x: len(x[1]), reverse=True):
            for nid in members:
                if nid not in node_to_community:
                    node_to_community[nid] = agent_name

        # Create compound parent nodes
        for agent_name in set(node_to_community.values()):
            community_id = f"community:{agent_name}"
            agent_node = next((n for n in nodes if n["id"] == f"agent:{agent_name}"), None)
            role = ""
            if agent_node and agent_node.get("metrics"):
                role = agent_node["metrics"].get("role", "")
            _add_node(community_id, type="community", source="universe",
                      label=f"{agent_name}" + (f" ({role})" if role else ""),
                      status="ACTIVE",
                      metrics={"members": sum(1 for v in node_to_community.values()
                                              if v == agent_name)})

        # Orphan nodes (no community) → "system" community
        all_node_ids = {n["id"] for n in nodes}
        orphans = all_node_ids - set(node_to_community.keys()) - {
            n["id"] for n in nodes if n.get("type") == "community"}
        if orphans:
            sys_id = "community:system"
            _add_node(sys_id, type="community", source="universe",
                      label="System", status="IDLE",
                      metrics={"members": len(orphans)})
            for oid in orphans:
                node_to_community[oid] = "system"

        # Stamp parent onto each node's data
        for n in nodes:
            if n["id"] in node_to_community:
                n["parent"] = f"community:{node_to_community[n['id']]}"
```

- [ ] **Step 2: Verify compound parents are assigned**

Run: `python -c "import sys; sys.path.insert(0,'fleet'); import db; db.init_db(); from views_blueprint import _graph_universe; n,e = _graph_universe(db); parents = [x for x in n if x.get('type')=='community']; print(f'Communities: {len(parents)}'); [print(f'  {p[\"id\"]}: {p[\"metrics\"][\"members\"]} members') for p in parents]"`

Expected: 5-10 communities (one per active agent + system).

- [ ] **Step 3: Commit**

```bash
git add fleet/views_blueprint.py
git commit -m "feat(graph): compound parent nodes — group nodes into agent communities"
```

---

### Task 3: Backend — Edge Deduplication

**Files:**
- Modify: `fleet/views_blueprint.py:1039-1040`

- [ ] **Step 1: Replace edge list with dedup map**

Replace the `_add_edge` function near line 1039:

```python
    edge_map = {}  # (src, tgt, type) → weight

    def _add_edge(src, tgt, etype, weight=1):
        key = (src, tgt, etype)
        edge_map[key] = edge_map.get(key, 0) + weight
```

Then at the end of the function, before the community building code, convert the map to the edges list:

```python
        # Convert deduped edge map to list
        edges = [{"source": k[0], "target": k[1], "type": k[2], "weight": v}
                 for k, v in edge_map.items()]
```

Note: The community assignment code in Task 2 iterates `edges`, so this conversion must happen before section 10.

- [ ] **Step 2: Verify edge count decreased**

Run the same test script from Task 1 step 2. Expected: edge count < 300 (was ~600).

- [ ] **Step 3: Commit**

```bash
git add fleet/views_blueprint.py
git commit -m "fix(graph): deduplicate edges and sum weights"
```

---

### Task 4: Frontend (Dashboard) — Compound Nodes + fCoSE

**Files:**
- Modify: `fleet/templates/dashboard.html:3225-3404`

- [ ] **Step 1: Update `loadNeuralGraph` to pass parent data to Cytoscape**

In `loadNeuralGraph()`, find where nodes are built for Cytoscape (search for `cyNodes.push`). Add `parent` to the node data:

```javascript
cyNodes.push({
  data: {
    id: n.id,
    label: n.label || n.id,
    // ... existing fields ...
    parent: n.parent || undefined,  // compound parent
    count: (n.metrics || {}).count || 1,
  }
});
```

- [ ] **Step 2: Add compound parent node styles**

Add these styles to the `buildCyGraph` stylesheet array (after the existing node styles, before edge styles):

```javascript
        {
          selector: ':parent',
          style: {
            'background-opacity': 0.06,
            'background-color': 'data(color)',
            'border-width': 1.5,
            'border-color': 'data(color)',
            'border-opacity': 0.25,
            'border-style': 'dashed',
            'padding': 20,
            'text-valign': 'top',
            'text-halign': 'center',
            'label': 'data(label)',
            'font-size': '11px',
            'color': isDark() ? '#a0a0a0' : '#666',
            'shape': 'roundrectangle',
          }
        },
        {
          selector: 'node[type="task_group"]',
          style: {
            'width': function(n) { return 8 + Math.sqrt(n.data('count') || 1) * 3; },
            'height': function(n) { return 8 + Math.sqrt(n.data('count') || 1) * 3; },
            'shape': 'round-tag',
          }
        },
```

- [ ] **Step 3: Switch default layout to fCoSE with compound support**

In `_getLayoutConfig()`, replace the `fractal-brain` case:

```javascript
    if (name === 'fractal-brain') {
      return {
        name: 'fcose',
        quality: nodeCount > 200 ? 'default' : 'proof',
        randomize: true,
        animate: 'end',
        animationDuration: 500,
        fit: true,
        padding: 30,
        nodeRepulsion: function(node) {
          return node.isParent ? 12000 : 6000;
        },
        idealEdgeLength: function(edge) {
          var src = edge.source(), tgt = edge.target();
          if (src.parent() && tgt.parent() && src.parent().id() === tgt.parent().id()) return 40;
          return 100;
        },
        edgeElasticity: 0.45,
        gravity: 0.25,
        gravityRange: 3.8,
        nestingFactor: 0.1,
        numIter: nodeCount > 200 ? 2500 : 5000,
        packComponents: true,
        componentSpacing: 40,
        nodeDimensionsIncludeLabels: false,
      };
    }
```

- [ ] **Step 4: Add node shapes by type**

In the `nodeTypeStyle` object (~line 3226), add shapes:

```javascript
  var nodeTypeStyle = {
    hub:        { color: '#3b82f6', size: 44, fontSize: 14, shape: 'ellipse' },
    agent:      { color: '#10b981', size: 36, fontSize: 12, shape: 'ellipse' },
    supervisor: { color: '#059669', size: 32, fontSize: 12, shape: 'ellipse' },
    skill:      { color: '#8b5cf6', size: 24, fontSize: 10, shape: 'diamond' },
    task:       { color: '#f59e0b', size: 16, fontSize: 8,  shape: 'ellipse' },
    task_group: { color: '#f59e0b', size: 20, fontSize: 9,  shape: 'round-tag' },
    model:      { color: '#ec4899', size: 22, fontSize: 10, shape: 'hexagon' },
    folder:     { color: '#06b6d4', size: 22, fontSize: 10, shape: 'round-rectangle' },
    message:    { color: '#6366f1', size: 18, fontSize: 9,  shape: 'ellipse' },
    config:     { color: '#64748b', size: 14, fontSize: 8,  shape: 'ellipse' },
    api_call:   { color: '#ef4444', size: 16, fontSize: 8,  shape: 'tag' },
    community:  { color: '#444444', size: 60, fontSize: 11, shape: 'roundrectangle' },
  };
```

- [ ] **Step 5: Reload dashboard and verify**

Refresh the dashboard. The Knowledge Graph card should show:
- Agent communities as dashed rounded rectangles
- Nodes clustered inside their agent's community
- Organic layout (no concentric rings)
- Task aggregates as sized bubbles instead of 200 individual dots

- [ ] **Step 6: Commit**

```bash
git add fleet/templates/dashboard.html
git commit -m "feat(graph): compound nodes + fCoSE layout for organic clustering"
```

---

### Task 5: Frontend (Standalone Graph Page) — Same Changes

**Files:**
- Modify: `fleet/templates/view_graph.html:660-860`
- Modify: `fleet/static/view_engine.js:25-93`

- [ ] **Step 1: Add compound parent styles to view_graph.html**

Add after the existing node styles (after the `node.zoom-close` selector, before edges):

```javascript
        // Compound parent nodes (agent communities)
        {
          selector: ':parent',
          style: {
            'background-opacity': 0.06,
            'background-color': 'data(color)',
            'border-width': 1.5,
            'border-color': 'data(color)',
            'border-opacity': 0.25,
            'border-style': 'dashed',
            'padding': 20,
            'text-valign': 'top',
            'text-halign': 'center',
            'label': 'data(label)',
            'font-size': '11px',
            'color': '#a0a0a0',
            'shape': 'roundrectangle',
          },
        },
        {
          selector: 'node[type="task_group"]',
          style: {
            'width': function(n) { return 8 + Math.sqrt(n.data('count') || 1) * 3; },
            'height': function(n) { return 8 + Math.sqrt(n.data('count') || 1) * 3; },
            'shape': 'round-tag',
          },
        },
```

- [ ] **Step 2: Update `renderGraphData` to pass parent field**

In `renderGraphData()` (~line 950), add `parent` to the node data push:

```javascript
        elements.push({
          group: 'nodes',
          data: {
            id:          n.id,
            label:       n.label || n.id,
            type:        n.type || '',
            source:      srcName,
            status:      n.status || '',
            metrics:     n.metrics || null,
            color:       nColor,
            borderColor: statusBorderColor(n.status),
            parent:      n.parent || undefined,
            count:       (n.metrics || {}).count || 1,
          },
          classes: isError ? 'source-unavailable' : '',
        });
```

- [ ] **Step 3: Update fCoSE layout config in `layoutOpts`**

Replace the `fractal-brain` case in `layoutOpts()` with the fCoSE compound config (same as Task 4 Step 3).

- [ ] **Step 4: Update view_engine.js LAYOUT_MAP**

Replace the `fractal-brain` entry with the fCoSE compound config:

```javascript
    "fractal-brain": {
      name: "fcose",
      quality: "proof",
      randomize: true,
      animate: "end",
      animationDuration: 500,
      fit: true,
      padding: 30,
      nodeRepulsion: 6000,
      idealEdgeLength: 60,
      edgeElasticity: 0.45,
      gravity: 0.25,
      gravityRange: 3.8,
      nestingFactor: 0.1,
      numIter: 5000,
      packComponents: true,
      componentSpacing: 40,
    },
```

- [ ] **Step 5: Commit**

```bash
git add fleet/templates/view_graph.html fleet/static/view_engine.js
git commit -m "feat(graph): compound nodes + fCoSE in standalone graph page"
```

---

### Task 6: Smoke Test

**Files:**
- Modify: `fleet/smoke_test.py`

- [ ] **Step 1: Add graph data shape test**

Add a test to verify task aggregation is working:

```python
def test_graph_universe_task_aggregation():
    """Graph should return task_group nodes, not individual tasks."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import db
    db.init_db()
    from views_blueprint import _graph_universe
    nodes, edges = _graph_universe(db)
    types = [n.get("type") for n in nodes]
    individual_tasks = types.count("task")
    task_groups = types.count("task_group")
    communities = types.count("community")
    assert individual_tasks == 0, f"Expected 0 individual tasks, got {individual_tasks}"
    assert task_groups >= 0, "task_group nodes should exist (if tasks exist)"
    assert communities >= 1, f"Expected at least 1 community, got {communities}"
    # Verify compound parents
    nodes_with_parent = [n for n in nodes if n.get("parent")]
    assert len(nodes_with_parent) > 0, "Some nodes should have compound parents"
    print(f"  Graph: {len(nodes)} nodes, {len(edges)} edges, "
          f"{task_groups} task groups, {communities} communities")
```

- [ ] **Step 2: Run smoke tests**

Run: `python fleet/smoke_test.py --fast`

Expected: All tests pass including the new graph test.

- [ ] **Step 3: Commit**

```bash
git add fleet/smoke_test.py
git commit -m "test: add graph universe task aggregation smoke test"
```

---

### Task 7: Update LOD to Use Community Centroids

**Files:**
- Modify: `fleet/templates/view_graph.html` (LOD section)

- [ ] **Step 1: Update `_buildLodGroups` to group by community instead of type**

Replace the LOD group logic to create one summary node per community, positioned at the community's centroid:

```javascript
  function _buildLodGroups(cyInst) {
    var elements = [];
    // Group by compound parent (community)
    var communityNodes = cyInst.nodes(':parent');
    if (communityNodes.length === 0) return elements;  // no communities, skip LOD

    communityNodes.forEach(function(parent, i) {
      var children = parent.children();
      if (children.length === 0) return;
      var bb = children.boundingBox();
      var cx = (bb.x1 + bb.x2) / 2;
      var cy = (bb.y1 + bb.y2) / 2;
      var gid = 'lod:' + parent.id();
      _lodGroupIds.push(gid);

      var totalTasks = 0;
      children.forEach(function(c) { totalTasks += c.data('count') || 1; });

      elements.push({
        group: 'nodes',
        data: {
          id: gid,
          label: parent.data('label') + '\n' + children.length + ' nodes',
          type: 'lod-group',
          color: parent.data('color') || '#666',
          borderColor: parent.data('color') || '#666',
        },
        position: { x: cx, y: cy },
        classes: 'lod-group',
      });
    });

    // Add edges between LOD groups based on inter-community edges
    var interEdges = {};
    cyInst.edges().forEach(function(e) {
      var srcParent = e.source().parent();
      var tgtParent = e.target().parent();
      if (!srcParent.length || !tgtParent.length) return;
      if (srcParent.id() === tgtParent.id()) return;
      var key = srcParent.id() + ':' + tgtParent.id();
      interEdges[key] = (interEdges[key] || 0) + (e.data('weight') || 1);
    });

    Object.keys(interEdges).forEach(function(key) {
      var parts = key.split(':');
      var fromId = 'lod:community:' + parts[1];
      var toId = 'lod:community:' + parts[2];
      var eid = 'lod:edge:' + key;
      _lodGroupIds.push(eid);
      elements.push({
        group: 'edges',
        data: {
          id: eid, source: fromId, target: toId,
          color: '#666', weight: interEdges[key],
          label: interEdges[key] + ' connections',
        },
        classes: 'lod-edge',
      });
    });

    return elements;
  }
```

- [ ] **Step 2: Verify LOD transitions**

Refresh dashboard, zoom out past 0.45x. Should see community summary bubbles at their spatial positions with inter-community edges.

- [ ] **Step 3: Commit**

```bash
git add fleet/templates/view_graph.html
git commit -m "feat(graph): LOD uses community centroids for spatial overview"
```
