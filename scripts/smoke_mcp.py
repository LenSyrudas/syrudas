"""Smoke test MCP integration: agent should use a filesystem MCP tool."""
import asyncio
import json
import sys

import httpx

BASE = "http://127.0.0.1:8040/api"


async def main() -> int:
    async with httpx.AsyncClient(timeout=300) as client:
        pid = (await client.get(f"{BASE}/providers")).json()[0]["id"]
        req = {
            "provider_id": pid,
            "model": "llama3.1:8b",
            "message": "Call the filesystem_list_directory tool with path "
                       "D:\\projects\\syrudas\\data and tell me what entries it returns.",
            "agent_mode": True,
            "params": {"temperature": 0},
        }
        used, results = [], []
        async with client.stream("POST", f"{BASE}/chat", json=req) as resp:
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev["type"] == "tool_call":
                    used.append(ev["tool_call"]["name"])
                    print("tool_call:", ev["tool_call"]["name"], ev["tool_call"]["arguments"])
                elif ev["type"] == "tool_result":
                    results.append(ev["content"])
                    print("tool_result:", ev["content"][:200].replace("\n", " | "))
                elif ev["type"] == "error":
                    print("ERROR:", ev["message"])
                    return 1
        assert any(n.startswith("filesystem_") for n in used), f"MCP tool not used: {used}"
        assert any("workspace" in r or "syrudas.db" in r for r in results), "unexpected MCP result"
        print("\nMCP SMOKE TEST PASSED (tools used: %s)" % ", ".join(used))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
