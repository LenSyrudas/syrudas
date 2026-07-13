"""Tests for the writing editor: document CRUD, the lightweight list, and the
stateless streaming /api/documents/edit endpoint (prompt shape + robustness).
Temp DB, fake provider - no network.

Run: .venv\\Scripts\\python.exe scripts\\test_documents.py
"""
import sys
import tempfile
from pathlib import Path
from typing import AsyncIterator, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-docs-"))
from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server.providers.base import ModelProvider  # noqa: E402
from server.schemas import GenParams, Message, ModelInfo, StreamEvent, ToolSpec  # noqa: E402


class CaptureProvider(ModelProvider):
    """Echoes the prompt back so the test can assert what the edit endpoint
    sent, and can be switched to error/no-done modes."""
    type_id = "fake"
    display_name = "Fake"
    last_messages: list[Message] = []

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
        CaptureProvider.last_messages = messages
        if self.mode == "raise":
            raise RuntimeError("boom")
        if self.mode == "no_done":
            yield StreamEvent(type="text_delta", text="partial edit")
            return
        yield StreamEvent(type="text_delta", text="revised text")
        yield StreamEvent(type="done")


async def test_crud():
    doc = await db.create_document("My Essay", "First draft.")
    assert doc["title"] == "My Essay" and doc["content"] == "First draft."
    got = await db.get_document(doc["id"])
    assert got["content"] == "First draft."

    updated = await db.update_document(doc["id"], content="Second draft.", title="Essay v2")
    assert updated["content"] == "Second draft." and updated["title"] == "Essay v2"
    assert updated["updated_at"] >= doc["created_at"]

    # empty title falls back to Untitled at creation
    d2 = await db.create_document("", "")
    assert d2["title"] == "Untitled"

    # list is lightweight: chars, no content field; newest first
    listing = await db.list_documents()
    assert all("content" not in row and "chars" in row for row in listing)
    assert listing[0]["id"] == d2["id"], "newest document first"
    by_id = {r["id"]: r for r in listing}
    assert by_id[doc["id"]]["chars"] == len("Second draft.")

    assert await db.delete_document(doc["id"]) is True
    assert await db.get_document(doc["id"]) is None
    assert await db.delete_document(doc["id"]) is False
    assert await db.update_document("nonexistent", content="x") is None
    await db.delete_document(d2["id"])
    print("document CRUD: create/get/update/delete, lightweight list, fallbacks OK")


def test_routes():
    from starlette.testclient import TestClient
    import server.routes.documents as docmod
    from server.main import app

    docmod.create_provider = lambda t, c: CaptureProvider("ok")
    client = TestClient(app)
    local = {"Host": "127.0.0.1:8040"}

    # CRUD over HTTP
    doc = client.post("/api/documents", headers=local,
                      json={"title": "T", "content": "hello"}).json()
    assert client.get(f"/api/documents/{doc['id']}", headers=local).json()["content"] == "hello"
    r = client.put(f"/api/documents/{doc['id']}", headers=local, json={"content": "world"})
    assert r.status_code == 200 and r.json()["content"] == "world"
    assert client.get("/api/documents/nope", headers=local).status_code == 404
    assert client.put("/api/documents/nope", headers=local, json={"content": "x"}).status_code == 404
    assert client.delete("/api/documents/nope", headers=local).status_code == 404

    # edit: validation
    seed = client.post("/api/providers", headers=local, json={
        "type_id": "openai_compat", "name": "P", "config": {"base_url": "http://x/v1"}}).json()
    assert client.post("/api/documents/edit", headers=local,
                       json={"provider_id": "missing", "model": "m", "instruction": "x"}
                       ).status_code == 400
    assert client.post("/api/documents/edit", headers=local,
                       json={"provider_id": seed["id"], "model": "m", "instruction": "  "}
                       ).status_code == 400

    # edit with a selection: prompt carries selection + context + instruction
    def do_edit(payload):
        with client.stream("POST", "/api/documents/edit", headers=local, json=payload) as r:
            assert r.status_code == 200
            return "".join(r.iter_text())

    before = len(client.get("/api/documents", headers=local).json())
    text = do_edit({"provider_id": seed["id"], "model": "m", "instruction": "shorten it",
                    "selection": "the quick brown fox", "context": "full doc here"})
    assert "revised text" in text and '"done"' in text
    user_msg = CaptureProvider.last_messages[1].content
    assert "the quick brown fox" in user_msg and "shorten it" in user_msg and "full doc here" in user_msg
    assert "Selected text to revise" in user_msg
    # edit must NOT create a document
    assert len(client.get("/api/documents", headers=local).json()) == before

    # edit with no selection -> "insert at the cursor" framing
    do_edit({"provider_id": seed["id"], "model": "m", "instruction": "continue", "selection": ""})
    assert "no selection" in CaptureProvider.last_messages[1].content.lower()

    # robustness: raising provider -> error event + synthetic done
    docmod.create_provider = lambda t, c: CaptureProvider("raise")
    text = do_edit({"provider_id": seed["id"], "model": "m", "instruction": "x"})
    assert '"error"' in text and '"done"' in text
    # provider that never emits done still gets one
    docmod.create_provider = lambda t, c: CaptureProvider("no_done")
    text = do_edit({"provider_id": seed["id"], "model": "m", "instruction": "x"})
    assert "partial edit" in text and '"done"' in text
    print("document routes: CRUD+404s, edit validation, prompt shape, stateless, robustness OK")


async def main():
    await test_crud()
    await db.close_db()
    test_routes()
    print("\nALL DOCUMENT TESTS PASSED")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
