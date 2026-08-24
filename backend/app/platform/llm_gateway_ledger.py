"""Usage-ledger persistence for :mod:`app.platform.llm_gateway`.

Dependencies are resolved through the canonical module at call time so the
existing public import and monkeypatch surface remains compatible.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.platform.llm_gateway_model_alias import resolve_model_alias


def _gateway_module() -> Any:
    from app.platform import llm_gateway

    return llm_gateway


def _summarize_exception(exc: BaseException) -> str:
    """异常类名 + 首行(脱敏)。复用 budget_guard 的脱敏口径(lazy import 防顶层倒挂)。"""
    try:
        from app.domains.costs.budget_guard_errors import summarize_exception

        return summarize_exception(exc)
    except Exception:  # noqa: BLE001 - 可见性辅助绝不反过来炸记账
        return type(exc).__name__


def _actor_staff_id(gateway: Any, conn: Any, staff: Any, triggered_by: Any) -> int | None:
    """台账 staff 外键的唯一取值口径:staff dict → 否则 triggered_by(int 或 dict)→ 再做 PK 存在校验。

    身份类型化(C2):``triggered_by_user_id`` 是 user id,绝不直接当 staff 外键;调用方
    (worker 子进程的 llm_context)已按 payload.staff_id 传 triggered_by,这里统一兜底。
    """
    sid = gateway.resolve_staff_id(staff) if isinstance(staff, dict) else None
    if not sid and triggered_by is not None:
        if isinstance(triggered_by, dict):
            sid = gateway.resolve_staff_id(triggered_by)
        else:
            try:
                sid = int(triggered_by)
            except (TypeError, ValueError):
                sid = None
    return gateway._existing_staff_id(conn, sid or None)


def _attach_ledger_error(conn: Any, gateway: Any, call_uid: str, metadata: Any, summary: str) -> None:
    """把台账失败摘要补进已落的 vkpi_llm_calls.metadata_json(best effort,不抛)。"""
    try:
        merged = {**(metadata if isinstance(metadata, dict) else {}), "cost_ledger_error": summary}
        conn.execute(
            "UPDATE vkpi_llm_calls SET metadata_json=? WHERE call_uid=?",
            (gateway._json(merged), call_uid),
        )
        conn.commit()
    except Exception:  # noqa: BLE001
        gateway.logger.warning("vkpi.llm_gateway.ledger_error_note_failed", exc_info=True)


def record_call(
    *,
    provider: str,
    model: str = "",
    purpose: str = "",
    prompt: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_cents: int = 0,
    cost_micro_usd: int | None = None,
    status: str = "not_configured",
    fallback_used: bool = True,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    metadata: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
    update_budget_scopes: bool = True,
    force_cost_ledger: bool = False,
) -> dict[str, Any]:
    gateway = _gateway_module()
    if force_cost_ledger and not str(cost_tag or "").strip():
        raise RuntimeError("forced_ai_cost_ledger_scope_missing")
    # 台账只记精确名:上游漏网的 *-latest 别名在这里兜底映射,原别名进 metadata 留痕。
    requested_model = str(model or "").strip()
    model = resolve_model_alias(provider, requested_model)
    if model != requested_model:
        metadata = {**(metadata or {}), "model_alias": requested_model}
    gateway.ensure_vkpi_product_industry_schema()
    uid = f"llm-{secrets.token_hex(8)}"
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else ""
    micro = (
        int(cost_micro_usd)
        if cost_micro_usd is not None
        else int(round(int(cost_cents or 0) * 10000))
    )
    final_cents = (
        gateway._micro_usd_to_cents(micro)
        if cost_micro_usd is not None
        else int(cost_cents or 0)
    )
    conn = gateway.get_conn()
    actor_staff_id = _actor_staff_id(conn=conn, gateway=gateway, staff=staff, triggered_by=triggered_by)
    conn.execute(
        """
        INSERT INTO vkpi_llm_calls
            (call_uid, provider, model, purpose, prompt_hash, input_tokens, output_tokens, cost_cents,
             cost_micro_usd, latency_ms, status, fallback_used, created_by_staff_id, created_at, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            provider or "unknown",
            model or "",
            purpose or "",
            prompt_hash,
            int(input_tokens or 0),
            int(output_tokens or 0),
            int(final_cents or 0),
            int(micro or 0),
            int((metadata or {}).get("latency_ms") or 0)
            if isinstance(metadata, dict)
            and (metadata or {}).get("latency_ms") is not None
            else None,
            status or "not_configured",
            bool(fallback_used),
            actor_staff_id,
            gateway._utcnow(),
            gateway._json(metadata),
        ),
    )
    conn.commit()
    cost_ledger: dict[str, Any] | None = None
    cost_ledger_error: str | None = None
    if cost_tag and (
        bool(force_cost_ledger) or status == "success" or int(micro or 0) > 0
    ):
        try:
            provider_scope = gateway._provider_budget_scope(provider)
            cumulative_scopes = [
                scope for scope in ("monthly_total", provider_scope) if scope
            ]
            cost_ledger = gateway._budget_guard().record_cost(
                scope=cost_tag,
                cron_task=purpose,
                ai_provider=provider or "unknown",
                model_name=model or "",
                cost_usd=float(micro or 0) / 1_000_000,
                tokens_in=int(input_tokens or 0),
                tokens_out=int(output_tokens or 0),
                staff_id=actor_staff_id,
                metadata={
                    **(metadata or {}),
                    "llm_call_uid": uid,
                    "purpose": purpose,
                    "status": status,
                    "fallback_used": bool(fallback_used),
                },
                triggered_by=triggered_by if triggered_by is not None else staff,
                extra_scopes=cumulative_scopes,
                optional_scopes=(
                    [cost_tag, *cumulative_scopes] if not force_cost_ledger else ()
                ),
                update_budget_scopes=bool(update_budget_scopes),
            )
            if force_cost_ledger:
                if not isinstance(cost_ledger, dict) or not bool(
                    cost_ledger.get("recorded")
                ):
                    raise RuntimeError("forced_ai_cost_ledger_write_unconfirmed")
                if int(cost_ledger.get("ledger_id") or 0) <= 0:
                    raise RuntimeError("forced_ai_cost_ledger_id_missing")
                if int(cost_ledger.get("cost_micro_usd") or 0) != int(micro or 0):
                    raise RuntimeError("forced_ai_cost_ledger_amount_mismatch")
        except Exception as exc:
            # 台账异常透明(C1):根因类名 + 首行(脱敏)进日志、进调用行 metadata、进异常信息。
            # 此前只有一句 forced_ai_cost_ledger_write_failed,ForeignKeyViolation(staff_id 不存在)
            # 只在子进程 stderr 里,6270 个单测都没拦住。
            cost_ledger_error = _summarize_exception(exc)
            gateway.logger.warning(
                "vkpi.llm_gateway.ai_cost_record_failed | call_uid=%s scope=%s staff_id=%s | %s",
                uid,
                cost_tag,
                actor_staff_id,
                cost_ledger_error,
                exc_info=True,
            )
            _attach_ledger_error(conn, gateway, uid, metadata, cost_ledger_error)
            if force_cost_ledger:
                raise RuntimeError(
                    f"forced_ai_cost_ledger_write_failed: {cost_ledger_error}"
                ) from exc
    row = conn.execute(
        "SELECT * FROM vkpi_llm_calls WHERE call_uid=?", (uid,)
    ).fetchone()
    result: dict[str, Any] = {
        "call": dict(row) if row else {"call_uid": uid},
        "cost_ledger": cost_ledger,
    }
    if cost_ledger_error:
        result["cost_ledger_error"] = cost_ledger_error
    return result


