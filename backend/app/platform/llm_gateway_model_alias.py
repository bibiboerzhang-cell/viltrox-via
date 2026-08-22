"""Floating ``*-latest`` model alias resolution for the LLM gateway.

审计证据(2026-08,隔离库近 60 天):prod env ``GEMINI_MODEL`` / ``VKPI_GEMINI_MODEL``
配的是别名 ``gemini-flash-latest``,worker 侧实际跑的是 ``gemini-2.5-flash``——台账里
184 行记的是别名而非精确名,就绪闸/定价/模型对账全对不上。

本模块把浮动别名在网关入口一次性映射成精确名:

- google ``*flash*-latest`` → ``VKPI_GEMINI_MODEL_EXACT``(默认 ``gemini-3.6-flash``;
  2026-08-22 模型升级刀:``gemini-flash-latest`` 本身已漂到 3.7(无 thinking minimal
  档,禁用),所以别名绝不能原样放行,必须映射成精确 id)
- google ``*pro*-latest``   → ``VKPI_GEMINI_PRO_MODEL_EXACT``(默认 ``gemini-2.5-pro``,Pro 档本刀不动)
- 其它 provider 的 ``*-latest`` → ``VKPI_<PROVIDER>_MODEL_EXACT``,未配则原样保留
  (没有可靠默认值,宁可让就绪闸按原名拦下,也不凭空猜一个精确名)。

映射发生时每个 (provider, alias, exact) 组合只打一次 warning 日志「别名已映射」,
避免高频调用把日志刷爆。映射是纯函数 + 进程内去重集合,不触库。
"""
from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_PROVIDER_ALIASES = {"gemini": "google", "claude": "anthropic"}
_GOOGLE_FLASH_EXACT_ENV = "VKPI_GEMINI_MODEL_EXACT"
_GOOGLE_PRO_EXACT_ENV = "VKPI_GEMINI_PRO_MODEL_EXACT"
_GOOGLE_FLASH_EXACT_DEFAULT = "gemini-3.6-flash"
_GOOGLE_PRO_EXACT_DEFAULT = "gemini-2.5-pro"
_LATEST_SUFFIX = "-latest"

# (provider, alias, exact) 组合已告警集合——日志一次性。
_WARNED: set[tuple[str, str, str]] = set()


def normalise_provider(provider: Any) -> str:
    key = str(provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(key, key)


def is_floating_alias(model_id: Any) -> bool:
    """``True`` when the model id is a floating ``*-latest`` alias."""

    return str(model_id or "").strip().lower().endswith(_LATEST_SUFFIX)


def exact_model_for_alias(provider: Any, model_id: Any) -> str:
    """Return the exact model id an alias maps to (no logging, no side effects).

    Non-alias ids are returned unchanged (stripped). Aliases without a known
    exact mapping are also returned unchanged so downstream gates keep their
    fail-closed behaviour instead of silently substituting a guessed model.
    """

    model = str(model_id or "").strip()
    if not is_floating_alias(model):
        return model
    provider_key = normalise_provider(provider)
    lowered = model.lower()
    if provider_key == "google":
        if "pro" in lowered:
            configured = os.environ.get(_GOOGLE_PRO_EXACT_ENV, "").strip()
            return configured or _GOOGLE_PRO_EXACT_DEFAULT
        configured = os.environ.get(_GOOGLE_FLASH_EXACT_ENV, "").strip()
        return configured or _GOOGLE_FLASH_EXACT_DEFAULT
    if provider_key:
        configured = os.environ.get(
            f"VKPI_{provider_key.upper()}_MODEL_EXACT", ""
        ).strip()
        if configured:
            return configured
    return model


def resolve_model_alias(provider: Any, model_id: Any) -> str:
    """Map a floating alias to its exact id, warning once per distinct mapping."""

    model = str(model_id or "").strip()
    exact = exact_model_for_alias(provider, model)
    if exact != model:
        marker = (normalise_provider(provider), model, exact)
        if marker not in _WARNED:
            _WARNED.add(marker)
            logger.warning(
                "vkpi.llm_gateway.model_alias_mapped 别名已映射 %s/%s -> %s",
                marker[0],
                model,
                exact,
                extra={"provider": marker[0], "alias": model, "exact_model": exact},
            )
    return exact


def alias_mappings_seen() -> list[dict[str, str]]:
    """Diagnostics: every alias→exact mapping this process has applied."""

    return [
        {"provider": provider, "alias": alias, "exact_model": exact}
        for provider, alias, exact in sorted(_WARNED)
    ]


def reset_alias_warnings() -> None:
    """Test hook: forget which mappings were already logged."""

    _WARNED.clear()


__all__ = [
    "alias_mappings_seen",
    "exact_model_for_alias",
    "is_floating_alias",
    "normalise_provider",
    "reset_alias_warnings",
    "resolve_model_alias",
]
