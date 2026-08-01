"""Turning bytes on disk into text, in one place.

Both the knowledge indexer and the attachment endpoint used
`data.decode("utf-8", errors="replace")`, which never fails and quietly
substitutes U+FFFD for every byte it could not read. For an attachment that is
merely ugly; for the index it is permanent, because the mangled text is what
gets embedded and the vectors carry the damage for as long as the index lives.
"""
from __future__ import annotations

# Tried in order, first clean decode wins. cp1252 covers the Windows-authored
# CSVs and notes this application is most likely to meet; latin-1 accepts any
# byte at all, so it is the terminator rather than a real guess.
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

NUL = bytes([0])


def decode_text(data: bytes) -> tuple[str, str]:
    """Decode bytes to text. Returns (text, the encoding actually used)."""
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    # unreachable while latin-1 is last, but do not depend on that staying true
    return data.decode("utf-8", errors="replace"), "utf-8/replaced"


def looks_binary(data: bytes) -> bool:
    """A NUL byte near the start is the one reliable signal.

    Checked on the raw bytes rather than after decoding: latin-1 decodes a JPEG
    perfectly happily, so by the time it is a str the evidence has gone.
    """
    return NUL in data[:8192]
