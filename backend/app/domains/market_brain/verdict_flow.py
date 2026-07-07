"""GTM 强制裁决流(闭环波 L2 · 规格第四章:治 finalized=0 的核心机制)。

三件事:
- spawn_due_verdicts(dry_run=True):扫 vkpi_action_inbox 里带 bet 且 review_at 到期
  未裁决的条目 → 经既有 inbox 写入链(producers.make_suggestion + inbox.persist_suggestions)
  生成 gtm_verdict 类型置顶裁决任务(priority=high;dedupe_key 幂等,复跑零新增)。
- record_verdict(outcome_id 或 inbox_id, decision, lesson, ...):人工裁决唯一落点 ——
  写 vkpi_gtm_outcomes 的 decision + lesson + next_weight_change + decided_at + decided_by;
  decided 即 finalized,已裁决行拒绝二次改判(诚实回 already_decided)。
- list_pending_verdicts() / list_outcomes():读端(待裁决清单 + 结果总账列表)。

红线(规格 + 用户点名「保证审」):
- 裁决只能人工 POST:record_verdict 要求真实 staff id(无 staff 直接拒绝),
  本模块绝无自动裁决路径 —— spawn 只生成任务,永远不写 decision。
- next_weight_change 在 L2 只做结构化记录;权重真回流由 L4 走既有
  recommendation_feedback 生效链且带样本闸(样本小于 5 不改权重),这里不抢跑。
- 绝不写 viltrox_fit_score、不碰 rule_v0;唯一写入面 = vkpi_gtm_outcomes 自身行 +
  vkpi_action_inbox 裁决任务行(新增经 inbox 既有链;裁决后把对应任务置 executed)。
- compat SQL 纪律:问号占位、零字面百分号;jsonb 键存在性判断用
  jsonb_extract_path_text(ASCII 问号操作符会被 compat 层当占位符,禁用)。

bet 读取契约(与 L1 materialize 对齐,规格第一章七要素):
vkpi_action_inbox.payload_json 的 bet 键(jsonb)= {why, what, who, expected, cost,
risk, escalate_if, retreat_if, review_at, ...};review_at 兼容 bet 内与 payload 顶层。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

_INBOX = "vkpi_action_inbox"
_OUTCOMES = "vkpi_gtm_outcomes"

VERDICT_CATEGORY = "gtm_verdict"
_VERDICT_KEY_PREFIX = "gtm_verdict:"

# POST 裁决只收后六种;open 是初始态,人不许把行「裁决回 open」。
DECISIONS = ("validated", "failed", "partial", "retry", "escalate", "retreat")

# 动作类型十一种口径(迁移 217 注释同款;库层不 CHECK,应用层白名单在此)。
ACTION_TYPES = (
    "kol_outreach", "dealer_push", "official_post", "indie_site_update",
    "review_collection", "community_test", "paid_boost", "content_retry",
    "landing_page_fix", "price_message_test", "competitor_response",
)

_SCAN_LIMIT = 500  # 单次扫 bet 行上限(bet 量级为每计划 10-30 条,足够)


# ── 小工具 ──────────────────────────────────────────────────────────


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, default=str, ensure_ascii=False)


def _loads(value: Any) -> Any:
    if value in (None, ""):
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        logger.warning("verdict_flow.payload_parse_failed", exc_info=True)
        return {}


def _text(value: Any, limit: int = 200) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    """ISO 串(容忍尾部 Z 与纯日期)→ aware datetime;解析失败诚实回 None。"""
    raw = _text(value, 40)
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _bet_of(payload: dict[str, Any]) -> dict[str, Any]:
    bet = payload.get("bet")
    return bet if isinstance(bet, dict) else {}


def _review_at_of(payload: dict[str, Any]) -> str:
    bet = _bet_of(payload)
    return _text(bet.get("review_at") or payload.get("review_at"), 40)


# ── 到期 bet 扫描(纯读) ─────────────────────────────────────────────


def _scan_bet_rows(conn: Any) -> list[dict[str, Any]]:
    """带 bet 键的 inbox 行(dismissed 除外;gtm_verdict 任务自身除外)。"""
    rows = conn.execute(
        f"""
        SELECT id, dedupe_key, category, title, detail, status, owner_staff_id,
               entity_type, entity_id, payload_json, created_at
        FROM {_INBOX}
        WHERE category <> ?
          AND status <> 'dismissed'
          AND jsonb_extract_path_text(payload_json, 'bet') IS NOT NULL
        ORDER BY id
        LIMIT ?
        """,
        (VERDICT_CATEGORY, _SCAN_LIMIT),
    ).fetchall()
    return [dict(r) for r in rows]


def _finalized_inbox_ids(conn: Any) -> set[int]:
    if not table_exists(_OUTCOMES):
        return set()
    rows = conn.execute(
        f"""
        SELECT DISTINCT action_inbox_id FROM {_OUTCOMES}
        WHERE decision <> 'open' AND action_inbox_id IS NOT NULL
        """
    ).fetchall()
    return {int(dict(r)["action_inbox_id"]) for r in rows}


def _open_outcome_by_inbox(conn: Any) -> dict[int, int]:
    """action_inbox_id → 未裁决(open)结果行 id(回填 job 可能先建行)。"""
    if not table_exists(_OUTCOMES):
        return {}
    rows = conn.execute(
        f"""
        SELECT id, action_inbox_id FROM {_OUTCOMES}
        WHERE decision = 'open' AND action_inbox_id IS NOT NULL
        ORDER BY id
        """
    ).fetchall()
    out: dict[int, int] = {}
    for r in rows:
        d = dict(r)
        out.setdefault(int(d["action_inbox_id"]), int(d["id"]))
    return out


def _verdict_tasks(conn: Any) -> dict[str, dict[str, Any]]:
    """既有裁决任务(任意状态)按 dedupe_key 索引 —— 幂等判定依据。"""
    rows = conn.execute(
        f"SELECT id, dedupe_key, status FROM {_INBOX} WHERE category = ?",
        (VERDICT_CATEGORY,),
    ).fetchall()
    return {str(dict(r)["dedupe_key"]): dict(r) for r in rows}


def _due_bets(conn: Any, now: datetime) -> tuple[list[dict[str, Any]], int]:
    """review_at 到期且未 finalized 的 bet 行。返回 (due 列表, 无法解析的 review_at 计数)。"""
    finalized = _finalized_inbox_ids(conn)
    due: list[dict[str, Any]] = []
    malformed = 0
    for row in _scan_bet_rows(conn):
        payload = _loads(row.get("payload_json"))
        if not isinstance(payload, dict):
            continue
        review_raw = _review_at_of(payload)
        if not review_raw:
            continue  # bet 未带复盘日期 —— L1 契约要求必填,缺席不猜
        review_dt = _parse_dt(review_raw)
        if review_dt is None:
            malformed += 1
            continue
        if review_dt > now:
            continue
        if int(row["id"]) in finalized:
            continue
        row["payload"] = payload
        row["review_at"] = review_raw
        row["review_dt"] = review_dt
        due.append(row)
    due.sort(key=lambda r: r["review_dt"])
    return due, malformed


# ── bet 上下文抽取(GTM 维度,防御式,与 L1 契约对齐) ─────────────────


def _bet_context(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else _loads(row.get("payload_json"))
    bet = _bet_of(payload)

    def pick(*keys: str) -> Any:
        for src in (bet, payload):
            for k in keys:
                v = src.get(k)
                if v not in (None, ""):
                    return v
        return ""

    kol_pool_id = _int_or_none(pick("kol_pool_id"))
    # 兜底放宽:L1 materialize 落的行 entity_type='kol'(subject 契约),旧口径 'kol_pool' 一并认;
    # entity_id 经 _int_or_none 容错(非数字 → None,不炸)。
    if kol_pool_id in (None, 0) and _text(row.get("entity_type"), 40) in ("kol", "kol_pool"):
        kol_pool_id = _int_or_none(row.get("entity_id"))
    kol_pool_id = kol_pool_id or None

    expected = bet.get("expected") or payload.get("expected_result")
    if isinstance(expected, str) and expected.strip():
        expected = {"statement": expected.strip()[:500]}
    if not isinstance(expected, dict):
        expected = None

    action_type = _text(pick("action_type"), 40)
    if action_type and action_type not in ACTION_TYPES:
        # 白名单外的口径诚实保留原文但打日志(库层无 CHECK,应用层留痕)。
        logger.warning("verdict_flow.action_type_outside_whitelist", extra={"action_type": action_type})
    if not action_type and kol_pool_id:
        action_type = "kol_outreach"

    channel = _text(pick("channel"), 40)
    if not channel and kol_pool_id:
        channel = "creator"

    return {
        "gtm_plan_id": _text(pick("gtm_plan_id", "plan_id"), 120),
        "product_sku": _text(pick("product_sku", "sku"), 120),
        "market": _text(pick("market", "country"), 40),
        "segment": _text(pick("segment", "persona"), 120),
        "channel": channel,
        "action_type": action_type,
        "content_angle": _text(pick("content_angle", "angle"), 120),
        "expected_result": expected,
        "kol_pool_id": kol_pool_id,
        "bet": bet,
    }


# ── 裁决任务生成(经既有 inbox 写入链,幂等) ─────────────────────────


def _build_verdict_suggestion(row: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    from app.domains.actions.producers import make_suggestion

    bet_id = int(row["id"])
    review_at = _text(row.get("review_at"), 40)
    owner = _int_or_none(row.get("owner_staff_id"))
    return make_suggestion(
        category=VERDICT_CATEGORY,
        dedupe_key=f"{_VERDICT_KEY_PREFIX}{bet_id}",
        title=_text(f"[到期裁决] {row.get('title') or 'bet ' + str(bet_id)}", 200),
        detail=_text(
            f"bet 已到复盘日期({review_at}),请对答案:当时预判 vs 三窗实际,"
            f"一键 decision(validated/failed/partial/retry/escalate/retreat)+ lesson 一句话。",
            500,
        ),
        reason="review_at 到期未裁决 —— 强制裁决流(不可跳过;七天不裁决升级到晨报头条点名,L4 接线)。",
        priority="high",  # 置顶:list_inbox 按 priority 高优先排序
        entity_type="action_inbox",
        entity_id=str(bet_id),
        suggested_endpoint=f"/api/admin/vkpi/gtm/verdicts/{bet_id}/decide",
        requires_approval=True,
        owner_staff_id=owner,
        payload={
            "verdict_for_inbox_id": bet_id,
            "review_at": review_at,
            "gtm_plan_id": ctx.get("gtm_plan_id"),
            "product_sku": ctx.get("product_sku"),
            "bet": ctx.get("bet") or {},
        },
        expected_gain="裁决落 vkpi_gtm_outcomes(decision+lesson),finalized 样本增加,预判置信度按账本可校准",
        risk_level="low",
        evidence_refs=[{"type": "action_inbox", "id": str(bet_id)}],
        verification_plan=[
            "人工 POST /gtm/verdicts/{id}/decide 后 vkpi_gtm_outcomes 落 decision+lesson",
            "decided_at 非空即 finalized;spawn 复跑零新增(dedupe_key 幂等)",
        ],
        affected_tables=["vkpi_gtm_outcomes"],
    )


def spawn_due_verdicts(dry_run: bool = True, *, limit: int = 100) -> dict[str, Any]:
    """扫到期未裁决 bet → 生成 gtm_verdict 置顶裁决任务(幂等;dry_run 零落库)。

    只生成任务,绝不写 decision(自动裁决路径不存在)。
    """
    if not table_exists(_INBOX):
        return {"status": "empty", "reason": "migration_141_not_applied", "dry_run": bool(dry_run)}
    conn = get_conn()
    now = _utcnow()
    due, malformed = _due_bets(conn, now)
    existing = _verdict_tasks(conn)

    to_create: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    skipped_existing = 0
    for row in due[: max(1, int(limit or 100))]:
        bet_id = int(row["id"])
        key = f"{_VERDICT_KEY_PREFIX}{bet_id}"
        if key in existing:
            skipped_existing += 1
            continue
        ctx = _bet_context(row)
        to_create.append(_build_verdict_suggestion(row, ctx))
        items.append({
            "bet_inbox_id": bet_id,
            "title": _text(row.get("title"), 160),
            "review_at": row.get("review_at"),
            "product_sku": ctx.get("product_sku"),
            "gtm_plan_id": ctx.get("gtm_plan_id"),
        })

    created = 0
    if not dry_run and to_create:
        from app.domains.actions.inbox import persist_suggestions

        created = persist_suggestions(to_create)

    return {
        "status": "ready",
        "dry_run": bool(dry_run),
        "due_count": len(due),
        "would_create": len(to_create),
        "created": created,
        "skipped_existing": skipped_existing,
        "malformed_review_at": malformed,
        "items": items[:50],
        "generated_at": now.isoformat(),
        "note": "幂等:已有同 dedupe_key 裁决任务(任意状态)或 bet 已 finalized 的一律跳过;dry_run 零落库。",
    }


# ── 人工裁决(唯一写 decision 的路径) ────────────────────────────────


def _close_verdict_task(conn: Any, bet_inbox_id: int) -> bool:
    """裁决落账后把对应 gtm_verdict 任务置 executed(best-effort,独立提交,只动该行)。

    调用点在结果行 commit 之后 —— 本函数失败绝不回滚已 finalized 的裁决。
    """
    try:
        cur = conn.execute(
            f"""
            UPDATE {_INBOX} SET status = 'executed', updated_at = NOW()
            WHERE dedupe_key = ? AND category = ?
              AND status IN ('suggested', 'approved', 'snoozed')
            """,
            (f"{_VERDICT_KEY_PREFIX}{int(bet_inbox_id)}", VERDICT_CATEGORY),
        )
        conn.commit()
        return int(getattr(cur, "rowcount", 0) or 0) > 0
    except Exception:
        logger.warning("verdict_flow.close_verdict_task_failed", extra={"bet_inbox_id": bet_inbox_id}, exc_info=True)
        try:
            conn.rollback()
        except Exception:
            logger.warning("verdict_flow.close_verdict_task_rollback_failed", exc_info=True)
        return False


def record_verdict(
    outcome_id: int | None = None,
    inbox_id: int | None = None,
    *,
    decision: str,
    lesson: str = "",
    weight_change: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """人工裁决:写 vkpi_gtm_outcomes 的 decision+lesson(+next_weight_change),decided 即 finalized。

    - outcome_id 与 inbox_id 二选一(都给以 outcome_id 为准);inbox_id 收 bet 行 id,
      也兼容传 gtm_verdict 任务行 id(自动解到其 payload 的 verdict_for_inbox_id)。
    - 已裁决行拒绝改判(already_decided);无 staff id 拒绝(裁决只能人工,绝无自动路径)。
    - weight_change 只做结构化记录进 next_weight_change;真回流 L4 走 recommendation_feedback 链。
    """
    from app.domains.access import scope

    dec = _text(decision, 20).lower()
    if dec not in DECISIONS:
        return {"ok": False, "reason": "invalid_decision", "allowed": list(DECISIONS)}
    actor = int(scope.actor_staff_id(staff))
    if actor <= 0:
        return {"ok": False, "reason": "staff_required",
                "note": "裁决只能人工发起;无有效 staff id 一律拒绝(绝无自动裁决路径)。"}
    if not table_exists(_OUTCOMES):
        return {"ok": False, "reason": "migration_217_not_applied"}

    lesson_clean = _text(lesson, 2000)
    wc: dict[str, Any] | None = None
    if weight_change not in (None, "", {}):
        wc = weight_change if isinstance(weight_change, dict) else {"note": _text(weight_change, 500)}
        wc = dict(wc)
        wc.setdefault("applied", False)  # L2 只记录;L4 走 recommendation_feedback 生效链后置真
        wc.setdefault("effect_chain", "recommendation_feedback")
        wc.setdefault("min_sample_gate", 5)

    conn = get_conn()
    now_iso = _utcnow().isoformat()

    # ── 定位目标结果行 ──
    target_outcome_id: int | None = None
    bet_inbox_id: int | None = None
    if outcome_id is not None:
        row = conn.execute(
            f"SELECT id, decision, action_inbox_id FROM {_OUTCOMES} WHERE id = ?",
            (int(outcome_id),),
        ).fetchone()
        if row is None:
            return {"ok": False, "reason": "outcome_not_found", "outcome_id": int(outcome_id)}
        d = dict(row)
        if _text(d.get("decision"), 20) != "open":
            return {"ok": False, "reason": "already_decided", "outcome_id": int(outcome_id),
                    "decision": _text(d.get("decision"), 20)}
        target_outcome_id = int(d["id"])
        bet_inbox_id = _int_or_none(d.get("action_inbox_id")) or None
        inbox_row = None
    elif inbox_id is not None:
        inbox_row = conn.execute(
            f"""
            SELECT id, dedupe_key, category, title, status, owner_staff_id,
                   entity_type, entity_id, payload_json
            FROM {_INBOX} WHERE id = ?
            """,
            (int(inbox_id),),
        ).fetchone()
        if inbox_row is None:
            return {"ok": False, "reason": "inbox_not_found", "inbox_id": int(inbox_id)}
        inbox_row = dict(inbox_row)
        if _text(inbox_row.get("category"), 40) == VERDICT_CATEGORY:
            # 传的是裁决任务行 → 解回 bet 行
            payload = _loads(inbox_row.get("payload_json"))
            ref = _int_or_none((payload or {}).get("verdict_for_inbox_id"))
            if not ref:
                return {"ok": False, "reason": "verdict_task_missing_bet_ref", "inbox_id": int(inbox_id)}
            bet_row = conn.execute(
                f"""
                SELECT id, dedupe_key, category, title, status, owner_staff_id,
                       entity_type, entity_id, payload_json
                FROM {_INBOX} WHERE id = ?
                """,
                (int(ref),),
            ).fetchone()
            if bet_row is None:
                return {"ok": False, "reason": "bet_inbox_not_found", "inbox_id": int(ref)}
            inbox_row = dict(bet_row)
        bet_inbox_id = int(inbox_row["id"])
        # 该 bet 的既有结果行:先拒已 finalized,再复用 open 行
        rows = conn.execute(
            f"SELECT id, decision FROM {_OUTCOMES} WHERE action_inbox_id = ? ORDER BY id",
            (bet_inbox_id,),
        ).fetchall()
        open_row = None
        for r in rows:
            d = dict(r)
            if _text(d.get("decision"), 20) != "open":
                return {"ok": False, "reason": "already_decided", "outcome_id": int(d["id"]),
                        "decision": _text(d.get("decision"), 20)}
            open_row = open_row or d
        target_outcome_id = int(open_row["id"]) if open_row else None
    else:
        return {"ok": False, "reason": "missing_id", "note": "outcome_id 与 inbox_id 至少给一个。"}

    # ── 落账(UPDATE 既有 open 行,或从 bet 上下文 INSERT 新行) ──
    if target_outcome_id is not None:
        cur = conn.execute(
            f"""
            UPDATE {_OUTCOMES}
            SET decision = ?, lesson = ?, next_weight_change = ?::jsonb,
                decided_at = NOW(), decided_by = ?
            WHERE id = ? AND decision = 'open'
            """,
            (dec, lesson_clean, _dumps(wc) if wc is not None else None, actor, target_outcome_id),
        )
        if int(getattr(cur, "rowcount", 0) or 0) <= 0:
            conn.commit()
            return {"ok": False, "reason": "already_decided", "outcome_id": target_outcome_id}
        final_id = target_outcome_id
    else:
        ctx = _bet_context(inbox_row or {})
        row = conn.execute(
            f"""
            INSERT INTO {_OUTCOMES}
              (gtm_plan_id, product_sku, market, segment, channel, action_type, content_angle,
               expected_result, decision, lesson, next_weight_change, action_inbox_id, kol_pool_id,
               created_at, decided_at, decided_by)
            VALUES (?,?,?,?,?,?,?,?::jsonb,?,?,?::jsonb,?,?,NOW(),NOW(),?)
            RETURNING id
            """,
            (
                ctx["gtm_plan_id"], ctx["product_sku"], ctx["market"], ctx["segment"],
                ctx["channel"], ctx["action_type"], ctx["content_angle"],
                _dumps(ctx["expected_result"]) if ctx["expected_result"] is not None else None,
                dec, lesson_clean, _dumps(wc) if wc is not None else None,
                bet_inbox_id, ctx["kol_pool_id"], actor,
            ),
        ).fetchone()
        final_id = int(dict(row)["id"]) if row else 0

    conn.commit()  # 结果行先落定,再 best-effort 关任务(关任务失败不影响 finalized)
    task_closed = _close_verdict_task(conn, bet_inbox_id) if bet_inbox_id else False

    logger.info(
        "verdict_flow.verdict_recorded",
        extra={"outcome_row_id": final_id, "decision": dec, "decided_by": actor,
               "bet_inbox_id": bet_inbox_id},
    )
    return {
        "ok": True,
        "outcome_id": final_id,
        "decision": dec,
        "finalized": True,
        "decided_at": now_iso,
        "decided_by": actor,
        "bet_inbox_id": bet_inbox_id,
        "verdict_task_closed": task_closed,
        "weight_change_recorded": wc is not None,
        "note": "decided 即 finalized;next_weight_change 仅结构化记录,真回流走 recommendation_feedback 链(样本闸 5)。",
    }


# ── 读端 ────────────────────────────────────────────────────────────


def list_pending_verdicts(limit: int = 50) -> dict[str, Any]:
    """待裁决清单:review_at 到期且未 finalized 的 bet(带裁决任务与 open 结果行线索)。全只读。"""
    if not table_exists(_INBOX):
        return {"status": "empty", "reason": "migration_141_not_applied", "items": [], "count": 0}
    conn = get_conn()
    now = _utcnow()
    due, malformed = _due_bets(conn, now)
    tasks = _verdict_tasks(conn)
    open_map = _open_outcome_by_inbox(conn)

    items: list[dict[str, Any]] = []
    for row in due[: max(1, min(int(limit or 50), 200))]:
        bet_id = int(row["id"])
        task = tasks.get(f"{_VERDICT_KEY_PREFIX}{bet_id}")
        overdue_days = max(0, (now - row["review_dt"]).days)
        items.append({
            "id": bet_id,  # POST /gtm/verdicts/{id}/decide 用这个 id
            "bet_inbox_id": bet_id,
            "title": _text(row.get("title"), 200),
            "category": _text(row.get("category"), 40),
            "status": _text(row.get("status"), 20),
            "owner_staff_id": _int_or_none(row.get("owner_staff_id")),
            "review_at": row.get("review_at"),
            "overdue_days": overdue_days,
            "bet": _bet_of(row.get("payload") or {}),
            "verdict_task": (
                {"id": int(task["id"]), "status": _text(task.get("status"), 20)} if task else None
            ),
            "open_outcome_id": open_map.get(bet_id),
            "decide_endpoint": f"/api/admin/vkpi/gtm/verdicts/{bet_id}/decide",
        })

    return {
        "status": "ready",
        "count": len(items),
        "due_total": len(due),
        "malformed_review_at": malformed,
        "items": items,
        "generated_at": now.isoformat(),
        "note": "到期未裁决的 bet;spawn_due_verdicts 会为无任务者生成置顶裁决任务(幂等)。",
    }


def list_outcomes(decision: str | None = None, limit: int = 50) -> dict[str, Any]:
    """GTM 结果总账只读列表(可按 decision 过滤;附 finalized 计数与分布)。"""
    if not table_exists(_OUTCOMES):
        return {"status": "empty", "reason": "migration_217_not_applied", "items": [], "count": 0}
    conn = get_conn()
    safe_limit = max(1, min(int(limit or 50), 200))

    where = ""
    params: list[Any] = []
    dec = _text(decision, 20).lower()
    if dec:
        if dec not in DECISIONS and dec != "open":
            return {"ok": False, "status": "error", "reason": "invalid_decision_filter",
                    "allowed": ["open", *DECISIONS], "items": [], "count": 0}
        where = "WHERE decision = ?"
        params.append(dec)
    params.append(safe_limit)

    rows = conn.execute(
        f"""
        SELECT id, gtm_plan_id, product_sku, market, segment, channel, action_type,
               content_angle, expected_result, actual_result,
               window_7d, window_14d, window_28d,
               decision, lesson, next_weight_change,
               action_inbox_id, kol_pool_id, created_at, decided_at, decided_by
        FROM {_OUTCOMES}
        {where}
        ORDER BY COALESCE(decided_at, created_at) DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()

    items: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        for key in ("expected_result", "actual_result", "window_7d", "window_14d",
                    "window_28d", "next_weight_change"):
            if row.get(key) is not None:
                row[key] = _loads(row.get(key))
        items.append(row)

    stats_rows = conn.execute(
        f"SELECT decision, COUNT(*) AS n FROM {_OUTCOMES} GROUP BY decision"
    ).fetchall()
    by_decision = {str(dict(r)["decision"]): int(dict(r)["n"]) for r in stats_rows}
    finalized = sum(n for k, n in by_decision.items() if k != "open")

    return {
        "status": "ready",
        "count": len(items),
        "items": items,
        "by_decision": by_decision,
        "finalized_count": finalized,
        "note": "总账只读;KOL 明细留 recommendation_outcomes,经 kol_pool_id/action_inbox_id 桥接。",
    }


__all__ = [
    "spawn_due_verdicts",
    "record_verdict",
    "list_pending_verdicts",
    "list_outcomes",
    "DECISIONS",
    "ACTION_TYPES",
    "VERDICT_CATEGORY",
]
