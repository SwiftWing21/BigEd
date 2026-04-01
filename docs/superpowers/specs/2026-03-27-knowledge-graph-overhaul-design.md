# Knowledge Graph Overhaul — Design Spec

**Date:** 2026-03-27
**Status:** Draft
**Goal:** Transform the knowledge graph from concentric rings into organic, brain-like community clusters with semantic zoom.

---

## Problem Statement

The current graph renders ~370 nodes and ~600 edges as concentric rings because:

1. **Task flooding:** 200 individual task nodes (54% of graph) all have degree=2, landing at identical radius → outer ring
2. **Tier-sorted golden angle = rings:** Sorting by type then placing with golden angle produces geometric shells, not organic clusters
3. **No semantic grouping:** Layout ignores edge topology — never asks "which nodes are connected?"
4. **Edge mesh:** 600+ edges at low opacity create a dense web that obscures structure
5. **Hover displacement:** Dashboard pushes neighbors outward on hover but never restores positions (fixed in this session)

## Solution Overview

Three changes that reduce the graph from 370 nodes / 600 edges to ~80 nodes / ~120 edges with organic clustering:

### 1. Task Aggregation (Backend)

Replace 200 individual task nodes with ~15 aggregate nodes grouped by `(skill_type, assigned_to, status)`.

**Before:** 200 nodes like `task:#73047 summarize`, each with 2 edges
**After:** 15 nodes like `tasks:researcher:summarize:DONE (47)` with 2 edges each

### 2. Compound Parent Nodes (Backend + Frontend)

Group nodes into agent communities using Cytoscape compound parents:

```
community:coder_1
  ├── agent:coder_1
  ├── tasks:coder_1:code_review:DONE (12)
  └── skill:code_review (shared — also in community:coder_2)

community:researcher
  ├── agent:researcher
  ├── tasks:researcher:summarize:DONE (47)
  └── skill:summarize
```

Shared skills (used by multiple agents) attach to their primary agent's community. Orphan nodes (config, models without edges) go into a "system" community.

### 3. fCoSE Layout (Frontend)

Replace fractal-brain with fCoSE for the default layout. fCoSE supports compound nodes and produces organic force-directed clusters. Keep fractal-brain as a layout option in the dropdown.

fCoSE parameters:
- `nodeRepulsion: 8000` — push unrelated nodes apart
- `idealEdgeLength: 60` — connected nodes stay close
- `gravity: 0.25` — gentle pull toward center
- `nestingFactor: 0.1` — children don't inflate parent too much
- `numIter: 5000` — enough iterations for convergence
- `packComponents: true` — compact disconnected components

### 4. Edge Deduplication (Backend)

Deduplicate edges by `(source, target, type)` and sum weights. Currently the same relationship can appear from multiple code paths (task assignment + SKILL_IO_MAP + role hints).

### 5. Community-Based Coloring (Frontend)

Color nodes by community (agent group) instead of by type. Each community gets a hue family. Node type is indicated by shape/size instead:
- Agent: large circle
- Skill: medium diamond
- Task aggregate: sized by count
- Folder: square
- Model: hexagon
- Config: small circle

### 6. LOD at Overview Zoom (Frontend — already partially built)

At overview zoom, collapse to pipeline summary nodes positioned at community centroids (not flat horizontal). Already partially implemented in this session — needs centroid positioning and community-aware grouping.

## Files Changed

| File | Change |
|------|--------|
| `fleet/views_blueprint.py` | Task aggregation, compound parents, edge dedup in `_graph_universe()` |
| `fleet/templates/dashboard.html` | Compound node styles, fCoSE config, community coloring, shape-by-type |
| `fleet/templates/view_graph.html` | Same compound/fCoSE/coloring changes for standalone graph page |
| `fleet/static/view_engine.js` | Update LAYOUT_MAP, add compound parent support |
| `fleet/static/layout_fractal.js` | Keep as fallback layout option, no changes needed |

## Out of Scope

- Dynamic node drift animation (SSE-driven position changes) — separate spec
- Drill-down into task aggregates (click to expand) — future enhancement
- WebGL renderer — not needed at 80-120 nodes
- Markov clustering algorithm — simple agent-based grouping is sufficient
