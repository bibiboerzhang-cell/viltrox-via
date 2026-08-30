"""Behavior-frozen write pipeline for Via memory-retention statistics.

This module is a leaf of :mod:`via_control_stats`: it owns the two historical
table layouts and their SQL write paths, but it does not import the public
repository facade.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class MemoryRetentionDependencies:
    """Repository primitives supplied by the public facade.

    Supplying these callables keeps the existing monkeypatch surface and makes
    characterization tests independent of a real database.
    """

    get_conn: Callable[[], Any]
    utcnow: Callable[[], str]
    table_columns: Callable[[Any, str], set[str]]
    load_json: Callable[[Any, Any], Any]
    dump_json: Callable[[Any, Any], str]
    nullable_timestamp: Callable[[Any], Any]
    row_mapper: Callable[[Any], dict[str, Any]]


@dataclass(frozen=True)
class _RetentionWrite:
    key: str
    now: str
    user_id: int
    session_key: str
    memory_tier: str
    memory_kind: str
    fact_key: str
    source_ref: str
    target_type: str
    target_id: str
    confirmed_hit_increment: int
    reinforcement_increment: int
    reward_delta: float
    last_hit_at: str
    last_promoted_at: str
    decay_state: str
    status: str
    metrics: Any


_CURRENT_SELECT_SQL = "SELECT * FROM via_memory_retention_stats WHERE retention_key=?"
_LEGACY_SELECT_SQL = """
        SELECT * FROM via_memory_retention_stats
        WHERE memory_key=? AND target_type=? AND target_id=?
        """
_CURRENT_UPDATE_SQL = """
                UPDATE via_memory_retention_stats
                SET user_id=?, session_key=?, memory_tier=?, memory_kind=?, fact_key=?, source_ref=?,
                    confirmed_hits=?, reinforcement_count=?, cumulative_reward=?, last_hit_at=?,
                    last_promoted_at=?, decay_state=?, status=?, metrics_json=?, updated_at=?
                WHERE retention_key=?
                """
_LEGACY_UPDATE_SQL = """
                UPDATE via_memory_retention_stats
                SET memory_kind=?, memory_tier=?, target_type=?, target_id=?, status=?,
                    confirmed_hits=?, reinforcement_count=?, cumulative_reward=?, last_hit_at=?,
                    last_promoted_at=?, metrics_json=?, updated_at=?
                WHERE memory_key=? AND target_type=? AND target_id=?
                """
_CURRENT_INSERT_SQL = """
                    INSERT INTO via_memory_retention_stats (
                        retention_key, user_id, session_key, memory_tier, memory_kind, fact_key,
                        source_ref, confirmed_hits, reinforcement_count, cumulative_reward,
                        last_hit_at, last_promoted_at, decay_state, status, metrics_json, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """
_LEGACY_INSERT_SQL = """
                INSERT INTO via_memory_retention_stats (
                    memory_key, memory_kind, memory_tier, target_type, target_id, status,
                    confirmed_hits, reinforcement_count, cumulative_reward, last_hit_at,
                    last_promoted_at, metrics_json, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """


def _build_write(
    *,
    deps: MemoryRetentionDependencies,
    retention_key: str,
    memory_key: str,
    user_id: int,
    session_key: str,
    memory_tier: str,
    memory_kind: str,
    fact_key: str,
    source_ref: str,
    target_type: str,
    target_id: str,
    confirmed_hit_increment: int,
    reinforcement_increment: int,
    reward_delta: float,
    last_hit_at: str,
    last_promoted_at: str,
    decay_state: str,
    status: str,
    metrics: Any,
) -> _RetentionWrite:
    key = str(retention_key or memory_key or "").strip()
    now = deps.utcnow()
    source_ref_value = str(source_ref or "").strip()
    if not source_ref_value and (str(target_type or "").strip() or str(target_id or "").strip()):
        source_ref_value = ":".join(
            part for part in (str(target_type or "").strip(), str(target_id or "").strip()) if part
        )
    return _RetentionWrite(
        key=key,
        now=now,
        user_id=user_id,
        session_key=session_key,
        memory_tier=memory_tier,
        memory_kind=memory_kind,
        fact_key=fact_key,
        source_ref=source_ref_value,
        target_type=target_type,
        target_id=target_id,
        confirmed_hit_increment=confirmed_hit_increment,
        reinforcement_increment=reinforcement_increment,
        reward_delta=reward_delta,
        last_hit_at=last_hit_at,
        last_promoted_at=last_promoted_at,
        decay_state=decay_state,
        status=status,
        metrics=metrics,
    )


def _has_current_schema(deps: MemoryRetentionDependencies, conn: Any) -> bool:
    return "retention_key" in deps.table_columns(conn, "via_memory_retention_stats")


def _select_current(conn: Any, key: str) -> Any:
    return conn.execute(_CURRENT_SELECT_SQL, (key,)).fetchone()


def _select_legacy(conn: Any, write: _RetentionWrite) -> Any:
    return conn.execute(
        _LEGACY_SELECT_SQL,
        (
            write.key,
            str(write.target_type or "").strip(),
            str(write.target_id or "").strip(),
        ),
    ).fetchone()


def _select_for_schema(
    deps: MemoryRetentionDependencies,
    conn: Any,
    write: _RetentionWrite,
    *,
    current_schema: bool,
) -> Any:
    if current_schema:
        return _select_current(conn, write.key)
    return _select_legacy(conn, write)


def _metrics_for_write(
    deps: MemoryRetentionDependencies,
    write: _RetentionWrite,
    *,
    existing: Any = None,
) -> dict[str, Any]:
    if existing:
        merged_metrics = dict(deps.load_json(existing["metrics_json"], {}))
        if isinstance(write.metrics, dict):
            merged_metrics.update(write.metrics)
    else:
        merged_metrics = dict(write.metrics) if isinstance(write.metrics, dict) else {}
    if str(write.target_type or "").strip():
        merged_metrics["target_type"] = str(write.target_type).strip()
    if str(write.target_id or "").strip():
        merged_metrics["target_id"] = str(write.target_id).strip()
    return merged_metrics


def _promoted_at_for_update(
    requested: Any,
    existing: Any,
    *,
    now_fallback: Any = None,
) -> Any:
    if now_fallback is not None:
        return requested or existing or now_fallback
    return requested or existing


def _update_current(
    deps: MemoryRetentionDependencies,
    conn: Any,
    write: _RetentionWrite,
    existing: Any,
    merged_metrics: dict[str, Any],
    *,
    promote_now_fallback: bool,
) -> None:
    conn.execute(
        _CURRENT_UPDATE_SQL,
        (
            int(write.user_id or existing["user_id"] or 0),
            str(write.session_key or existing["session_key"] or ""),
            str(write.memory_tier or existing["memory_tier"] or ""),
            str(write.memory_kind or existing["memory_kind"] or ""),
            str(write.fact_key or existing["fact_key"] or ""),
            str(write.source_ref or existing["source_ref"] or ""),
            int(existing["confirmed_hits"] or 0) + int(write.confirmed_hit_increment or 0),
            int(existing["reinforcement_count"] or 0) + int(write.reinforcement_increment or 0),
            float(existing["cumulative_reward"] or 0.0) + float(write.reward_delta or 0.0),
            deps.nullable_timestamp(write.last_hit_at or existing["last_hit_at"]),
            deps.nullable_timestamp(
                _promoted_at_for_update(
                    write.last_promoted_at,
                    existing["last_promoted_at"],
                    now_fallback=write.now if promote_now_fallback else None,
                )
            ),
            str(write.decay_state or existing["decay_state"] or "fresh"),
            str(write.status or existing["status"] or "active"),
            deps.dump_json(merged_metrics, {}),
            write.now,
            write.key,
        ),
    )


def _update_legacy(
    deps: MemoryRetentionDependencies,
    conn: Any,
    write: _RetentionWrite,
    existing: Any,
    merged_metrics: dict[str, Any],
) -> None:
    conn.execute(
        _LEGACY_UPDATE_SQL,
        (
            str(write.memory_kind or existing["memory_kind"] or ""),
            str(write.memory_tier or existing["memory_tier"] or ""),
            str(write.target_type or existing["target_type"] or ""),
            str(write.target_id or existing["target_id"] or ""),
            str(write.status or existing["status"] or "active"),
            int(existing["confirmed_hits"] or 0) + int(write.confirmed_hit_increment or 0),
            int(existing["reinforcement_count"] or 0) + int(write.reinforcement_increment or 0),
            float(existing["cumulative_reward"] or 0.0) + float(write.reward_delta or 0.0),
            deps.nullable_timestamp(write.last_hit_at or existing["last_hit_at"]),
            deps.nullable_timestamp(write.last_promoted_at or existing["last_promoted_at"]),
            deps.dump_json(merged_metrics, {}),
            write.now,
            write.key,
            str(existing["target_type"] or ""),
            str(existing["target_id"] or ""),
        ),
    )


def _insert_current(
    deps: MemoryRetentionDependencies,
    conn: Any,
    write: _RetentionWrite,
    insert_metrics: dict[str, Any],
) -> None:
    conn.execute(
        _CURRENT_INSERT_SQL,
        (
            write.key,
            int(write.user_id or 0),
            str(write.session_key or "").strip(),
            str(write.memory_tier or "").strip(),
            str(write.memory_kind or "").strip(),
            str(write.fact_key or "").strip(),
            write.source_ref,
            int(write.confirmed_hit_increment or 0),
            int(write.reinforcement_increment or 0),
            float(write.reward_delta or 0.0),
            deps.nullable_timestamp(write.last_hit_at),
            deps.nullable_timestamp(write.last_promoted_at or write.now),
            str(write.decay_state or "fresh"),
            str(write.status or "active"),
            deps.dump_json(insert_metrics, {}),
            write.now,
        ),
    )


def _insert_legacy(
    deps: MemoryRetentionDependencies,
    conn: Any,
    write: _RetentionWrite,
    insert_metrics: dict[str, Any],
) -> None:
    conn.execute(
        _LEGACY_INSERT_SQL,
        (
            write.key,
            str(write.memory_kind or "").strip(),
            str(write.memory_tier or "").strip(),
            str(write.target_type or "").strip(),
            str(write.target_id or "").strip(),
            str(write.status or "active"),
            int(write.confirmed_hit_increment or 0),
            int(write.reinforcement_increment or 0),
            float(write.reward_delta or 0.0),
            deps.nullable_timestamp(write.last_hit_at),
            deps.nullable_timestamp(write.last_promoted_at or write.now),
            deps.dump_json(insert_metrics, {}),
            write.now,
        ),
    )


def _insert_or_recover_current(
    deps: MemoryRetentionDependencies,
    conn: Any,
    write: _RetentionWrite,
    insert_metrics: dict[str, Any],
) -> None:
    try:
        _insert_current(deps, conn, write, insert_metrics)
    except Exception as exc:
        text = str(exc).lower()
        if "duplicate key" not in text and "unique" not in text:
            raise
        existing = _select_current(conn, write.key)
        if not existing:
            raise
        merged_metrics = dict(deps.load_json(existing["metrics_json"], {}))
        merged_metrics.update(insert_metrics)
        _update_current(
            deps,
            conn,
            write,
            existing,
            merged_metrics,
            promote_now_fallback=True,
        )


def _write_existing(
    deps: MemoryRetentionDependencies,
    conn: Any,
    write: _RetentionWrite,
    existing: Any,
) -> None:
    merged_metrics = _metrics_for_write(deps, write, existing=existing)
    if _has_current_schema(deps, conn):
        _update_current(
            deps,
            conn,
            write,
            existing,
            merged_metrics,
            promote_now_fallback=False,
        )
    else:
        _update_legacy(deps, conn, write, existing, merged_metrics)


def _write_new(
    deps: MemoryRetentionDependencies,
    conn: Any,
    write: _RetentionWrite,
) -> None:
    insert_metrics = _metrics_for_write(deps, write)
    if _has_current_schema(deps, conn):
        _insert_or_recover_current(deps, conn, write, insert_metrics)
    else:
        _insert_legacy(deps, conn, write, insert_metrics)


def upsert_via_memory_retention_stat(
    *,
    deps: MemoryRetentionDependencies,
    retention_key: str = "",
    memory_key: str = "",
    user_id: int = 0,
    session_key: str = "",
    memory_tier: str = "",
    memory_kind: str = "",
    fact_key: str = "",
    source_ref: str = "",
    target_type: str = "",
    target_id: str = "",
    confirmed_hit_increment: int = 0,
    reinforcement_increment: int = 0,
    reward_delta: float = 0.0,
    last_hit_at: str = "",
    last_promoted_at: str = "",
    decay_state: str = "",
    status: str = "",
    metrics: Any = None,
) -> dict[str, Any]:
    """Upsert against the observed schema while preserving legacy call order."""
    conn = deps.get_conn()
    write = _build_write(
        deps=deps,
        retention_key=retention_key,
        memory_key=memory_key,
        user_id=user_id,
        session_key=session_key,
        memory_tier=memory_tier,
        memory_kind=memory_kind,
        fact_key=fact_key,
        source_ref=source_ref,
        target_type=target_type,
        target_id=target_id,
        confirmed_hit_increment=confirmed_hit_increment,
        reinforcement_increment=reinforcement_increment,
        reward_delta=reward_delta,
        last_hit_at=last_hit_at,
        last_promoted_at=last_promoted_at,
        decay_state=decay_state,
        status=status,
        metrics=metrics,
    )
    existing = _select_for_schema(
        deps,
        conn,
        write,
        current_schema=_has_current_schema(deps, conn),
    )
    if existing:
        _write_existing(deps, conn, write, existing)
    else:
        _write_new(deps, conn, write)
    row = _select_for_schema(
        deps,
        conn,
        write,
        current_schema=_has_current_schema(deps, conn),
    )
    conn.commit()
    return deps.row_mapper(row)
