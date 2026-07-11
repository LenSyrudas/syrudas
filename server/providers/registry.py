"""Discovers provider plugins: builtin modules in this package plus any .py
file dropped into the top-level plugins/ directory."""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import pkgutil
from typing import Any

from ..config import PLUGINS_DIR
from .base import ModelProvider

log = logging.getLogger(__name__)

_types: dict[str, type[ModelProvider]] = {}
_loaded = False


def _register_from_module(module) -> None:
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, ModelProvider) and obj is not ModelProvider and obj.type_id:
            if obj.type_id in _types and _types[obj.type_id] is not obj:
                log.warning("Duplicate provider type_id %r; keeping first", obj.type_id)
                continue
            _types[obj.type_id] = obj


def load_provider_types() -> dict[str, type[ModelProvider]]:
    global _loaded
    if _loaded:
        return _types

    package = importlib.import_module(__package__)
    for mod_info in pkgutil.iter_modules(package.__path__):
        if mod_info.name in ("base", "registry"):
            continue
        _register_from_module(importlib.import_module(f"{__package__}.{mod_info.name}"))

    if PLUGINS_DIR.is_dir():
        for path in sorted(PLUGINS_DIR.glob("*.py")):
            spec = importlib.util.spec_from_file_location(f"argos_plugin_{path.stem}", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
                _register_from_module(module)
            except Exception:
                log.exception("Failed to load plugin %s", path)

    _loaded = True
    return _types


def provider_types() -> list[dict[str, Any]]:
    """Metadata for the settings UI."""
    return [
        {
            "type_id": cls.type_id,
            "display_name": cls.display_name,
            "config_fields": [f.model_dump() for f in cls.config_fields],
        }
        for cls in load_provider_types().values()
    ]


def create_provider(type_id: str, config: dict[str, Any]) -> ModelProvider:
    types = load_provider_types()
    if type_id not in types:
        raise KeyError(f"Unknown provider type: {type_id}")
    return types[type_id](config)
