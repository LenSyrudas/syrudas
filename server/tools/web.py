"""Web tools: fetch a URL as readable text, search via DuckDuckGo HTML (no API key)."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from . import Tool, truncate

FETCH_LIMIT = 9000
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ArgosAgent/1.0"
TIMEOUT = httpx.Timeout(20.0)


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "Fetch a URL and return the page's readable text content."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The http(s) URL to fetch"},
        },
        "required": ["url"],
    }

    async def run(self, args: dict[str, Any]) -> str:
        url = str(args.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return "Error: url must start with http:// or https://"
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA}
            ) as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:
            return f"Error fetching {url}: {exc}"
        content_type = resp.headers.get("content-type", "")
        if "html" in content_type:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = " ".join(soup.get_text(" ").split())
        else:
            text = resp.text
        return truncate(f"[HTTP {resp.status_code}] {text}", FETCH_LIMIT)


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web (DuckDuckGo) and return the top results with URLs and snippets."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query"},
        },
        "required": ["query"],
    }

    async def run(self, args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "Error: empty query"
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA}
            ) as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:
            return f"Error searching: {exc}"
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for res in soup.select(".result")[:6]:
            link = res.select_one(".result__a")
            snippet = res.select_one(".result__snippet")
            if not link:
                continue
            results.append(
                f"- {link.get_text(' ', strip=True)}\n  {link.get('href', '')}\n"
                f"  {snippet.get_text(' ', strip=True) if snippet else ''}"
            )
        return "\n".join(results) if results else "No results found."
