"""Bet Ledger 概率押注账本(cut5)—— 蓝图"猜未来/押注"系统。

押注 = {hypothesis, probability, expected_gain, cost, risk, evidence, review_at};到期由调度逼出
outcome(won/lost/void)+ lesson,形成 hypothesis→outcome→lesson 校准回路(命中率可统计)。
红线:lesson/outcome 仅人工或非 fit 路径写;LLM 永不触 viltrox_fit_score。
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

_TABLE = "vkpi_bet_ledger"


def _staff_id(staff: dict[str, Any] | None) -> int | None:
    try:
        return int((staff or {}).get("id") or 0) or None
    except Exception:
        return None


def _emit(event_type: str, **kw: Any) -> None:
    try:
        from app.domains.platform import event_ledger

        event_ledger.emit(event_type, source="bet_ledger", **kw)
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass


def create_bet(*, hypothesis: str, probability: float | None = None, expected_gain_cents: int = 0,
               cost_cents: int = 0, risk_level: str = "med", evidence: dict[str, Any] | None = None,
               review_at: str | None = None, source_action_inbox_id: int | None = None,
               staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """下一注押注(outcome 默认 open)。"""
    if not table_exists(_TABLE):
        return {"status": "unavailable"}
    h = str(hypothesis or "").strip()
    if not h:
        return {"status": "error", "error": "hypothesis required"}
    risk = str(risk_level or "med").lower()
    if risk not in ("low", "med", "high"):
        risk = "med"
    bet_uid = "bet_" + uuid.uuid4().hex[:16]
    conn = get_conn()
    row = conn.execute(
        f"INSERT INTO {_TABLE} (bet_uid, hypothesis, probability, expected_gain_cents, cost_cents, "
        f"risk_level, evidence_json, review_at, source_action_inbox_id, created_by_staff_id) "
        f"VALUES (?,?,?,?,?,?,?::jsonb,?,?,?) RETURNING id",
        (bet_uid, h, probability, int(expected_gain_cents or 0), int(cost_cents or 0), risk,
         json.dumps(evidence or {}, ensure_ascii=False), review_at, source_action_inbox_id, _staff_id(staff)),
    ).fetchone()
    conn.commit()
    bet_id = int(dict(row)["id"]) if row else None
    _emit("bet.created", entity_type="bet", entity_id=bet_id,
          payload={"hypothesis": h[:120], "probability": probability})
    return {"status": "ok", "bet_id": bet_id, "bet_uid": bet_uid}


def list_bets(outcome: str = "", limit: int = 50, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """列押注(可按 outcome 过滤)。附命中率统计(已结算里 won 占比)。"""
    if not table_exists(_TABLE):
        return {"status": "unavailable", "bets": []}
    where, params = "", []
    o = str(outcome or "").strip().lower()
    if o in ("open", "won", "lost", "void"):
        where = "WHERE outcome = ?"
        params.append(o)
    rows = get_conn().execute(
        f"SELECT id, bet_uid, hypothesis, probability, expected_gain_cents, cost_cents, risk_level, "
        f"review_at, outcome, realized_gain_cents, lesson, created_at FROM {_TABLE} {where} "
        f"ORDER BY id DESC LIMIT ?", (*params, int(max(1, min(limit, 500)))),
    ).fetchall()
    stat = get_conn().execute(
        f"SELECT COUNT(*) FILTER (WHERE outcome='won') AS won, "
        f"COUNT(*) FILTER (WHERE outcome IN ('won','lost')) AS settled FROM {_TABLE}"
    ).fetchone()
    s = dict(stat) if stat else {}
    settled = int(s.get("settled") or 0)
    hit_rate = round(int(s.get("won") or 0) / settled, 3) if settled else None
    return {"status": "ok", "bets": [dict(r) for r in rows], "hit_rate": hit_rate, "settled": settled}


def resolve_bet(bet_id: int, outcome: str, *, realized_gain_cents: int | None = None,
                lesson: str = "", staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """到期复盘:把 open 押注结算为 won/lost/void + 写 lesson(经验沉淀)。幂等:只动 open 行。"""
    if not table_exists(_TABLE):
        return {"status": "unavailable"}
    o = str(outcome or "").strip().lower()
    if o not in ("won", "lost", "void"):
        return {"status": "error", "error": "outcome must be won|lost|void"}
    conn = get_conn()
    cur = conn.execute(
        f"UPDATE {_TABLE} SET outcome=?, realized_gain_cents=?, lesson=?, updated_at=NOW() "
        f"WHERE id=? AND outcome='open'",
        (o, realized_gain_cents, str(lesson or ""), int(bet_id)),
    )
    conn.commit()
    if int(getattr(cur, "rowcount", 0) or 0) <= 0:
        return {"status": "error", "error": "bet not found or already resolved"}
    _emit("bet.resolved", entity_type="bet", entity_id=int(bet_id),
          payload={"outcome": o, "realized_gain_cents": realized_gain_cents})
    return {"status": "ok", "bet_id": int(bet_id), "outcome": o}


def scan_due_bets(limit: int = 100) -> dict[str, Any]:
    """扫到期未结算押注(review_at <= now AND outcome='open')——调度据此产复盘建议。"""
    if not table_exists(_TABLE):
        return {"status": "unavailable", "due": []}
    rows = get_conn().execute(
        f"SELECT id, bet_uid, hypothesis, review_at, probability FROM {_TABLE} "
        f"WHERE outcome='open' AND review_at IS NOT NULL AND review_at <= NOW() "
        f"ORDER BY review_at ASC LIMIT ?", (int(max(1, min(limit, 500))),),
    ).fetchall()
    return {"status": "ok", "due": [dict(r) for r in rows], "count": len(rows)}
