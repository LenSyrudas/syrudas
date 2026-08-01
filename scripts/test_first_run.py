"""First-run advice, hardware ratings and where the app is allowed to live.

Three defects that only ever showed up on someone else's machine:

- a probe had two outcomes, so "Ollama is running but you have not pulled a
  model" - the commonest first run there is - was reported as "no backend
  answered, start Ollama", advice for a state the user was not in;
- an estimated VRAM figure that looked too small returned "unknown" for every
  catalog entry, so an integrated GPU with plenty of system RAM got a page of
  shrugs, strictly worse than a machine reporting no GPU at all;
- the data folders were created at import, so installing under Program Files
  produced a traceback and no log, because creating the log folder was the
  thing that failed.

Run: .venv\\Scripts\\python.exe scripts\\test_first_run.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-firstrun-"))

from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server import config, onboarding  # noqa: E402
from server.cookbook import rate_fit  # noqa: E402
from server.onboarding import NO_BACKEND, NO_MODELS, READY  # noqa: E402

ENTRY = {"name": "m", "params": "8B", "size_gb": 4.7,
         "min_vram_gb": 6, "min_ram_gb": 10, "tags": [], "blurb": ""}


def fake_probe(outcomes: dict[str, str]):
    async def _probe(name: str, base_url: str):
        return (outcomes.get(name, NO_BACKEND), name, base_url)
    onboarding._probe = _probe


# --- onboarding ---

async def test_a_running_backend_with_no_models_says_so():
    fake_probe({"Ollama local": NO_MODELS})
    result = await onboarding.probe_backends()

    assert result["state"] == NO_MODELS, result
    assert "pull" in result["hint"].lower(), result["hint"]
    assert "start" not in result["hint"].lower(), \
        f"must not tell the user to start what is already running: {result['hint']}"
    print(f"backend up, no models: {result['hint'][:60]}... OK")


async def test_nothing_running_still_says_install_it():
    fake_probe({})
    result = await onboarding.probe_backends()
    assert result["state"] == NO_BACKEND, result
    assert "ollama.com" in result["hint"], result["hint"]
    print("nothing running: told to install a backend OK")


async def test_a_ready_backend_is_reported_ready():
    fake_probe({"LM Studio local": READY})
    result = await onboarding.probe_backends()
    assert result["state"] == READY, result
    assert result["running"], result
    print("backend with models: reported ready OK")


async def test_an_empty_backend_is_not_auto_configured():
    """A provider instance pointing at a model-less backend is a broken entry."""
    fake_probe({"Ollama local": NO_MODELS})
    added = await onboarding.detect_local_providers()
    assert added == [], f"should not configure a backend with no models: {added}"
    print("backend with no models: not added to the picker OK")


# --- hardware ratings ---

def test_estimated_vram_falls_through_to_ram():
    hw = {"gpus": [{"name": "Intel UHD", "vendor": "Intel", "vram_total_mb": 1024,
                    "vram_estimated": True}],
          "ram": {"total_mb": 32 * 1024}}
    fit, reason = rate_fit(hw, ENTRY)

    assert fit != "unknown", "an estimated GPU must not shrug when RAM can answer"
    assert fit in ("cpu", "tight", "good"), (fit, reason)
    assert "could not be measured" in reason, reason
    print(f"estimated VRAM + 32GB RAM: {fit} - {reason[:50]}... OK")


def test_a_machine_with_no_gpu_is_unchanged():
    hw = {"gpus": [], "ram": {"total_mb": 32 * 1024}}
    fit, _ = rate_fit(hw, ENTRY)
    assert fit == "cpu", fit
    print("no GPU at all: still rated on RAM, unchanged OK")


def test_a_real_gpu_measurement_still_wins():
    hw = {"gpus": [{"name": "RTX 4080", "vendor": "NVIDIA", "vram_total_mb": 16 * 1024,
                    "vram_estimated": False}],
          "ram": {"total_mb": 32 * 1024}}
    fit, reason = rate_fit(hw, ENTRY)
    assert fit == "good" and "GPU" in reason, (fit, reason)
    print("measured GPU: still judged on VRAM OK")


def test_no_hardware_info_is_still_unknown():
    assert rate_fit({"gpus": [], "ram": {"total_mb": None}}, ENTRY)[0] == "unknown"
    print("genuinely no information: still unknown OK")


# --- install location ---

def test_ensure_dirs_reports_where_it_failed():
    blocked = TMP / "blocked"
    blocked.write_text("I am a file, not a directory", encoding="utf-8")
    original = config.DATA_DIR
    config.DATA_DIR = blocked / "data"
    try:
        config.ensure_dirs()
        raise AssertionError("creating a folder under a file should have failed")
    except RuntimeError as exc:
        assert "cannot write to" in str(exc), exc
        assert "Program Files" in str(exc), "the message should say what to do instead"
    finally:
        config.DATA_DIR = original
    print("unwritable install location: named, with advice, not a traceback OK")


def test_running_from_temp_is_false_when_not_frozen():
    assert config.running_from_temp() is False
    print("source checkout: the temp-folder guard stays out of the way OK")


async def main() -> None:
    try:
        await test_a_running_backend_with_no_models_says_so()
        await test_nothing_running_still_says_install_it()
        await test_a_ready_backend_is_reported_ready()
        await test_an_empty_backend_is_not_auto_configured()
        test_estimated_vram_falls_through_to_ram()
        test_a_machine_with_no_gpu_is_unchanged()
        test_a_real_gpu_measurement_still_wins()
        test_no_hardware_info_is_still_unknown()
        test_ensure_dirs_reports_where_it_failed()
        test_running_from_temp_is_false_when_not_frozen()
    finally:
        await db.close_db()
    print("\nALL FIRST RUN TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
