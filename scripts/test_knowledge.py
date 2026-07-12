"""Tests for the knowledge (local RAG) layer: chunking, indexing, search,
the agent tool, prompt block, and REST routes.

Uses a deterministic fake embedder (bag-of-words over hash buckets) so cosine
ranking is meaningful without any network or real model. Temp DB + temp
workspace - the user's data is untouched.

Run: .venv\\Scripts\\python.exe scripts\\test_knowledge.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from typing import AsyncIterator, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-knowledge-"))
WORKSPACE = TMP / "workspace"
WORKSPACE.mkdir()

from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server.tools import files as files_mod  # noqa: E402
files_mod.DEFAULT_WORKSPACE = WORKSPACE

from server import knowledge as knowledge_mod  # noqa: E402
from server.agent import stream_agent_chat  # noqa: E402
from server.knowledge import chunk_text, normalize, pack, unpack  # noqa: E402
from server.providers.base import ModelProvider  # noqa: E402
from server.schemas import GenParams, Message, ModelInfo, StreamEvent, ToolCall, ToolSpec  # noqa: E402
from server.tools.knowledge import KnowledgeSearchTool, knowledge_prompt_block  # noqa: E402

DIM = 256


def fake_vec(text: str) -> list[float]:
    """Deterministic bag-of-words embedding (md5 buckets - NOT builtin hash(),
    whose per-process seed randomization made rankings flaky across runs)."""
    import hashlib

    v = [0.0] * DIM
    for tok in text.lower().split():
        v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % DIM] += 1.0
    return v


class FakeEmbedder(ModelProvider):
    type_id = "fake_embed"
    display_name = "Fake embedder"

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-embed")]

    async def chat(self, model, messages, tools=None, params=None):  # pragma: no cover
        yield StreamEvent(type="done")

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        return [fake_vec(t) for t in texts]


async def fake_get_embedder():
    return FakeEmbedder({}), "fake-embed"


# keep the real implementations reachable for the config-contract tests
REAL_GET_EMBEDDER = knowledge_mod.get_embedder
REAL_PROBE = knowledge_mod.probe
knowledge_mod.get_embedder = fake_get_embedder


def text_turn(text):
    return [StreamEvent(type="text_delta", text=text)]


def tool_turn(name, args, call_id="tc1"):
    return [StreamEvent(type="tool_call",
                        tool_call=ToolCall(id=call_id, name=name, arguments=args))]


class FakeChatProvider(ModelProvider):
    type_id = "fake"
    display_name = "Fake"

    def __init__(self, turns=None):
        super().__init__({})
        self.turns = list(turns) if turns else None
        self.calls: list[dict] = []

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-model")]

    async def chat(
        self,
        model: str,
        messages: list[Message],
        tools: Optional[list[ToolSpec]] = None,
        params: Optional[GenParams] = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append({"messages": list(messages), "tools": tools})
        turn = self.turns.pop(0) if self.turns else text_turn("ok")
        for ev in turn:
            yield ev
        yield StreamEvent(type="done")


def test_chunking():
    assert chunk_text("") == []
    assert chunk_text("short text") == ["short text"]

    text = " ".join(f"word{i}" for i in range(600))
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= knowledge_mod.CHUNK_CHARS for c in chunks)
    # every word survives chunking somewhere
    for i in range(600):
        assert any(f"word{i} " in c + " " for c in chunks), f"word{i} lost"
    # consecutive chunks actually overlap: the start of chunk N+1 re-covers
    # the tail of chunk N (regressing CHUNK_OVERLAP to 0 must fail this)
    for a, b in zip(chunks, chunks[1:]):
        first_word = b.split()[0]
        assert first_word in a[-(knowledge_mod.CHUNK_OVERLAP + 60):], \
            f"no overlap between consecutive chunks ({first_word!r})"

    # pathological: one giant token cannot loop forever or produce empties
    chunks = chunk_text("x" * 5000)
    assert chunks and all(c for c in chunks)
    # long whitespace runs must not produce consecutive duplicate chunks
    chunks = chunk_text("A" * 800 + " " * 3000 + "B" * 800)
    assert all(a != b for a, b in zip(chunks, chunks[1:])), chunks

    vec = [3.0, 4.0]
    assert abs(sum(x * x for x in normalize(vec)) - 1.0) < 1e-6
    assert list(unpack(pack([0.5, -1.5]))) == [0.5, -1.5]
    print("chunking + vector helpers OK")


async def test_indexing():
    (WORKSPACE / "bread.md").write_text(
        "Banana bread needs ripe banana, flour, butter and an oven.", "utf-8")
    (WORKSPACE / "physics.txt").write_text(
        "Quantum flux capacitors resonate at tachyon frequencies.", "utf-8")
    (WORKSPACE / "binary.exe.log").write_bytes(b"text\x00null")
    (WORKSPACE / "image.png").write_bytes(b"\x89PNG")  # extension not indexable

    result = await knowledge_mod.index_path(str(WORKSPACE))
    paths = [i["path"] for i in result["indexed"]]
    assert any("bread.md" in p for p in paths), result
    assert any("physics.txt" in p for p in paths), result
    assert any("binary.exe.log" in s for s in result["skipped"]), "binary must be skipped"
    assert not any("image.png" in p for p in paths), "non-text extension must be ignored"

    sources = await db.list_knowledge_sources()
    assert len(sources) == 2
    # reindexing the same folder must replace, not duplicate
    await knowledge_mod.index_path(str(WORKSPACE))
    assert len(await db.list_knowledge_sources()) == 2

    # reindex must replace chunks, never orphan them
    before = await db.count_knowledge_chunks()
    await knowledge_mod.index_path(str(WORKSPACE / "bread.md"))
    assert await db.count_knowledge_chunks() == before, "reindex leaked chunks"

    try:
        await knowledge_mod.index_path("C:\\Windows\\System32")
        raise AssertionError("indexing outside the sandbox must be refused")
    except ValueError as exc:
        assert "not in an allowed folder" in str(exc).lower() or "escapes" in str(exc).lower()
    print("indexing: folder walk, binary skip, reindex-no-dup, sandbox OK")


async def test_junction_escape_blocked():
    """A junction inside the workspace pointing outside must not leak files."""
    outside = TMP / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.md").write_text("the launch codes are 0000", "utf-8")
    try:
        import _winapi
        _winapi.CreateJunction(str(outside), str(WORKSPACE / "jump"))
    except (ImportError, OSError) as exc:
        print(f"junction escape: SKIPPED (cannot create junction: {exc})")
        return
    try:
        result = await knowledge_mod.index_path(str(WORKSPACE))
        assert not any("secret" in i["path"] for i in result["indexed"]), \
            "file behind a junction escaped the sandbox"
        assert any("outside the allowed folders" in s for s in result["skipped"]), \
            f"expected an outside-sandbox skip entry, got {result['skipped']}"
        assert not any("secret" in s["path"] for s in await db.list_knowledge_sources())
    finally:
        (WORKSPACE / "jump").rmdir()
    print("junction escape: files resolving outside the sandbox refused OK")


def _make_pdf(text: str) -> bytes:
    """A minimal but structurally valid single-page PDF with one text run."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
            b"startxref\n" + str(xref).encode() + b"\n%%EOF\n")
    return bytes(out)


