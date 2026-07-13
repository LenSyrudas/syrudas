"""Tests for the blind arena: the stateless /api/complete endpoint, vote
recording, and leaderboard aggregation. Temp DB, fake provider - no network.

Run: .venv\\Scripts\\python.exe scripts\\test_arena.py
"""
import sys
import tempfile
from pathlib import Path
from typing import AsyncIterator, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-arena-"))
from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server.providers.base import ModelProvider  # noqa: E402
from server.schemas import GenParams, Message, ModelInfo, StreamEvent, ToolSpec  # noqa: E402


class EchoProvider(ModelProvider):
    type_id = "fake"
    display_name = "Fake"

    def __init__(self, mode="ok"):
        super().__init__({})
        self.mode = mode

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="m")]

    async def chat(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[ToolSpec]] = None,
        params: Optional[GenParams] = None,
    ) -> AsyncIterator[StreamEvent]:
        if self.mode == "raise":
            raise RuntimeError("provider blew up")
        if self.mode == "error_event":
            # yields an error but NO done - exercises the got_done fallback
            yield StreamEvent(type="error", message="upstream 500")
            return
        if self.mode == "no_done":
            yield StreamEvent(type="text_delta", text="partial")
            return  # never emits done - exercises the synthetic-done fallback
        yield StreamEvent(type="text_delta", text=f"{model} says hi")
        yield StreamEvent(type="done")


async def test_leaderboard_math():
    await db.clear_arena()
    # gpt beats claude twice, claude beats gpt once, one tie, one both_bad
    await db.add_arena_result("gpt", "claude", "a")   # gpt win
    await db.add_arena_result("claude", "gpt", "b")   # gpt win (b == gpt)
    await db.add_arena_result("gpt", "claude", "b")   # claude win
    await db.add_arena_result("gpt", "claude", "tie")
    await db.add_arena_result("gpt", "claude", "both_bad")

    board = {s["model"]: s for s in await db.arena_leaderboard()}
    gpt, claude = board["gpt"], board["claude"]
    assert gpt["games"] == 5 and claude["games"] == 5
    assert gpt["wins"] == 2 and gpt["losses"] == 1 and gpt["ties"] == 1, gpt
    assert claude["wins"] == 1 and claude["losses"] == 2 and claude["ties"] == 1, claude
    assert gpt["win_rate"] == round(2 / 5, 3)
    # sorted best-first
    assert [s["model"] for s in await db.arena_leaderboard()] == ["gpt", "claude"]

    assert await db.clear_arena() == 5
    assert await db.arena_leaderboard() == []

    # win_rate carries 3 decimals (1 win / 3 games = 0.333), not just clean values
    for w in ("a", "b", "b"):  # gpt: 1 win, 2 losses
        await db.add_arena_result("gpt", "claude", w)
    assert next(s for s in await db.arena_leaderboard()
                if s["model"] == "gpt")["win_rate"] == 0.333
    await db.clear_arena()
    print("leaderboard: win/loss/tie/both_bad tallies + sort + reset + rounding OK")


async def test_self_match_ignored():
    await db.clear_arena()
    # a legacy self-match row must NOT double-count (games=2, win+loss on one model)
    await db.add_arena_result("solo", "solo", "a")
    board = await db.arena_leaderboard()
    assert board == [], f"self-match must be excluded from the leaderboard, got {board}"
    await db.clear_arena()
    print("self-match: excluded from aggregation, no double-count OK")


def test_routes():
    from starlette.testclient import TestClient
    import server.routes.chat as chatmod
    from server.main import app

    chatmod.create_provider = lambda t, c: EchoProvider({})
    client = TestClient(app)
    local = {"Host": "127.0.0.1:8040"}

    # /api/complete: validation
    r = client.post("/api/complete", headers=local,
                    json={"provider_id": "missing", "model": "m", "message": "hi"})
    assert r.status_code == 400
    inst = client.post("/api/providers", headers=local, json={
        "type_id": "openai_compat", "name": "P", "config": {"base_url": "http://x/v1"}}).json()
    r = client.post("/api/complete", headers=local,
                    json={"provider_id": inst["id"], "model": "m", "message": "   "})
    assert r.status_code == 400, "blank message must 400"

    # /api/complete: streams a stateless completion, creates NO conversation
    before = len(client.get("/api/conversations", headers=local).json())
    with client.stream("POST", "/api/complete", headers=local,
                       json={"provider_id": inst["id"], "model": "gpt-x", "message": "hi"}) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    assert "gpt-x says hi" in text and '"done"' in text
    after = len(client.get("/api/conversations", headers=local).json())
    assert before == after, "/api/complete must not persist a conversation"

    # /api/complete robustness: a raising provider becomes an error event + a
    # synthetic done (never a broken mid-stream 500)
    def stream_complete():
        with client.stream("POST", "/api/complete", headers=local,
                           json={"provider_id": inst["id"], "model": "m", "message": "hi"}) as r:
            assert r.status_code == 200
            return "".join(r.iter_text())

    chatmod.create_provider = lambda t, c: EchoProvider("raise")
    text = stream_complete()
    assert '"error"' in text and '"done"' in text, f"raise path: {text}"
    # a provider that emits an error event but no done still gets a synthetic done
    chatmod.create_provider = lambda t, c: EchoProvider("error_event")
    text = stream_complete()
    assert '"error"' in text and '"done"' in text, f"error_event path: {text}"
    # a provider that never emits done still gets one (got_done fallback)
    chatmod.create_provider = lambda t, c: EchoProvider("no_done")
    text = stream_complete()
    assert "partial" in text and '"done"' in text, f"no_done path: {text}"
    chatmod.create_provider = lambda t, c: EchoProvider("ok")

    # vote validation + recording
    r = client.post("/api/arena/vote", headers=local,
                    json={"model_a": "x", "model_b": "y", "winner": "nonsense"})
    assert r.status_code == 400
    r = client.post("/api/arena/vote", headers=local,
                    json={"model_a": "x", "model_b": "", "winner": "a"})
    assert r.status_code == 400, "empty label must 400"
    r = client.post("/api/arena/vote", headers=local,
                    json={"model_a": "same", "model_b": "same", "winner": "a"})
    assert r.status_code == 400, "self-match vote must 400"
    r = client.post("/api/arena/vote", headers=local,
                    json={"model_a": "x", "model_b": "y", "winner": "a"})
    assert r.status_code == 200

    board = client.get("/api/arena/leaderboard", headers=local).json()
    assert {s["model"] for s in board} == {"x", "y"}
    assert next(s for s in board if s["model"] == "x")["wins"] == 1

    assert client.delete("/api/arena/leaderboard", headers=local).json()["deleted"] == 1
    assert client.get("/api/arena/leaderboard", headers=local).json() == []
    print("routes: /complete stateless+error-paths, vote validation+self-match, leaderboard OK")


async def main():
    await test_leaderboard_math()
    await test_self_match_ignored()
    await db.close_db()
    test_routes()
    print("\nALL ARENA TESTS PASSED")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
