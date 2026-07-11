"""Per-conversation stream coordination.

Single process, single event loop - a couple of dicts are enough to:
- prevent two generation streams from running on one conversation at once
  (two windows, or a double-triggered regenerate),
- block rewind/regenerate/delete while a stream is still writing, and
- stop zombie streams (client gone, loop not yet cancelled) from persisting
  messages into a history that was rewound or deleted under them.
"""
from __future__ import annotations

import asyncio

_active: dict[str, int] = {}
_generation: dict[str, int] = {}


def generation(conv_id: str) -> int:
    return _generation.get(conv_id, 0)


def bump_generation(conv_id: str) -> None:
    """Call whenever history is rewritten out-of-band (rewind, delete)."""
    _generation[conv_id] = _generation.get(conv_id, 0) + 1


def is_active(conv_id: str) -> bool:
    return _active.get(conv_id, 0) > 0


async def wait_idle(conv_id: str, timeout: float = 2.0) -> bool:
    """Give a just-aborted stream a moment to observe its cancellation."""
    deadline = asyncio.get_running_loop().time() + timeout
    while is_active(conv_id):
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.1)
    return True


class StreamGuard:
    """Marks a conversation's stream as active for the guard's lifetime.

    Synchronous context manager on purpose: __exit__ must be safe to run
    during async-generator finalization (GeneratorExit), where awaiting is
    not allowed.
    """

    def __init__(self, conv_id: str):
        self.conv_id = conv_id
        self.gen = generation(conv_id)

    def __enter__(self) -> "StreamGuard":
        _active[self.conv_id] = _active.get(self.conv_id, 0) + 1
        return self

    def __exit__(self, *exc) -> None:
        remaining = _active.get(self.conv_id, 1) - 1
        if remaining <= 0:
            _active.pop(self.conv_id, None)
        else:
            _active[self.conv_id] = remaining

    @property
    def stale(self) -> bool:
        """True when history was rewritten after this stream started."""
        return generation(self.conv_id) != self.gen
