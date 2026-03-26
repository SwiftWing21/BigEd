/**
 * BigEdViewEngine — Cytoscape.js progressive zoom graph renderer
 *
 * Core view engine for the Hybrid ViewPort. Renders graph data from
 * the view registry onto a Cytoscape canvas with progressive zoom levels,
 * layout engine mapping, SSE real-time updates, and edge animation.
 *
 * Phase 3 of the Hybrid ViewPort spec.
 *
 * @version 0.400.00b
 */
(function (global) {
  "use strict";

  // ---------------------------------------------------------------------------
  // Constants
  // ---------------------------------------------------------------------------

  var ZOOM_OVERVIEW_THRESHOLD = 0.5;
  var ZOOM_DETAIL_THRESHOLD = 1.0;

  var ZOOM_LEVELS = { OVERVIEW: "overview", MID: "mid", DETAIL: "detail" };

  /** Map layout_hint -> Cytoscape layout config */
  var LAYOUT_MAP = {
    radial: {
      name: "concentric",
      concentric: function (node) {
        return node.degree();
      },
      levelWidth: function () {
        return 2;
      },
      animate: true,
      animationDuration: 400,
      padding: 40,
    },
    cluster: {
      name: "cose",
      animate: true,
      animationDuration: 600,
      nodeRepulsion: function () {
        return 40000;
      },
      idealEdgeLength: function () {
        return 120;
      },
      edgeElasticity: function () {
        return 200;
      },
      gravity: 0.25,
      numIter: 300,
      padding: 50,
    },
    swimlane: {
      name: "grid",
      rows: undefined,
      cols: 6,
      animate: true,
      animationDuration: 400,
      padding: 30,
      avoidOverlap: true,
    },
    tree: {
      // breadthfirst handles cyclic graphs (dagre crashes on cycles)
      name: "breadthfirst",
      directed: true,
      spacingFactor: 1.1,
      animate: true,
      animationDuration: 400,
      padding: 40,
      circle: false,
      avoidOverlap: true,
    },
  };

  /** Status -> color mapping */
  var STATUS_COLORS = {
    IDLE: null, // resolved from CSS --status-green
    BUSY: null, // resolved from CSS --status-orange
    ERROR: null, // resolved from CSS --status-red
    OFFLINE: "#888888",
  };

  /** Default category colors (mirrors view_registry.py CATEGORY_DEFAULTS) */
  var CATEGORY_COLORS = {
    fleet: "#4caf50",
    training: "#ff9800",
    storage: "#4fc3f7",
    external: "#9c7cfc",
    security: "#f44336",
  };

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  /**
   * Read a CSS custom property from :root, with fallback.
   */
  function cssVar(name, fallback) {
    var val = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    return val || fallback || "";
  }

  /**
   * Resolve status colors from CSS custom properties once on init.
   */
  function resolveStatusColors() {
    STATUS_COLORS.IDLE = cssVar("--status-green", "#4caf50");
    STATUS_COLORS.BUSY = cssVar("--status-orange", "#ff9800");
    STATUS_COLORS.ERROR = cssVar("--status-red", "#f44336");
    STATUS_COLORS.OFFLINE = cssVar("--dim", "#888888");
  }

  /**
   * Get color for a node based on its status, source color, or category.
   */
  function nodeColor(nodeData, sourceColor, category) {
    var status = (nodeData.status || "").toUpperCase();
    if (STATUS_COLORS[status]) {
      return STATUS_COLORS[status];
    }
    if (sourceColor) {
      return sourceColor;
    }
    if (category && CATEGORY_COLORS[category]) {
      return CATEGORY_COLORS[category];
    }
    return cssVar("--accent", "#7c3aed");
  }

  /**
   * Determine current zoom level name from numeric zoom.
   */
  function zoomLevelName(zoom) {
    if (zoom < ZOOM_OVERVIEW_THRESHOLD) return ZOOM_LEVELS.OVERVIEW;
    if (zoom > ZOOM_DETAIL_THRESHOLD) return ZOOM_LEVELS.DETAIL;
    return ZOOM_LEVELS.MID;
  }

  /**
   * Simple fetch wrapper with timeout and error handling.
   */
  function fetchJSON(url, timeoutMs) {
    timeoutMs = timeoutMs || 15000;
    return new Promise(function (resolve, reject) {
      var controller =
        typeof AbortController !== "undefined"
          ? new AbortController()
          : null;
      var timer = setTimeout(function () {
        if (controller) controller.abort();
        reject(new Error("Request timed out: " + url));
      }, timeoutMs);

      var opts = {};
      if (controller) opts.signal = controller.signal;

      fetch(url, opts)
        .then(function (resp) {
          clearTimeout(timer);
          if (!resp.ok) {
            throw new Error("HTTP " + resp.status + " from " + url);
          }
          return resp.json();
        })
        .then(resolve)
        .catch(function (err) {
          clearTimeout(timer);
          reject(err);
        });
    });
  }

  /**
   * Remove all child nodes from an element (safe alternative to innerHTML = "").
   */
  function clearElement(el) {
    while (el.firstChild) {
      el.removeChild(el.firstChild);
    }
  }

  /**
   * Render an error message inside the container element.
   */
  function showError(container, message) {
    var el = document.createElement("div");
    el.style.cssText =
      "display:flex;align-items:center;justify-content:center;" +
      "height:100%;width:100%;font-family:sans-serif;font-size:14px;" +
      "color:" +
      cssVar("--dim", "#a0a0a0") +
      ";text-align:center;padding:20px;";
    el.textContent = message;
    clearElement(container);
    container.appendChild(el);
  }

  // ---------------------------------------------------------------------------
  // Animation System
  // ---------------------------------------------------------------------------

  /**
   * Canvas overlay animation manager for edge particles.
   *
   * Draws animated particles on a canvas layered over the Cytoscape container.
   * Only active at detail zoom level (> 1.0).
   */
  function AnimationManager(cy, container) {
    this._cy = cy;
    this._container = container;
    this._canvas = null;
    this._ctx = null;
    this._running = false;
    this._animationId = null;
    this._rules = {}; // edge_type -> animation_type
    this._particles = [];
    this._lastTick = 0;
  }

  AnimationManager.prototype.setRules = function (rules) {
    this._rules = rules || {};
  };

  AnimationManager.prototype.start = function () {
    if (this._running) return;
    this._ensureCanvas();
    this._running = true;
    this._lastTick = performance.now();
    this._spawnParticles();
    this._tick();
  };

  AnimationManager.prototype.stop = function () {
    this._running = false;
    if (this._animationId) {
      cancelAnimationFrame(this._animationId);
      this._animationId = null;
    }
    this._particles = [];
    if (this._ctx && this._canvas) {
      this._ctx.clearRect(0, 0, this._canvas.width, this._canvas.height);
    }
  };

  AnimationManager.prototype.destroy = function () {
    this.stop();
    if (this._canvas && this._canvas.parentNode) {
      this._canvas.parentNode.removeChild(this._canvas);
    }
    this._canvas = null;
    this._ctx = null;
  };

  AnimationManager.prototype._ensureCanvas = function () {
    if (this._canvas) return;
    var canvas = document.createElement("canvas");
    canvas.style.cssText =
      "position:absolute;top:0;left:0;width:100%;height:100%;" +
      "pointer-events:none;z-index:10;";
    // Set canvas resolution to match container
    var rect = this._container.getBoundingClientRect();
    canvas.width = rect.width * (window.devicePixelRatio || 1);
    canvas.height = rect.height * (window.devicePixelRatio || 1);
    this._container.style.position = "relative";
    this._container.appendChild(canvas);
    this._canvas = canvas;
    this._ctx = canvas.getContext("2d");
    this._ctx.scale(
      window.devicePixelRatio || 1,
      window.devicePixelRatio || 1
    );
  };

  AnimationManager.prototype._spawnParticles = function () {
    this._particles = [];
    var cy = this._cy;
    var rules = this._rules;
    var edges = cy.edges();

    for (var i = 0; i < edges.length; i++) {
      var edge = edges[i];
      var edgeType = edge.data("edgeType") || "";
      var animType = rules[edgeType];
      if (!animType) continue;

      var count = animType === "flow" ? 2 : 1;
      for (var j = 0; j < count; j++) {
        this._particles.push({
          edge: edge,
          type: animType,
          progress: animType === "flow" ? j / count : Math.random(),
          speed: 0.002 + Math.random() * 0.003,
          opacity: 1.0,
          color: edge.data("color") || cssVar("--accent", "#7c3aed"),
        });
      }
    }
  };

  AnimationManager.prototype._tick = function () {
    if (!this._running) return;
    var self = this;
    this._animationId = requestAnimationFrame(function () {
      self._update();
      self._draw();
      self._tick();
    });
  };

  AnimationManager.prototype._update = function () {
    var now = performance.now();
    var dt = now - this._lastTick;
    this._lastTick = now;

    for (var i = 0; i < this._particles.length; i++) {
      var p = this._particles[i];
      switch (p.type) {
        case "flow":
          p.progress += p.speed * (dt / 16);
          if (p.progress > 1) p.progress -= 1;
          break;
        case "pulse":
          p.progress += p.speed * (dt / 16);
          if (p.progress > 1) p.progress -= 1;
          p.opacity = 0.3 + 0.7 * Math.abs(Math.sin(p.progress * Math.PI));
          break;
        case "fade":
          p.progress += p.speed * 0.5 * (dt / 16);
          if (p.progress > 1) p.progress -= 1;
          p.opacity = 0.2 + 0.8 * (1 - p.progress);
          break;
      }
    }
  };

  AnimationManager.prototype._draw = function () {
    if (!this._ctx || !this._canvas) return;

    var rect = this._container.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    if (
      this._canvas.width !== rect.width * dpr ||
      this._canvas.height !== rect.height * dpr
    ) {
      this._canvas.width = rect.width * dpr;
      this._canvas.height = rect.height * dpr;
      this._ctx.scale(dpr, dpr);
    }

    this._ctx.clearRect(0, 0, rect.width, rect.height);

    for (var i = 0; i < this._particles.length; i++) {
      var p = this._particles[i];
      if (!p.edge || p.edge.removed()) continue;

      var srcPos = p.edge.source().renderedPosition();
      var tgtPos = p.edge.target().renderedPosition();
      if (!srcPos || !tgtPos) continue;

      var x, y;
      if (p.type === "flow") {
        // Particle moves along the edge path
        x = srcPos.x + (tgtPos.x - srcPos.x) * p.progress;
        y = srcPos.y + (tgtPos.y - srcPos.y) * p.progress;
      } else {
        // Pulse/fade at midpoint
        x = (srcPos.x + tgtPos.x) / 2;
        y = (srcPos.y + tgtPos.y) / 2;
      }

      this._ctx.globalAlpha = p.opacity;
      this._ctx.fillStyle = p.color;
      this._ctx.beginPath();
      this._ctx.arc(x, y, p.type === "flow" ? 4 : 6, 0, Math.PI * 2);
      this._ctx.fill();
    }

    this._ctx.globalAlpha = 1.0;
  };

  // ---------------------------------------------------------------------------
  // Metric Badge Overlay
  // ---------------------------------------------------------------------------

  /**
   * DOM-based metric badge manager. Shows badges on hover at mid zoom.
   */
  function BadgeManager(cy, container) {
    this._cy = cy;
    this._container = container;
    this._badge = null;
    this._bound = false;
  }

  BadgeManager.prototype.enable = function () {
    if (this._bound) return;
    this._bound = true;
    var self = this;

    this._cy.on("mouseover", "node", function (evt) {
      self._show(evt.target);
    });

    this._cy.on("mouseout", "node", function () {
      self._hide();
    });
  };

  BadgeManager.prototype.disable = function () {
    this._hide();
    // Note: Cytoscape event listeners are cleaned up on cy.destroy()
    this._bound = false;
  };

  BadgeManager.prototype.destroy = function () {
    this._hide();
    if (this._badge && this._badge.parentNode) {
      this._badge.parentNode.removeChild(this._badge);
    }
    this._badge = null;
    this._bound = false;
  };

  BadgeManager.prototype._show = function (node) {
    var metrics = node.data("metrics");
    if (!metrics || typeof metrics !== "object") return;

    var keys = Object.keys(metrics);
    if (keys.length === 0) return;

    if (!this._badge) {
      this._badge = document.createElement("div");
      this._badge.style.cssText =
        "position:absolute;pointer-events:none;z-index:20;" +
        "background:" +
        cssVar("--glass-panel", "#2a2a2a") +
        ";" +
        "border:1px solid " +
        cssVar("--glass-border", "#404040") +
        ";" +
        "border-radius:" +
        cssVar("--btn-radius", "8px") +
        ";" +
        "padding:6px 10px;font-size:12px;line-height:1.4;" +
        "color:" +
        cssVar("--text", "#e0e0e0") +
        ";" +
        "font-family:Consolas,monospace;white-space:nowrap;" +
        "box-shadow:0 2px 8px rgba(0,0,0,0.4);";
      this._container.appendChild(this._badge);
    }

    // Build badge content using safe DOM methods (no innerHTML)
    clearElement(this._badge);
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      var v = metrics[k];
      if (typeof v === "number") v = v.toFixed(1);

      var row = document.createElement("div");
      row.style.color = cssVar("--dim", "#a0a0a0");

      var labelSpan = document.createTextNode(k + ": ");
      row.appendChild(labelSpan);

      var valueSpan = document.createElement("span");
      valueSpan.style.color = cssVar("--text", "#e0e0e0");
      valueSpan.textContent = String(v);
      row.appendChild(valueSpan);

      this._badge.appendChild(row);
    }

    var pos = node.renderedPosition();
    this._badge.style.left = pos.x + 15 + "px";
    this._badge.style.top = pos.y - 10 + "px";
    this._badge.style.display = "block";
  };

  BadgeManager.prototype._hide = function () {
    if (this._badge) {
      this._badge.style.display = "none";
    }
  };

  // ---------------------------------------------------------------------------
  // BigEdViewEngine
  // ---------------------------------------------------------------------------

  /**
   * @constructor
   * @param {Object} opts
   * @param {HTMLElement} opts.container    — Cytoscape mount point
   * @param {Object}      opts.viewConfig   — View JSON config (from /api/views/config/<name>)
   * @param {string}      opts.dataEndpoint — Graph data URL
   * @param {string}      opts.sseEndpoint  — SSE endpoint for live updates
   * @param {string}      [opts.theme]      — Active theme name (informational)
   */
  function BigEdViewEngine(opts) {
    if (!opts || !opts.container) {
      throw new Error("BigEdViewEngine: container element is required");
    }
    this._container = opts.container;
    this._viewConfig = opts.viewConfig || {};
    this._dataEndpoint = opts.dataEndpoint || "";
    this._sseEndpoint = opts.sseEndpoint || "/api/stream";
    this._theme = opts.theme || "figma";

    this._cy = null;
    this._sse = null;
    this._animationMgr = null;
    this._badgeMgr = null;
    this._currentZoomLevel = ZOOM_LEVELS.MID;
    this._graphData = null;
    this._destroyed = false;
    this._dagreWarned = false;

    // Node bridge for native launcher integration
    this._onNodeClick = null;
    this._onEdgeClick = null;
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  /**
   * Initialize the view engine: fetch data, render graph, connect SSE.
   * @returns {Promise<void>}
   */
  BigEdViewEngine.prototype.init = function () {
    var self = this;
    resolveStatusColors();

    return this._fetchData()
      .then(function (data) {
        self._graphData = data;
        self._render(data);
        self._connectSSE();
      })
      .catch(function (err) {
        showError(
          self._container,
          "Failed to load graph data: " + (err.message || String(err))
        );
      });
  };

  /**
   * Destroy the engine: disconnect SSE, stop animations, destroy Cytoscape.
   */
  BigEdViewEngine.prototype.destroy = function () {
    this._destroyed = true;
    this._disconnectSSE();
    if (this._animationMgr) {
      this._animationMgr.destroy();
      this._animationMgr = null;
    }
    if (this._badgeMgr) {
      this._badgeMgr.destroy();
      this._badgeMgr = null;
    }
    if (this._cy) {
      this._cy.destroy();
      this._cy = null;
    }
  };

  /**
   * Re-fetch data and re-render the graph.
   * @returns {Promise<void>}
   */
  BigEdViewEngine.prototype.refresh = function () {
    var self = this;
    return this._fetchData()
      .then(function (data) {
        self._graphData = data;
        self._render(data);
      })
      .catch(function (err) {
        showError(
          self._container,
          "Failed to refresh graph: " + (err.message || String(err))
        );
      });
  };

  /**
   * Set zoom level programmatically.
   * @param {number} level — Numeric zoom (e.g. 0.3, 0.7, 1.5)
   */
  BigEdViewEngine.prototype.setZoom = function (level) {
    if (this._cy) {
      this._cy.zoom({ level: level, renderedPosition: this._cy.center() });
      this._onZoomChange();
    }
  };

  /**
   * Get current zoom level name.
   * @returns {string} 'overview', 'mid', or 'detail'
   */
  BigEdViewEngine.prototype.getZoomLevel = function () {
    return this._currentZoomLevel;
  };

  /**
   * Register a callback for node clicks (native bridge integration).
   * @param {function(string, string)} fn — Called with (nodeId, nodeType)
   */
  BigEdViewEngine.prototype.onNodeClick = function (fn) {
    this._onNodeClick = fn;
  };

  /**
   * Register a callback for edge clicks (native bridge integration).
   * @param {function(string, string)} fn — Called with (edgeId, edgeType)
   */
  BigEdViewEngine.prototype.onEdgeClick = function (fn) {
    this._onEdgeClick = fn;
  };

  // ── Data Fetching ───────────────────────────────────────────────────────────

  BigEdViewEngine.prototype._fetchData = function () {
    if (!this._dataEndpoint) {
      return Promise.reject(new Error("No data endpoint configured"));
    }
    return fetchJSON(this._dataEndpoint, 15000);
  };

  // ── Rendering ───────────────────────────────────────────────────────────────

  BigEdViewEngine.prototype._render = function (data) {
    // Tear down previous instance
    if (this._animationMgr) {
      this._animationMgr.destroy();
      this._animationMgr = null;
    }
    if (this._badgeMgr) {
      this._badgeMgr.destroy();
      this._badgeMgr = null;
    }
    if (this._cy) {
      this._cy.destroy();
      this._cy = null;
    }

    // Clear container safely
    clearElement(this._container);

    // Parse sources and build Cytoscape elements
    var elements = this._buildElements(data);
    var layoutHint = this._resolveLayout(data);
    var layoutOpts = this._buildLayoutOptions(layoutHint, elements);

    // Check dagre availability — fall back to cose if not loaded
    if (
      layoutOpts.name === "dagre" &&
      typeof cytoscape !== "undefined" &&
      typeof cytoscapeDagre === "undefined"
    ) {
      // Test if dagre is registered as an extension
      try {
        var testLayout = cytoscape({
          headless: true,
          elements: [],
        });
        var hasDagre = false;
        try {
          testLayout.layout({ name: "dagre" });
          hasDagre = true;
        } catch (e) {
          hasDagre = false;
        }
        testLayout.destroy();

        if (!hasDagre && !this._dagreWarned) {
          console.warn(
            "BigEdViewEngine: dagre layout requested but cytoscape-dagre not loaded. Falling back to cose."
          );
          this._dagreWarned = true;
          layoutOpts = {};
          var fallback = LAYOUT_MAP.cluster;
          for (var fk in fallback) {
            if (fallback.hasOwnProperty(fk)) {
              layoutOpts[fk] = fallback[fk];
            }
          }
        }
      } catch (e) {
        // If headless test fails, just use the layout and let Cytoscape handle it
      }
    }

    // Initialize Cytoscape
    this._cy = cytoscape({
      container: this._container,
      elements: elements,
      style: this._buildStylesheet(),
      layout: layoutOpts,
      minZoom: 0.1,
      maxZoom: 4.0,
      wheelSensitivity: 0.3,
      boxSelectionEnabled: false,
    });

    // Initialize subsystems
    this._animationMgr = new AnimationManager(this._cy, this._container);
    this._badgeMgr = new BadgeManager(this._cy, this._container);

    // Set animation rules from merged sources
    var animRules = this._collectAnimationRules(data);
    this._animationMgr.setRules(animRules);

    // Bind zoom handler
    var self = this;
    this._cy.on("zoom", function () {
      self._onZoomChange();
    });

    // Bind click handlers for native bridge
    this._cy.on("tap", "node", function (evt) {
      var node = evt.target;
      if (self._onNodeClick) {
        self._onNodeClick(node.id(), node.data("type") || "");
      }
      // Also notify pywebview bridge if present
      if (
        typeof window !== "undefined" &&
        window.bigEdBridge &&
        window.bigEdBridge.onNodeClick
      ) {
        window.bigEdBridge.onNodeClick(node.id(), node.data("type") || "");
      }
    });

    this._cy.on("tap", "edge", function (evt) {
      var edge = evt.target;
      if (self._onEdgeClick) {
        self._onEdgeClick(edge.id(), edge.data("edgeType") || "");
      }
      if (
        typeof window !== "undefined" &&
        window.bigEdBridge &&
        window.bigEdBridge.onEdgeClick
      ) {
        window.bigEdBridge.onEdgeClick(edge.id(), edge.data("edgeType") || "");
      }
    });

    // Apply initial zoom level styling
    this._onZoomChange();
  };

  /**
   * Build Cytoscape elements array from the graph data response.
   */
  BigEdViewEngine.prototype._buildElements = function (data) {
    var nodes = [];
    var edges = [];
    var nodeIdSet = {};
    var sources = data.sources || [];

    for (var s = 0; s < sources.length; s++) {
      var src = sources[s];
      var srcName = src.source || "unknown";
      var srcColor = src.color || CATEGORY_COLORS[src.category] || null;
      var category = src.category || "";

      var srcNodes = src.nodes || [];
      for (var n = 0; n < srcNodes.length; n++) {
        var nd = srcNodes[n];
        if (nodeIdSet[nd.id]) continue; // deduplicate
        nodeIdSet[nd.id] = true;

        nodes.push({
          group: "nodes",
          data: {
            id: nd.id,
            label: nd.id,
            type: nd.type || "",
            status: nd.status || "",
            metrics: nd.metrics || {},
            source: srcName,
            category: category,
            sourceColor: srcColor,
            color: nodeColor(nd, srcColor, category),
            throughput: (nd.metrics && nd.metrics.tok_s) || 0,
          },
        });
      }

      var srcEdges = src.edges || [];
      for (var e = 0; e < srcEdges.length; e++) {
        var ed = srcEdges[e];
        var edgeId =
          "e_" + (ed.source || "") + "_" + (ed.target || "") + "_" + e + "_" + s;
        edges.push({
          group: "edges",
          data: {
            id: edgeId,
            source: ed.source,
            target: ed.target,
            edgeType: ed.type || "",
            weight: ed.weight || 1,
            color: srcColor || cssVar("--accent", "#7c3aed"),
          },
        });

        // Ensure source/target nodes exist (auto-create if missing)
        if (!nodeIdSet[ed.source]) {
          nodeIdSet[ed.source] = true;
          nodes.push({
            group: "nodes",
            data: {
              id: ed.source,
              label: ed.source,
              type: "auto",
              status: "",
              metrics: {},
              source: srcName,
              category: category,
              sourceColor: srcColor,
              color: srcColor || cssVar("--dim", "#888888"),
              throughput: 0,
            },
          });
        }
        if (!nodeIdSet[ed.target]) {
          nodeIdSet[ed.target] = true;
          nodes.push({
            group: "nodes",
            data: {
              id: ed.target,
              label: ed.target,
              type: "auto",
              status: "",
              metrics: {},
              source: srcName,
              category: category,
              sourceColor: srcColor,
              color: srcColor || cssVar("--dim", "#888888"),
              throughput: 0,
            },
          });
        }
      }
    }

    return nodes.concat(edges);
  };

  /**
   * Resolve the layout hint from graph data or view config.
   */
  BigEdViewEngine.prototype._resolveLayout = function (data) {
    // View config takes precedence
    if (this._viewConfig && this._viewConfig.layout) {
      return this._viewConfig.layout;
    }
    // Then data-level view field
    if (data.view && data.view.layout) {
      return data.view.layout;
    }
    // Then first source's layout hint
    var sources = data.sources || [];
    if (sources.length > 0 && sources[0].layout_hint) {
      return sources[0].layout_hint;
    }
    return "cluster"; // safe default
  };

  /**
   * Build layout options from hint, with swimlane special handling.
   */
  BigEdViewEngine.prototype._buildLayoutOptions = function (hint, elements) {
    var base = LAYOUT_MAP[hint];
    if (!base) {
      base = LAYOUT_MAP.cluster;
    }

    // Deep copy to avoid mutating the template
    var opts = {};
    for (var k in base) {
      if (base.hasOwnProperty(k)) {
        opts[k] = base[k];
      }
    }

    // Swimlane: use position-based layout from view config lanes
    if (hint === "swimlane" && this._viewConfig && this._viewConfig.lanes) {
      opts = this._buildSwimlaneLayout(this._viewConfig.lanes, elements);
    }

    return opts;
  };

  /**
   * Build a preset layout for swimlane views using lane definitions.
   */
  BigEdViewEngine.prototype._buildSwimlaneLayout = function (lanes, elements) {
    var positions = {};
    var laneWidth = 250;
    var nodeSpacing = 80;

    for (var i = 0; i < lanes.length; i++) {
      var lane = lanes[i];
      var laneSources = lane.sources || [];
      var laneX = i * laneWidth + laneWidth / 2;

      // Find nodes belonging to this lane's sources
      var yOffset = 0;
      for (var j = 0; j < elements.length; j++) {
        var el = elements[j];
        if (el.group !== "nodes") continue;
        var nodeSrc = el.data.source || "";
        if (laneSources.indexOf(nodeSrc) !== -1) {
          positions[el.data.id] = { x: laneX, y: yOffset * nodeSpacing + 60 };
          yOffset++;
        }
      }
    }

    return {
      name: "preset",
      positions: function (node) {
        return positions[node.id()] || { x: 0, y: 0 };
      },
      animate: true,
      animationDuration: 400,
      padding: 40,
    };
  };

  /**
   * Collect animation rules from all sources in the graph data.
   */
  BigEdViewEngine.prototype._collectAnimationRules = function (data) {
    var rules = {};
    var sources = data.sources || [];

    // Merge animation rules from each source
    for (var s = 0; s < sources.length; s++) {
      var src = sources[s];
      if (src.animation_rules) {
        for (var key in src.animation_rules) {
          if (src.animation_rules.hasOwnProperty(key)) {
            rules[key] = src.animation_rules[key];
          }
        }
      }
    }

    // View config animation overrides
    if (this._viewConfig && this._viewConfig.animation) {
      for (var ak in this._viewConfig.animation) {
        if (this._viewConfig.animation.hasOwnProperty(ak)) {
          rules[ak] = this._viewConfig.animation[ak];
        }
      }
    }

    return rules;
  };

  // ── Cytoscape Stylesheet ────────────────────────────────────────────────────

  BigEdViewEngine.prototype._buildStylesheet = function () {
    var bgColor = cssVar("--bg", "#1e1e1e");
    var textColor = cssVar("--text", "#e0e0e0");
    var borderColor = cssVar("--glass-border", "#404040");
    var accentColor = cssVar("--accent", "#7c3aed");

    return [
      // ── Node base style ──
      {
        selector: "node",
        style: {
          "background-color": "data(color)",
          label: "data(label)",
          color: textColor,
          "font-size": "11px",
          "font-family": "Consolas, monospace",
          "text-valign": "bottom",
          "text-halign": "center",
          "text-margin-y": 6,
          width: 28,
          height: 28,
          "border-width": 2,
          "border-color": borderColor,
          "text-outline-width": 2,
          "text-outline-color": bgColor,
          "text-outline-opacity": 0.8,
          "transition-property":
            "background-color, width, height, border-color, border-width",
          "transition-duration": "200ms",
        },
      },
      // ── Node: status-based border highlights ──
      {
        selector: 'node[status = "BUSY"]',
        style: {
          "border-color": cssVar("--status-orange", "#ff9800"),
          "border-width": 3,
        },
      },
      {
        selector: 'node[status = "ERROR"]',
        style: {
          "border-color": cssVar("--status-red", "#f44336"),
          "border-width": 3,
        },
      },
      {
        selector: 'node[status = "OFFLINE"]',
        style: {
          opacity: 0.5,
        },
      },
      // ── Node: selected ──
      {
        selector: "node:selected",
        style: {
          "border-color": accentColor,
          "border-width": 3,
          "overlay-color": accentColor,
          "overlay-opacity": 0.1,
        },
      },
      // ── Edge base style ──
      {
        selector: "edge",
        style: {
          width: "mapData(weight, 0, 10, 1, 6)",
          "line-color": "data(color)",
          "target-arrow-color": "data(color)",
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          opacity: 0.6,
          "transition-property": "width, line-color, opacity",
          "transition-duration": "200ms",
        },
      },
      // ── Edge: selected ──
      {
        selector: "edge:selected",
        style: {
          opacity: 1.0,
          width: "mapData(weight, 0, 10, 2, 8)",
          "overlay-color": accentColor,
          "overlay-opacity": 0.1,
        },
      },
    ];
  };

  // ── Zoom Level Management ───────────────────────────────────────────────────

  BigEdViewEngine.prototype._onZoomChange = function () {
    if (!this._cy) return;
    var zoom = this._cy.zoom();
    var newLevel = zoomLevelName(zoom);

    if (newLevel === this._currentZoomLevel) return;
    var prevLevel = this._currentZoomLevel;
    this._currentZoomLevel = newLevel;

    this._applyZoomLevel(newLevel, prevLevel);
  };

  BigEdViewEngine.prototype._applyZoomLevel = function (level) {
    if (!this._cy) return;
    var self = this;

    switch (level) {
      case ZOOM_LEVELS.OVERVIEW:
        // Lightweight: hide labels, small nodes, thin edges, no badges, no animation
        this._cy.batch(function () {
          self._cy.nodes().style({
            label: "",
            width: 16,
            height: 16,
            "border-width": 1,
          });
          self._cy.edges().style({
            width: 1,
            opacity: 0.4,
          });
        });
        this._applyHeatmap();
        if (this._badgeMgr) this._badgeMgr.disable();
        if (this._animationMgr) this._animationMgr.start();
        break;

      case ZOOM_LEVELS.MID:
        // Moderate: show labels, weighted edges, badges on hover, animations on
        this._cy.batch(function () {
          self._cy.nodes().style({
            label: "data(label)",
            width: 28,
            height: 28,
            "border-width": 2,
            "font-size": "11px",
          });
          self._cy.edges().style({
            width: "mapData(weight, 0, 10, 1, 6)",
            opacity: 0.6,
          });
        });
        this._clearHeatmap();
        if (this._badgeMgr) this._badgeMgr.enable();
        if (this._animationMgr) this._animationMgr.start();
        break;

      case ZOOM_LEVELS.DETAIL:
        // Heavy: large nodes, thick edges, badges, animations
        this._cy.batch(function () {
          self._cy.nodes().style({
            label: "data(label)",
            width: 36,
            height: 36,
            "border-width": 2,
            "font-size": "13px",
          });
          self._cy.edges().style({
            width: "mapData(weight, 0, 10, 2, 8)",
            opacity: 0.8,
          });
        });
        this._clearHeatmap();
        if (this._badgeMgr) this._badgeMgr.enable();
        if (this._animationMgr) this._animationMgr.start();
        break;
    }
  };

  /**
   * Apply heat map coloring to nodes based on throughput at overview zoom.
   * Maps throughput (tok_s or similar) to a green->yellow->red gradient.
   */
  BigEdViewEngine.prototype._applyHeatmap = function () {
    if (!this._cy) return;

    var nodes = this._cy.nodes();
    var maxThroughput = 0;

    // Find max throughput for normalization
    for (var i = 0; i < nodes.length; i++) {
      var t = nodes[i].data("throughput") || 0;
      if (t > maxThroughput) maxThroughput = t;
    }

    if (maxThroughput === 0) return; // no throughput data, keep default colors

    var self = this;
    this._cy.batch(function () {
      for (var i = 0; i < nodes.length; i++) {
        var node = nodes[i];
        var t = node.data("throughput") || 0;
        var ratio = t / maxThroughput;
        var color = _heatColor(ratio);
        node.style("background-color", color);
      }
    });
  };

  /**
   * Clear heat map, revert to data-driven colors.
   */
  BigEdViewEngine.prototype._clearHeatmap = function () {
    if (!this._cy) return;
    var self = this;
    this._cy.batch(function () {
      self._cy.nodes().style("background-color", "data(color)");
    });
  };

  /**
   * Interpolate a green->yellow->red heat color from ratio [0..1].
   */
  function _heatColor(ratio) {
    // 0.0 = low (cool green), 0.5 = mid (yellow), 1.0 = high (hot red)
    var r, g, b;
    if (ratio < 0.5) {
      var t = ratio * 2; // 0..1 within first half
      r = Math.round(255 * t);
      g = Math.round(200 + 55 * (1 - t));
      b = 50;
    } else {
      var t2 = (ratio - 0.5) * 2; // 0..1 within second half
      r = 255;
      g = Math.round(255 * (1 - t2));
      b = 50;
    }
    return "rgb(" + r + "," + g + "," + b + ")";
  }

  // ── SSE Real-Time Updates ───────────────────────────────────────────────────

  BigEdViewEngine.prototype._connectSSE = function () {
    if (this._destroyed || !this._sseEndpoint) return;

    var self = this;
    try {
      this._sse = new EventSource(this._sseEndpoint);
    } catch (err) {
      console.warn("BigEdViewEngine: SSE connection failed:", err);
      return;
    }

    this._sse.onmessage = function (evt) {
      if (self._destroyed) return;
      try {
        var msg = JSON.parse(evt.data);
        self._handleSSE(msg);
      } catch (err) {
        // Ignore unparseable SSE messages (keepalives, etc.)
      }
    };

    this._sse.onerror = function () {
      // EventSource auto-reconnects; nothing to do
    };
  };

  BigEdViewEngine.prototype._disconnectSSE = function () {
    if (this._sse) {
      try {
        this._sse.close();
      } catch (err) {
        // Ignore close errors
      }
      this._sse = null;
    }
  };

  /**
   * Handle an SSE message and patch the graph in-place.
   *
   * Expected SSE data format (from /api/stream):
   * { "type": "status", "data": { "agents": [...], "tasks": {...} } }
   */
  BigEdViewEngine.prototype._handleSSE = function (msg) {
    if (!this._cy || !msg) return;
    if (msg.type !== "status") return;

    var data = msg.data || {};
    var agents = data.agents || [];

    if (agents.length === 0) return;

    var self = this;
    this._cy.batch(function () {
      for (var i = 0; i < agents.length; i++) {
        var agent = agents[i];
        var nodeId = agent.name || agent.id;
        if (!nodeId) continue;

        var node = self._cy.getElementById(nodeId);
        if (node && node.length > 0) {
          // Patch existing node data
          var newStatus = (agent.status || "").toUpperCase();
          var oldStatus = (node.data("status") || "").toUpperCase();

          if (newStatus !== oldStatus) {
            node.data("status", agent.status || "");
            var color = nodeColor(
              { status: agent.status },
              node.data("sourceColor"),
              node.data("category")
            );
            node.data("color", color);
          }

          // Update metrics if present
          if (agent.metrics) {
            node.data("metrics", agent.metrics);
            if (agent.metrics.tok_s !== undefined) {
              node.data("throughput", agent.metrics.tok_s);
            }
          }
        }
        // We do not add new nodes from SSE — only patch existing ones.
        // New nodes require a full refresh() call.
      }
    });

    // Re-apply heatmap if in overview zoom
    if (this._currentZoomLevel === ZOOM_LEVELS.OVERVIEW) {
      this._applyHeatmap();
    }
  };

  // ── Expose on window ────────────────────────────────────────────────────────

  global.BigEdViewEngine = BigEdViewEngine;

  // Also expose constants for external use
  global.BigEdViewEngine.ZOOM_LEVELS = ZOOM_LEVELS;
  global.BigEdViewEngine.LAYOUT_MAP = LAYOUT_MAP;
  global.BigEdViewEngine.STATUS_COLORS = STATUS_COLORS;
  global.BigEdViewEngine.CATEGORY_COLORS = CATEGORY_COLORS;
})(typeof window !== "undefined" ? window : this);
