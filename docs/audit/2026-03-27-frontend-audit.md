# Frontend Audit — 2026-03-27

Audited files:
- `fleet/templates/dashboard.html` (~6938 lines)
- `fleet/templates/view_graph.html` (~1630 lines)
- `fleet/static/view_engine.js` (~1300 lines)
- `fleet/static/layout_fractal.js` (184 lines)
- `fleet/static/tokens.css` (92 lines)
- `BigEd/launcher/launcher_tkinter.py` (~4400 lines)
- `BigEd/launcher/ui/theme.py` (~200 lines)
- `BigEd/launcher/ui/boot.py` (~200+ lines)

---

## Critical (must fix before v1.0)

1. **[dashboard.html:6573] Unsafe DOM mutation in boot overlay renderer** — `renderBootStages()` uses direct HTML string assignment (`list.INNER_HTML = ''`) instead of the safe `textContent` or `clearChildren()` pattern used everywhere else. While the value is a static empty string (low XSS risk), this is the only remaining instance and should be migrated for consistency with the rest of the codebase.

2. **[dashboard.html:2432] `isDark()` function called but never defined** — The `loadActivityGraph()` function calls `isDark()` in 7 places (lines 2432, 2442, 2452, 2453, 2459, 2463, 2467) for Cytoscape style computation, but this function is never defined anywhere in dashboard.html. This will throw a `ReferenceError` at runtime whenever the activity graph view is toggled to "Graph" mode. Likely needs `function isDark() { return document.documentElement.classList.contains('dark'); }`.

3. **[dashboard.html:1341] Undefined CSS variable `--destructive`** — The "Stop Fleet" button uses `background:var(--destructive)` but this variable is never defined in the `:root` or `.dark` theme blocks. Same issue at lines 6339, 6455, and 6849. Should be `var(--danger)` to match the existing theme token.

4. **[dashboard.html:6634-6646] `switchModel()` references non-existent element** — The `switchModel()` function on line 6636 does `document.getElementById('header-model-select')` but the actual elements are `header-model-gpu` and `header-model-cpu` (lines 1333-1338). This means the select never gets disabled/re-enabled during model switch, and would throw at `.disabled = true` if `sel` is null. The function should reference the correct dropdown based on the `mode` parameter.

5. **[dashboard.html:3748] Unbounded `requestAnimationFrame` loop in neural graph** — `_pulseLoop()` (line 3721-3750) runs continuously via `requestAnimationFrame` even when no particles exist and even when the Pipeline section is not visible. This wastes CPU/GPU. Should check `_particles.length > 0` or section visibility before scheduling next frame.

6. **[dashboard.html:6106-6110] Activity chart polls every 2 seconds unconditionally** — `_schedulePoll('activity', ...)` calls `loadActivityChart()` which re-calls `loadNeuralLanes()` every 2 seconds while the dashboard is active. `loadNeuralLanes()` makes an API call AND restarts the animation loop. This is excessive — the SSE handler at line 5549 already calls `loadActivityChart()` on new data. The poll should either be removed or set to a much longer interval (30-60s).

## High (should fix before v1.0)

7. **[dashboard.html:2903] Neural lane animation never stops** — `renderNeuralFrame()` chains `requestAnimationFrame` indefinitely (line 2903). When navigating away from Dashboard, the canvas animation continues consuming resources. Should check if the dashboard section is visible before scheduling the next frame. Same issue with `renderAnalyticsNeuralFrame()` at line 4615.

8. **[dashboard.html:6113] `buildGateCard()` called during init, targets missing mount point** — `buildGateCard()` is called at line 6113 during page init, but `gate-card-mount` element doesn't exist in the main HTML. It only exists after the API Keys settings tab is opened (created dynamically in `_loadApiKeysTab()`). The function silently returns due to the null check, but it's a wasted call. The gate card should only be built when the API Keys tab is opened.

9. **[dashboard.html:1902] `createEl()` handler binding via `setAttribute`** — The `createEl()` helper at line 1902 uses `el.setAttribute(k, attrs[k])` for `onclick` attributes, which injects string event handlers instead of function references. While not a vulnerability since the values come from code (not user input), it's an anti-pattern. Several places in the ingest code also use `setAttribute('onclick', ...)` (lines 6173, 6181, 6188, 6258, 6310, 6317, 6333, 6403, 6528, 6533).

10. **[dashboard.html:4928-4937] Double JSON serialization in `enableGate()`** — The `enableGate()` function manually sets `Content-Type` header and calls `JSON.stringify()` on the body (line 4932), but `apiFetch()` already does this automatically at lines 2094-2097 when the body is an object. This results in the body being double-stringified. Should pass a plain object to `apiFetch()`.

