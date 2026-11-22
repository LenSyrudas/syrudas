"""Image and video attachments for chat messages.

Layout under data/attachments/:
  <id>.json             metadata sidecar: {id, name, mime, kind, ext, frames}
  <id><ext>             the stored image (downscaled, re-encoded) or video
  <id>.frames/fNN.jpg   frames sampled across a video, for the model and UI

Images are re-encoded on the way in: a phone photo is ~12MP, but a vision
model cannot tell it apart from 1600px, and the base64 payload of the former
is ten times the tokens. Videos keep their original file (the UI can play it)
but the model only ever sees sampled frames.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import shutil
from pathlib import Path
from typing import Optional

from .config import ATTACHMENTS_DIR, MAX_IMAGE_DIM, VIDEO_FRAME_COUNT
from .db import new_id

log = logging.getLogger(__name__)

JPEG_QUALITY = 85


def _meta_path(att_id: str) -> Path:
    return ATTACHMENTS_DIR / f"{att_id}.json"


def _frames_dir(att_id: str) -> Path:
    return ATTACHMENTS_DIR / f"{att_id}.frames"


def load_meta(att_id: str) -> Optional[dict]:
    """The sidecar for an attachment id, or None if it isn't ours."""
    if not att_id or any(c not in "0123456789abcdef" for c in att_id):
        return None
    try:
        return json.loads(_meta_path(att_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_meta(meta: dict) -> None:
    _meta_path(meta["id"]).write_text(json.dumps(meta), encoding="utf-8")


def stored_path(meta: dict) -> Path:
    return ATTACHMENTS_DIR / f"{meta['id']}{meta['ext']}"


def frame_paths(meta: dict) -> list[Path]:
    d = _frames_dir(meta["id"])
    if not d.is_dir():
        return []
    return sorted(d.glob("f*.jpg"))


def delete_media(att_id: str) -> None:
    """Best-effort removal of everything belonging to an attachment id."""
    _meta_path(att_id).unlink(missing_ok=True)
    shutil.rmtree(_frames_dir(att_id), ignore_errors=True)
    for f in ATTACHMENTS_DIR.glob(f"{att_id}.*"):
        if f.suffix != ".json" and f.is_file():
            f.unlink(missing_ok=True)


# --- images ---

def save_image(data: bytes, name: str) -> dict:
    """Validate, downscale and store an uploaded image. Raises ValueError
    with a user-readable reason when the bytes are not a usable image."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        if getattr(img, "is_animated", False):  # animated gif/webp: first frame
            img.seek(0)
    except Exception as exc:
        raise ValueError(f"'{name}' is not a readable image ({exc})") from exc

    has_alpha = img.mode in ("RGBA", "LA", "PA") or (
        img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")
    if max(img.size) > MAX_IMAGE_DIM:
        img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM), Image.LANCZOS)

    att_id = new_id()
    ext, mime = (".png", "image/png") if has_alpha else (".jpg", "image/jpeg")
    out = ATTACHMENTS_DIR / f"{att_id}{ext}"
    try:
        img.save(out, "PNG" if has_alpha else "JPEG", quality=JPEG_QUALITY)
    except OSError as exc:
        out.unlink(missing_ok=True)
        raise ValueError(f"Could not store '{name}': {exc}") from exc

    meta = {"id": att_id, "name": name, "mime": mime, "kind": "image", "ext": ext}
    _write_meta(meta)
    return meta


# --- videos ---

def save_video(data: bytes, name: str) -> dict:
    """Store a video and sample frames across its duration for the model.

    Decoding is OpenCV's job; a file it cannot open (or one with no readable
    frames) is rejected rather than attached as a silent no-op."""
    ext = Path(name).suffix.lower() or ".mp4"
    att_id = new_id()
    path = ATTACHMENTS_DIR / f"{att_id}{ext}"
    path.write_bytes(data)

    try:
        frames = _sample_frames(path, _frames_dir(att_id))
    except ValueError:
        path.unlink(missing_ok=True)
        shutil.rmtree(_frames_dir(att_id), ignore_errors=True)
        raise

    meta = {"id": att_id, "name": name, "mime": _video_mime(ext), "kind": "video",
            "ext": ext, "frames": frames}
    _write_meta(meta)
    return meta


def _video_mime(ext: str) -> str:
    return {
        ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
        ".ogv": "video/ogg", ".mov": "video/quicktime",
    }.get(ext, "video/mp4")


def _sample_frames(path: Path, out_dir: Path) -> int:
    # imported lazily: cv2+numpy cost ~a second of startup, and chat without
    # video attachments should not pay it
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"Could not open '{path.name}' as a video")
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            # some containers report no frame count; treat as one-shot decode
            targets = [0]
        else:
            n = min(VIDEO_FRAME_COUNT, total)
            targets = sorted({int(i * (total - 1) / max(n - 1, 1)) for i in range(n)})

        out_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for idx in targets:
            frame = _read_at(cap, idx)
            if frame is None:
                continue
            h, w = frame.shape[:2]
            if max(h, w) > MAX_IMAGE_DIM:
                scale = MAX_IMAGE_DIM / max(h, w)
                frame = cv2.resize(frame, (round(w * scale), round(h * scale)),
                                   interpolation=cv2.INTER_AREA)
            saved += 1
            cv2.imwrite(str(out_dir / f"f{saved:02d}.jpg"), frame,
                        [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if saved == 0:
            raise ValueError(f"Could not decode any frames from '{path.name}'")
        return saved
    finally:
        cap.release()


def _read_at(cap, idx: int):
    """The frame at an index, seeking back/forward a little if the seek lands
    badly (some codecs only seek to keyframes)."""
    import cv2

    for candidate in (idx, idx + 1, idx - 1):
        if candidate < 0:
            continue
        cap.set(cv2.CAP_PROP_POS_FRAMES, candidate)
        ok, frame = cap.read()
        if ok and frame is not None:
            return frame
    return None


# --- model payloads ---

def _data_url(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def image_b64(meta: dict) -> Optional[tuple[str, str]]:
    """(mime, base64) for a stored image, None when the file is gone."""
    path = stored_path(meta)
    if not path.is_file():
        return None
    return meta["mime"], base64.b64encode(path.read_bytes()).decode()


def video_frames_b64(meta: dict) -> list[str]:
    """base64 strings for a video's sampled frames (all image/jpeg)."""
    return [base64.b64encode(p.read_bytes()).decode() for p in frame_paths(meta)]


def image_payload(meta: dict) -> Optional[str]:
    """data: URL for a stored image, None when the file is gone."""
    found = image_b64(meta)
    return f"data:{found[0]};base64,{found[1]}" if found else None


def video_payloads(meta: dict) -> list[str]:
    """data: URLs for a video's sampled frames (all image/jpeg)."""
    return [f"data:image/jpeg;base64,{b}" for b in video_frames_b64(meta)]
