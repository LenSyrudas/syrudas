"""Tests for hardware detection (model cookbook).

The OS collectors are environment-dependent, so the deterministic value is in
the pure parsers and in detect_hardware()'s never-raise contract. Both are
covered here without depending on the machine's actual hardware.

Run: .venv\\Scripts\\python.exe scripts\\test_hardware.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import hardware  # noqa: E402
from server.hardware import (  # noqa: E402
    adapter_ram_mb, merge_gpus, parse_nvidia_smi, vendor_from_name,
)


def test_parse_nvidia_smi():
    out = "NVIDIA GeForce RTX 3090 Ti, 24576, 23000\nNVIDIA A100, 81920, 80000\n"
    gpus = parse_nvidia_smi(out)
    assert len(gpus) == 2
    assert gpus[0] == {"name": "NVIDIA GeForce RTX 3090 Ti", "vendor": "NVIDIA",
                       "vram_total_mb": 24576, "vram_free_mb": 23000, "vram_estimated": False}
    assert gpus[1]["name"] == "NVIDIA A100" and gpus[1]["vram_total_mb"] == 81920
    # robustness: blank output, malformed/short lines, non-numeric memory
    assert parse_nvidia_smi("") == []
    assert parse_nvidia_smi("\n  \n") == []
    assert parse_nvidia_smi("Broken line only") == []
    assert parse_nvidia_smi("GPU, notanumber, 100") == []
    # a value like "12282.0" (float form) still parses
    assert parse_nvidia_smi("RTX 4070, 12282.0, 10000.0")[0]["vram_total_mb"] == 12282
    print("parse_nvidia_smi: multi-gpu, floats, and malformed lines OK")


def test_vendor_from_name():
    cases = {
        "NVIDIA GeForce RTX 3090 Ti": "NVIDIA",
        "AMD Radeon RX 7900 XTX": "AMD",
        "Intel(R) Arc(TM) A770": "Intel",
        "Apple M3 Max": "Apple",
        "Microsoft Basic Display Adapter": "Unknown",
    }
    for name, vendor in cases.items():
        assert vendor_from_name(name) == vendor, name
    assert vendor_from_name("") == "Unknown"
    print("vendor_from_name: NVIDIA/AMD/Intel/Apple/unknown classified OK")


def test_adapter_ram_mb():
    assert adapter_ram_mb(2 * 1024 * 1024 * 1024) == (2048, False)  # 2 GB, not capped
    mb, capped = adapter_ram_mb(4_293_918_720)  # the uint32 saturation value
    assert mb == 4095 and capped is True, (mb, capped)
    assert adapter_ram_mb(0) == (None, False)
    assert adapter_ram_mb(-1) == (None, False)
    assert adapter_ram_mb(None) == (None, False)
    assert adapter_ram_mb("garbage") == (None, False)
    print("adapter_ram_mb: normal, uint32-cap flag, and junk inputs OK")


def test_merge_gpus():
    nvidia = [{"name": "NVIDIA GeForce RTX 4090", "vendor": "NVIDIA",
               "vram_total_mb": 24564, "vram_free_mb": 24000, "vram_estimated": False}]
    wmi = [
        {"name": "NVIDIA GeForce RTX 4090", "vendor": "NVIDIA", "vram_total_mb": 4095,
         "vram_free_mb": None, "vram_estimated": True, "vram_capped": True},
        {"name": "AMD Radeon RX 7900 XTX", "vendor": "AMD", "vram_total_mb": 4095,
         "vram_free_mb": None, "vram_estimated": True, "vram_capped": True},
    ]
    gpus, notes = merge_gpus(nvidia, wmi)
    names = [g["name"] for g in gpus]
    # the NVIDIA card is NOT double-counted (nvidia-smi wins); AMD is added
    assert names.count("NVIDIA GeForce RTX 4090") == 1, names
    assert "AMD Radeon RX 7900 XTX" in names
    assert next(g for g in gpus if g["vendor"] == "NVIDIA")["vram_estimated"] is False
    assert any("estimated" in n for n in notes), "AMD estimate must be noted"
    # with no nvidia-smi data, a WMI NVIDIA entry is kept (better than nothing)
    gpus2, _ = merge_gpus([], wmi)
    assert len(gpus2) == 2
    print("merge_gpus: nvidia-smi authoritative, no double-count, AMD noted OK")


def test_detect_never_raises(monkeypatch):
    # simulate a machine with no nvidia-smi and no working PowerShell/CIM
    monkeypatch.setattr(hardware, "_run", lambda cmd, timeout: None)
    info = hardware.detect_hardware()
    assert set(info) >= {"os", "cpu", "ram", "gpus", "notes"}
    assert info["gpus"] == [] and any("No GPU" in n for n in info["notes"])
    # RAM/CPU come from stdlib (ctypes/os), so they still populate
    assert info["ram"]["total_mb"] is None or info["ram"]["total_mb"] > 0
    assert info["cpu"]["threads"] is None or info["cpu"]["threads"] >= 1
    print("detect_hardware: degrades gracefully with no tools, never raises OK")


def test_route():
    from starlette.testclient import TestClient
    from server import db
    db.DB_PATH = Path(tempfile.mkdtemp(prefix="syrudas-hw-")) / "t.db"
    from server.main import app

    client = TestClient(app)
    r = client.get("/api/hardware", headers={"Host": "127.0.0.1:8040"})
    assert r.status_code == 200, r.text
    info = r.json()
    assert set(info) >= {"os", "cpu", "ram", "gpus", "notes"}
    assert isinstance(info["gpus"], list)
    print("route: GET /api/hardware returns a well-formed snapshot OK")


class _MonkeyPatch:
    """Tiny monkeypatch shim so this suite needs no pytest, like the siblings."""
    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self):
        for obj, name, old in reversed(self._undo):
            setattr(obj, name, old)


def main():
    test_parse_nvidia_smi()
    test_vendor_from_name()
    test_adapter_ram_mb()
    test_merge_gpus()
    mp = _MonkeyPatch()
    try:
        test_detect_never_raises(mp)
    finally:
        mp.undo()
    test_route()
    print("\nALL HARDWARE TESTS PASSED")


if __name__ == "__main__":
    main()