async def test_pdf_and_truncation():
    (WORKSPACE / "protocol.pdf").write_bytes(_make_pdf("Stormglass lantern protocol lives here"))
    result = await knowledge_mod.index_path(str(WORKSPACE / "protocol.pdf"))
    assert result["indexed"], result
    hits = await knowledge_mod.search("stormglass lantern protocol")
    assert hits and "protocol.pdf" in hits[0]["path"], hits
    assert "Stormglass" in hits[0]["content"]

    # a byte-truncated file that cuts a multi-byte codepoint must not crash
    old_max = knowledge_mod.MAX_FILE_CHARS
    knowledge_mod.MAX_FILE_CHARS = 25  # truncates to 100 bytes; euro sign is 3 bytes
    try:
        (WORKSPACE / "unicode.txt").write_text("€" * 200, "utf-8")
        result = await knowledge_mod.index_path(str(WORKSPACE / "unicode.txt"))
        assert result["indexed"], result
    finally:
        knowledge_mod.MAX_FILE_CHARS = old_max
        await db.clear_knowledge()
    print("pdf extraction + multi-byte truncation OK")


async def test_cap_guard():
    await db.clear_knowledge()
    caps = WORKSPACE / "caps"
    caps.mkdir()
    big = "\n\n".join("paragraph " + "word " * 80 for _ in range(8))  # several chunks
    (caps / "big1.md").write_text(big + " unique-alpha", "utf-8")
    result = await knowledge_mod.index_path(str(caps / "big1.md"))
    n1 = result["indexed"][0]["chunks"]
    assert n1 >= 2, f"fixture too small ({n1} chunks)"

    old_cap = knowledge_mod.MAX_TOTAL_CHUNKS
    knowledge_mod.MAX_TOTAL_CHUNKS = n1  # index now exactly full
    try:
        # reindexing an unchanged source at the cap is net-zero and must succeed
        result = await knowledge_mod.index_path(str(caps / "big1.md"))
        assert result["indexed"], f"reindex at cap wrongly refused: {result}"
        assert await db.count_knowledge_chunks() == n1

        # new files over the cap: each is reported, none silently dropped
        (caps / "big2.md").write_text(big + " unique-beta", "utf-8")
        (caps / "big3.md").write_text(big + " unique-gamma", "utf-8")
        result = await knowledge_mod.index_path(str(caps))
        full = [s for s in result["skipped"] if "index is full" in s]
        assert len(full) == 2, f"every over-cap file needs a skip entry: {result['skipped']}"
    finally:
        knowledge_mod.MAX_TOTAL_CHUNKS = old_cap
        for name in ("big1.md", "big2.md", "big3.md"):
            (caps / name).unlink(missing_ok=True)
        caps.rmdir()
        await db.clear_knowledge()
    print("cap guard: reindex-at-cap net-zero, per-file skip reporting OK")


