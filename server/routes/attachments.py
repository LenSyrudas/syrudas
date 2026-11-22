"""File attachment extraction for chat messages.

The frontend uploads a file here, gets extracted text back, and embeds it in
the outgoing message as a <file name="..."> block. Nothing is stored server-side.

Images and videos are different: they are stored under data/attachments/ and
the message row carries their metadata, so the model can be sent the actual
pixels (sampled frames for video) on every later turn.
"""
from __future__ import annotations

import io

from .. import media
from ..text import decode_text, looks_binary
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

router = APIRouter(tags=["attachments"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 120_000
# images get downscaled anyway, so the cap is about memory while decoding;
# videos are kept whole for playback, so theirs is about disk
MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_VIDEO_BYTES = 500 * 1024 * 1024


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


# --- images and videos ---

@router.post("/attachments/media")
async def upload_media(file: UploadFile = File(...)):
    kind = (file.content_type or "").split("/", 1)[0]
    if kind not in ("image", "video"):
        raise HTTPException(415, "Only image/* and video/* files can be attached as media")
    limit = MAX_IMAGE_BYTES if kind == "image" else MAX_VIDEO_BYTES
    data = await file.read()
    if len(data) > limit:
        raise HTTPException(413, f"File too large (max {limit // (1024 * 1024)} MB)")
    if not data:
        raise HTTPException(400, "Empty file")
    name = file.filename or ("image" if kind == "image" else "video")
    try:
        meta = (media.save_image if kind == "image" else media.save_video)(data, name)
    except ValueError as exc:
        raise HTTPException(415, str(exc))
    return {**meta, "url": f"/api/attachments/file/{meta['id']}"}


@router.get("/attachments/file/{att_id}")
async def serve_attachment(att_id: str, frame: int | None = None):
    """The stored media itself (image, or playable video), or one sampled
    frame (?frame=1..N) - the UI uses frame 1 as a video's poster."""
    meta = media.load_meta(att_id)
    if not meta:
        raise HTTPException(404, "Attachment not found")
    if frame is not None:
        frames = media.frame_paths(meta)
        if not 1 <= frame <= len(frames):
            raise HTTPException(404, "No such frame")
        return FileResponse(frames[frame - 1], media_type="image/jpeg")
    path = media.stored_path(meta)
    if not path.is_file():
        raise HTTPException(404, "The file is gone from the attachments store")
    return FileResponse(path, media_type=meta["mime"])
