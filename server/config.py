"""Paths, port, and simple settings for Syrudas AI."""
import os
import sys
from pathlib import Path

APP_VERSION = "1.0.0"

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    # PyInstaller onefile: bundled read-only assets unpack to _MEIPASS;
    # persistent state (db, workspace, plugins) lives next to the exe.
    _BUNDLE = Path(getattr(sys, "_MEIPASS"))
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    WEB_DIST = _BUNDLE / "web" / "dist"
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    WEB_DIST = PROJECT_ROOT / "web" / "dist"

DATA_DIR = PROJECT_ROOT / "data"
PLUGINS_DIR = PROJECT_ROOT / "plugins"
DB_PATH = DATA_DIR / "syrudas.db"
DEFAULT_WORKSPACE = DATA_DIR / "workspace"

HOST = "127.0.0.1"
PORT = 8040

MAX_AGENT_STEPS = 15

# Fallback char budget for history, used only when the backend will not say how
# much context the chosen model has. Deliberately conservative: overshooting
# makes the backend drop the OLDEST messages, taking the system prompt and the
# user's request with them, so too little is a worse answer than too much only
# in the cases where we are guessing anyway.
MAX_HISTORY_CHARS = 24_000

# When the context window IS known, the budget is derived from it instead.
# Rough but honest: English averages a little under 4 characters per token, and
# 3.5 errs toward sending less than the window can hold.
CHARS_PER_TOKEN = 3.5
# Held back from the window for the reply itself, and for the tool schemas the
# agent sends on every request (measured at ~3.7k characters for the builtins).
RESPONSE_RESERVE_TOKENS = 1_024
TOOL_SCHEMA_RESERVE_TOKENS = 1_500

def ensure_dirs() -> None:
    """Create the folders the app writes to, reporting where it failed.

    These ran at import time, so installing under Program Files produced a
    PyInstaller traceback and no log - because creating the log directory was
    the thing that failed. Calling it explicitly lets the caller set up logging
    first and say something useful.
    """
    for d in (DATA_DIR, DEFAULT_WORKSPACE, PLUGINS_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Syrudas cannot write to {d}.\n\n"
                "It keeps its data next to the executable, so it needs a folder "
                "you can write to - your Documents or Desktop, not Program Files.\n\n"
                f"({exc})") from exc


ALLOW_TEMP_ENV = "SYRUDAS_ALLOW_TEMP"


def running_from_temp() -> bool:
    """True when a frozen build is running out of a temp/extraction folder.

    Double-clicking the exe straight from Explorer's zip viewer gives a working
    session whose data folder Windows deletes later, taking the conversations
    with it.

    SYRUDAS_ALLOW_TEMP opts out, and exists because this guard broke the one
    check that matters: verify_release.ps1 unpacks the finished archive into
    %TEMP% and launches it, which is precisely the shape being refused. Without
    an escape hatch, adding this protection would have silently disabled the
    test that catches a broken build - trading a rare data-loss case for the
    thing that stopped 0.7.3 shipping broken.
    """
    if not FROZEN or os.environ.get(ALLOW_TEMP_ENV):
        return False
    import tempfile
    try:
        # BOTH sides resolved. Comparing an unresolved root against a resolved
        # temp path meant the guard silently never fired wherever the two are
        # spelled differently - Windows 8.3 short names being the usual cause,
        # e.g. a TEMP under RUNNER~1 or a profile with a space in it.
        return PROJECT_ROOT.resolve().is_relative_to(
            Path(tempfile.gettempdir()).resolve())
    except (OSError, ValueError):
        return False


ensure_dirs()

# carry over a database created before the rename to Syrudas AI
_legacy_db = DATA_DIR / "argos.db"
if _legacy_db.exists() and not DB_PATH.exists():
    _legacy_db.rename(DB_PATH)
