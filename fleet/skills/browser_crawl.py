"""
Browser crawl — full DOM rendering via Playwright (headless Chromium).

Handles JS-heavy pages that httpx can't render. Falls back to httpx if
Playwright is not installed.

Actions:
  crawl       — render page, extract text + links
  screenshot  — capture viewport screenshot (PNG)
  extract     — extract specific CSS selector content

Payload:
  url          str   target URL (required)
  selector     str   CSS selector to extract (for extract action)
  wait_sec     int   seconds to wait after load (default 2, for JS rendering)
  viewport     dict  {"width": 1280, "height": 720} (for screenshot)

Requires: playwright (pip install playwright && playwright install chromium)
Falls back to httpx for basic crawl if Playwright unavailable.

Returns: {action, url, content/screenshot_path, links}
"""
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
BROWSER_DIR = KNOWLEDGE_DIR / "browser"
SKILL_NAME = "browser_crawl"
DESCRIPTION = "Browser crawl — full DOM rendering via Playwright (headless Chromium)."

REQUIRES_NETWORK = True

_HAS_PLAYWRIGHT = None  # lazy check
_MCP_CHECKED = None      # (available: bool, url: str|None)

_BLOCKED_HOSTS = {'127.0.0.1', 'localhost', '169.254.169.254', '::1', '0.0.0.0', 'metadata.google.internal'}


def _check_ssrf(url):
    """Block requests to internal/metadata endpoints (SSRF protection)."""
    parsed = urlparse(url)
    if parsed.hostname in _BLOCKED_HOSTS:
        return False, f"Blocked internal URL: {parsed.hostname}"
    if parsed.hostname and parsed.hostname.startswith('10.'):
        return False, "Blocked private network"
    return True, ""


def _check_mcp_playwright():
    """Check if Playwright MCP server is configured and reachable (user's .mcp.json)."""
    global _MCP_CHECKED
    if _MCP_CHECKED is None:
        try:
            import sys
            sys.path.insert(0, str(FLEET_DIR))
            from mcp_manager import is_mcp_available
            available, url = is_mcp_available("playwright", timeout=2)
            _MCP_CHECKED = (available, url)
        except Exception:
            _MCP_CHECKED = (False, None)
    return _MCP_CHECKED


def _check_playwright():
    global _HAS_PLAYWRIGHT
    if _HAS_PLAYWRIGHT is None:
        try:
            from playwright.sync_api import sync_playwright
            _HAS_PLAYWRIGHT = True
        except ImportError:
            _HAS_PLAYWRIGHT = False
    return _HAS_PLAYWRIGHT


def _crawl_mcp(url, mcp_url, wait_sec=2, selector=None):
    """Crawl via Playwright MCP server — URL read from user's .mcp.json config."""
    import urllib.request

    # MCP Playwright uses JSON-RPC-like calls
    body = json.dumps({
        "method": "browser_navigate",
        "params": {"url": url},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{mcp_url}/navigate" if "/navigate" not in mcp_url else mcp_url,
        data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30 + wait_sec) as resp:
        data = json.loads(resp.read())

    # Extract content from MCP response
    content = data.get("content", data.get("text", data.get("body", "")))
    if isinstance(content, list):
        content = "\n".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )

    title = data.get("title", "")
    links = data.get("links", [])

    return {
        "title": title,
        "content": content[:10000] if isinstance(content, str) else str(content)[:10000],
        "links": links[:50] if isinstance(links, list) else [],
        "renderer": "mcp_playwright",
        "mcp_url": mcp_url,
    }


