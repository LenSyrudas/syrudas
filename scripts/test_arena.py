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

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="m")]

    async def chat(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[ToolSpec]] = None,
        params: Optional[GenParams] = None,
    ) -> AsyncIterator[StreamEvent]:
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
    print("leaderboard: win/loss/tie/both_bad tallies + sort + reset OK")


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

    # vote validation + recording
    r = client.post("/api/arena/vote", headers=local,
                    json={"model_a": "x", "model_b": "y", "winner": "nonsense"})
    assert r.status_code == 400
    r = client.post("/api/arena/vote", headers=local,
                    json={"model_a": "x", "model_b": "", "winner": "a"})
    assert r.status_code == 400, "empty label must 400"
    r = client.post("/api/arena/vote", headers=local,
                    json={"model_a": "x", "model_b": "y", "winner": "a"})
    assert r.status_code == 200

    board = client.get("/api/arena/leaderboard", headers=local).json()
    assert {s["model"] for s in board} == {"x", "y"}
    assert next(s for s in board if s["model"] == "x")["wins"] == 1

    assert client.delete("/api/arena/leaderboard", headers=local).json()["deleted"] == 1
    assert client.get("/api/arena/leaderboard", headers=local).json() == []
    print("routes: /complete stateless+validated, vote validation, leaderboard, reset OK")


async def main():
    await test_leaderboard_math()
    await db.close_db()
    test_routes()
    print("\nALL ARENA TESTS PASSED")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
