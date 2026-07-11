"""Smoke test the /api/chat NDJSON stream end-to-end."""
import asyncio
import json
import sys

import httpx

BASE = "http://127.0.0.1:8040/api"


async def main() -> int:
    async with httpx.AsyncClient(timeout=120) as client:
        providers = (await client.get(f"{BASE}/providers")).json()
        assert providers, "no provider instances configured"
        pid = providers[0]["id"]

        req = {
            "provider_id": pid,
            "model": "llama3.1:8b",
            "message": "In one short sentence, what is a lighthouse?",
        }
        events = []
        async with client.stream("POST", f"{BASE}/chat", json=req) as resp:
            assert resp.status_code == 200, resp.status_code
            async for line in resp.aiter_lines():
                if line.strip():
                    events.append(json.loads(line))

        types = [e["type"] for e in events]
        assert types[0] == "meta", types[:3]
        assert "text_delta" in types, "no text streamed"
        assert types[-1] == "done", types[-3:]
        text = "".join(e.get("text", "") for e in events if e["type"] == "text_delta")
        conv_id = events[0]["conversation_id"]
        print("streamed reply:", text.strip()[:120])

        conv = (await client.get(f"{BASE}/conversations/{conv_id}")).json()
        roles = [m["role"] for m in conv["messages"]]
        assert roles == ["user", "assistant"], roles
        print("persisted title:", conv["title"])

        # follow-up on same conversation exercises history replay
        req["conversation_id"] = conv_id
        req["message"] = "Now say it like a pirate."
        async with client.stream("POST", f"{BASE}/chat", json=req) as resp:
            async for line in resp.aiter_lines():
                pass
        conv = (await client.get(f"{BASE}/conversations/{conv_id}")).json()
        assert len(conv["messages"]) == 4, len(conv["messages"])
        print("follow-up reply:", conv["messages"][-1]["content"].strip()[:120])
        print("CHAT API SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
