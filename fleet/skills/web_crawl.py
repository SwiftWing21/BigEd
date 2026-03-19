"""
Web crawler skill — visits a URL and extracts business contact info.
Uses stdlib html.parser + regex. No extra deps beyond httpx.
Saves to knowledge/leads/crawled/<domain>.json
"""
import html as _html
import json
import re
import urllib.parse
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; fleet-crawler/1.0)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
REQUIRES_NETWORK = True

_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
_PHONE_RE = re.compile(r'(?:\+1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}')
_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.IGNORECASE | re.DOTALL)
_META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_JUNK_EMAILS = {"example.", "domain.", "email@", "user@", "your@", "test@"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "head"):
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "head"):
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data):
        if not self._skip_depth:
            t = data.strip()
            if t:
                self.chunks.append(t)


def _extract(html_text: str, url: str) -> dict:
    title_m = _TITLE_RE.search(html_text)
    desc_m = _META_DESC_RE.search(html_text)

    parser = _TextExtractor()
    parser.feed(html_text)
    visible = " ".join(parser.chunks)

    emails = list(dict.fromkeys(_EMAIL_RE.findall(visible)))
    emails = [e for e in emails if not any(j in e.lower() for j in _JUNK_EMAILS)][:5]

    phones = list(dict.fromkeys(_PHONE_RE.findall(visible)))[:5]
    domain = urllib.parse.urlparse(url).netloc.lstrip("www.")

    return {
        "url": url,
        "domain": domain,
        "title": _html.unescape(title_m.group(1).strip()) if title_m else "",
        "description": _html.unescape(desc_m.group(1).strip()) if desc_m else "",
        "emails": emails,
        "phones": phones,
        "text_preview": visible[:600],
    }


def run(payload, config):
    url = payload.get("url", "")
    if not url:
        return {"error": "No URL provided"}

    try:
        resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=15)
        if resp.status_code >= 400:
            return {"error": f"HTTP {resp.status_code}", "url": url}
        result = _extract(resp.text, str(resp.url))
    except Exception as e:
        return {"error": str(e), "url": url}

    if payload.get("save", True):
        out_dir = KNOWLEDGE_DIR / "leads" / "crawled"
        out_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[^a-zA-Z0-9_.\-]', '_', result["domain"])[:60]
        out = out_dir / f"{datetime.now().strftime('%Y%m%d')}_{safe}.json"
        out.write_text(json.dumps(result, indent=2))
        result["saved_to"] = str(out)

    return result
