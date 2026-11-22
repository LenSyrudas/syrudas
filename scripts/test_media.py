"""Image/video attachments: storage, payloads, provider wiring, persistence.

Run: .venv\\Scripts\\python.exe scripts\\test_media.py
"""
import asyncio
import importlib.util
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import db, media  # noqa: E402
from server.chat import _msg_chars
from server.providers.openai_compat import _wire_messages
from server.schemas import MediaRef, Message


def load_plugin(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_plugin", ROOT / "plugins" / f"{name}.py")
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    return plugin


def fresh_store() -> Path:
    d = Path(tempfile.mkdtemp(prefix="syrudas-media-"))
    media.ATTACHMENTS_DIR = d
    return d


def png_bytes(size=(4000, 3000), color=(200, 30, 30, 255), fmt="PNG", mode="RGBA") -> bytes:
    from PIL import Image
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def gif_bytes(frames=3) -> bytes:
    from PIL import Image
    imgs = [Image.new("RGB", (64, 64), (i * 60, 0, 0)) for i in range(frames)]
    buf = io.BytesIO()
    imgs[0].save(buf, "GIF", save_all=True, append_images=imgs[1:], duration=50)
    return buf.getvalue()


def video_bytes(frames=60) -> bytes:
    import cv2
    import numpy as np
    path = Path(tempfile.mktemp(suffix=".mp4"))
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
    assert writer.isOpened(), "cv2 could not open an mp4 writer on this machine"
    for i in range(frames):
        writer.write(np.full((240, 320, 3), (i * 4) % 255, dtype=np.uint8))
    writer.release()
    data = path.read_bytes()
    path.unlink(missing_ok=True)
    return data


def ref(meta: dict) -> MediaRef:
    return MediaRef(**meta)


# --- storage ---

def test_image_is_downscaled_and_stored():
    store = fresh_store()
    meta = media.save_image(png_bytes(mode="RGB", color=(200, 30, 30)), "photo.png")
    assert meta["kind"] == "image" and meta["mime"] == "image/jpeg"
    stored = media.stored_path(meta)
    assert stored.is_file(), "the downscaled image should be on disk"
    assert media.load_meta(meta["id"]) == meta, "sidecar should round-trip"

    from PIL import Image
    with Image.open(stored) as img:
        assert max(img.size) <= media.MAX_IMAGE_DIM, \
            f"a 12MP upload must come down to the model's budget, got {img.size}"
    payload = media.image_payload(meta)
    assert payload and payload.startswith("data:image/jpeg;base64,")
    print("image: downscaled to the budget, stored, served as a data URL OK")


def test_alpha_images_keep_png():
    fresh_store()
    meta = media.save_image(png_bytes((100, 100)), "icon.png")
    assert meta["mime"] == "image/png", f"alpha would go black in JPEG, got {meta['mime']}"
    print("alpha image: stays PNG so transparency survives OK")


def test_animated_gif_becomes_a_still():
    fresh_store()
    meta = media.save_image(gif_bytes(), "loop.gif")
    assert meta["kind"] == "image"
    assert media.image_payload(meta), "a gif should still be viewable as one frame"
    print("animated gif: stored as a still frame instead of rejected OK")


def test_garbage_is_rejected():
    fresh_store()
    try:
        media.save_image(b"definitely not an image" * 100, "notes.txt")
        raise AssertionError("non-image bytes must not be stored as an image")
    except ValueError as exc:
        assert "not a readable image" in str(exc)
    assert not list(media.ATTACHMENTS_DIR.iterdir()), "a rejected upload must not litter"
    print("garbage upload: rejected cleanly, nothing left on disk OK")


def test_video_frames_are_sampled():
    fresh_store()
    meta = media.save_video(video_bytes(), "clip.mp4")
    assert meta["kind"] == "video" and meta["frames"] == media.VIDEO_FRAME_COUNT, \
        f"expected {media.VIDEO_FRAME_COUNT} sampled frames, got {meta['frames']}"
    frames = media.frame_paths(meta)
    assert len(frames) == meta["frames"]
    payloads = media.video_payloads(meta)
    assert len(payloads) == meta["frames"]
    assert all(p.startswith("data:image/jpeg;base64,") for p in payloads)
    print(f"video: original kept, {meta['frames']} frames sampled for the model OK")


def test_delete_removes_everything():
    store = fresh_store()
    img = media.save_image(png_bytes((100, 100)), "a.png")
    vid = media.save_video(video_bytes(), "b.mp4")
    assert len(list(store.iterdir())) > 2
    media.delete_media(img["id"])
    media.delete_media(vid["id"])
    assert not list(store.iterdir()), "delete_media should leave no trace"
    print("delete: image, video, frames and sidecars all removed OK")


# --- provider wiring ---

def test_openai_wire_parts():
    fresh_store()
    img = media.save_image(png_bytes((100, 100)), "shot.png")
    vid = media.save_video(video_bytes(), "clip.mp4")
    msg = Message(role="user", content="what do you see?",
                  attachments=[ref(img), ref(vid)])
    [wired] = _wire_messages([msg])
    parts = wired["content"]
    assert isinstance(parts, list), "attachments must turn content into parts"
    images = [p for p in parts if p["type"] == "image_url"]
    texts = [p["text"] for p in parts if p["type"] == "text"]
    assert len(images) == 1 + vid["frames"], f"1 photo + {vid['frames']} frames, got {len(images)}"
    assert any("frames sampled" in t for t in texts), "the video needs its note"
    print(f"openai wire: text + 1 image + {vid['frames']} frames + video note OK")


def test_missing_files_degrade_to_notes():
    fresh_store()
    meta = media.save_image(png_bytes((100, 100)), "gone.png")
    media.stored_path(meta).unlink()  # user cleaned the folder by hand
    [wired] = _wire_messages([Message(role="user", content="", attachments=[ref(meta)])])
    parts = wired["content"]
    assert all(p["type"] == "text" for p in parts), "a missing file must not 500 the turn"
    assert any("missing" in p["text"] for p in parts)
    print("missing file: degrades to a note instead of failing the turn OK")


def test_plugin_wire_formats():
    fresh_store()
    meta = media.save_image(png_bytes((100, 100)), "shot.png")
    msg = Message(role="user", content="look", attachments=[ref(meta)])

    anthropic = load_plugin("anthropic")
    _, messages = anthropic._wire_messages([msg])
    blocks = messages[0]["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["type"] == "base64"
    assert blocks[1] == {"type": "text", "text": "look"}

    gemini = load_plugin("gemini")
    _, contents = gemini._wire_contents([msg])
    parts = contents[0]["parts"]
    assert "inline_data" in parts[0] and parts[0]["inline_data"]["mime_type"] == meta["mime"]
    assert parts[1] == {"text": "look"}
    print("anthropic + gemini plugins: image blocks in their own dialects OK")


def test_vision_heuristics():
    anthropic = load_plugin("anthropic")
    gemini = load_plugin("gemini")

    async def check():
        assert await anthropic.AnthropicProvider({}).supports_vision("claude-sonnet-4-5") is True
        assert await anthropic.AnthropicProvider({}).supports_vision("claude-2.1") is False
        assert await gemini.GeminiProvider({}).supports_vision("gemini-2.5-pro") is True
        assert await gemini.GeminiProvider({}).supports_vision("gemini-pro") is False

    asyncio.run(check())
    print("vision heuristics: claude/gemini eras classified OK")


# --- budget ---

def test_images_count_toward_history_budget():
    plain = Message(role="user", content="hi")
    photo = Message(role="user", content="hi", attachments=[
        MediaRef(id="x" * 32, name="a.png", mime="image/png", kind="image")])
    video = Message(role="user", content="hi", attachments=[
        MediaRef(id="y" * 32, name="b.mp4", mime="video/mp4", kind="video", frames=8)])
    assert _msg_chars(photo) > _msg_chars(plain) + 1000, \
        "an image must cost real budget or photo-heavy chats overflow the window"
    assert _msg_chars(video) > _msg_chars(photo) * 4, "8 frames cost ~8 images"
    print(f"budget: image ~{_msg_chars(photo)} chars, 8-frame video ~{_msg_chars(video)} OK")


# --- persistence ---

async def test_db_round_trip():
    from server import config
    tmp = Path(tempfile.mkdtemp(prefix="syrudas-media-db-"))
    await db.close_db()
    db.DB_PATH = tmp / "test.db"
    try:
        conv = await db.create_conversation("p", "m", False)
        atts = [{"id": "a" * 32, "name": "p.png", "mime": "image/png",
                 "kind": "image", "ext": ".png"}]
        first = await db.add_message(conv["id"], "user", "first")
        await db.add_message(conv["id"], "user", "see this", attachments=atts)
        rows = await db.list_messages(conv["id"])
        assert rows[1]["attachments"] == atts, "attachments must parse back out"
        last = await db.get_last_user_message(conv["id"])
        assert last["attachments"] == atts, "the rewind path needs them parsed too"
        # regenerate rollback restores raw rows: attachments must come back too
        captured = await db.get_messages_after(conv["id"], first["id"])
        assert len(captured) == 1
        await db.delete_messages_from(conv["id"], first["id"], inclusive=False)
        await db.restore_messages(captured)
        again = await db.list_messages(conv["id"])
        assert len(again) == 2 and again[1]["attachments"] == atts, \
            "rollback must not drop attachments"
        assert db.SCHEMA_VERSION == 4, "the attachments column should be migration v4"
        print("db: attachments persist, rewind parses, rollback restores OK")
    finally:
        await db.close_db()
        db.DB_PATH = config.DB_PATH


def main():
    test_image_is_downscaled_and_stored()
    test_alpha_images_keep_png()
    test_animated_gif_becomes_a_still()
    test_garbage_is_rejected()
    test_video_frames_are_sampled()
    test_delete_removes_everything()
    test_openai_wire_parts()
    test_missing_files_degrade_to_notes()
    test_plugin_wire_formats()
    test_vision_heuristics()
    test_images_count_toward_history_budget()
    asyncio.run(test_db_round_trip())
    print("\nALL MEDIA TESTS PASSED")


if __name__ == "__main__":
    main()
