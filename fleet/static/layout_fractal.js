/**
 * fractal-brain — Custom Cytoscape layout extension
 *
 * BA fractal + Fibonacci spiral: radial degree placement, golden angle
 * distribution, island grouping on Fibonacci arms, offline orbits,
 * activity drift.  O(n) single-pass — handles 10K+ nodes instantly.
 */
(function () {
  'use strict';

  var GOLDEN_ANGLE = 137.508 * Math.PI / 180; // radians
  var PHI = 1.618034;

  var TIER_MAP = {
    hub: 0, supervisor: 0,
    agent: 1,
    skill: 2,
    model: 3, folder: 3, config: 3,
    task: 4, chunk: 4, message: 4, api_call: 4
  };

  var defaults = {
    padding: 30,
    animate: false,
    rMax: 0,       // 0 = auto from container
    rBase: 80,     // base radius for island Fibonacci spiral
    degreeExp: 0.6,
    activityWeight: 0.4,
    offlineOrbitScale: 1.3
  };

  function FractalBrainLayout(options) {
    this.options = Object.assign({}, defaults, options);
  }

  FractalBrainLayout.prototype.run = function () {
    var opts = this.options;
    var eles = opts.eles;
    var nodes = eles.nodes();
    var nCount = nodes.length;
    if (nCount === 0) return this;

    var container = opts.cy ? opts.cy.container() : null;
    var W = container ? container.clientWidth : 800;
    var H = container ? container.clientHeight : 600;
    var cx = W / 2, cy = H / 2;
    var rMax = opts.rMax || Math.min(W, H) / 2.5;
    var rBase = opts.rBase;

    // ── 1. Compute degrees + max degree ──────────────────────────
    var maxDeg = 1;
    nodes.forEach(function (n) {
      var d = n.degree(false) || 0;
      n.scratch('_fb_deg', d);
      if (d > maxDeg) maxDeg = d;
    });

    // ── 2. Find connected components (islands) ───────────────────
    var visited = {};
    var components = [];

    function bfs(start) {
      var comp = [];
      var queue = [start];
      visited[start.id()] = true;
      while (queue.length) {
        var cur = queue.shift();
        comp.push(cur);
        cur.neighborhood('node').forEach(function (nb) {
          if (!visited[nb.id()]) {
            visited[nb.id()] = true;
            queue.push(nb);
          }
        });
      }
      return comp;
    }

    nodes.forEach(function (n) {
      if (!visited[n.id()]) {
        components.push(bfs(n));
      }
    });

    // Sort: largest component first
    components.sort(function (a, b) { return b.length - a.length; });

    // ── 3. Position each island on a Fibonacci spiral ────────────
    var positions = {};

    for (var g = 0; g < components.length; g++) {
      var comp = components[g];
      // Island center on Fibonacci spiral (first island at center)
      var groupR = g === 0 ? 0 : rBase * Math.pow(PHI, g * 0.5);
      var groupTheta = g * GOLDEN_ANGLE;
      var islandCx = cx + groupR * Math.cos(groupTheta);
      var islandCy = cy + groupR * Math.sin(groupTheta);

      // Scale island radius by component size
      var islandR = rMax * Math.min(1, comp.length / (nCount || 1) + 0.15);

      // Sort nodes within island: by tier then degree descending
      comp.sort(function (a, b) {
        var ta = TIER_MAP[a.data('type')] !== undefined ? TIER_MAP[a.data('type')] : 3;
        var tb = TIER_MAP[b.data('type')] !== undefined ? TIER_MAP[b.data('type')] : 3;
        if (ta !== tb) return ta - tb;
        return (b.scratch('_fb_deg') || 0) - (a.scratch('_fb_deg') || 0);
      });

      // ── 4. Place nodes radially within island ──────────────────
      for (var i = 0; i < comp.length; i++) {
        var node = comp[i];
        var deg = node.scratch('_fb_deg') || 0;
        var status = (node.data('status') || '').toUpperCase();
        var activity = parseFloat(node.data('activity_score')) || 0;

        // Radial distance: high-degree nodes closer to center
        var r = islandR * Math.pow(1 - deg / maxDeg, opts.degreeExp);

        // Golden angle spiral within the island
        var theta = i * GOLDEN_ANGLE;

        // Activity drift: active nodes pull toward center
        if (activity > 0) {
          r = r * (1 - activity * opts.activityWeight);
        }

        // Offline/idle orbit: push outward
        if (status === 'IDLE' || status === 'OFFLINE' || status === 'ERROR') {
          r = Math.max(r, islandR * opts.offlineOrbitScale);
          // Slightly jitter angle for visual separation
          theta += (i % 3 - 1) * 0.15;
        }

        positions[node.id()] = {
          x: islandCx + r * Math.cos(theta),
          y: islandCy + r * Math.sin(theta)
        };
      }
    }

    // ── 5. Apply positions ───────────────────────────────────────
    nodes.layoutPositions(this, opts, function (node) {
      return positions[node.id()] || { x: cx, y: cy };
    });

    return this;
  };

  // No-op for non-continuous layouts
  FractalBrainLayout.prototype.stop = function () { return this; };
  FractalBrainLayout.prototype.destroy = function () { return this; };

  // Register with Cytoscape
  if (typeof cytoscape !== 'undefined') {
    cytoscape('layout', 'fractal-brain', FractalBrainLayout);
  }

  // Also export for manual registration
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = FractalBrainLayout;
  }
})();
