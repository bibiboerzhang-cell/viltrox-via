"""platform/llm_binding_stats.py — 任务模型链的纯读统计 + 链内选节(2026-08-30)。

数据源 = vkpi_llm_calls(近 30 天,按 task_binding + provider/model 聚合
success 率与延迟 p50)。本模块**只读零写库**:唯一 SQL 是一条 SELECT,
不 commit、不 INSERT/UPDATE;读失败记日志并让调用侧按「缺水」回落链首。

选节契约(与 llm_production._apply_chain_selection 配套):
- 总闸 ``VKPI_MODEL_CHAIN_SELECTION_ENABLED`` 默认关,开闸交用户;
- 单节链 / 统计缺水(链首样本 < ``CHAIN_SELECTION_SAMPLE_FLOOR``)/ 读库失败
  一律恒选链首 = 现行为,零回归;
- 只在链内选节(chain 由 model_registry.allowed_task_model_bindings 语义给出),
  绝不选链外绑定;升级链首(改主绑定)仍走人审,这里只做数据选节;
- 每次选节把「哪节、为何」完整落 trace,由调用侧写进 metadata 留痕。
"""
from __future__ import annotations

import json
import os
from typing import Any

from app.core.logging import get_logger

logger = get_logger("llm_binding_stats")

CHAIN_SELECTION_ENV_KEY = "VKPI_MODEL_CHAIN_SELECTION_ENABLED"
CHAIN_SELECTION_SAMPLE_FLOOR = 20
STATS_WINDOW_DAYS = 30
# 近 30 天单任务调用行的扫描上限(id DESC,最新优先);防超长窗口全表拖行。
STATS_SCAN_LIMIT = 5000

_TRUTHY = {"1", "true", "yes", "on"}


def chain_selection_enabled() -> bool:
    """链内选节总闸,默认关(env 缺省 / 任何非真值都算关)。"""

    return os.environ.get(CHAIN_SELECTION_ENV_KEY, "").strip().lower() in _TRUTHY


def _task_binding_needle(task: str) -> str:
    """metadata_json 里 task_binding 键值对的落库字面(与 llm_gateway._json 的
    json.dumps 默认分隔符一字不差;经 dumps 生成,转义规则天然一致)。"""

    return json.dumps({"task_binding": str(task or "")}, ensure_ascii=False)[1:-1]


def _since_iso(days: int) -> str:
    from datetime import datetime, timedelta, timezone

    window = max(1, min(365, int(days)))
    since = datetime.now(timezone.utc) - timedelta(days=window)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ")


def _calls_sql() -> str:
    """方言差异同 llm_gateway 台账读法:Postgres strpos + metadata_json 显式
    cast text;SQLite instr。子串 needle 走 ``?`` 参数,SQL 无 percent 字面。"""

    from app.db.connection import is_postgres_runtime

    if is_postgres_runtime():
        contains, column = "strpos", "COALESCE(metadata_json::text, '')"
    else:
        contains, column = "instr", "COALESCE(metadata_json, '')"
    return (
        "SELECT provider, model, status, latency_ms "
        "FROM vkpi_llm_calls "
        f"WHERE created_at >= ? AND {contains}({column}, ?) > 0 "
        "ORDER BY id DESC LIMIT ?"
    )


def _fetch_task_call_rows(task: str, since_iso: str, get_conn: Any) -> list[Any]:
    if get_conn is None:
        from app.db.connection import get_conn as runtime_get_conn

        get_conn = runtime_get_conn
    return get_conn().execute(
        _calls_sql(), (since_iso, _task_binding_needle(task), STATS_SCAN_LIMIT)
    ).fetchall()


