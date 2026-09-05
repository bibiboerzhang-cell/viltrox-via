"""Result cache for :func:`app.platform.llm_gateway.invoke` / ``invoke_json``.

审计证据:近 60 天 2240 条 success 调用里只有 1916 个不同 (purpose, prompt_hash),
同 prompt 重打 provider 的钱一直在白花,而「按 purpose 命中」近零——此前根本没有
网关级结果缓存。

设计(W-L1):

- 键 = ``llm_result:{purpose}:{UTC 桶}:{sha256(v2 + 模型 + 契约 + 上下文 + 规范化提示)}``
  默认 TTL 1 天 → 桶 = UTC 日期;按 purpose 可配 TTL(``VKPI_LLM_RESULT_CACHE_TTL_BY_PURPOSE``
  形如 ``purpose=秒,purpose2=秒``;全局默认 ``VKPI_LLM_RESULT_CACHE_TTL_SECONDS``)。
- 存 ``persistent_cache``(003 baseline 表,零新表零迁移;health_sentinel / runtime
  metrics 同款模式)。
- 视频深析类 purpose(``audit_video_analysis`` 等)走既有 analysis_cache,本缓存明确排除,
  不重复;``VKPI_LLM_RESULT_CACHE_EXCLUDE_PURPOSES`` 可再追加。
- 只缓存真实 provider 成功结果(status=success、provider≠rule_v0、正文非空)。
  degraded / 错误 / 空响应绝不入缓存——否则一次降级会被放大成一整天的降级。
- 命中:cost=0、不发 HTTP、不扣预算;台账 metadata 写 ``cache_hit=true`` + ``cache_key``
  供 :func:`llm_gateway_ledger.llm_degrade_rate` 统计命中率。
- 全局开关 ``VKPI_LLM_RESULT_CACHE_ENABLED``(默认开);调用方 metadata 里
  ``llm_result_cache=false`` 可按次绕过。
- 服务器提供的身份/权限/策略上下文只作为不可逆指纹参与分区，不代替授权。
  ``VKPI_LLM_RESULT_CACHE_POLICY_VERSION`` 可在未反映到 prompt/model 的策略变更时
  显式失效旧缓存；未提供的租户、ACL 或数据版本不能由网关推断。

所有库访问 best-effort:缓存层任何异常只降级成「未命中 / 未写入」并打 warning,
绝不让 LLM 调用本身失败。SQL 只用 compat ``?`` 占位符。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.platform.models.runtime import response_model_matches

logger = get_logger(__name__)

CACHE_KEY_PREFIX = "llm_result"
DEFAULT_TTL_SECONDS = 86400
_ENABLED_ENV = "VKPI_LLM_RESULT_CACHE_ENABLED"
_TTL_DEFAULT_ENV = "VKPI_LLM_RESULT_CACHE_TTL_SECONDS"
_TTL_BY_PURPOSE_ENV = "VKPI_LLM_RESULT_CACHE_TTL_BY_PURPOSE"
_EXCLUDE_PURPOSES_ENV = "VKPI_LLM_RESULT_CACHE_EXCLUDE_PURPOSES"
_METADATA_BYPASS_KEY = "llm_result_cache"
_POLICY_VERSION_ENV = "VKPI_LLM_RESULT_CACHE_POLICY_VERSION"
_CONTEXT_VERSION = "llm-result-context-v2"
_STAFF_CONTEXT_KEYS = (
    "id", "staff_id", "user_id", "tenant_id", "org_id", "organization_id",
    "role", "is_owner", "is_admin", "permissions", "permissions_json",
    "token_version", "authorization_version", "acl_version",
)
_METADATA_CONTEXT_KEYS = (
    "tenant_id", "org_id", "organization_id", "brand_scope", "project_id",
    "staff_id", "user_id", "actor_staff_id", "requested_staff_id", "view_as_staff_id",
    "effective_staff_id", "authorization_scope", "acl_version", "permissions_version",
    "policy_version", "schema_version", "prompt_version", "data_revision",
    "execution_class", "provider_account_id", "llm_cache_context",
)
# 视频深析家族:结果由 analysis_cache(target_type/target_id/derive_method)持有,
# 网关层不再叠一层按 prompt 的缓存。
ANALYSIS_CACHE_PURPOSES = frozenset(
    {
        "audit_video_analysis",
        "audit_vision_fallback",
        "audit_deep_score",
        "vkpi_analysis_worker",
    }
)
_MAX_CACHED_TEXT_CHARS = 200_000
_TABLE_READY = False


@dataclass(frozen=True)
class CachePlan:
    key: str
    prompt_hash: str
    bucket: str
    ttl_seconds: int
    purpose: str
    model: str
    context_hash: str = ""


def _context_fingerprint(*, staff: Any, metadata: Any, cost_scope: str, policy: Any) -> str | None:
    """Partition supplied server context; this is not an authorization check.

    No token, password or arbitrary staff/profile fields enter the material.
    Legacy background calls without staff still work, but callers must supply
    tenant/ACL/data revisions for isolation beyond the context they provide.
    Unsupported context disables caching instead of sharing an ambiguous key.
    """
    if staff is not None and (not isinstance(staff, dict) or not any(
        staff.get(key) for key in ("id", "staff_id", "user_id")
    )):
        return None
    if metadata is not None and not isinstance(metadata, dict):
        return None
    context = {
        "version": _CONTEXT_VERSION,
        "operator_policy_version": str(os.environ.get(_POLICY_VERSION_ENV) or "default"),
        "staff": {key: staff[key] for key in _STAFF_CONTEXT_KEYS if key in staff} if staff else None,
        "metadata": {key: metadata[key] for key in _METADATA_CONTEXT_KEYS if key in metadata} if metadata else {},
        "cost_scope": cost_scope,
        "policy": policy,
    }
    try:
        encoded = json.dumps(context, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
    if len(encoded) > 16_384:
        return None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def result_matches_plan(result: dict[str, Any], plan: CachePlan) -> bool:
    """Do not store a fallback model under the requested primary model's key."""
    expected_provider, separator, expected_model = plan.model.partition("/")
    if not separator or not expected_provider or not expected_model:
        return False
    if str(result.get("provider") or "").strip().lower() != expected_provider.lower():
        return False
    if not response_model_matches(expected_model, str(result.get("model") or "")):
        return False
    binding = result.get("resolved_model_binding")
    if binding is not None:
        if not isinstance(binding, dict):
            return False
        if str(binding.get("provider") or "").lower() != expected_provider.lower():
            return False
        if str(binding.get("model_id") or "").lower() != expected_model.lower():
            return False
    return True