async def test_embed_batching_and_mismatch():
    await db.clear_knowledge()
    big = "\n\n".join(f"topic-{i} " + "filler " * 80 for i in range(6))
    (WORKSPACE / "batched.md").write_text(big, "utf-8")

    old_batch = knowledge_mod.EMBED_BATCH
    knowledge_mod.EMBED_BATCH = 2  # force multi-batch aggregation
    try:
        result = await knowledge_mod.index_path(str(WORKSPACE / "batched.md"))
        assert result["indexed"][0]["chunks"] > 2
        # pairing check: a term unique to a LATE chunk must retrieve that chunk
        hits = await knowledge_mod.search("topic-5")
        assert hits and "topic-5" in hits[0]["content"], \
            f"chunk/vector pairing broken across batches: {hits[0]['content'][:80]}"
    finally:
        knowledge_mod.EMBED_BATCH = old_batch

    class MiscountingEmbedder(FakeEmbedder):
        async def embed(self, model, texts):
            return [fake_vec(t) for t in texts[:-1]] if len(texts) > 1 else [fake_vec(texts[0])]

    async def miscounting():
        return MiscountingEmbedder({}), "fake-embed"
    knowledge_mod.get_embedder = miscounting
    try:
        result = await knowledge_mod.index_path(str(WORKSPACE / "batched.md"))
        assert not result["indexed"], "mismatched embed counts must not be stored"
        assert any("embeddings" in s for s in result["skipped"]), result
    finally:
        knowledge_mod.get_embedder = fake_get_embedder
        (WORKSPACE / "batched.md").unlink(missing_ok=True)
        await db.clear_knowledge()
    print("embedding: multi-batch pairing, count-mismatch refusal OK")