_CACHE_HIT_NEEDLE = '"cache_hit": true'


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _row_int(row: dict[str, Any], key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def llm_degrade_rate(days: int = 7) -> dict[str, Any]:
    """Read-only degrade/cache telemetry over ``vkpi_llm_calls`` for the last ``days``.

    供 health_sentinel(L6)等只读消费方调用;纯 SELECT、compat ``?`` 占位符、
    不写库、不抛(库异常时返回 ``available=False`` + reason)。口径:

    - ``fallback_rate``  = fallback_used 行 / 总行
    - ``rule_v0_rate``   = provider='rule_v0' 行 / 总行(纯占位降级)
    - ``cache_hit_rate`` = metadata 含 ``cache_hit=true`` 行 / 总行(结果缓存命中,cost=0)
    - ``deferred_rate``  = status='deferred' 行 / 总行(闸拦下诚实推迟,非占位)
    - ``by_purpose``     = 同口径按 purpose 分组,按调用量降序

    Returns::

        {"available": bool, "days": int, "since": iso, "calls": int, "success": int,
         "fallback": int, "fallback_rate": float, "rule_v0": int, "rule_v0_rate": float,
         "cache_hit": int, "cache_hit_rate": float, "deferred": int, "deferred_rate": float,
         "by_purpose": [{"purpose", "calls", "success", "fallback", "fallback_rate",
                         "rule_v0", "rule_v0_rate", "cache_hit", "cache_hit_rate",
                         "deferred", "deferred_rate"}, ...]}
    """

    gateway = _gateway_module()
    try:
        window_days = max(1, min(365, int(days)))
    except (TypeError, ValueError):
        window_days = 7
    since_dt = datetime.now(timezone.utc) - timedelta(days=window_days)
    since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    base: dict[str, Any] = {
        "available": False,
        "days": window_days,
        "since": since,
        "calls": 0,
        "success": 0,
        "fallback": 0,
        "fallback_rate": 0.0,
        "rule_v0": 0,
        "rule_v0_rate": 0.0,
        "cache_hit": 0,
        "cache_hit_rate": 0.0,
        "deferred": 0,
        "deferred_rate": 0.0,
        "by_purpose": [],
    }
    # 子串函数方言:sqlite instr / Postgres strpos;needle 走参数,SQL 里不出现
    # 双引号或 percent 字面(compat 层禁 LIKE/% 字面)。
    contains = "strpos" if gateway.is_postgres_runtime() else "instr"
    sql = (
        "SELECT purpose, COUNT(*) AS calls, "
        "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_calls, "
        "SUM(CASE WHEN fallback_used THEN 1 ELSE 0 END) AS fallback_calls, "
        "SUM(CASE WHEN provider='rule_v0' THEN 1 ELSE 0 END) AS rule_v0_calls, "
        "SUM(CASE WHEN status='deferred' THEN 1 ELSE 0 END) AS deferred_calls, "
        f"SUM(CASE WHEN {contains}(COALESCE(metadata_json, ''), ?) > 0 THEN 1 ELSE 0 END) "
        "AS cache_hit_calls "
        "FROM vkpi_llm_calls WHERE created_at >= ? "
        "GROUP BY purpose ORDER BY calls DESC, purpose ASC"
    )
    try:
        gateway.ensure_vkpi_product_industry_schema()
        rows = gateway.get_conn().execute(sql, (_CACHE_HIT_NEEDLE, since)).fetchall()
    except Exception as exc:  # noqa: BLE001 - telemetry must never raise into callers
        gateway.logger.warning(
            "vkpi.llm_gateway.degrade_rate_query_failed", exc_info=True
        )
        base["reason"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return base

    by_purpose: list[dict[str, Any]] = []
    totals = {"calls": 0, "success": 0, "fallback": 0, "rule_v0": 0, "cache_hit": 0, "deferred": 0}
    for raw in rows or []:
        row = dict(raw)
        calls = _row_int(row, "calls")
        item = {
            "purpose": str(row.get("purpose") or ""),
            "calls": calls,
            "success": _row_int(row, "success_calls"),
            "fallback": _row_int(row, "fallback_calls"),
            "rule_v0": _row_int(row, "rule_v0_calls"),
            "cache_hit": _row_int(row, "cache_hit_calls"),
            "deferred": _row_int(row, "deferred_calls"),
        }
        item["fallback_rate"] = _rate(item["fallback"], calls)
        item["rule_v0_rate"] = _rate(item["rule_v0"], calls)
        item["cache_hit_rate"] = _rate(item["cache_hit"], calls)
        item["deferred_rate"] = _rate(item["deferred"], calls)
        by_purpose.append(item)
        for key in totals:
            totals[key] += int(item[key])
    base.update(totals)
    base.update(
        {
            "available": True,
            "fallback_rate": _rate(totals["fallback"], totals["calls"]),
            "rule_v0_rate": _rate(totals["rule_v0"], totals["calls"]),
            "cache_hit_rate": _rate(totals["cache_hit"], totals["calls"]),
            "deferred_rate": _rate(totals["deferred"], totals["calls"]),
            "by_purpose": by_purpose,
        }
    )
    return base


__all__ = ["llm_degrade_rate", "record_call"]