def _truthy(value: Any, *, default: bool) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def cache_enabled() -> bool:
    return _truthy(os.environ.get(_ENABLED_ENV), default=True)


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def excluded_purposes() -> frozenset[str]:
    raw = str(os.environ.get(_EXCLUDE_PURPOSES_ENV) or "")
    extra = {
        item.strip().lower()
        for item in raw.replace(";", ",").split(",")
        if item.strip()
    }
    return ANALYSIS_CACHE_PURPOSES | frozenset(extra)


def _ttl_by_purpose() -> dict[str, int]:
    raw = str(os.environ.get(_TTL_BY_PURPOSE_ENV) or "")
    table: dict[str, int] = {}
    for item in raw.replace(";", ",").split(","):
        purpose, separator, seconds = item.partition("=")
        purpose_key = purpose.strip().lower()
        if separator and purpose_key:
            table[purpose_key] = max(0, _parse_int(seconds, 0))
    return table


def cache_ttl_seconds(purpose: str) -> int:
    """Effective TTL for a purpose; ``0`` means the purpose is not cached."""

    purpose_key = str(purpose or "").strip().lower()
    if not purpose_key or not cache_enabled():
        return 0
    if purpose_key in excluded_purposes():
        return 0
    per_purpose = _ttl_by_purpose()
    if purpose_key in per_purpose:
        return per_purpose[purpose_key]
    return max(0, _parse_int(os.environ.get(_TTL_DEFAULT_ENV), DEFAULT_TTL_SECONDS))