async def test_real_embedder_config():
    """Exercise the REAL get_embedder/probe against the settings-JSON contract."""
    from server.providers import registry
    registry.load_provider_types()
    registry._types["fake_embed"] = FakeEmbedder

    try:
        await REAL_GET_EMBEDDER()
        raise AssertionError("unconfigured get_embedder must raise")
    except ValueError as exc:
        assert "No embedding model configured" in str(exc)

    import json
    await db.set_setting(knowledge_mod.EMBEDDING_KEY,
                         json.dumps({"provider_id": "gone", "model": "m"}))
    try:
        await REAL_GET_EMBEDDER()
        raise AssertionError("missing provider instance must raise")
    except ValueError as exc:
        assert "no longer exists" in str(exc)

    inst = await db.create_provider_instance("fake_embed", "Fake E", {})
    await db.set_setting(knowledge_mod.EMBEDDING_KEY,
                         json.dumps({"provider_id": inst["id"], "model": "fake-embed"}))
    provider, model = await REAL_GET_EMBEDDER()
    assert isinstance(provider, FakeEmbedder) and model == "fake-embed"
    assert await REAL_PROBE(inst["id"], "fake-embed") == DIM

    await db.set_setting(knowledge_mod.EMBEDDING_KEY, "")
    await db.delete_provider_instance(inst["id"])
    print("real get_embedder/probe: config contract, missing-config errors OK")


async def test_plain_chat_excludes_knowledge():
    from server.chat import stream_plain_chat

    (WORKSPACE / "bread.md").write_text("Banana bread needs flour.", "utf-8")
    await knowledge_mod.index_path(str(WORKSPACE / "bread.md"))
    conv = await db.create_conversation("inst", "fake-model", False, "Be terse.")
    await db.add_message(conv["id"], "user", "hi")
    provider = FakeChatProvider()
    async for _ in stream_plain_chat(conv, provider):
        pass
    sys_text = provider.calls[0]["messages"][0].content
    assert "knowledge index" not in sys_text and "bread.md" not in sys_text
    # the block is request-local: nothing baked into stored prompts
    convs = await db.list_conversations()
    assert all("knowledge index" not in (c["system_prompt"] or "") for c in convs)
    await db.clear_knowledge()
    print("plain chat: knowledge block excluded, never persisted OK")


async def test_search_ranking():
    hits = await knowledge_mod.search("banana bread flour")
    assert hits and "bread.md" in hits[0]["path"], hits
    hits = await knowledge_mod.search("quantum tachyon frequencies")
    assert hits and "physics.txt" in hits[0]["path"], hits

    # chunks embedded under a different-dimension model are skipped, not crashed
    await db.replace_knowledge_source(
        "stale-model-source", "file", 10, [("old dims", pack([1.0, 2.0]))])
    hits = await knowledge_mod.search("banana bread flour")
    assert all(h["path"] != "stale-model-source" for h in hits)
    await db.clear_knowledge()
    assert await db.count_knowledge_chunks() == 0
    print("search: ranking by topic, dim-mismatch skip, clear OK")


async def test_tool_and_agent_loop():
    tool = KnowledgeSearchTool()

    r = await tool.run({"query": "  "})
    assert r.startswith("Error: empty"), r
    r = await tool.run({"query": "anything"})
    assert "index is empty" in r, r
    assert await knowledge_prompt_block() == ""

    (WORKSPACE / "bread.md").write_text(
        "Banana bread needs ripe banana, flour, butter and an oven.", "utf-8")
    await knowledge_mod.index_path(str(WORKSPACE / "bread.md"))

    r = await tool.run({"query": "banana flour", "k": "not-a-number"})
    assert "bread.md" in r and "Banana bread" in r, r

    block = await knowledge_prompt_block()
    assert "bread.md" in block and "knowledge_search" in block

    conv = await db.create_conversation("inst", "fake-model", True)
    await db.add_message(conv["id"], "user", "what goes in banana bread?")
    provider = FakeChatProvider(turns=[
        tool_turn("knowledge_search", {"query": "banana bread"}),
        text_turn("done"),
    ])
    events = [ev async for ev in stream_agent_chat(conv, provider)]
    assert not any(e["type"] == "approval_required" for e in events), \
        "knowledge_search must be ungated"
    result = next(e for e in events if e["type"] == "tool_result")
    assert "Banana bread" in result["content"], result
    sys_text = provider.calls[0]["messages"][0].content
    assert "local knowledge index" in sys_text and "bread.md" in sys_text

    # unconfigured embedder surfaces as a readable tool error, not a crash
    async def broken():
        raise ValueError("No embedding model configured (Settings > Knowledge)")
    old = knowledge_mod.get_embedder
    knowledge_mod.get_embedder = broken
    try:
        r = await tool.run({"query": "anything"})
        assert r.startswith("Error: No embedding model configured"), r
    finally:
        knowledge_mod.get_embedder = old
    await db.clear_knowledge()
    print("tool + agent loop: ungated dispatch, prompt block, config errors OK")


