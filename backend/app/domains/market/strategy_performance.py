"""S4 实际战略表现看板(strategy_performance_v1)—— 「我们的战略判断到底准不准」。

plan vs actual 三账对齐(纯 SQL/规则聚合已有真实数据,决定性、零 LLM、零写库、零新采集):
  ① 押注账(vkpi_bet_ledger):每注 hypothesis/probability/outcome(won/lost/open/void)
     + lesson + 账龄;最老 open 注单独点名(到期未复盘=战略欠账)。
  ② 预测军团(prediction_ledger.ledger_summary,守卫 import):各组命中率精选
     + 待对答案积压(kol_recommend 那本 777 条账,pending=有预测无结果)。
  ③ 履约战果(vkpi_workflow_runs/steps + vkpi_project_content_observation_windows
     + vkpi_project_content_posts + vkpi_projects):完成 loop 数(首条真实闭环
     run_id=5 逐步列出)+ planned vs actual 样例——计划基线=观察窗口(项目
     target_post_date 全空,诚实说明),actual=发布时点+实际播放读数。

lessons:已沉淀教训 top5,只从三处真实留痕读(bet_ledger.lesson /
vkpi_analysis_cache 的 project_retrospective_v1 risks+next_steps / miss_review 原因聚类),
绝不现编。honesty_note:哪本账还在数据荒直说。

诚实态:一切数字带 basis/method/confidence 可追溯;样本<5 = insufficient;
判不出=None/空态+reason,绝不编造市场数据、不引外部行情。
compat 约定:SQL 占位符 ?;SQL 字符串零字面 percent(不用 LIKE);BOOLEAN 判真走 _truthy;
漂移列走 to_jsonb(t.*)->>'col';函数内懒 import get_conn。
红线(永久):只读聚合,绝不写 viltrox_fit_score、绝不碰 rule_v0、不编辑 .env、零迁移。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

METHOD = "strategy_performance_v1"

# 置信度阶梯(与 prediction_ledger 同款口径,写进 basis 可追溯)
MIN_SAMPLE_FOR_CONFIDENCE = 5
LOW_CONFIDENCE_CEILING = 20
MEDIUM_CONFIDENCE_CEILING = 50

MAX_LESSONS = 5
MAX_PLAN_VS_ACTUAL_SAMPLES = 5
MAX_PREDICTION_GROUPS = 6
SCAN_LIMIT = 500

_FULFILLMENT_WORKFLOW = "fulfillment_sweep"
_RETRO_METHOD = "project_retrospective_v1"


# ── 小工具(与 prediction_ledger / miss_review 同款容错约定)──────────────


def _truthy(value: Any) -> bool:
    """compat 层 BOOLEAN 读回可能是 int 1/0 / 't' / 'true',统一容错。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "t", "true", "yes", "y")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _parse_ts(value: Any) -> datetime | None:
    """ISO 时间戳容错解析(兼容尾部 Z);解析不了诚实 None,绝不猜。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age_days(value: Any) -> int | None:
    dt = _parse_ts(value)
    if dt is None:
        return None
    return max(0, int((_utcnow() - dt).total_seconds() // 86400))


def _num(value: Any) -> float | None:
    """Decimal/str/int 统一转 float;转不动诚实 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _confidence(sample_count: int) -> str:
    """<5 insufficient / <20 low / <50 medium / >=50 high(与预测台账同阶梯)。"""
    if sample_count <= 0:
        return "none"
    if sample_count < MIN_SAMPLE_FOR_CONFIDENCE:
        return "insufficient"
    if sample_count < LOW_CONFIDENCE_CEILING:
        return "low"
    if sample_count < MEDIUM_CONFIDENCE_CEILING:
        return "medium"
    return "high"


