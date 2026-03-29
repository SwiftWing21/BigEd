/**
 * fractal-brain — Custom Cytoscape layout extension
 *
 * BA fractal + Fibonacci spiral: radial degree placement, golden angle
 * distribution, island grouping on Fibonacci arms, offline orbits,
 * activity drift.  O(n) single-pass — handles 10K+ nodes instantly.
 *
 * Positions in model-space (0,0 center). Cytoscape fit() handles viewport.
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
    padding: 10,
    fit: true,
    animate: false,
    brainRadius: 300,    // model-space radius for the main brain
    islandGap: 40,       // gap between island edge and main brain
    degreeExp: 0.55,
    activityWeight: 0.4,
    offlineOrbitScale: 1.08  // subtle — keeps idle nodes near the surface
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

    // Model-space center (Cytoscape fit() will map to viewport)
    var cx = 0, cy = 0;
    // Scale brain radius to node count — sqrt keeps density consistent
    var brainR = Math.max(60, Math.sqrt(nCount) * 12);

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

    // ── 3. Position each island ────────────────────────────────────
    var positions = {};

    for (var g = 0; g < components.length; g++) {
      var comp = components[g];
      var frac = comp.length / (nCount || 1);

      // Island radius proportional to sqrt of its share of total nodes
      var islandR = brainR * Math.sqrt(frac) * 1.1;
      // Minimum so tiny components aren't invisible
      islandR = Math.max(islandR, 20);

      var islandCx, islandCy;
      if (g === 0) {
        // Main component centered
        islandCx = cx;
        islandCy = cy;
      } else {
        // Tuck secondary islands into the brain surface, not outside it
        // Small components (≤3 nodes) scatter along the outer shell
        // Larger components get their own pocket just outside
        var mainR = brainR * Math.sqrt(components[0].length / nCount) * 1.1;
        var theta = g * GOLDEN_ANGLE;
        var orbitR;
        if (comp.length <= 3) {
          // Scatter singles/pairs along the brain's outer edge
          orbitR = mainR * (0.8 + (g % 5) * 0.08);
        } else {
          // Larger islands sit snug against the main brain
          orbitR = mainR + islandR * 0.5;
        }
        islandCx = cx + orbitR * Math.cos(theta);
        islandCy = cy + orbitR * Math.sin(theta);
      }

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
        var angle = i * GOLDEN_ANGLE;

        // Activity drift: active nodes pull toward center
        if (activity > 0) {
          r = r * (1 - activity * opts.activityWeight);
        }

        // Offline/idle: nudge slightly outward (not a hard orbit)
        if (status === 'IDLE' || status === 'OFFLINE' || status === 'ERROR') {
          r = r * opts.offlineOrbitScale;
          angle += (i % 3 - 1) * 0.1;
        }

        positions[node.id()] = {
          x: islandCx + r * Math.cos(angle),
          y: islandCy + r * Math.sin(angle)
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
