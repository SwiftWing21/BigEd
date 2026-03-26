# Web URL Ingestion (Phase 2) — Design Spec

**Date:** 2026-03-26
**Status:** Draft
**Depends on:** Ingestion Hub Phase 1 (2026-03-26-ingestion-hub-design.md), Firecrawl MCP/API
**Location:** Web dashboard Ingest page — new "Web URL" source type

## Problem

Phase 1 of the Ingestion Hub supports HuggingFace datasets and local files. Web content (documentation, blogs, research sites, API docs) is a major source of training material for fleet agents but was deferred due to complexity: scraping libraries, robots.txt, rate limiting, content extraction.

Firecrawl solves the scraping infrastructure problem. The remaining risk is **quality** — crawling a junk site wastes credits and pollutes the knowledge base. We need a pre-gate quality check before committing to a full crawl.

## Solution

A three-stage gated pipeline for web URL ingestion:

1. **Free probe** — HEAD request + robots.txt (zero cost, instant)
2. **Single-page scrape** — Firecrawl `/scrape` on root URL (1 credit, ~2s)
3. **Quality gate** — local model scores relevance, UI shows preview + cost estimate
4. **Full crawl** — Firecrawl `/crawl` only after user approves (N credits)

## Three-Stage Pre-Gate Pipeline

### Stage 1: Free Probe (0 credits, <1s)

Before touching Firecrawl, do a zero-cost check:

```python
def probe_url(url: str) -> dict:
    """Free pre-check: HEAD request + robots.txt."""
    # 1. HEAD request
    #    - Status code (reject 4xx/5xx)
    #    - Content-Type (reject non-text: images, binaries, video)
    #    - Redirect chain (flag if > 3 redirects)
    #    - Content-Length hint (flag if > 50MB)

    # 2. robots.txt compliance
    #    - Fetch {origin}/robots.txt
    #    - Parse with urllib.robotparser
    #    - Check if our user-agent is allowed for the target path
    #    - Extract Sitemap URLs (useful for crawl scope estimation)

    # 3. Domain basic checks
    #    - Is it a known content farm / SEO spam domain? (optional blocklist)
    #    - Is it behind a paywall indicator? (common paywall meta tags)

    return {
        "status": 200,
        "content_type": "text/html",
        "redirects": 1,
        "robots_allowed": True,
        "sitemap_urls": ["https://example.com/sitemap.xml"],
        "warnings": [],       # e.g., ["3+ redirects", "large page"]
        "blocked": False,     # True = stop here, don't proceed
        "block_reason": None, # e.g., "robots.txt disallows"
    }
```

**Gate:** If `blocked=True`, show reason in UI and stop. Otherwise proceed to Stage 2.

### Stage 2: Single-Page Scrape (1 credit, ~2s)

Use Firecrawl `/scrape` to extract the root page content:

```python
def scrape_probe(url: str) -> dict:
    """Single-page Firecrawl scrape for quality assessment."""
    # Call Firecrawl /scrape API (or MCP tool)
    # Returns: markdown content, metadata, links

    # Extract quality signals:
    result = {
        "title": "...",
        "word_count": 1250,
        "text_to_markup_ratio": 0.62,    # >0.3 is good, <0.15 is ad-heavy
        "language": "en",
        "has_main_content": True,        # detected <main> or <article> tag
        "outbound_links": 45,            # estimate crawl scope
        "internal_links": 120,           # pages available to crawl
        "content_preview": "First 500 chars of extracted text...",
        "markdown_preview": "First 1000 chars of markdown...",
        "metadata": {
            "description": "...",
            "author": "...",
            "publish_date": "...",
        },
        "paywall_detected": False,       # common paywall patterns in HTML
        "credits_used": 1,
    }
    return result
```

