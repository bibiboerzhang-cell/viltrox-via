"""Shared leaf for the LLM gateway family (provider config + key/cost primitives).

拆 import 期真活环(2026-08-30):llm_gateway.py 底部 re-export llm_gateway_providers,
而 providers 顶部又反向 import gateway 的 PROVIDER_CONFIG/_get_api_key/成本估算——
两个模块在 import 期互相进入对方的半初始化命名空间。本叶子模块收编「无 gateway
依赖」的共享原语,两边都改 import 叶子;llm_gateway 原位保留同名 re-export,
所有既有 import 点与 monkeypatch 路径(app.platform.llm_gateway.<name>)逐字不变。

行为不变量:定义逐字搬迁自 llm_gateway.py,不改任何默认值/异常处理/日志文案。
注意:本模块绝不 import llm_gateway / llm_gateway_providers / models.*(保持叶子)。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.core.config import CLAUDE_MODEL
from app.core.logging import get_logger
from app.platform.llm_gateway_model_alias import resolve_model_alias as _resolve_model_alias


logger = get_logger(__name__)

# 2026-08-22 模型升级刀:provider 默认模型与分/百万价必须与
# platform/models/registry.py ModelSpec 和 platform/models/runtime._EXACT_CATALOG 一致
# (tests/test_model_registry_defaults.py 比对)。env 覆盖优先于代码默认——prod .env
# 不随部署 rsync,切换需 E 车道手改。
PROVIDER_CONFIG: dict[str, dict[str, Any]] = {
    "openai": {
        # gpt-5.6-luna $0.20/$1.20;调用须 reasoning.effort='none'。
        "model": os.getenv("VKPI_OPENAI_MODEL", os.getenv("OPENAI_MODEL", "gpt-5.6-luna")),
        "endpoint": "https://api.openai.com/v1/responses",
        "input_cents_per_million": 20,
        "output_cents_per_million": 120,
        "timeout": int(os.getenv("VKPI_LLM_HTTP_TIMEOUT", "90") or 90),
    },
    "google": {
        # W-L1 止血:prod env 曾配浮动别名 gemini-flash-latest(现已漂到 3.7,禁用),
        # 默认路由在入口就映射成精确名(VKPI_GEMINI_MODEL_EXACT 可覆盖),台账只记精确名。
        # gemini-3.6-flash $0.75/$3.75 促销价至 2026-12-31。
        "model": _resolve_model_alias(
            "google",
            os.getenv("VKPI_GEMINI_MODEL", os.getenv("GEMINI_MODEL", "gemini-3.6-flash")),
        ),
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "input_cents_per_million": 75,
        "output_cents_per_million": 375,
        "timeout": int(os.getenv("VKPI_LLM_HTTP_TIMEOUT", "90") or 90),
    },
    "anthropic": {
        # 模型继承 config.CLAUDE_MODEL(默认 claude-sonnet-5 $2/$10);
        # env 优先级 VKPI_CLAUDE_MODEL > VKPI_WEEKLY_SUMMARY_MODEL > CLAUDE_MODEL 不变。
        "model": os.getenv("VKPI_CLAUDE_MODEL", os.getenv("VKPI_WEEKLY_SUMMARY_MODEL", CLAUDE_MODEL)),
        "endpoint": "https://api.anthropic.com/v1/messages",
        "input_cents_per_million": 200,
        "output_cents_per_million": 1000,
        # 2026-07-18 事故修:官号日报长文生成 >30s 全超时→熔断锁死 LLM 面板。
        "timeout": int(os.getenv("VKPI_LLM_HTTP_TIMEOUT", "90") or 90),
    },
}


def _read_env_key(name: str) -> str:
    """Read a key from process env or local .env without exposing the value."""
    value = os.environ.get(name, "")
    if value:
        return value.strip()
    try:
        for line in Path(".env").read_text(errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.startswith(name + "="):
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception as exc:
        logger.warning("vkpi llm gateway env key lookup failed for %s: %s", name, exc)
    return ""


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


# B1 key broker:gateway provider → key 池 provider 名(google→gemini)。
_POOL_PROVIDER = {"openai": "openai", "google": "gemini", "anthropic": "anthropic"}


def _pooled_api_key(provider: str) -> str:
    """B1 key broker(Fabric 增量3):从 vkpi_api_key_pool 取一个启用 key(解密明文仅运行时内存)。

    只在配置了 VKPI_KEY_POOL_FERNET_KEY 时咨询 key 池(否则池里只有不可逆占位、decrypt 必 None,
    白跑一次 DB 查询)——未配置直接回退 env,零开销、零行为变更。任何异常吞掉回 env。
    绝不记录 key 明文。account_name 仅供后续按 key 计量(本刀只接 key 解析)。
    """
    pool_provider = _POOL_PROVIDER.get(provider)
    if not pool_provider:
        return ""
    if not os.environ.get("VKPI_KEY_POOL_FERNET_KEY"):
        return ""
    try:
        from app.domains.settings import api_key_pool

        picked = api_key_pool.pick_active_key(pool_provider)
        if picked and picked.get("key"):
            return str(picked["key"]).strip()
    except Exception as exc:  # noqa: BLE001 — 池任何异常都回退 env,绝不打断 LLM 调用
        logger.warning("vkpi llm gateway key pool lookup failed for %s (fallback to env): %s", provider, exc)
    return ""


def _get_api_key(provider: str) -> str:
    if _truthy_env("VKPI_LLM_GATEWAY_FORCE_OFFLINE"):
        return ""
    # B1:先咨询 key 池(配了 Fernet 才查;支持多账号轮转/分发),空/未配 → 回退 env(现状=纯 env)。
    pooled = _pooled_api_key(provider)
    if pooled:
        return pooled
    if provider == "openai":
        return _read_env_key("OPENAI_API_KEY")
    if provider == "google":
        return _read_env_key("GEMINI_API_KEY") or _read_env_key("GOOGLE_API_KEY")
    if provider == "anthropic":
        return _read_env_key("ANTHROPIC_API_KEY")
    return ""


def _micro_usd_to_cents(micro_usd: int) -> int:
    # 1 cent = 10_000 micro_usd; 四舍五入而非截断(亚分仍可为 0,但不再因 // 系统性归零)。
    return int(round(int(micro_usd or 0) / 10000.0))
