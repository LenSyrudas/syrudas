"""Hardware detection for the model cookbook.

Reports CPU, RAM, and GPU(s) so the cookbook can judge which models fit.
Design goals, because detection is the fragile part:
  - stdlib only (ctypes/winreg/subprocess); no new dependencies
  - every collector is isolated in try/except and degrades to unknowns, so
    detect_hardware() NEVER raises and always returns a well-formed dict
  - pure parsers are split out so they can be unit-tested without the machine
  - honest about uncertainty: non-NVIDIA VRAM from Windows is flagged estimated

Accurate GPU VRAM comes from `nvidia-smi` (NVIDIA only). AMD/Intel GPUs are
listed by name via Win32_VideoController, whose AdapterRAM is a uint32 field
capped at ~4 GB, so their VRAM is reported as an estimate with a caveat.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import platform
import subprocess

log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NVIDIA_SMI_TIMEOUT = 5
_CIM_TIMEOUT = 10
_WMI_ADAPTER_RAM_CAP = 4_293_918_720  # ~4 GB; AdapterRAM is uint32 and saturates here


# --------------------------------------------------------------------------
# pure parsers (unit-testable, no OS access)
# --------------------------------------------------------------------------

def vendor_from_name(name: str) -> str:
    n = (name or "").lower()
    if any(k in n for k in ("nvidia", "geforce", "quadro", "tesla", "rtx", "gtx")):
        return "NVIDIA"
    if any(k in n for k in ("radeon", "amd", "ryzen")):
        return "AMD"
    if "intel" in n:
        return "Intel"
    if "apple" in n:
        return "Apple"
    return "Unknown"


def parse_nvidia_smi(output: str) -> list[dict]:
    """Parse `nvidia-smi --query-gpu=name,memory.total,memory.free
    --format=csv,noheader,nounits` output (MiB) into GPU dicts."""
    gpus: list[dict] = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0]:
            continue
        try:
            total = int(float(parts[1]))
            free = int(float(parts[2]))
        except ValueError:
            continue
        gpus.append({
            "name": parts[0], "vendor": "NVIDIA",
            "vram_total_mb": total, "vram_free_mb": free, "vram_estimated": False,
        })
    return gpus


def adapter_ram_mb(value: object) -> tuple[int | None, bool]:
    """WMI AdapterRAM (bytes, uint32) -> (megabytes, capped). Capped means the
    real VRAM is >= the reported value (the field saturates near 4 GB)."""
    try:
        b = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None, False
    if b <= 0:
        return None, False
    return b // (1024 * 1024), b >= _WMI_ADAPTER_RAM_CAP


def merge_gpus(nvidia: list[dict], wmi: list[dict]) -> tuple[list[dict], list[str]]:
    """Authoritative NVIDIA list from nvidia-smi, plus any non-NVIDIA adapter
    from WMI (names + estimated VRAM). Avoids double-counting the NVIDIA card."""
    gpus = list(nvidia)
    notes: list[str] = []
    for g in wmi:
        if vendor_from_name(g.get("name", "")) == "NVIDIA" and nvidia:
            continue  # nvidia-smi already covers it with accurate VRAM
        gpus.append(g)
        if g.get("vram_estimated"):
            notes.append(
                f"{g['name']}: VRAM is estimated from Windows and may be wrong "
                "for cards larger than 4 GB.")
    return gpus, notes


# --------------------------------------------------------------------------
# OS collectors (best-effort, isolated)
# --------------------------------------------------------------------------

class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _ram() -> dict:
    """(total, available) physical RAM in MB."""
    try:
        if os.name == "nt":
            stat = _MemoryStatusEx()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            return {"total_mb": stat.ullTotalPhys // (1024 * 1024),
                    "available_mb": stat.ullAvailPhys // (1024 * 1024)}
        # POSIX: total from sysconf; available best-effort from /proc/meminfo
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        avail = None
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        avail = int(line.split()[1]) // 1024
                        break
        except OSError:
            pass
        return {"total_mb": total // (1024 * 1024), "available_mb": avail}
    except Exception:
        log.exception("RAM detection failed")
        return {"total_mb": None, "available_mb": None}


def _run(cmd: list[str], timeout: int) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _nvidia_gpus() -> list[dict]:
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits"], _NVIDIA_SMI_TIMEOUT)
    return parse_nvidia_smi(out) if out else []


_CIM_SCRIPT = (
    "$c = Get-CimInstance Win32_Processor | Select-Object -First 1 "
    "Name,NumberOfCores,NumberOfLogicalProcessors; "
    "$g = Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM; "
    "ConvertTo-Json -Compress -Depth 4 @{cpu=$c; gpus=@($g)}"
)


def _windows_cim() -> dict:
    """One PowerShell call for CPU (name/cores) and GPU names/AdapterRAM."""
    out = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _CIM_SCRIPT],
               _CIM_TIMEOUT)
    if not out:
        return {}
    try:
        return json.loads(out)
    except (ValueError, TypeError):
        return {}


def _cpu(cim: dict) -> dict:
    name = platform.processor() or None
    cores = None
    threads = os.cpu_count()
    cpu = cim.get("cpu") if isinstance(cim, dict) else None
    if isinstance(cpu, dict):
        name = (cpu.get("Name") or name or "").strip() or name
        cores = cpu.get("NumberOfCores") or cores
        threads = cpu.get("NumberOfLogicalProcessors") or threads
    if not name and os.name == "nt":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            name = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    return {"name": name or "Unknown CPU", "cores": cores, "threads": threads}


def _wmi_gpus(cim: dict) -> list[dict]:
    out: list[dict] = []
    for g in (cim.get("gpus") or []) if isinstance(cim, dict) else []:
        if not isinstance(g, dict):
            continue
        name = (g.get("Name") or "").strip()
        if not name:
            continue
        mb, capped = adapter_ram_mb(g.get("AdapterRAM"))
        out.append({
            "name": name, "vendor": vendor_from_name(name),
            "vram_total_mb": mb, "vram_free_mb": None, "vram_estimated": True,
            "vram_capped": capped,
        })
    return out


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------

def detect_hardware() -> dict:
    """Best-effort hardware snapshot. Never raises."""
    notes: list[str] = []
    cim = _windows_cim() if os.name == "nt" else {}

    try:
        nvidia = _nvidia_gpus()
    except Exception:
        log.exception("nvidia-smi detection failed")
        nvidia = []
    try:
        wmi = _wmi_gpus(cim)
    except Exception:
        log.exception("WMI GPU detection failed")
        wmi = []

    gpus, gpu_notes = merge_gpus(nvidia, wmi)
    notes.extend(gpu_notes)
    if not gpus:
        notes.append(
            "No GPU detected. Install NVIDIA drivers (nvidia-smi) for accurate "
            "VRAM; AMD/Intel GPUs are detected only on Windows.")

    return {
        "os": f"{platform.system()} {platform.release()}".strip(),
        "cpu": _cpu(cim),
        "ram": _ram(),
        "gpus": gpus,
        "notes": notes,
    }
