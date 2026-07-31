"""Paths, port, and simple settings for Syrudas AI."""
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

DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)
PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

# carry over a database created before the rename to Syrudas AI
_legacy_db = DATA_DIR / "argos.db"
if _legacy_db.exists() and not DB_PATH.exists():
    _legacy_db.rename(DB_PATH)