def _text(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def _error_block(name: str, exc: Exception) -> dict[str, Any]:
    """单账聚合失败不拖垮整板:诚实 error 块。"""
    logger.warning("strategy_performance block failed name=%s: %s", name, exc)
    return {
        "status": "error",
        "reason": str(exc)[:300],
        "basis": {"method": METHOD, "generated_at": _utcnow_iso()},
    }


# ── 账① 押注账本(vkpi_bet_ledger)────────────────────────────────────────


def _collect_bets(conn: Any) -> dict[str, Any]:
    from app.db.connection import table_exists

    if not table_exists("vkpi_bet_ledger"):
        return {
            "status": "empty",
            "reason": "vkpi_bet_ledger 表不存在(押注账本尚未建立)",
            "won": 0, "lost": 0, "open": 0, "void": 0, "total": 0,
            "basis": {"method": METHOD, "source": ["vkpi_bet_ledger"], "generated_at": _utcnow_iso()},
        }
    rows = conn.execute(
        "SELECT id, bet_uid, hypothesis, probability, risk_level, review_at, outcome, "
        "realized_gain_cents, lesson, created_at FROM vkpi_bet_ledger ORDER BY id DESC LIMIT ?",
        (SCAN_LIMIT,),
    ).fetchall()
    bets = [dict(r) for r in rows]
    counts = {"won": 0, "lost": 0, "open": 0, "void": 0}
    items: list[dict[str, Any]] = []
    oldest_open: dict[str, Any] | None = None
    now = _utcnow()
    for row in bets:
        outcome = str(row.get("outcome") or "open").strip().lower()
        if outcome not in counts:
            outcome = "open"
        counts[outcome] += 1
        review_dt = _parse_ts(row.get("review_at"))
        item = {
            "bet_id": row.get("id"),
            "bet_uid": _text(row.get("bet_uid"), 40),
            "hypothesis": _text(row.get("hypothesis"), 200),
            "probability": _num(row.get("probability")),
            "risk_level": _text(row.get("risk_level"), 10) or None,
            "outcome": outcome,
            "lesson": _text(row.get("lesson"), 300) or None,
            "review_at": _iso(row.get("review_at")),
            "created_at": _iso(row.get("created_at")),
            "age_days": _age_days(row.get("created_at")),
            "review_overdue": bool(outcome == "open" and review_dt is not None and review_dt <= now),
        }
        items.append(item)
        if outcome == "open":
            if oldest_open is None or (item.get("age_days") or 0) > (oldest_open.get("age_days") or 0):
                oldest_open = item
    settled = counts["won"] + counts["lost"]
    hit_rate = round(counts["won"] / settled, 4) if settled > 0 else None
    total = len(bets)
    return {
        "status": "ok" if total > 0 else "empty",
        "reason": None if total > 0 else "押注账本 0 注(还没人敢押,谈不上准不准)",
        "won": counts["won"], "lost": counts["lost"], "open": counts["open"], "void": counts["void"],
        "total": total,
        "settled": settled,
        "hit_rate": hit_rate,
        "confidence": _confidence(settled),
        "oldest_open": oldest_open,
        "bets": items[:20],
        "basis": {
            "method": METHOD,
            "source": ["vkpi_bet_ledger"],
            "hit_definition": "已结算注里 outcome=won 的占比(void 作废剔除,open 不计分母)",
            "age_definition": "账龄=NOW-created_at(天);review_overdue=open 且 review_at 已过期(到期未复盘)",
            "scanned": total,
            "generated_at": _utcnow_iso(),
        },
    }


# ── 账② 预测军团(prediction_ledger,守卫 import)─────────────────────────


def _collect_predictions() -> dict[str, Any]:
    try:
        from app.domains.agents import prediction_ledger  # 守卫 import:唯一命中率真源,绝不重造
    except Exception as exc:  # noqa: BLE001 — 模块缺席诚实说,不编命中率
        return {
            "status": "unavailable",
            "reason": "prediction_ledger 模块不可用:" + str(exc)[:200],
            "groups": [],
            "basis": {"method": METHOD, "generated_at": _utcnow_iso()},
        }
    summary = prediction_ledger.ledger_summary()
    if str(summary.get("status") or "") != "ok":
        return {
            "status": "error",
            "reason": "ledger_summary 失败:" + _text(summary.get("reason"), 200),
            "groups": [],
            "basis": {"method": METHOD, "source": ["prediction_ledger.ledger_summary"], "generated_at": _utcnow_iso()},
        }
    groups_raw = summary.get("groups") or []
    selected: list[dict[str, Any]] = []
    for g in groups_raw:
        basis = g.get("basis") or {}
        selected.append({
            "action_type": g.get("action_type"),
            "label": g.get("label"),
            "status": g.get("status"),
            "hit_rate": g.get("hit_rate"),
            "sample_count": int(g.get("sample_count") or 0),
            "confidence": g.get("confidence"),
            "hits": int(basis.get("hits") or 0),
            "misses": int(basis.get("misses") or 0),
            "pending_count": int(basis.get("pending_count") or 0),
            "window": basis.get("window"),
        })
    # 精选排序:有样本的组(按样本量降序)优先,其次积压大的 pending 组(那本 777 条账浮上来)
    selected.sort(key=lambda g: (-(g["sample_count"]), -(g["pending_count"])))
    totals = summary.get("totals") or {}
    pending_total = int(totals.get("pending_total") or 0)
    backlog_top = max(selected, key=lambda g: g["pending_count"], default=None)
    return {
        "status": "ok",
        "groups": selected[:MAX_PREDICTION_GROUPS],
        "groups_total": len(groups_raw),
        "groups_with_sample": int(totals.get("groups_with_sample") or 0),
        "judged_total": int(totals.get("judged_total") or 0),
        "pending_total": pending_total,
        "backlog_top": (
            {"action_type": backlog_top["action_type"], "label": backlog_top["label"],
             "pending_count": backlog_top["pending_count"]}
            if backlog_top is not None and backlog_top["pending_count"] > 0 else None
        ),
        "basis": {
            "method": METHOD,
            "source": ["prediction_ledger.ledger_summary(window=近 " + str(summary.get("window") or "") + " 次)"],
            "pending_definition": "待对答案=有预测无结果(pending 不计命中率分母),积压即欠的答案",
            "selection": "按样本量降序取前 " + str(MAX_PREDICTION_GROUPS) + " 组;积压最大组单独点名",
            "generated_at": _utcnow_iso(),
        },
    }


# ── 账③ 履约战果(workflow runs + 观察窗口 + 内容帖)────────────────────


def _first_loop(conn: Any) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, workflow_name, status, current_step, created_at FROM vkpi_workflow_runs "
        "WHERE workflow_name = ? AND status = 'completed' ORDER BY id ASC LIMIT 1",
        (_FULFILLMENT_WORKFLOW,),
    ).fetchone()
    if not row:
        return None
    run = dict(row)
    steps = conn.execute(
        "SELECT step_index, step_name, status, started_at, finished_at FROM vkpi_workflow_steps "
        "WHERE run_id = ? ORDER BY step_index ASC",
        (int(run["id"]),),
    ).fetchall()
    return {
        "run_id": int(run["id"]),
        "workflow_name": run.get("workflow_name"),
        "status": run.get("status"),
        "created_at": _iso(run.get("created_at")),
        "steps": [
            {"step_index": dict(s).get("step_index"), "step_name": dict(s).get("step_name"),
             "status": dict(s).get("status"), "finished_at": _iso(dict(s).get("finished_at"))}
            for s in steps
        ],
    }


