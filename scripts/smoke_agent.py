"""Smoke test agent mode via /api/chat: expects file tools to be used."""
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
            "message": "List the files in your workspace folder, then read notes.txt and "
                       "summarize it in one sentence.",
            "agent_mode": True,
        }
        tool_calls, tool_results, text = [], [], []
        async with client.stream("POST", f"{BASE}/chat", json=req) as resp:
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev["type"] == "tool_call":
                    tool_calls.append(ev["tool_call"]["name"])
                    print("tool_call:", ev["tool_call"]["name"], ev["tool_call"]["arguments"])
                elif ev["type"] == "tool_result":
                    tool_results.append(ev["name"])
                    print("tool_result:", ev["name"], "->", ev["content"][:100].replace("\n", " | "))
                elif ev["type"] == "text_delta":
                    text.append(ev.get("text", ""))
                elif ev["type"] == "error":
                    print("ERROR:", ev["message"])
                    return 1
        final = "".join(text).strip()
        print("\nfinal text:", final[:300])
        assert tool_calls, "agent made no tool calls"
        assert tool_results, "no tool results returned"
        assert final, "no final text"
        print("\nAGENT SMOKE TEST PASSED  (tools used: %s)" % ", ".join(tool_calls))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