def _latency_p50(latencies: list[float]) -> int | None:
    if not latencies:
        return None
    ordered = sorted(latencies)
    return int(ordered[(len(ordered) - 1) // 2])


def _bucket_stats(bucket: dict[str, Any]) -> dict[str, Any]:
    samples = int(bucket["samples"])
    success = int(bucket["success"])
    return {
        "samples": samples,
        "success": success,
        "success_rate": round(success / samples, 4) if samples > 0 else 0.0,
        "latency_p50_ms": _latency_p50(bucket["latencies"]),
    }


def _ingest_row(buckets: dict[str, dict[str, Any]], row: Any) -> None:
    data = dict(row)
    provider = str(data.get("provider") or "").strip().lower()
    model = str(data.get("model") or "").strip()
    bucket = buckets.get(f"{provider}/{model}")
    if bucket is None:  # 链外行(同 task 其它模型)不计入
        return
    bucket["samples"] += 1
    if str(data.get("status") or "").strip().lower() != "success":
        return
    bucket["success"] += 1
    latency = data.get("latency_ms")
    if isinstance(latency, (int, float)) and latency > 0:
        bucket["latencies"].append(float(latency))


def _aggregate_rows(
    rows: list[Any], bindings: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {
        binding: {"samples": 0, "success": 0, "latencies": []} for binding in bindings
    }
    for row in rows or []:
        _ingest_row(buckets, row)
    return {binding: _bucket_stats(bucket) for binding, bucket in buckets.items()}


def binding_call_stats(
    task: str,
    bindings: tuple[str, ...],
    *,
    days: int = STATS_WINDOW_DAYS,
    get_conn: Any = None,
) -> dict[str, dict[str, Any]] | None:
    """近 ``days`` 天按 task+binding 的纯读聚合;读失败记日志回 ``None``。

    返回 ``{binding: {samples, success, success_rate, latency_p50_ms}}``;
    ``None`` 表示统计不可用(调用侧必须按缺水语义回落链首,不许抛进业务)。
    """

    nodes = tuple(str(item or "").strip() for item in bindings if str(item or "").strip())
    if not nodes:
        return {}
    try:
        rows = _fetch_task_call_rows(str(task or ""), _since_iso(days), get_conn)
    except Exception:
        logger.warning(
            "vkpi.llm_binding_stats.read_failed",
            extra={"task": str(task or "")},
            exc_info=True,
        )
        return None
    return _aggregate_rows(rows, nodes)


def _sort_key(entry: dict[str, Any]) -> tuple[float, float]:
    p50 = entry.get("latency_p50_ms")
    latency_rank = float(p50) if isinstance(p50, (int, float)) else float("inf")
    return (float(entry.get("success_rate") or 0.0), -latency_rank)


def _node_samples(stats: dict[str, dict[str, Any]], binding: str) -> int:
    entry = stats.get(binding) or {}
    return int(entry.get("samples") or 0)


def _pick_best(
    chain: tuple[str, ...], stats: dict[str, dict[str, Any]], sample_floor: int
) -> tuple[str, str]:
    head = chain[0]
    if _node_samples(stats, head) < int(sample_floor):
        return head, "insufficient_samples_head_holds"
    best = head
    for candidate in chain[1:]:
        if _node_samples(stats, candidate) < int(sample_floor):
            continue
        if _sort_key(stats.get(candidate) or {}) > _sort_key(stats.get(best) or {}):
            best = candidate
    if best == head:
        return head, "head_best_by_stats"
    return best, "stats_preferred_fallback_node"


def _decision(
    task: str,
    chain: tuple[str, ...],
    selected: str,
    reason: str,
    stats: dict[str, dict[str, Any]],
    sample_floor: int,
    days: int,
) -> dict[str, Any]:
    return {
        "binding": selected,
        "reason": reason,
        "trace": {
            "selector": "llm_binding_stats_v1",
            "task_binding": str(task or ""),
            "chain": list(chain),
            "selected_binding": selected,
            "selected_index": chain.index(selected),
            "reason": reason,
            "sample_floor": int(sample_floor),
            "window_days": int(days),
            "stats": dict(stats or {}),
        },
    }


def select_chain_binding(
    task: str,
    chain: tuple[str, ...],
    *,
    sample_floor: int = CHAIN_SELECTION_SAMPLE_FLOOR,
    days: int = STATS_WINDOW_DAYS,
    get_conn: Any = None,
) -> dict[str, Any]:
    """在一条任务链内按统计选最优节;缺水/单节/读失败恒选链首(现行为)。

    返回 ``{"binding", "reason", "trace"}``;``trace`` 由调用侧落 metadata 留痕。
    只在传入 chain 内选,不查 registry、不放宽到链外;空链抛 ValueError
    (调用侧应先做绑定校验,空链是编程错误而非运行时数据问题)。
    """

    nodes = tuple(str(item or "").strip() for item in chain if str(item or "").strip())
    if not nodes:
        raise ValueError("chain must contain at least one binding")
    if len(nodes) == 1:
        return _decision(task, nodes, nodes[0], "single_node_chain", {}, sample_floor, days)
    stats = binding_call_stats(task, nodes, days=days, get_conn=get_conn)
    if stats is None:
        return _decision(
            task, nodes, nodes[0], "stats_read_failed_head_holds", {}, sample_floor, days
        )
    selected, reason = _pick_best(nodes, stats, sample_floor)
    return _decision(task, nodes, selected, reason, stats, sample_floor, days)


__all__ = [
    "CHAIN_SELECTION_ENV_KEY",
    "CHAIN_SELECTION_SAMPLE_FLOOR",
    "STATS_WINDOW_DAYS",
    "binding_call_stats",
    "chain_selection_enabled",
    "select_chain_binding",
]
