"""Paths, port, and simple settings for Argos."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PLUGINS_DIR = PROJECT_ROOT / "plugins"
WEB_DIST = PROJECT_ROOT / "web" / "dist"
DB_PATH = DATA_DIR / "argos.db"
DEFAULT_WORKSPACE = DATA_DIR / "workspace"

HOST = "127.0.0.1"
PORT = 8040

MAX_AGENT_STEPS = 15

DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)
