"""Hardware info for the model cookbook (CPU / RAM / GPU detection)."""
import asyncio

from fastapi import APIRouter

from .. import hardware

router = APIRouter(tags=["hardware"])


@router.get("/hardware")
async def get_hardware():
    # detection shells out (nvidia-smi, PowerShell), so keep it off the loop
    return await asyncio.to_thread(hardware.detect_hardware)
