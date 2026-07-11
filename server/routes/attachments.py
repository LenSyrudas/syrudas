"""File attachment extraction for chat messages.

The frontend uploads a file here, gets extracted text back, and embeds it in
the outgoing message as a <file name="..."> block. Nothing is stored server-side.
"""
from __future__ import annotations

import io

from fastapi import APIRouter, File, HTTPException, UploadFile

router = APIRouter(tags=["attachments"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 120_000


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise HTTPException(415, f"Could not read PDF: {exc}")
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    text = "\n\n".join(pages).strip()
    if not text:
        raise HTTPException(415, "This PDF has no extractable text (it may be scanned images).")
    return text


def _extract_text(data: bytes, name: str) -> str:
    text = data.decode("utf-8", errors="replace")
    replacement_count = text.count("�")
    if "\x00" in text or replacement_count > max(20, len(text) * 0.05):
        raise HTTPException(
            415, f"'{name}' looks like a binary file - only text-based files and PDFs are supported.")
    return text


@router.post("/attachments")
async def upload_attachment(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
    if not data:
        raise HTTPException(400, "Empty file")
    name = file.filename or "file"

    if name.lower().endswith(".pdf"):
        text = _extract_pdf(data)
    else:
        text = _extract_text(data, name)

    truncated = len(text) > MAX_TEXT_CHARS
    return {
        "name": name,
        "content": text[:MAX_TEXT_CHARS],
        "chars": min(len(text), MAX_TEXT_CHARS),
        "truncated": truncated,
    }