**Reject signals** (auto-fail, don't proceed):
- `word_count < 100` — empty or stub page
- `text_to_markup_ratio < 0.10` — almost entirely navigation/ads
- `paywall_detected = True` — content behind paywall
- `language` mismatch (if user specified expected language)

### Stage 3: Quality Gate (0 credits, ~1-3s)

Run the extracted content through the local conductor model for relevance scoring:

```python
def score_relevance(content: str, target_skill: str, config: dict) -> dict:
    """Score content relevance using local model (qwen3:4b, zero cost)."""
    prompt = f"""Rate how useful this web content would be for training an AI agent
that performs "{target_skill}" tasks.

Content (first 2000 chars):
{content[:2000]}

Reply with ONLY a JSON object:
{{"score": <1-10>, "reason": "<one sentence>", "topics": ["<topic1>", "<topic2>"]}}"""

    # Call via providers._call_local_model() or direct Ollama
    # Parse JSON response

    return {
        "relevance_score": 8,         # 1-10
        "reason": "Contains detailed code review practices with examples",
        "topics": ["code review", "pull requests", "best practices"],
        "model": "qwen3:4b",
    }
```

**Quality thresholds** (configurable in fleet.toml):
```toml
[ingest.web]
min_relevance_score = 6        # reject below this (1-10)
min_word_count = 100           # reject thin pages
min_text_ratio = 0.10          # reject ad-heavy pages
max_crawl_pages = 200          # hard cap per crawl
max_crawl_credits = 200        # budget cap per crawl
```

### Gate Decision UI

After all three stages, the dashboard shows a decision card:

```
┌─────────────────────────────────────────────────┐
│  🌐 https://example.com/docs                    │
│                                                   │
│  Probe: ✓ 200 OK • robots.txt allowed            │
│  Quality: 8/10 — "Detailed API docs with examples"│
│  Content: 1,250 words • English • 0.62 text ratio │
│                                                   │
│  ── Preview ──────────────────────────────────── │
│  # API Authentication                             │
│  All API requests require a Bearer token...       │
│  (click to expand)                                │
│                                                   │
│  ── Crawl Estimate ──────────────────────────── │
│  ~120 internal pages • ~120 credits               │
│  Est. storage: ~15 MB                             │
│                                                   │
│  Depth: [1] [2] [3▼] [5] [All]   Limit: [50▼]   │
│                                                   │
│  [Cancel]           [Scrape This Page Only]       │
│                     [Crawl Site (est. 120 credits)]│
└─────────────────────────────────────────────────┘
```

User can:
- **Cancel** — stop, no further credits spent
- **Scrape This Page Only** — just stage the probe page (already fetched, 0 additional credits)
- **Crawl Site** — proceed to full crawl with depth/limit controls

## Full Crawl Flow

After user approves:

1. Call Firecrawl `/crawl` with:
   - `url`: target URL
   - `limit`: user-selected page cap
   - `maxDepth`: user-selected depth
   - `excludePaths`: respect robots.txt disallows

2. Firecrawl returns pages asynchronously — poll status endpoint

3. For each returned page:
   - Run through quality scorer (batch, local model)
   - Pages scoring below threshold are flagged but still stored (user can review)
   - Store as JSONL in `knowledge/ingest_cache/web_<domain>/`
   - Each page is a cache entry with: url, title, markdown content, score, word count

4. Stage all passing pages in the Ingest staging area
   - Color: purple (web source)
   - Destination toggle: Tasks or RAG (same as HF sources)

5. SSE events during crawl:
   - `ingest_crawl_start`: `{url, estimated_pages}`
   - `ingest_crawl_progress`: `{pages_done, pages_total, avg_score}`
   - `ingest_crawl_complete`: `{total_pages, passed_quality, credits_used}`

## Firecrawl Integration

### API Client (`ingest_manager.py` additions)

```python
FIRECRAWL_API = "https://api.firecrawl.dev/v1"

def _get_firecrawl_key() -> str | None:
    """Get Firecrawl API key from env or ~/.secrets."""
    key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not key:
        # Try ~/.secrets
        secrets_path = Path.home() / ".secrets"
        if secrets_path.exists():
            for line in secrets_path.read_text().splitlines():
                if line.strip().startswith("export FIRECRAWL_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return key or None

def firecrawl_scrape(url: str) -> dict:
    """Single-page scrape via Firecrawl API."""
    key = _get_firecrawl_key()
    if not key:
        return {"error": "missing_key", "key_name": "FIRECRAWL_API_KEY",
                "signup_hint": "firecrawl.dev/app"}
    # POST to /v1/scrape with url, formats: ["markdown"]
    # timeout=30
    ...

def firecrawl_crawl(url: str, limit: int = 50, max_depth: int = 3) -> dict:
    """Start a crawl job via Firecrawl API. Returns job ID for polling."""
    key = _get_firecrawl_key()
    if not key:
        return {"error": "missing_key", "key_name": "FIRECRAWL_API_KEY",
                "signup_hint": "firecrawl.dev/app"}
    # POST to /v1/crawl with url, limit, maxDepth
    # Returns: {id: "crawl-job-id", url: "status-url"}
    ...

def firecrawl_crawl_status(job_id: str) -> dict:
    """Poll crawl job status. Returns pages when complete."""
    # GET /v1/crawl/{job_id}
    # Returns: {status: "completed", data: [{markdown, metadata, ...}]}
    ...
```

### MCP Alternative

If the Firecrawl MCP server is running (configured in `.mcp.json`), the fleet can also use it directly via `mcp_manager.py` tool routing. The MCP path is preferred when available (no API key management needed — key is in the MCP server env).

Detection order:
1. Check if `firecrawl-mcp` is registered in MCP manager → use MCP tools
2. Fall back to direct API with `FIRECRAWL_API_KEY`
3. If neither available → show "missing_key" prompt in UI

## Missing Key Flow (Contextual Prompts)

When any ingest operation hits a missing key, the API returns:

```json
{
  "error": "missing_key",
  "key_name": "FIRECRAWL_API_KEY",
  "label": "Firecrawl Web Scraping API",
  "signup_hint": "firecrawl.dev/app → Free tier: 500 credits/month",
  "action_url": "/settings#api-keys"
}
```

Dashboard JS detects `error === "missing_key"` and shows a toast notification:

```
⚠️ Firecrawl API key required
   Free tier: 500 credits/month
   [Enter Key]  [Learn More ↗]
```

"Enter Key" navigates to Settings > API Keys panel (Phase 2 dashboard work).
"Learn More" opens the signup_hint URL in a new tab.

This pattern applies to ALL keyed services, not just Firecrawl — Brave, Tavily, Anthropic, Gemini, HuggingFace, etc.

## New API Endpoints

Added to `ingest_blueprint.py`:

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/ingest/web/probe` | Free probe (HEAD + robots.txt) |
| POST | `/api/ingest/web/scrape` | Single-page Firecrawl scrape |
| POST | `/api/ingest/web/score` | Local model relevance scoring |
| POST | `/api/ingest/web/crawl` | Start full crawl (returns job ID) |
| GET | `/api/ingest/web/crawl/<job_id>` | Poll crawl status |

## New Config (`fleet.toml`)

```toml
[ingest.web]
enabled = true                   # master switch for web ingestion
min_relevance_score = 6          # reject below this (1-10 scale)
min_word_count = 100             # reject thin pages
min_text_ratio = 0.10            # reject ad-heavy pages
max_crawl_pages = 200            # hard cap per crawl job
max_crawl_credits = 200          # budget cap per crawl job
scorer_model = "qwen3:4b"        # local model for relevance scoring
user_agent = "BigEdBot/1.0"      # for robots.txt compliance
blocked_domains = []             # domain blocklist
```

## Dashboard UI Additions

### Add Source Modal — New "Web URL" Tab

The existing "Add HuggingFace Dataset" modal gets a tab bar:
- **Tab 1:** HuggingFace Dataset (existing)
- **Tab 2:** Web URL (new)

Web URL tab contains:
- URL input field
- Target skill dropdown
- "Probe" button → runs Stage 1+2, shows quality card
- Quality decision card (as shown above)
- Depth/limit controls
- "Scrape Page" / "Crawl Site" / "Cancel" buttons

### Web Source Pills

Web sources appear in the source pill bar with purple color (`#8b5cf6`).
Each pill shows: `🌐 example.com (45 pages)`.
Clicking expands the cached pages for staging (same inline expand pattern as HF sources).

## Credit Tracking

Track Firecrawl credit usage in the `usage` table:

```sql
INSERT INTO usage (skill, model, input_tokens, output_tokens, cost_usd, provider)
VALUES ('web_scrape', 'firecrawl', 0, :word_count, :estimated_cost, 'firecrawl');
```

Estimated cost: ~$0.001 per credit on free tier (for dashboard display purposes).
Show in the Ingest cache bar tooltip: "Firecrawl: 45/500 credits used this month"

## File Structure Additions

```
fleet/
  ingest_manager.py              # + firecrawl_scrape(), firecrawl_crawl(), probe_url(), score_relevance()
  ingest_blueprint.py            # + 5 web endpoints
  knowledge/
    ingest_cache/
      web_example-com/           # cached crawl results
        source_meta.json
        batch_001.jsonl          # {url, title, markdown, score, word_count}
```

## Out of Scope (Phase 3+)

- Scheduled re-crawl (check for content updates)
- Differential crawl (only fetch changed pages)
- JavaScript rendering for SPAs (Firecrawl handles this, but adds cost)
- Screenshot capture alongside content
- PDF extraction from web pages
- RSS/Atom feed monitoring (continuous ingestion)
- Domain-wide content deduplication
- Firecrawl webhook integration (push vs poll)
