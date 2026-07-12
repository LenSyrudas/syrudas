"""Web tools: fetch a URL as readable text, search via DuckDuckGo HTML (no API key)."""
from __future__ import annotations

import asyncio
import ipaddress
from typing import Any
from urllib.parse import quote_plus

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
        if not url.startswith(("http://", "https://")):
            return "Error: url must start with http:// or https://"
        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": UA},
                event_hooks={"request": [_refuse_private_hosts]},
            ) as client:
                resp = await client.get(url)
        except PrivateAddressError as exc:
            return f"Error: {exc}"
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
