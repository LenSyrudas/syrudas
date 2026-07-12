"""Local RAG core: chunk -> embed -> store in SQLite -> cosine search.

Deliberately boring: embeddings are float32 blobs in the same SQLite file as
everything else, normalized at index time so search is a dot product. At the
few-thousand-chunk scale this app targets, brute force in pure Python is
plenty; no vector database, no numpy.

Indexing is sandboxed to the same roots as the file tools (workspace +
user-granted folders), so the knowledge index can never reach further than
the agent already could.
"""
from __future__ import annotations

import json
import logging
from array import array
from math import sqrt
from pathlib import Path

from . import db
from .providers.base import ModelProvider

log = logging.getLogger(__name__)

EMBEDDING_KEY = "knowledge_embedding"  # settings JSON: {"provider_id", "model"}

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
EMBED_BATCH = 32
MAX_FILE_CHARS = 400_000
MAX_FILES_PER_INDEX = 200
MAX_TOTAL_CHUNKS = 20_000
SEARCH_K = 5

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".pdf",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".cs",
    ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".bat", ".sql",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".csv", ".tsv",
    ".html", ".htm", ".css", ".xml", ".log",
}


# --- vectors ---

def pack(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def unpack(blob: bytes) -> array:
    a = array("f")
    a.frombytes(blob)
    return a


def normalize(vec: list[float]) -> list[float]:
    norm = sqrt(sum(x * x for x in vec))
    if norm == 0:
        return list(vec)
    return [x / norm for x in vec]


# --- chunking ---

def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Sliding window that prefers paragraph, then line, then word boundaries."""
    text = text.strip()
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            for sep in ("\n\n", "\n", " "):
                cut = text.rfind(sep, start + size // 2, end)
                if cut != -1:
                    end = cut
                    break
        piece = text[start:end].strip()
        # long whitespace runs can make the overlap window re-cover the same
        # text - never emit consecutive identical chunks
        if piece and (not chunks or piece != chunks[-1]):
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


# --- extraction ---

def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        if not text.strip():
            raise ValueError("no extractable text (scanned images?)")
        return text
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_CHARS * 4:
        raw = raw[: MAX_FILE_CHARS * 4]
    text = raw.decode("utf-8", errors="replace")
    if "\x00" in text:
        raise ValueError("binary file")
    return text


# --- embedding config ---

async def get_embedder() -> tuple[ModelProvider, str]:
    """The configured embedding provider instance + model, or ValueError."""
    raw = await db.get_setting(EMBEDDING_KEY)
    if not raw:
        raise ValueError("No embedding model configured (Settings > Knowledge)")
    cfg = json.loads(raw)
    inst = await db.get_provider_instance(cfg.get("provider_id", ""))
    if not inst:
        raise ValueError("The configured embedding provider no longer exists")
    from .providers.registry import create_provider

    return create_provider(inst["type_id"], inst["config"]), cfg.get("model", "")


async def probe(provider_id: str, model: str) -> int:
    """Validate an embedding config by embedding a probe text; returns the dim."""
    inst = await db.get_provider_instance(provider_id)
    if not inst:
        raise ValueError("Provider instance not found")
    from .providers.registry import create_provider

    provider = create_provider(inst["type_id"], inst["config"])
    [vec] = await provider.embed(model, ["Syrudas embedding probe"])
    if not vec:
        raise ValueError("Provider returned an empty embedding")
    return len(vec)


# --- indexing ---

def _collect_files(target: Path) -> tuple[list[Path], list[str]]:
    if target.is_file():
        return [target], []
    if not target.is_dir():
        raise ValueError(f"Not found: {target}")
    files = sorted(
        (p for p in target.rglob("*")
         if p.is_file() and p.suffix.lower() in TEXT_EXTS),
        key=lambda p: str(p).lower(),
    )
    skipped = []
    if len(files) > MAX_FILES_PER_INDEX:
        skipped.append(f"{len(files) - MAX_FILES_PER_INDEX} files over the "
                       f"{MAX_FILES_PER_INDEX}-file limit")
        files = files[:MAX_FILES_PER_INDEX]
    return files, skipped


def _in_roots(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


async def index_path(path_str: str) -> dict:
    """Index a file or folder (sandboxed to the agent's allowed roots)."""
    from .tools.files import _resolve, allowed_roots

    roots = await allowed_roots()
    target = _resolve(path_str, roots)  # ValueError if outside
    provider, model = await get_embedder()
    files, skipped = _collect_files(target)
    existing = {s["path"]: s for s in await db.list_knowledge_sources()}

    indexed: list[dict] = []
    total_chunks = await db.count_knowledge_chunks()
    for f in files:
        # the folder walk can pass through junctions/symlinks that point
        # OUTSIDE the sandbox - re-check every fully resolved file
        real = f.resolve()
        if not _in_roots(real, roots):
            skipped.append(f"{f.name}: outside the allowed folders")
            continue
        old = existing.get(str(real))
        try:
            text = extract_text(real)[:MAX_FILE_CHARS]
        except Exception as exc:
            skipped.append(f"{f.name}: {exc}")
            continue
        chunks = chunk_text(text)
        if not chunks:
            if old:  # file emptied since it was indexed: drop the stale entry
                await db.delete_knowledge_source(old["id"])
                total_chunks -= old["chunk_count"]
                skipped.append(f"{f.name}: now empty - removed stale index entry")
            else:
                skipped.append(f"{f.name}: empty")
            continue
        # count the replacement's NET growth: reindexing an existing source
        # frees its old chunks first
        net_new = len(chunks) - (old["chunk_count"] if old else 0)
        if total_chunks + net_new > MAX_TOTAL_CHUNKS:
            skipped.append(f"{f.name}: index is full ({MAX_TOTAL_CHUNKS} chunks)")
            continue
        vectors: list[list[float]] = []
        for i in range(0, len(chunks), EMBED_BATCH):
            vectors.extend(await provider.embed(model, chunks[i:i + EMBED_BATCH]))
        if len(vectors) != len(chunks):
            skipped.append(f"{f.name}: provider returned {len(vectors)} embeddings "
                           f"for {len(chunks)} chunks")
            continue
        rows = [(c, pack(normalize(v))) for c, v in zip(chunks, vectors)]
        src = await db.replace_knowledge_source(str(real), "file", len(text), rows)
        total_chunks += net_new
        indexed.append({"path": src["path"], "chunks": src["chunk_count"]})
    return {"indexed": indexed, "skipped": skipped}


# --- search ---

async def search(query: str, k: int = SEARCH_K) -> list[dict]:
    provider, model = await get_embedder()
    [qvec] = await provider.embed(model, [query])
    q = normalize(qvec)
    results: list[tuple[float, dict]] = []
    for row in await db.all_knowledge_chunks():
        vec = unpack(row["embedding"])
        if len(vec) != len(q):
            continue  # indexed under a different embedding model
        score = sum(a * b for a, b in zip(q, vec))
        results.append((score, row))
    results.sort(key=lambda t: t[0], reverse=True)
    return [
        {"path": r["path"], "seq": r["seq"], "score": round(s, 4), "content": r["content"]}
        for s, r in results[:k]
    ]