def test_routes():
    from starlette.testclient import TestClient
    from server.main import app

    async def fake_probe(provider_id, model):
        if provider_id != "p-ok":
            raise ValueError("Provider instance not found")
        return DIM
    knowledge_mod.probe = fake_probe

    client = TestClient(app)
    local = {"Host": "127.0.0.1:8040"}

    r = client.get("/api/knowledge", headers=local)
    assert r.status_code == 200 and r.json()["embedding"] is None

    r = client.put("/api/knowledge/embedding", headers=local,
                   json={"provider_id": "p-bad", "model": "m"})
    assert r.status_code == 400
    r = client.put("/api/knowledge/embedding", headers=local,
                   json={"provider_id": "p-ok", "model": "fake-embed"})
    assert r.status_code == 200 and r.json()["dim"] == DIM

    (WORKSPACE / "route.md").write_text("Routing tests eat banana bread too.", "utf-8")
    r = client.post("/api/knowledge/index", headers=local,
                    json={"path": str(WORKSPACE / "route.md")})
    assert r.status_code == 200 and r.json()["indexed"], r.text
    r = client.post("/api/knowledge/index", headers=local,
                    json={"path": "C:\\Windows\\System32"})
    assert r.status_code == 400

    r = client.post("/api/knowledge/search", headers=local, json={"query": "banana"})
    assert r.status_code == 200 and r.json()["results"], r.text

    # saving the SAME config must not clear; CHANGING the model must clear
    r = client.put("/api/knowledge/embedding", headers=local,
                   json={"provider_id": "p-ok", "model": "fake-embed"})
    assert r.status_code == 200 and r.json()["cleared_sources"] == 0, r.text
    assert client.get("/api/knowledge", headers=local).json()["sources"], \
        "same-config save must keep the index"
    r = client.put("/api/knowledge/embedding", headers=local,
                   json={"provider_id": "p-ok", "model": "different-model"})
    assert r.status_code == 200 and r.json()["cleared_sources"] == 1, r.text
    assert client.get("/api/knowledge", headers=local).json()["sources"] == [], \
        "model change must clear the incomparable index"

    r = client.post("/api/knowledge/index", headers=local,
                    json={"path": str(WORKSPACE / "route.md")})
    assert r.status_code == 200 and r.json()["indexed"], r.text

    source_id = client.get("/api/knowledge", headers=local).json()["sources"][0]["id"]
    r = client.delete(f"/api/knowledge/sources/{source_id}", headers=local)
    assert r.status_code == 200
    r = client.delete(f"/api/knowledge/sources/{source_id}", headers=local)
    assert r.status_code == 404
    r = client.delete("/api/knowledge", headers=local)
    assert r.status_code == 200
    print("routes: config validation, index, search, delete, sandbox 400 OK")


async def main():
    test_chunking()
    await test_indexing()
    await test_search_ranking()
    await test_junction_escape_blocked()
    await test_pdf_and_truncation()
    await test_cap_guard()
    await test_embed_batching_and_mismatch()
    await test_real_embedder_config()
    await test_tool_and_agent_loop()
    await test_plain_chat_excludes_knowledge()
    # TestClient drives the app in its own event loop - hand the connection over
    await db.close_db()
    test_routes()
    print("\nALL KNOWLEDGE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