11. **[view_graph.html:1532] SSE connection not cleaned up on view switch** — `eventSource` is created at line 1532 but only closed on `beforeunload` (line 1628). If the user navigates to another view via sidebar links, the old SSE connection leaks. Should close on view change.

12. **[view_graph.html:22-38] Self-referencing CSS variable fallbacks** — Lines 22-38 define fallbacks like `--bg: var(--bg, #1e1e1e)` which is a self-reference. Modern browsers handle this by using the fallback, but it's technically invalid CSS and may behave unexpectedly. Should use different names or just set the values directly.

13. **[dashboard.html:2783-2786] Global state via `window._neuralSideScroll`** — Neural lane sidebar scroll position is stored on `window` (lines 2783-2786, 2910-2914). This pollutes the global namespace and could conflict with other code.

14. **[launcher_tkinter.py:10] Raw `sqlite3` import at module level** — Line 10 imports `sqlite3` directly, which the project CLAUDE.md explicitly warns against: "never raw `sqlite3.connect()` in skills". While this is the launcher (not a skill), it sets a bad example and may bypass connection pooling if used.

## Medium (fix soon after v1.0)

15. **[dashboard.html:1180-1199] Mobile responsive breakpoints only cover basic cases** — The `@media (max-width: 768px)` block collapses grids to 1 column and stacks some elements, but several sections have hardcoded widths that break on mobile: Pipeline's `grid-template-columns: 1fr 1fr` (line 1559), swimlane's `min-width: 700px` (line 823), and the header controls inline styles (lines 1331-1342).

16. **[dashboard.html:no line] No keyboard navigation for sidebar** — Sidebar nav items are `<button>` elements (good for accessibility), but there's no `tabindex` management, no arrow key navigation, and no focus-visible styling. The omnibox has keyboard support, but the rest of the UI relies entirely on mouse interaction.

17. **[dashboard.html:no line] No ARIA labels on icon-only buttons** — Several buttons use only emoji/symbol content with no `aria-label`: the hamburger button (line 1328), refresh buttons (e.g., line 1427), and theme toggle buttons (lines 1316-1318). Screen readers would announce these as empty or with the raw unicode character.

18. **[dashboard.html:1997] Toast notification cleanup** — Toasts fade out via opacity transition (line 1997) but the element is removed after a fixed 300ms delay. If the browser is under load, the removal could happen before the transition completes, causing a visual jump. Should use `transitionend` event.

19. **[dashboard.html:6069-6090] Visibility change handler is a no-op** — The `visibilitychange` listener at line 6088 iterates poll timer keys but doesn't actually reschedule them. The comment says "timers auto-adjust on next tick" which is true (the `setTimeout` callback checks `document.hidden`), but the first interval after un-hiding still runs at the old rate.

20. **[view_engine.js:322-340] Animation canvas not resized on window resize** — `AnimationManager._ensureCanvas()` sets canvas resolution once on creation. If the window is resized, the canvas dimensions become stale. The `_draw()` method (line 404-447) does resize the canvas, but only while animation is running. A window resize while animations are stopped would leave a mismatched canvas.

21. **[tokens.css:1-92] No light theme** — `tokens.css` only defines dark themes (default, "classic", "modern"). The dashboard has its own light/dark toggle in `:root` / `.dark` blocks, but `view_graph.html` uses `tokens.css` which has no light mode. Users switching to light theme get a broken graph view.

22. **[dashboard.html:6123-6134] Right-click context menu globally suppressed** — `e.preventDefault()` on `contextmenu` (line 6124) blocks the browser's native context menu on the entire page. This prevents users from using "Inspect Element", "Copy", or other browser tools. Should be limited to specific elements or removed for production.

23. **[dashboard.html:2501-2511] Recursive animation via Cytoscape `.animate()` callback** — `pulseRunningEdges()` recursively chains Cytoscape animations. If the activity graph stays open for hours, this creates an ever-growing chain of animation callbacks. If `_activityCy` is destroyed while mid-animation, the guards help but the pattern is fragile.

24. **[layout_fractal.js:36] `Object.assign` used without polyfill** — `FractalBrainLayout` constructor uses `Object.assign` (line 36), which is not available in IE11. While IE11 support may not be a goal, the rest of the codebase uses ES5-compatible patterns.

## Low (nice to have)

25. **[dashboard.html:no line] No debounce on omnibox search** — `omniboxSuggest()` fires on every keystroke (`oninput`, line 1376). For fast typists, this generates unnecessary DOM updates. A 100-200ms debounce would improve performance.

26. **[dashboard.html:no line] Chart.js instances not destroyed on theme change** — `setTheme()` at line 1926 calls `.update()` on existing charts but doesn't recreate them. Chart.js colors (grid lines, text) are set at creation time from computed CSS variables. Theme switching only partially updates chart appearance.