def _collect_fulfillment(conn: Any) -> dict[str, Any]:
    from app.db.connection import table_exists

    needed = ("vkpi_workflow_runs", "vkpi_project_content_observation_windows", "vkpi_project_content_posts")
    missing = [t for t in needed if not table_exists(t)]
    if missing:
        return {
            "status": "empty",
            "reason": "履约相关表缺席:" + ", ".join(missing),
            "loops_completed": 0,
            "basis": {"method": METHOD, "source": list(needed), "generated_at": _utcnow_iso()},
        }
    loops = dict(conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_workflow_runs WHERE workflow_name = ? AND status = 'completed'",
        (_FULFILLMENT_WORKFLOW,),
    ).fetchone() or {})
    loops_completed = int(loops.get("n") or 0)

    win_rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM vkpi_project_content_observation_windows GROUP BY status"
    ).fetchall()
    windows = {str(dict(r).get("status") or "unknown"): int(dict(r).get("n") or 0) for r in win_rows}
    post_rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM vkpi_project_content_posts GROUP BY status"
    ).fetchall()
    posts = {str(dict(r).get("status") or "unknown"): int(dict(r).get("n") or 0) for r in post_rows}
    confirmed_posts = int(posts.get("matched") or 0) + int(posts.get("retrospective_ready") or 0)

    # planned vs actual 样例:计划=观察窗口(starts_at~ends_at,因项目 target_post_date 全空,
    # 用真实开窗承诺当基线,不编计划);actual=内容帖发布时点+实际播放读数。
    sample_rows = conn.execute(
        "SELECT p.id AS post_id, p.project_id, p.kol_pool_id, p.platform, p.content_url, p.title, "
        "p.published_at, p.view_count, p.like_count, p.comment_count, p.match_confidence, "
        "p.status AS post_status, "
        "w.id AS window_id, w.starts_at AS window_starts_at, w.ends_at AS window_ends_at, "
        "w.scan_count AS window_scan_count, "
        "to_jsonb(pr.*)->>'project_name' AS project_name, "
        "to_jsonb(pr.*)->>'target_post_date' AS target_post_date "
        "FROM vkpi_project_content_posts p "
        "LEFT JOIN vkpi_project_content_observation_windows w ON w.matched_content_post_id = p.id "
        "LEFT JOIN vkpi_projects pr ON pr.id = p.project_id "
        "WHERE p.status IN ('matched','retrospective_ready') OR w.id IS NOT NULL "
        "ORDER BY CASE WHEN p.status = 'retrospective_ready' THEN 0 "
        "WHEN p.status = 'matched' THEN 1 ELSE 2 END, "
        "COALESCE(p.view_count, 0) DESC, p.id DESC LIMIT ?",
        (SCAN_LIMIT,),
    ).fetchall()
    samples: list[dict[str, Any]] = []
    max_actual_views = 0
    planned_dates_seen = 0
    for raw in sample_rows:
        row = dict(raw)
        views = int(_num(row.get("view_count")) or 0)
        max_actual_views = max(max_actual_views, views)
        if _text(row.get("target_post_date")):
            planned_dates_seen += 1
        if len(samples) >= MAX_PLAN_VS_ACTUAL_SAMPLES:
            continue
        published = _parse_ts(row.get("published_at"))
        starts = _parse_ts(row.get("window_starts_at"))
        ends = _parse_ts(row.get("window_ends_at"))
        within: bool | None = None
        if published is not None and starts is not None and ends is not None:
            within = bool(starts <= published <= ends)
        samples.append({
            "post_id": row.get("post_id"),
            "project_id": row.get("project_id"),
            "project_name": _text(row.get("project_name"), 80) or None,
            "kol_pool_id": row.get("kol_pool_id"),
            "post_status": _text(row.get("post_status"), 30),
            "platform": _text(row.get("platform"), 20) or None,
            "content_url": _text(row.get("content_url"), 300) or None,
            "planned": {
                "window_id": row.get("window_id"),
                "starts_at": _iso(row.get("window_starts_at")),
                "ends_at": _iso(row.get("window_ends_at")),
                "scan_count": row.get("window_scan_count"),
                "baseline": ("observation_window" if row.get("window_id") is not None
                             else "none_manual_entry"),
            },
            "actual": {
                "published_at": _iso(row.get("published_at")),
                "view_count": views,
                "like_count": int(_num(row.get("like_count")) or 0),
                "comment_count": int(_num(row.get("comment_count")) or 0),
            },
            "published_within_window": within,
            "match_confidence": _num(row.get("match_confidence")),
        })
    windows_total = sum(windows.values())
    return {
        "status": "ok" if loops_completed > 0 or windows_total > 0 else "empty",
        "reason": None if (loops_completed > 0 or windows_total > 0) else "履约账全空:无完成 loop、无观察窗口",
        "loops_completed": loops_completed,
        "first_loop": _first_loop(conn),
        "windows": {"total": windows_total,
                    "matched": int(windows.get("matched") or 0),
                    "scanning": int(windows.get("scanning") or 0),
                    "by_status": windows},
        "posts": {"confirmed": confirmed_posts,
                  "candidates": int(posts.get("candidate") or 0),
                  "by_status": posts},
        "plan_vs_actual": {
            "samples": samples,
            "sample_pool": len(sample_rows),
            "max_actual_views": max_actual_views,
            "planned_baseline": "observation_window",
            "planned_baseline_reason": (
                "vkpi_projects.target_post_date 有值 " + str(planned_dates_seen) + " 条(扫描池内)"
                if planned_dates_seen > 0
                else "项目 target_post_date 全空 → 计划基线用真实观察窗口(starts_at~ends_at)替代,不编计划日期"
            ),
        },
        "confidence": _confidence(confirmed_posts),
        "basis": {
            "method": METHOD,
            "source": ["vkpi_workflow_runs", "vkpi_workflow_steps",
                       "vkpi_project_content_observation_windows", "vkpi_project_content_posts", "vkpi_projects"],
            "loop_definition": "完成 loop=workflow_name=fulfillment_sweep 且 status=completed 的 durable run",
            "plan_vs_actual_definition": "planned=签收开出的观察窗口;actual=窗口匹配/人工确认内容帖的发布时点+播放读数",
            "confidence_basis": "置信度按已确认内容帖数(confirmed=" + str(confirmed_posts) + ")套样本阶梯",
            "generated_at": _utcnow_iso(),
        },
    }


