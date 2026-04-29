from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_ROOT_HINTS = (
    Path.home() / "Downloads",
    Path.cwd(),
    Path.cwd().parent,
)

_KNOWLEDGE_FILES = (
    "viltrox_knowledge_base ENG.py",
    "viltrox_knowledge_base.py",
)

_GUARD_FILES = (
    "viltrox_only_guard ENG.py",
    "viltrox_only_guard.py",
)

_KNOWLEDGE_MODULE: ModuleType | None = None
_GUARD_MODULE: ModuleType | None = None


def _candidate_paths(filenames: tuple[str, ...]) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()
    for root in _ROOT_HINTS:
        for filename in filenames:
            path = root / filename
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(path)
    return candidates


def _load_external_module(cache: ModuleType | None, filenames: tuple[str, ...], module_name: str) -> ModuleType | None:
    if cache is not None:
        return cache
    for path in _candidate_paths(filenames):
        if not path.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            logger.info("via.external_asset.loaded", extra={"module_name": module_name, "path": str(path)})
            return module
        except Exception:
            logger.warning(
                "via.external_asset.load_failed",
                extra={"module_name": module_name, "path": str(path)},
                exc_info=True,
            )
    return None


def _copy_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return copy.deepcopy(value)


def get_external_viltrox_knowledge() -> dict[str, dict[str, Any]]:
    global _KNOWLEDGE_MODULE
    _KNOWLEDGE_MODULE = _load_external_module(_KNOWLEDGE_MODULE, _KNOWLEDGE_FILES, "external_viltrox_knowledge")
    if _KNOWLEDGE_MODULE is None:
        return {}
    return {
        "mount": _copy_mapping(getattr(_KNOWLEDGE_MODULE, "MOUNT", {})),
        "lens_series": _copy_mapping(getattr(_KNOWLEDGE_MODULE, "LENS_SERIES", {})),
        "adapters": _copy_mapping(getattr(_KNOWLEDGE_MODULE, "ADAPTERS", {})),
        "monitors": _copy_mapping(getattr(_KNOWLEDGE_MODULE, "MONITORS", {})),
        "software": _copy_mapping(getattr(_KNOWLEDGE_MODULE, "SOFTWARE", {})),
    }


def _guard_fn(name: str):
    global _GUARD_MODULE
    _GUARD_MODULE = _load_external_module(_GUARD_MODULE, _GUARD_FILES, "external_viltrox_guard")
    if _GUARD_MODULE is None:
        return None
    fn = getattr(_GUARD_MODULE, name, None)
    return fn if callable(fn) else None


def handle_external_competitor_query(user_text: str) -> dict[str, Any] | None:
    fn = _guard_fn("handle_competitor_query")
    if fn is None:
        return None
    try:
        result = fn(user_text)
    except Exception:
        logger.warning("via.external_guard.handle_failed", exc_info=True)
        return None
    return dict(result or {}) or None


def get_external_system_prompt_injection() -> str:
    fn = _guard_fn("get_system_prompt_injection")
    if fn is None:
        return ""
    try:
        text = fn()
    except Exception:
        logger.warning("via.external_guard.prompt_failed", exc_info=True)
        return ""
    return str(text or "").strip()


def sanitize_external_via_output(text: str) -> str | None:
    fn = _guard_fn("sanitize_via_output")
    if fn is None:
        return None
    try:
        result = fn(text)
    except Exception:
        logger.warning("via.external_guard.sanitize_failed", exc_info=True)
        return None
    cleaned = str(result or "").strip()
    return cleaned or None