def _crawl_playwright(url, wait_sec=2, selector=None):
    """Full browser render via Playwright."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)

        if wait_sec:
            page.wait_for_timeout(wait_sec * 1000)

        if selector:
            elements = page.query_selector_all(selector)
            content = [el.text_content() or "" for el in elements]
        else:
            content = page.inner_text("body")

        # Extract links
        links = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({text: e.textContent.trim().slice(0,80), href: e.href}))"
        )

        title = page.title()
        browser.close()

    return {
        "title": title,
        "content": content if isinstance(content, list) else content[:10000],
        "links": links[:50],
    }


def _screenshot_playwright(url, viewport=None, wait_sec=2):
    """Capture viewport screenshot."""
    from playwright.sync_api import sync_playwright

    vp = viewport or {"width": 1280, "height": 720}

    BROWSER_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = BROWSER_DIR / f"screenshot_{ts}.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport_size=vp)
        page.goto(url, wait_until="networkidle", timeout=30000)

        if wait_sec:
            page.wait_for_timeout(wait_sec * 1000)

        page.screenshot(path=str(screenshot_path), full_page=False)
        title = page.title()
        browser.close()

    return {
        "title": title,
        "screenshot_path": str(screenshot_path),
        "viewport": vp,
    }


def _crawl_httpx_fallback(url):
    """Fallback: basic HTTP fetch without JS rendering."""
    import httpx
    from html.parser import HTMLParser

    resp = httpx.get(url, timeout=15, follow_redirects=True,
                     headers={"User-Agent": "fleet-browser/1.0"})
    resp.raise_for_status()
    html = resp.text

    # Simple text extraction
    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []
            self._skip = False
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript"):
                self._skip = True
        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript"):
                self._skip = False
        def handle_data(self, data):
            if not self._skip:
                t = data.strip()
                if t:
                    self.text.append(t)

    parser = TextExtractor()
    parser.feed(html)

    # Extract links
    import re
    links = [{"href": m.group(1), "text": ""} for m in re.finditer(r'href="([^"]+)"', html)]

    # Title
    title_m = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    title = title_m.group(1).strip() if title_m else ""

    return {
        "title": title,
        "content": "\n".join(parser.text)[:10000],
        "links": links[:50],
        "renderer": "httpx_fallback",
    }


def run(payload, config):
    url = payload.get("url", "")
    if not url:
        return {"error": "url required"}

    safe, ssrf_reason = _check_ssrf(url)
    if not safe:
        return {"error": ssrf_reason, "url": url}

    action = payload.get("action", "crawl")
    wait_sec = min(payload.get("wait_sec", 2), 10)

    BROWSER_DIR.mkdir(parents=True, exist_ok=True)

    # Resolve renderer: MCP server (user config) → local pip → httpx fallback
    mcp_available, mcp_url = _check_mcp_playwright()
    has_local = _check_playwright()

    try:
        if action == "screenshot":
            # Screenshots need local playwright (MCP doesn't support screenshots yet)
            if not has_local:
                return {"error": "Playwright required for screenshots. Run: pip install playwright && playwright install chromium"}
            viewport = payload.get("viewport", {"width": 1280, "height": 720})
            result = _screenshot_playwright(url, viewport, wait_sec)

        elif action == "extract":
            selector = payload.get("selector", "")
            if not selector:
                return {"error": "selector required for extract action"}
            if has_local:
                result = _crawl_playwright(url, wait_sec, selector)
            else:
                return {"error": "Playwright required for CSS selector extraction"}

        elif action == "crawl":
            # 3-tier fallback: MCP server → local playwright → httpx
            if mcp_available and mcp_url:
                try:
                    result = _crawl_mcp(url, mcp_url, wait_sec)
                except Exception:
                    # MCP failed — fall through to local
                    if has_local:
                        result = _crawl_playwright(url, wait_sec)
                    else:
                        result = _crawl_httpx_fallback(url)
            elif has_local:
                result = _crawl_playwright(url, wait_sec)
            else:
                result = _crawl_httpx_fallback(url)

        else:
            return {"error": f"Unknown action: {action}"}

        result["action"] = action
        result["url"] = url

        # Save to knowledge
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = BROWSER_DIR / f"browser_{action}_{ts}.json"
        out_file.write_text(json.dumps(result, indent=2, default=str))

        return result

    except Exception as e:
        return {"error": f"Browser crawl failed: {e}", "url": url, "action": action}