"""First-run backend detection.

The regression that matters: a first launch with no backend running must not
permanently disable detection. Before the fix the 'done' flag was written before
probing, so a user who installed Ollama afterwards was stuck with an empty model
picker forever.
"""
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="syrudas-onboard-"))
from server import db  # noqa: E402
db.DB_PATH = TMP / "test.db"

from server import onboarding  # noqa: E402


class FakeProvider:
    """Stands in for a local backend that may or may not be listening."""

    def __init__(self, models):
        self._models = models

    async def list_models(self):
        if self._models is None:
            raise ConnectionError("connection refused")
        return self._models


def backends(**by_url):
    """Patch create_provider so each base_url returns the given models."""
    def factory(type_id, config):
        return FakeProvider(by_url.get(config["base_url"]))
    onboarding.create_provider = factory


OLLAMA = "http://localhost:11434/v1"
LMS = "http://localhost:1234/v1"


async def reset():
    await db.close_db()
    if db.DB_PATH.exists():
        db.DB_PATH.unlink()


async def test_detects_when_backend_is_up():
    await reset()
    backends(**{OLLAMA: ["llama3.1:8b"]})
    await onboarding.auto_detect_providers()
    insts = await db.list_provider_instances()
    assert len(insts) == 1 and insts[0]["name"] == "Ollama local", insts
    assert await db.get_setting(onboarding.FLAG_KEY) == "1"
    print("detects a running backend on first launch OK")


async def test_no_backend_leaves_flag_unset():
    """The core regression: nothing found must not burn the flag."""
    await reset()
    backends()  # nothing listening anywhere
    await onboarding.auto_detect_providers()
    assert await db.list_provider_instances() == []
    assert await db.get_setting(onboarding.FLAG_KEY) == "", "flag must stay unset"
    print("a fruitless first run leaves detection armed OK")


async def test_recovers_after_user_installs_backend():
    """Launch with nothing, then install Ollama and relaunch."""
    await reset()
    backends()
    await onboarding.auto_detect_providers()
    assert await db.list_provider_instances() == []

    backends(**{OLLAMA: ["llama3.1:8b"]})       # user installs Ollama
    await onboarding.auto_detect_providers()     # next launch
    insts = await db.list_provider_instances()
    assert len(insts) == 1, insts
    assert await db.get_setting(onboarding.FLAG_KEY) == "1"
    print("recovers on the next launch after a backend appears OK")


async def test_does_not_resurrect_deleted_providers():
    """Once the user has been set up, deleting everything stays deleted."""
    await reset()
    backends(**{OLLAMA: ["llama3.1:8b"]})
    await onboarding.auto_detect_providers()
    for inst in await db.list_provider_instances():
        await db.delete_provider_instance(inst["id"])

    await onboarding.auto_detect_providers()     # relaunch with Ollama still up
    assert await db.list_provider_instances() == [], "must not re-add"
    print("does not resurrect deliberately deleted providers OK")


async def test_manual_provider_stops_probing():
    await reset()
    backends()
    await onboarding.auto_detect_providers()     # nothing found, flag unset
    await db.create_provider_instance("openai_compat", "Mine", {"base_url": "http://x/v1"})

    backends(**{OLLAMA: ["llama3.1:8b"]})
    await onboarding.auto_detect_providers()
    insts = await db.list_provider_instances()
    assert len(insts) == 1 and insts[0]["name"] == "Mine", insts
    assert await db.get_setting(onboarding.FLAG_KEY) == "1"
    print("a hand-added provider stops further probing OK")


async def test_detect_endpoint_is_idempotent():
    """The explicit 'look again' path must not duplicate what's configured."""
    await reset()
    backends(**{OLLAMA: ["llama3.1:8b"], LMS: ["local-model"]})
    first = await onboarding.detect_local_providers()
    assert len(first) == 2, first
    again = await onboarding.detect_local_providers()
    assert again == [], "second sweep should add nothing"
    assert len(await db.list_provider_instances()) == 2
    print("re-detect adds each backend once OK")


async def main():
    # close the connection even when an assertion fires: aiosqlite's worker is a
    # live thread, and leaving it open makes a FAILING run hang instead of
    # exiting - which would stall CI rather than report the failure
    try:
        await test_detects_when_backend_is_up()
        await test_no_backend_leaves_flag_unset()
        await test_recovers_after_user_installs_backend()
        await test_does_not_resurrect_deleted_providers()
        await test_manual_provider_stops_probing()
        await test_detect_endpoint_is_idempotent()
    finally:
        await db.close_db()
    print("\nALL ONBOARDING TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