def normalise_prompt(prompt: Any) -> str:
    """Canonical prompt text: unified newlines, no trailing blanks per line."""

    text = str(prompt or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def bucket_label(now: datetime, ttl_seconds: int) -> str:
    """UTC time bucket aligned to the TTL (1-day TTL → plain UTC date)."""

    ttl = max(1, int(ttl_seconds))
    stamp = int(now.astimezone(timezone.utc).timestamp())
    start = datetime.fromtimestamp((stamp // ttl) * ttl, tz=timezone.utc)
    if ttl % 86400 == 0:
        return start.strftime("%Y-%m-%d")
    return start.strftime("%Y-%m-%dT%H%M%S")


def build_cache_plan(
    purpose: str,
    prompt: str,
    *,
    model: str,
    contract: str = "text",
    max_output_tokens: int = 0,
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
    staff: dict[str, Any] | None = None,
    cost_scope: str = "",
    policy: dict[str, Any] | None = None,
) -> CachePlan | None:
    """Return the cache plan for this request, or ``None`` when not cacheable."""

    if isinstance(metadata, dict) and _METADATA_BYPASS_KEY in metadata:
        if not _truthy(metadata.get(_METADATA_BYPASS_KEY), default=True):
            return None
    purpose_key = str(purpose or "").strip()
    ttl = cache_ttl_seconds(purpose_key)
    if ttl <= 0:
        return None
    normalised = normalise_prompt(prompt)
    if not normalised:
        return None
    context_hash = _context_fingerprint(staff=staff, metadata=metadata, cost_scope=cost_scope, policy=policy)
    if context_hash is None:
        return None
    model_key = str(model or "").strip()
    material = "\n".join(
        (
            f"context={context_hash}",
            f"model={model_key}",
            f"contract={str(contract or 'text').strip().lower()}",
            f"max_output_tokens={int(max_output_tokens or 0)}",
            normalised,
        )
    )
    prompt_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
    current = now or datetime.now(timezone.utc)
    bucket = bucket_label(current, ttl)
    key = f"{CACHE_KEY_PREFIX}:{purpose_key}:{bucket}:{prompt_hash}"
    return CachePlan(
        key=key,
        prompt_hash=prompt_hash,
        bucket=bucket,
        ttl_seconds=ttl,
        purpose=purpose_key,
        model=model_key,
        context_hash=context_hash,
    )


def is_cacheable_result(result: Any) -> bool:
    """Only genuine provider successes may be cached; degraded results never."""

    if not isinstance(result, dict):
        return False
    if str(result.get("status") or "") != "success":
        return False
    provider = str(result.get("provider") or "").strip().lower()
    if not provider or provider == "rule_v0":
        return False
    if result.get("cache_hit"):
        return False
    text = str(result.get("text") or "")
    if not text.strip() or len(text) > _MAX_CACHED_TEXT_CHARS:
        return False
    return True


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_expiry(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _ensure_table() -> bool:
    """Make sure ``persistent_cache`` exists (sqlite test DBs lack migrations)."""

    global _TABLE_READY
    from app.db.connection import get_conn, is_postgres_runtime, table_exists

    # Postgres 的表由迁移 003 保证,进程内只探一次;sqlite(测试/本地)夹具会
    # 在模块间换库文件,每次都探,绝不让旧库的 ready 标记漏到新库。
    postgres = is_postgres_runtime()
    if _TABLE_READY and postgres:
        return True
    if table_exists("persistent_cache"):
        _TABLE_READY = postgres
        return True
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS persistent_cache (
            cache_key   TEXT PRIMARY KEY,
            value_json  TEXT NOT NULL,
            expires_at  TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
        """
    )
    conn.commit()
    _TABLE_READY = True
    return True


def lookup(plan: CachePlan, *, now: datetime | None = None) -> dict[str, Any] | None:
    """Return the cached entry for ``plan`` when present and unexpired."""

    current = now or datetime.now(timezone.utc)
    try:
        from app.db.connection import get_conn

        _ensure_table()
        row = get_conn().execute(
            "SELECT value_json, expires_at FROM persistent_cache WHERE cache_key=?",
            (plan.key,),
        ).fetchone()
    except Exception:  # noqa: BLE001 - cache miss is the safe degradation
        logger.warning(
            "vkpi.llm_gateway.result_cache_lookup_failed",
            extra={"cache_key": plan.key},
            exc_info=True,
        )
        return None
    if not row:
        return None
    data = dict(row)
    expiry = _parse_expiry(data.get("expires_at"))
    if expiry is None or expiry <= current:
        return None
    try:
        value = json.loads(str(data.get("value_json") or "{}"))
    except (TypeError, ValueError):
        logger.warning(
            "vkpi.llm_gateway.result_cache_corrupt_entry",
            extra={"cache_key": plan.key},
        )
        return None
    if not isinstance(value, dict) or not is_cacheable_result(value):
        return None
    if (
        value.get("context_hash") != plan.context_hash
        or value.get("prompt_hash") != plan.prompt_hash
        or value.get("purpose") != plan.purpose
        or not result_matches_plan(value, plan)
    ):
        return None
    return value


def store(
    plan: CachePlan,
    result: dict[str, Any],
    *,
    call_uid: str = "",
    now: datetime | None = None,
) -> bool:
    """Persist a successful result; returns ``False`` when nothing was written."""

    if not is_cacheable_result(result) or not result_matches_plan(result, plan):
        return False
    current = now or datetime.now(timezone.utc)
    entry = {
        "status": "success",
        "provider": str(result.get("provider") or ""),
        "model": str(result.get("model") or ""),
        "text": str(result.get("text") or ""),
        "json": result.get("json"),
        "input_tokens": int(result.get("input_tokens") or 0),
        "output_tokens": int(result.get("output_tokens") or 0),
        "latency_ms": int(result.get("latency_ms") or 0),
        "resolved_model_binding": result.get("resolved_model_binding"),
        "origin_call_uid": str(call_uid or ""),
        "cached_at": _utc_iso(current),
        "purpose": plan.purpose,
        "prompt_hash": plan.prompt_hash,
        "context_hash": plan.context_hash,
        "ttl_seconds": plan.ttl_seconds,
    }
    try:
        from app.db.connection import get_conn

        _ensure_table()
        conn = get_conn()
        expires = _utc_iso(current + timedelta(seconds=plan.ttl_seconds))
        conn.execute("DELETE FROM persistent_cache WHERE cache_key=?", (plan.key,))
        conn.execute(
            "INSERT INTO persistent_cache (cache_key, value_json, expires_at, created_at) "
            "VALUES (?,?,?,?)",
            (
                plan.key,
                json.dumps(entry, ensure_ascii=False, default=str),
                expires,
                _utc_iso(current),
            ),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - never let cache persistence break a call
        logger.warning(
            "vkpi.llm_gateway.result_cache_store_failed",
            extra={"cache_key": plan.key},
            exc_info=True,
        )
        return False
    return True


def hit_result(entry: dict[str, Any], plan: CachePlan, *, purpose: str) -> dict[str, Any]:
    """Shape a cached entry like a live gateway result with zero cost."""

    return {
        "status": "success",
        "provider": str(entry.get("provider") or ""),
        "model": str(entry.get("model") or ""),
        "text": str(entry.get("text") or ""),
        "json": entry.get("json"),
        "purpose": purpose,
        "prompt_hash": plan.prompt_hash,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_cents": 0,
        "cost_micro_usd": 0,
        "latency_ms": 0,
        "fallback_used": False,
        "cache_hit": True,
        "cache_key": plan.key,
        "cached_at": str(entry.get("cached_at") or ""),
        "cache_origin_call_uid": str(entry.get("origin_call_uid") or ""),
        "cached_usage": {
            "input_tokens": int(entry.get("input_tokens") or 0),
            "output_tokens": int(entry.get("output_tokens") or 0),
        },
        "resolved_model_binding": entry.get("resolved_model_binding"),
        "errors": [],
    }


def hit_ledger_metadata(
    entry: dict[str, Any],
    plan: CachePlan,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ledger metadata for a cache hit (``cache_hit``/``cache_key`` are the contract)."""

    return {
        **(metadata or {}),
        "cache_hit": True,
        "cache_key": plan.key,
        "cache_bucket": plan.bucket,
        "cache_ttl_seconds": plan.ttl_seconds,
        "cache_origin_call_uid": str(entry.get("origin_call_uid") or ""),
        "cached_at": str(entry.get("cached_at") or ""),
        "latency_ms": 0,
    }


def reset_table_state() -> None:
    """Test hook: forget the per-process table readiness flag."""

    global _TABLE_READY
    _TABLE_READY = False


__all__ = [
    "ANALYSIS_CACHE_PURPOSES",
    "CACHE_KEY_PREFIX",
    "CachePlan",
    "DEFAULT_TTL_SECONDS",
    "bucket_label",
    "build_cache_plan",
    "cache_enabled",
    "cache_ttl_seconds",
    "excluded_purposes",
    "hit_ledger_metadata",
    "hit_result",
    "is_cacheable_result",
    "lookup",
    "normalise_prompt",
    "reset_table_state",
    "store",
]