27. **[dashboard.html:no line] No loading/error states for several API calls** — Functions like `loadThermal()`, `loadLaneGraph()`, and the ingest functions have `.catch(function() {})` with no user feedback. Failed API calls silently leave stale data or spinners on screen.

28. **[dashboard.html:4308-4327] Queue drag-and-drop has no visual feedback** — The queue reorder feature uses HTML5 drag-and-drop but provides no visual indicator of where the item will be dropped. No `dragenter`/`dragleave` styling.

29. **[view_graph.html:22-38] Fallback colors don't match tokens.css** — The fallback values in view_graph.html (e.g., `--bg: #1e1e1e`, `--accent: #7c3aed`) don't match tokens.css defaults (`--bg: #0a0e1a`, `--accent: #3b82f6`). If tokens.css fails to load, the graph page gets a different color scheme.

30. **[dashboard.html:no line] No favicon or manifest** — The dashboard has no `<link rel="icon">` or web app manifest. When bookmarked or pinned, it shows a generic browser icon.

31. **[theme.py:22-27] Windows-only font loading** — `load_custom_fonts()` only works on Windows (`gdi32.AddFontResourceExW`). On macOS/Linux, custom RuneScape fonts are silently unavailable. The font preset selection still shows "RuneScape" as an option on non-Windows platforms.

32. **[boot.py:42-43] f-strings used in killed process messages** — Uses f-strings (e.g., line 42) while the broader launcher codebase uses `.format()` or `%` style. Minor inconsistency.

## Incomplete Features

33. **[dashboard.html:6491-6560] Add Source modal has no validation** — `showAddSourceModal()` creates a form but `submitAddSource()` only checks if `datasetId` is non-empty. No validation on skill name, role, or column fields. No feedback if the API call succeeds or fails beyond a console.warn.

34. **[dashboard.html:6741-6925] Walkthrough wizard has no error recovery** — The first-run walkthrough fetches `/api/fleet/health` but if it fails, the error state shows "Could not detect system" with no retry button. The "Next" button still works, potentially leading to incomplete setup.

35. **[dashboard.html:1848-1869] Ingest section uses inconsistent styling** — The Ingest Hub section (line 1847) uses `<h1>` and `class="section-subtitle"` which don't exist in the CSS. All other sections use `.section-header > .section-title + .section-desc`. The ingest section appears un-styled relative to the rest.

36. **[dashboard.html:no line] Views section "Open View Builder" link untested** — The Views section links to `/view/builder` (line 1835) but there's no builder page in the templates directory. This is likely a planned feature that currently 404s.

37. **[dashboard.html:6680-6685] Model dropdown update from SSE uses wrong ID** — `updateHeaderControls()` references `header-model-select` (line 6682) which doesn't exist. Same bug as item #4 — the SSE handler can't sync the model dropdown state.

## Positive Findings

- **XSS prevention is thorough** — The `escH()` function and `createEl()` helper are used consistently. The codebase has been migrated away from string-based DOM mutation almost entirely (only 1 remaining instance, and it's a static empty string).

- **Safe DOM construction throughout** — Nearly all dynamic content uses `document.createElement()` + `textContent` instead of string interpolation. The `clearElement()` pattern in view_engine.js is clean.

- **Responsive design has a solid foundation** — Mobile sidebar overlay, hamburger menu, and grid collapse breakpoints are implemented. The 768px breakpoint handles the main layout well.

- **SSE architecture is well-designed** — Adaptive push rate, reconnection handling, and section-gated updates prevent unnecessary work. The SSE handler correctly dispatches to section-specific update functions.

- **Theme system is production-ready** — Three theme presets (Classic, Modern, Figma) with proper CSS variable cascading. The dashboard light/dark toggle and tokens.css design token bridge are well-implemented.

- **Cytoscape integration is sophisticated** — Progressive zoom levels with different detail tiers, LOD (level of detail) management, animation system, and badge overlays. The fractal-brain layout is O(n) and handles 10K+ nodes.

- **View engine has proper cleanup** — `BigEdViewEngine.destroy()` correctly tears down SSE, animations, badges, and Cytoscape instance. No obvious memory leaks in the view engine itself.

- **Accessibility basics are present** — `lang="en"`, viewport meta tag, semantic `<nav>`/`<aside>`/`<header>`/`<footer>` elements, and button elements for interactive items.

- **Error boundaries are reasonable** — Most API calls have `.catch()` handlers that show user-facing error messages or fallback states. The dashboard degrades gracefully when the fleet is offline.

- **No external tracking or analytics** — The dashboard loads Chart.js and Cytoscape from CDN but includes no tracking scripts, beacons, or third-party analytics.
