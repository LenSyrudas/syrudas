"""Web tools: fetch a URL as readable text, search via DuckDuckGo HTML (no API key)."""
from __future__ import annotations

import asyncio
import ipaddress
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup

from . import Tool, truncate

FETCH_LIMIT = 9000
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SyrudasAgent/1.0"
TIMEOUT = httpx.Timeout(20.0)


class PrivateAddressError(Exception):
    pass


async def _refuse_private_hosts(request: httpx.Request) -> None:
    """httpx request hook: runs on every hop (redirects included) so a public
    page can't bounce the agent onto localhost, this app's own API, or the LAN."""
    host = request.url.host
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except OSError:
        return  # unresolvable: let the connection fail with its own error
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise PrivateAddressError(
                f"{host} resolves to {ip}, a private/loopback address. "
                "Fetching local or LAN resources is not allowed.")


async def fetch_readable(url: str, limit: int = FETCH_LIMIT) -> str:
    """Fetch a URL and return its readable text. Raises on transport errors or
    a private/loopback target; the SSRF hook fires on every redirect hop.

    Shared by the web_fetch tool and the deep-research pipeline."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA},
        event_hooks={"request": [_refuse_private_hosts]},
    ) as client:
        resp = await client.get(url)
    content_type = resp.headers.get("content-type", "")
    if "html" in content_type:
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ").split())
    else:
        text = resp.text
    return truncate(f"[HTTP {resp.status_code}] {text}", limit)


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
    requires_approval = True  # fetched URLs can carry conversation data off-machine

    async def run(self, args: dict[str, Any]) -> str:
        url = str(args.get("url", "")).strip()
        try:
            return await fetch_readable(url)
        except (ValueError, PrivateAddressError) as exc:
            return f"Error: {exc}"
        except httpx.HTTPError as exc:
            return f"Error fetching {url}: {exc}"


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo HTML wraps every hit in a redirect link
    (//duckduckgo.com/l/?uddg=<real url>); return the real target URL."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return target
    return href


async def search_web(query: str, limit: int = 6) -> list[dict]:
    """DuckDuckGo HTML search -> [{title, url, snippet}]. Raises on transport
    errors. Shared by the web_search tool and the deep-research pipeline."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA}
    ) as client:
        resp = await client.get(url)
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for res in soup.select(".result")[:limit]:
        link = res.select_one(".result__a")
        snippet = res.select_one(".result__snippet")
        if not link:
            continue
        real_url = _unwrap_ddg(link.get("href", ""))
        if not real_url.startswith(("http://", "https://")):
            continue
        results.append({
            "title": link.get_text(" ", strip=True),
            "url": real_url,
            "snippet": snippet.get_text(" ", strip=True) if snippet else "",
        })
    return results


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
        try:
            results = await search_web(query)
        except httpx.HTTPError as exc:
            return f"Error searching: {exc}"
        if not results:
            return "No results found."
        return "\n".join(
            f"- {r['title']}\n  {r['url']}\n  {r['snippet']}" for r in results)
