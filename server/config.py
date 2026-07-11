"""Paths, port, and simple settings for Syrudas AI."""
import sys
from pathlib import Path

APP_VERSION = "0.5.0"

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

# rough char budget for history sent to the model (~6k tokens); the newest
# messages always win, the system prompt is always kept
MAX_HISTORY_CHARS = 24_000

DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)
PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

# carry over a database created before the rename to Syrudas AI
_legacy_db = DATA_DIR / "argos.db"
if _legacy_db.exists() and not DB_PATH.exists():
    _legacy_db.rename(DB_PATH)
