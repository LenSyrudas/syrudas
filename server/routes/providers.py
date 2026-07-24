from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..onboarding import detect_local_providers
from ..providers.registry import create_provider, provider_types

router = APIRouter(tags=["providers"])


class ProviderInstanceIn(BaseModel):
    type_id: str
    name: str
    config: dict


class ProviderInstancePatch(BaseModel):
    name: str
    config: dict


def _mask(inst: dict) -> dict:
    """Never send secrets back to the browser in full."""
    masked = dict(inst)
    config = dict(inst["config"])
    for key, value in config.items():
        if "key" in key.lower() or "secret" in key.lower() or "token" in key.lower():
            if value:
                config[key] = "•••" + str(value)[-4:]
    masked["config"] = config
    return masked


async def _instance_or_404(inst_id: str) -> dict:
    inst = await db.get_provider_instance(inst_id)
    if not inst:
        raise HTTPException(404, "Provider instance not found")
    return inst


def _merge_secrets(new_config: dict, old_config: dict) -> dict:
    """A masked value coming back from the UI means 'keep the stored secret'."""
    merged = dict(new_config)
    for key, value in new_config.items():
        if isinstance(value, str) and value.startswith("•••") and key in old_config:
            merged[key] = old_config[key]
    return merged


@router.get("/provider-types")
async def get_provider_types():
    return provider_types()


@router.get("/providers")
async def list_providers():
    return [_mask(i) for i in await db.list_provider_instances()]


@router.post("/providers/detect")
async def detect_providers():
    """Look for local backends again.

    The recovery path for the common first run: the app is opened before Ollama
    or LM Studio is installed, so startup detection finds nothing. This lets the
    user fix that from the UI instead of typing a base URL by hand.
    """
    added = await detect_local_providers()
    return {
        "added": [_mask(i) for i in added],
        "providers": [_mask(i) for i in await db.list_provider_instances()],
    }


@router.post("/providers")
async def create_instance(body: ProviderInstanceIn):
    if body.type_id not in {t["type_id"] for t in provider_types()}:
        raise HTTPException(400, f"Unknown provider type: {body.type_id}")
    inst = await db.create_provider_instance(body.type_id, body.name, body.config)
    return _mask(inst)


@router.patch("/providers/{inst_id}")
async def update_instance(inst_id: str, body: ProviderInstancePatch):
    inst = await _instance_or_404(inst_id)
    config = _merge_secrets(body.config, inst["config"])
    await db.update_provider_instance(inst_id, body.name, config)
    return _mask(await db.get_provider_instance(inst_id))


@router.delete("/providers/{inst_id}")
async def delete_instance(inst_id: str):
    await db.delete_provider_instance(inst_id)
    return {"ok": True}


@router.post("/providers/{inst_id}/check")
async def check_instance(inst_id: str):
    inst = await _instance_or_404(inst_id)
    provider = create_provider(inst["type_id"], inst["config"])
    try:
        detail = await provider.check()
        return {"ok": True, "detail": detail}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:500]}


@router.get("/providers/{inst_id}/models")
async def instance_models(inst_id: str):
    inst = await _instance_or_404(inst_id)
    provider = create_provider(inst["type_id"], inst["config"])
    try:
        return [m.model_dump() for m in await provider.list_models()]
    except Exception as exc:
        raise HTTPException(502, f"Could not list models: {exc}")
