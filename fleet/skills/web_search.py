"""
Web search with waterfall fallback:
  1. Brave Search API  (free: 2k/mo) — needs BRAVE_API_KEY
  2. Tavily API        (free: 1k/mo) — needs TAVILY_API_KEY
  3. Jina s.jina.ai   (free, no key) — always available
  4. DuckDuckGo        (fallback)    — no key, limited results

Each provider is tried in order. On rate-limit (429) or missing key, falls through to next.
"""
import json
import os
from pathlib import Path

import httpx

HEADERS = {"User-Agent": "fleet-agent/1.0"}
WATERFALL_LOG = Path(__file__).parent.parent / "knowledge" / "search_waterfall.jsonl"


def _log_provider(query, provider, success):
    with open(WATERFALL_LOG, "a") as f:
        from datetime import datetime
        f.write(json.dumps({
            "ts": datetime.now().isoformat(),
            "query": query[:80],
            "provider": provider,
            "success": success,
        }) + "\n")


def _brave(query):
    key = os.environ.get("BRAVE_API_KEY", "")
    if not key:
        return None
    resp = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": 5},
        headers={**HEADERS, "X-Subscription-Token": key, "Accept": "application/json"},
        timeout=10,
    )
    if resp.status_code == 429:
        return None  # rate limited, fall through
    resp.raise_for_status()
    data = resp.json()
    results = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")}
        for r in data.get("web", {}).get("results", [])[:5]
    ]
    return {"provider": "brave", "query": query, "results": results}


def _tavily(query):
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        return None
    resp = httpx.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "max_results": 5},
        headers=HEADERS,
        timeout=15,
    )
    if resp.status_code == 429:
        return None
    resp.raise_for_status()
    data = resp.json()
    results = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in data.get("results", [])[:5]
    ]
    return {"provider": "tavily", "query": query, "results": results}


def _jina(query):
    import urllib.parse
    encoded = urllib.parse.quote_plus(query)
    resp = httpx.get(
        f"https://s.jina.ai/{encoded}",
        headers={**HEADERS, "Accept": "application/json"},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    results = [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "") or r.get("content", "")[:300]}
        for r in data.get("data", [])[:5]
    ]
    return {"provider": "jina", "query": query, "results": results}


def _duckduckgo(query):
    resp = httpx.get(
        "https://api.duckduckgo.com/",
        params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
        headers=HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    related = [
        {"title": "", "url": r.get("FirstURL", ""), "snippet": r.get("Text", "")}
        for r in data.get("RelatedTopics", [])
        if isinstance(r, dict) and "Text" in r
    ][:5]
    abstract = data.get("AbstractText", "")
    return {"provider": "duckduckgo", "query": query, "abstract": abstract, "results": related}


def run(payload, config):
    query = payload.get("query") or payload.get("description", "")
    if not query:
        return {"error": "No query provided"}

    for name, fn in [("brave", _brave), ("tavily", _tavily), ("jina", _jina), ("duckduckgo", _duckduckgo)]:
        try:
            result = fn(query)
            if result is not None:
                _log_provider(query, name, True)
                return result
            _log_provider(query, name, False)
        except Exception as e:
            _log_provider(query, name, False)
            continue

    return {"error": "All search providers failed", "query": query}