# ── lessons:已沉淀教训 top5(只读真实留痕,绝不现编)──────────────────────


def _lessons_from_bets(bets_block: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for bet in bets_block.get("bets") or []:
        lesson = _text(bet.get("lesson"), 300)
        if not lesson or str(bet.get("outcome")) == "open":
            continue
        out.append({
            "text": lesson,
            "source": "bet_ledger",
            "ref": "bet:" + str(bet.get("bet_id")),
            "context": _text(bet.get("hypothesis"), 120) + "(" + str(bet.get("outcome")) + ")",
            "at": bet.get("created_at"),
        })
    return out


def _lessons_from_retrospectives(conn: Any) -> list[dict[str, Any]]:
    from app.db.connection import table_exists

    if not table_exists("vkpi_analysis_cache"):
        return []
    rows = conn.execute(
        "SELECT id, target_id, created_at, to_jsonb(t.*)->>'result' AS result_text "
        "FROM vkpi_analysis_cache t "
        "WHERE derive_method = ? AND target_type = 'project' AND status = 'ready' "
        "ORDER BY id DESC LIMIT 10",
        (_RETRO_METHOD,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        try:
            payload = json.loads(row.get("result_text") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        ref = "project:" + _text(row.get("target_id"), 20)
        at = _iso(row.get("created_at"))
        risks = payload.get("risks") or []
        next_steps = payload.get("next_steps") or []
        if isinstance(risks, list) and risks:
            out.append({"text": _text(risks[0], 300), "source": "project_retrospective",
                        "ref": ref, "context": "复盘风险", "at": at})
        if isinstance(next_steps, list) and next_steps:
            out.append({"text": _text(next_steps[0], 300), "source": "project_retrospective",
                        "ref": ref, "context": "复盘下一步", "at": at})
    return out


def _lessons_from_miss_review() -> list[dict[str, Any]]:
    try:
        from app.domains.learning import miss_review  # 守卫 import:失败原因聚类真源
    except Exception:  # noqa: BLE001 — 模块缺席安静跳过(其余教训照出)
        return []
    try:
        review = miss_review.miss_review_list()
    except Exception:  # noqa: BLE001
        return []
    if str(review.get("status") or "") != "ok":
        return []
    out: list[dict[str, Any]] = []
    for group in review.get("groups") or []:
        if str(group.get("status") or "") != "ready":
            continue
        reasons = group.get("reasons") or []
        if not reasons:
            continue
        top = reasons[0]
        samples = top.get("samples") or []
        sample_text = _text(samples[0], 100) if samples else ""
        text = (_text(group.get("label"), 40) + " 头号失败原因:" + _text(top.get("label"), 60)
                + " ×" + str(int(top.get("count") or 0))
                + ((",样本:" + sample_text) if sample_text else ""))
        out.append({
            "text": text,
            "source": "miss_review",
            "ref": "miss:" + _text(group.get("action_type"), 40),
            "context": "失败池词表聚类",
            "at": None,
        })
    return out


def _collect_lessons(conn: Any, bets_block: dict[str, Any]) -> dict[str, Any]:
    pool: list[dict[str, Any]] = []
    pool.extend(_lessons_from_bets(bets_block))
    pool.extend(_lessons_from_retrospectives(conn))
    pool.extend(_lessons_from_miss_review())
    items = pool[:MAX_LESSONS]
    return {
        "status": "ok" if items else "empty",
        "reason": None if items else "三处留痕(押注 lesson/项目复盘/失败池聚类)都还没沉淀出教训",
        "items": items,
        "pool_size": len(pool),
        "basis": {
            "method": METHOD,
            "source": ["vkpi_bet_ledger.lesson(已结算注)",
                       "vkpi_analysis_cache(" + _RETRO_METHOD + " 的 risks+next_steps)",
                       "miss_review 词表聚类(守卫 import)"],
            "selection": "按 押注复盘 → 项目复盘 → 失败池聚类 顺序取前 " + str(MAX_LESSONS) + " 条,只读已沉淀文本绝不现编",
            "generated_at": _utcnow_iso(),
        },
    }


# ── honesty_note:哪本账还在数据荒,直说──────────────────────────────────


def _build_honesty(bets: dict[str, Any], predictions: dict[str, Any], fulfillment: dict[str, Any]) -> dict[str, Any]:
    notes: list[str] = []
    settled = int(bets.get("settled") or 0)
    total_bets = int(bets.get("total") or 0)
    if total_bets == 0:
        notes.append("押注账:0 注,这本账还没开张。")
    elif settled < MIN_SAMPLE_FOR_CONFIDENCE:
        notes.append("押注账:全账仅 " + str(total_bets) + " 注、已结算 " + str(settled)
                     + " 注(<5),命中率无统计意义,只能逐注复盘。")
    pending_total = int(predictions.get("pending_total") or 0)
    judged_total = int(predictions.get("judged_total") or 0)
    if str(predictions.get("status")) != "ok":
        notes.append("预测账:台账不可用(" + _text(predictions.get("reason"), 120) + "),命中率缺席。")
    elif pending_total > 0:
        notes.append("预测账:待对答案积压 " + str(pending_total) + " 条(有预测无结果),已裁决仅 "
                     + str(judged_total) + " 条——命中率只代表已对过答案的那一小截。")
    pva = fulfillment.get("plan_vs_actual") or {}
    confirmed = int((fulfillment.get("posts") or {}).get("confirmed") or 0)
    if str(fulfillment.get("status")) != "ok":
        notes.append("履约账:" + _text(fulfillment.get("reason"), 120))
    else:
        if int(pva.get("max_actual_views") or 0) <= 0:
            notes.append("履约账:已确认内容帖 " + str(confirmed)
                         + " 条,但实际播放读数全为 0(未回填)——planned vs actual 目前只有发布时点可对,播放账仍在数据荒。")
        if "全空" in _text(pva.get("planned_baseline_reason"), 200):
            notes.append("履约账:项目未填 target_post_date,计划基线用观察窗口替代(诚实降级,不编计划日期)。")
    return {
        "items": notes,
        "note": "数据荒是常态:空账如实空、低样本如实标 insufficient;本板只用库内真数据,零外部行情、零 LLM。",
    }


# ── 对外入口 ─────────────────────────────────────────────────────────────


def performance() -> dict[str, Any]:
    """S4 实际战略表现看板:三账对齐(押注/预测/履约)+ 教训 top5 + 数据荒诚实条。

    契约:永不 raise;整体失败回 {status:"error"};单账失败该账 status="error" 其余照出。
    红线:纯读聚合,零写库、零 LLM、零触 viltrox_fit_score / rule_v0。
    """
    try:
        from app.db.connection import get_conn

        conn = get_conn()
        try:
            bets = _collect_bets(conn)
        except Exception as exc:  # noqa: BLE001
            bets = _error_block("bets", exc)
        try:
            predictions = _collect_predictions()
        except Exception as exc:  # noqa: BLE001
            predictions = _error_block("predictions", exc)
        try:
            fulfillment = _collect_fulfillment(conn)
        except Exception as exc:  # noqa: BLE001
            fulfillment = _error_block("fulfillment", exc)
        try:
            lessons = _collect_lessons(conn, bets)
        except Exception as exc:  # noqa: BLE001
            lessons = _error_block("lessons", exc)
        return {
            "status": "ok",
            "method": METHOD,
            "generated_at": _utcnow_iso(),
            "scoreboard": {"bets": bets, "predictions": predictions, "fulfillment": fulfillment},
            "lessons": lessons,
            "honesty_note": _build_honesty(bets, predictions, fulfillment),
            "note": "plan vs actual 三账对齐(只读聚合真实表,决定性零 LLM);数字全带 basis 可追溯;本板永不影响任何评分。",
        }
    except Exception as exc:  # noqa: BLE001 — 契约:永不 raise
        logger.warning("strategy_performance failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300], "generated_at": _utcnow_iso()}
