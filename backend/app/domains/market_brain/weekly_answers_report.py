"""Read and shape helpers for the weekly answers facade."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


def empty_sections(group_dims: tuple[str, ...]) -> dict[str, Any]:
    return {
        "groups": {dimension: [] for dimension in group_dims},
        "what_worked": {"status": "empty", "items": [], "group_highlights": []},
        "what_failed": {"status": "empty", "items": [], "group_highlights": []},
        "what_to_change": {
            "status": "empty",
            "count": 0,
            "items": [],
            "effect_chain_note": "",
        },
    }


def load_rows(
    conn: Any,
    *,
    table: str,
    decided_decisions: tuple[str, ...],
    int0: Any,
) -> tuple[list[dict[str, Any]], int]:
    placeholders = ",".join("?" for _ in decided_decisions)
    decided = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT id, gtm_plan_id, product_sku, market, segment, channel,
                   action_type, content_angle, decision, lesson,
                   next_weight_change, actual_result,
                   window_7d, window_14d, window_28d,
                   action_inbox_id, created_at, decided_at, decided_by
            FROM {table}
            WHERE decision IN ({placeholders})
              AND decided_at IS NOT NULL
              AND decided_by IS NOT NULL
            ORDER BY id DESC
            """,
            tuple(decided_decisions),
        ).fetchall()
    ]
    open_row = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM {table}
        WHERE decision IS NULL OR decision NOT IN ({placeholders})
        """,
        tuple(decided_decisions),
    ).fetchone()
    return decided, int0(dict(open_row).get("n")) if open_row else 0


def period_rows(
    decided: list[dict[str, Any]],
    since: datetime | None,
    *,
    parse_dt: Any,
) -> list[dict[str, Any]]:
    if since is None:
        return list(decided)
    rows: list[dict[str, Any]] = []
    for row in decided:
        decided_at = parse_dt(row.get("decided_at"))
        if decided_at is not None and decided_at >= since:
            rows.append(row)
    return rows


def learning_groups(
    conn: Any,
    decided: list[dict[str, Any]],
    selected_period_rows: list[dict[str, Any]],
    *,
    now: datetime,
    ops: Mapping[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    dict[str, Any],
    bool,
]:
    from app.domains.market_brain.data_readiness import (
        build_learning_readiness,
        has_verified_outcome_evidence,
    )

    for row in decided:
        row["evidence_backed"] = has_verified_outcome_evidence(conn, row)
    evidence_decided = [row for row in decided if row["evidence_backed"]]
    evidence_period = [
        row for row in selected_period_rows if row.get("evidence_backed")
    ]
    groups = {
        dimension: ops["_group_stats"](evidence_decided, dimension)
        for dimension in ops["GROUP_DIMS"]
    }
    readiness = build_learning_readiness(conn=conn, now=now)
    claimable = bool(readiness.get("claimable"))
    for entries in groups.values():
        for group in entries:
            group["observed_win_rate"] = group.get("win_rate")
            group["claimable"] = claimable and not bool(group.get("insufficient"))
            group["claim_status"] = (
                "validated" if group["claimable"] else "descriptive_only"
            )
    return evidence_decided, evidence_period, groups, readiness, claimable


def conclusion_sections(
    conclusion_rows: list[dict[str, Any]],
    groups: dict[str, list[dict[str, Any]]],
    *,
    claimable: bool,
    ops: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    worked_items = [
        ops["_bet_brief"](row)
        for row in conclusion_rows
        if ops["_text"](row.get("decision"), 20) in ops["WIN_DECISIONS"]
    ]
    failed_items = [
        ops["_bet_brief"](row)
        for row in conclusion_rows
        if ops["_text"](row.get("decision"), 20) in ops["LOSS_DECISIONS"]
    ]
    what_worked = {
        "status": "ready" if worked_items else "empty",
        "items": worked_items[: ops["_ITEM_CAP"]],
        "group_highlights": (
            ops["_sufficient"](groups, min_rate=0.6) if claimable else []
        ),
        "claimable": claimable,
        "claim_status": "validated" if claimable else "human_verdicts_only",
        "note": (
            "对了什么=本期带真实窗口证据的 validated/escalate 裁决"
            " + 样本≥5 且胜率≥60% 的组合。"
            if claimable
            else "保留人工裁决明细,但 finalized outcome / prediction eval / 真实反馈三项未齐,不输出有效组合。"
        ),
    }
    what_failed = {
        "status": "ready" if failed_items else "empty",
        "items": failed_items[: ops["_ITEM_CAP"]],
        "group_highlights": (
            ops["_sufficient"](groups, max_rate=0.4) if claimable else []
        ),
        "claimable": claimable,
        "claim_status": "validated" if claimable else "human_verdicts_only",
        "note": (
            "错了什么=本期带真实窗口证据的 failed/retreat 裁决"
            " + 样本≥5 且胜率≤40% 的组合;lesson 原话随行。"
            if claimable
            else "保留人工裁决明细,但 DataReadiness 未通过,不把小样本命名为稳定失败规律。"
        ),
    }
    what_to_change = ops["_build_what_to_change"](conclusion_rows)
    what_to_change["claimable"] = claimable
    return what_worked, what_failed, what_to_change
