"""Agent 自治驾照制度(autonomy_license v1,件A)—— 挣来的自治 L0-L4。

作战地图廿一节 v2:每类 agent 动作持一张驾照(vkpi_autonomy_licenses,迁移212),
级别靠预测台账命中率挣、靠连续失手降,绝不一步到位放权:
  L0 观察 / L1 建议 / L2 内部执行 / L3 半自主 / L4 全自主(仅人工授予,绝不自动晋升)。

升降规则(全部纯规则判定,零 LLM):
  升:近 20 次命中率 >= 0.85 且样本 >= 20 → 升 1 级(自动晋升封顶 L3;L4 只能人工 override);
  降:连续 5 次未命中 → 降 1 级(保底 L0);
  hold:台账缺席 / 样本不足 / 命中率不够 → 原地不动并说明原因,绝不瞎升。

台账数据走守卫 import 兄弟件D(app.domains.agents.prediction_ledger,契约
hit_rate_for(action_type, window=20) -> dict 键 status/hit_rate/sample_count/confidence/basis);
模块缺席或异常 → 诚实 hold,不杜撰命中率。连续未命中数优先用台账可选接口,
其次从「窗口内命中率为 0」安全推断(窗口内全 miss 则最近 sample 次必为连续 miss),推不出则不降。

dimensions 五维能力布尔:write_db / call_llm / spend_money / contact_external / change_project_status。
红线:「影响评分」维度(affect_scoring)硬编码永久 False —— 任何级别(含 L4)、任何路径
(自动升降 / 人工 override / API)都不允许翻真;agent 永远无权触碰 viltrox_fit_score 与
rule_v0 打分逻辑。本模块只建判定与展示,绝不真执行任何外部动作(发邮件/花钱/写第三方)。

compat 约定:SQL 占位符用 ?;SQL 零 percent 字符(不用 LIKE);BOOLEAN/JSONB 读回
双态容错(_truthy / _loads);函数内懒 import get_conn;表未建诚实降级不炸。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

TABLE = "vkpi_autonomy_licenses"

LEVEL_MIN = 0
LEVEL_MAX = 4
AUTO_PROMOTE_CEILING = 3  # L4 本轮绝不自动授予,只能人工 override
PROMOTE_HIT_RATE = 0.85
PROMOTE_MIN_SAMPLE = 20
DEMOTE_MISS_STREAK = 5
LEDGER_WINDOW = 20

LEVEL_LABELS: dict[int, str] = {
    0: "观察",
    1: "建议",
    2: "内部执行",
    3: "半自主",
    4: "全自主",
}

DIMENSION_KEYS: tuple[str, ...] = (
    "write_db",
    "call_llm",
    "spend_money",
    "contact_external",
    "change_project_status",
)

# 红线:「影响评分」维度永久 False。任何级别、任何调级路径(自动升降/人工 override/API)
# 都不允许把它翻真 —— 驾照永不授予 agent 改 viltrox_fit_score / rule_v0 评分的能力。
FORBIDDEN_DIMENSION = "affect_scoring"

# 各级别授予的能力维度(累进);FORBIDDEN_DIMENSION 永不出现在任何级别的授予名单里。
_LEVEL_GRANTS: dict[int, tuple[str, ...]] = {
    0: (),
    1: ("write_db",),
    2: ("write_db", "call_llm"),
    3: ("write_db", "call_llm", "spend_money", "change_project_status"),
    4: ("write_db", "call_llm", "spend_money", "change_project_status", "contact_external"),
}


# ── 小工具(容错解析,与 signature_profile 同款口径)─────────────────


from app.core.coerce import _truthy


def _loads(value: Any) -> Any:
    """JSONB 经 compat 层可能回 dict 也可能回 str,双态容错。"""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _clamp_level(value: Any) -> int:
    level = _int_or_none(value)
    if level is None:
        return LEVEL_MIN
    return max(LEVEL_MIN, min(LEVEL_MAX, level))


def _safe_rollback(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 — 回滚失败不再连环炸
        logger.debug("回滚失败(best-effort)", exc_info=True)


def _dimensions_for_level(level: int) -> dict[str, bool]:
    """级别 → 五维能力布尔;affect_scoring 永久 False(红线,不随级别变)。"""
    grants = set(_LEVEL_GRANTS.get(_clamp_level(level), ()))
    dims = {key: (key in grants) for key in DIMENSION_KEYS}
    dims[FORBIDDEN_DIMENSION] = False  # 永久 False,任何路径不可翻真
    return dims


def _sanitize_dimensions(raw: Any, level: int) -> dict[str, bool]:
    """DB 读回的 dimensions 容错归一;空/坏则按级别推;affect_scoring 强制 False。"""
    parsed = _loads(raw)
    if not isinstance(parsed, dict) or not parsed:
        return _dimensions_for_level(level)
    dims = {key: _truthy(parsed.get(key)) for key in DIMENSION_KEYS}
    dims[FORBIDDEN_DIMENSION] = False  # 无论库里存了什么,读出必为 False(红线)
    return dims


# ── 台账读数(守卫 import 兄弟件D,缺席诚实降级)──────────────────────


def _ledger_hit_rate(action_type: str, window: int = LEDGER_WINDOW) -> dict[str, Any]:
    """prediction_ledger.hit_rate_for 守卫读数;模块缺席/异常回 unavailable,绝不杜撰。"""
    try:
        from app.domains.agents import prediction_ledger
    except ImportError:
        return {
            "status": "unavailable",
            "reason": "prediction_ledger 台账模块缺席(兄弟件D未落地),无命中率可依,按 hold 处理",
        }
    try:
        result = prediction_ledger.hit_rate_for(action_type, window=window)
        if isinstance(result, dict):
            return result
        return {"status": "error", "reason": "hit_rate_for 返回非 dict,台账读数不可用"}
    except Exception as exc:  # noqa: BLE001 — 台账内部炸不连累驾照接口
        logger.warning("prediction_ledger.hit_rate_for failed for %s: %s", action_type, exc)
        return {"status": "error", "reason": f"台账读数异常: {str(exc)[:200]}"}


def _consecutive_miss_streak(action_type: str, ledger: dict[str, Any]) -> tuple[int | None, str]:
    """最近连续未命中次数;台账可选接口优先,其次零命中率安全推断,推不出诚实 None。"""
    try:
        from app.domains.agents import prediction_ledger

        for name in ("consecutive_miss_streak", "miss_streak_for"):
            fn = getattr(prediction_ledger, name, None)
            if callable(fn):
                try:
                    streak = _int_or_none(fn(action_type))
                    if streak is not None and streak >= 0:
                        return streak, f"ledger_api:{name}"
                except Exception:  # noqa: BLE001 — 可选接口炸则退推断路径
                    logger.debug("ledger 可选接口读取失败,退推断路径(best-effort)", exc_info=True)
    except ImportError:
        pass
    # 安全推断:窗口内命中率为 0 且有样本 → 窗口内全 miss → 最近 sample 次必为连续 miss。
    hit_rate = _float_or_none(ledger.get("hit_rate"))
    sample = _int_or_none(ledger.get("sample_count"))
    if hit_rate is not None and sample is not None and sample > 0 and hit_rate == 0.0:
        return sample, "inferred_zero_hit_rate"
    return None, "unknown"


# ── 升降判定(纯规则,零 LLM)────────────────────────────────────────


def _decide(level: int, ledger: dict[str, Any], streak: int | None, streak_basis: str) -> dict[str, Any]:
    """单张驾照的升/降/hold 判定;只出建议,不落库(落库由 evaluate_promotions 控制)。"""
    hit_rate = _float_or_none(ledger.get("hit_rate"))
    sample = _int_or_none(ledger.get("sample_count"))
    base = {
        "hit_rate": hit_rate,
        "sample_count": sample,
        "ledger_status": str(ledger.get("status") or ""),
        "ledger_basis": ledger.get("basis"),
        "miss_streak": streak,
        "miss_streak_basis": streak_basis,
    }

    # 降级优先:连续 5 次未命中 → 降 1 级(保底 L0)。
    if streak is not None and streak >= DEMOTE_MISS_STREAK:
        if level <= LEVEL_MIN:
            return {
                **base,
                "decision": "hold",
                "proposed_level": LEVEL_MIN,
                "reason": f"连续 {streak} 次未命中但已在保底 L0,无级可降",
            }
        return {
            **base,
            "decision": "demote",
            "proposed_level": level - 1,
            "reason": f"连续 {streak} 次未命中(判据 {streak_basis}),降 1 级",
        }

    if hit_rate is None or sample is None:
        return {
            **base,
            "decision": "hold",
            "proposed_level": level,
            "reason": str(ledger.get("reason") or "台账无可用命中率读数,按 hold 处理"),
        }

    if sample < PROMOTE_MIN_SAMPLE:
        return {
            **base,
            "decision": "hold",
            "proposed_level": level,
            "reason": f"样本不足({sample}/{PROMOTE_MIN_SAMPLE}),继续观察不动级",
        }

    if hit_rate >= PROMOTE_HIT_RATE:
        if level >= LEVEL_MAX:
            return {**base, "decision": "hold", "proposed_level": level, "reason": "已是 L4 顶格"}
        if level >= AUTO_PROMOTE_CEILING:
            return {
                **base,
                "decision": "hold",
                "proposed_level": level,
                "reason": f"命中率 {hit_rate:.2f} 达标但已到自动晋升封顶 L{AUTO_PROMOTE_CEILING},L4 仅人工 override 授予",
            }
        return {
            **base,
            "decision": "promote",
            "proposed_level": level + 1,
            "reason": f"近 {min(sample, LEDGER_WINDOW)} 次命中率 {hit_rate:.2f} >= {PROMOTE_HIT_RATE} 且样本 {sample} >= {PROMOTE_MIN_SAMPLE},升 1 级",
        }

    return {
        **base,
        "decision": "hold",
        "proposed_level": level,
        "reason": f"命中率 {hit_rate:.2f} 低于晋升线 {PROMOTE_HIT_RATE},不动级",
    }


# ── DB 读写 ─────────────────────────────────────────────────────────

_SELECT_COLS = (
    "id, action_type, level, dimensions, hit_rate_snapshot, sample_count, "
    "last_change_reason, changed_at, created_at"
)


def _present(row: dict[str, Any]) -> dict[str, Any]:
    level = _clamp_level(row.get("level"))
    return {
        "action_type": str(row.get("action_type") or ""),
        "level": level,
        "level_label": LEVEL_LABELS.get(level, str(level)),
        "dimensions": _sanitize_dimensions(row.get("dimensions"), level),
        "hit_rate_snapshot": _float_or_none(row.get("hit_rate_snapshot")),
        "sample_count": _int_or_none(row.get("sample_count")) or 0,
        "last_change_reason": str(row.get("last_change_reason") or ""),
        "changed_at": _iso(row.get("changed_at")),
        "created_at": _iso(row.get("created_at")),
    }


def _rules_block() -> dict[str, Any]:
    return {
        "promote": f"近{LEDGER_WINDOW}次命中率 >= {PROMOTE_HIT_RATE} 且样本 >= {PROMOTE_MIN_SAMPLE} → 升1级(自动封顶 L{AUTO_PROMOTE_CEILING},L4 仅人工)",
        "demote": f"连续 {DEMOTE_MISS_STREAK} 次未命中 → 降1级(保底 L{LEVEL_MIN})",
        "hold": "台账缺席 / 样本不足 / 命中率不够 → 原地不动并说明",
        "forbidden_dimension": f"{FORBIDDEN_DIMENSION}(影响评分)永久 False,不可经任何路径修改",
    }


# ── 对外契约 ─────────────────────────────────────────────────────────


def current_level(action_type: str) -> dict[str, Any]:
    """某动作类型的当前驾照:级别 + 五维能力;无记录/表未建按 L0 观察诚实降级。"""
    from app.db.connection import get_conn

    action = str(action_type or "").strip()
    fallback = {
        "action_type": action,
        "level": LEVEL_MIN,
        "level_label": LEVEL_LABELS[LEVEL_MIN],
        "dimensions": _dimensions_for_level(LEVEL_MIN),
        "hit_rate_snapshot": None,
        "sample_count": 0,
        "last_change_reason": "",
        "changed_at": None,
    }
    if not action:
        return {"status": "error", "reason": "action_type 为空", **fallback}

    conn = get_conn()
    try:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM {TABLE} WHERE action_type = ?",
            (action,),
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 — 表未建(迁移212未apply)诚实降级
        _safe_rollback(conn)
        logger.warning("current_level read failed for %s: %s", action, exc)
        return {
            "status": "empty",
            "reason": f"驾照表不可读(迁移212未apply或异常): {str(exc)[:200]}",
            **fallback,
        }
    if not row:
        return {"status": "empty", "reason": "该动作类型无驾照记录,按 L0 观察处理", **fallback}
    return {"status": "ready", **_present(dict(row))}


def list_licenses() -> dict[str, Any]:
    """全部驾照 + 各自台账实时读数(守卫);面板主读数,全只读。"""
    from app.db.connection import get_conn

    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM {TABLE} ORDER BY action_type"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — 表未建诚实空态
        _safe_rollback(conn)
        logger.warning("list_licenses read failed: %s", exc)
        return {
            "status": "empty",
            "reason": f"驾照表不可读(迁移212未apply或异常): {str(exc)[:200]}",
            "items": [],
            "rules": _rules_block(),
        }

    items: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        item = _present(row)
        item["ledger"] = _ledger_hit_rate(item["action_type"], LEDGER_WINDOW)
        items.append(item)
    return {
        "status": "ready",
        "items": items,
        "rules": _rules_block(),
        "levels": {str(k): v for k, v in LEVEL_LABELS.items()},
        "note": "驾照只回答「许不许」;本轮只建判定与展示,不真执行任何外部动作。影响评分维度永久禁止。",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def evaluate_promotions(dry_run: bool = True) -> dict[str, Any]:
    """全量驾照升降评估:dry_run=True 只出建议;dry_run=False 才把 promote/demote 落库。"""
    from app.db.connection import get_conn

    conn = get_conn()
    try:
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM {TABLE} ORDER BY action_type"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — 表未建诚实空态
        _safe_rollback(conn)
        return {
            "status": "empty",
            "dry_run": bool(dry_run),
            "reason": f"驾照表不可读(迁移212未apply或异常): {str(exc)[:200]}",
            "items": [],
        }

    items: list[dict[str, Any]] = []
    applied_count = 0
    for raw in rows:
        row = dict(raw)
        action = str(row.get("action_type") or "")
        level = _clamp_level(row.get("level"))
        ledger = _ledger_hit_rate(action, LEDGER_WINDOW)
        streak, streak_basis = _consecutive_miss_streak(action, ledger)
        decision = _decide(level, ledger, streak, streak_basis)
        item: dict[str, Any] = {"action_type": action, "current_level": level, **decision, "applied": False}

        if not dry_run and decision["decision"] in ("promote", "demote"):
            new_level = _clamp_level(decision["proposed_level"])
            reason_text = f"auto_{decision['decision']}: {decision['reason']}"[:300]
            try:
                conn.execute(
                    f"""
                    UPDATE {TABLE}
                       SET level = ?,
                           dimensions = CAST(? AS JSONB),
                           hit_rate_snapshot = ?,
                           sample_count = ?,
                           last_change_reason = ?,
                           changed_at = NOW()
                     WHERE action_type = ?
                    """,
                    (
                        new_level,
                        json.dumps(_dimensions_for_level(new_level)),
                        decision.get("hit_rate"),
                        decision.get("sample_count") or 0,
                        reason_text,
                        action,
                    ),
                )
                conn.commit()
                item["applied"] = True
                applied_count += 1
            except Exception as exc:  # noqa: BLE001 — 单条落库失败不连累其余判定
                _safe_rollback(conn)
                logger.warning("apply license change failed for %s: %s", action, exc)
                item["apply_error"] = str(exc)[:200]
        items.append(item)

    return {
        "status": "ready",
        "dry_run": bool(dry_run),
        "window": LEDGER_WINDOW,
        "rules": _rules_block(),
        "items": items,
        "applied_count": applied_count,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "note": "dry_run=true 只出建议不落库;判定纯规则零 LLM;L4 永不自动授予;影响评分维度永久禁止。",
    }


def manual_override(
    action_type: str,
    level: Any,
    reason: str,
    staff_id: int | None = None,
) -> dict[str, Any]:
    """人工调级(必须带 reason):唯一能到 L4 的路径;dimensions 按级别重推,
    affect_scoring 依旧永久 False(红线:override 也改不了这条)。校验失败抛 ValueError。"""
    from app.db.connection import get_conn

    action = str(action_type or "").strip()
    if not action:
        raise ValueError("action_type 为空")
    new_level = _int_or_none(level)
    if new_level is None or not (LEVEL_MIN <= new_level <= LEVEL_MAX):
        raise ValueError(f"level 必须是 {LEVEL_MIN}-{LEVEL_MAX} 整数")
    why = " ".join(str(reason or "").split())[:280]
    if not why:
        raise ValueError("人工调级必须写 reason(为什么调、依据什么)")

    change_reason = f"manual_override(staff={staff_id if staff_id is not None else 'unknown'}): {why}"[:300]
    dims_json = json.dumps(_dimensions_for_level(new_level))

    conn = get_conn()
    previous_level: int | None = None
    try:
        existing = conn.execute(
            f"SELECT id, level FROM {TABLE} WHERE action_type = ?",
            (action,),
        ).fetchone()
        if existing:
            previous_level = _clamp_level(dict(existing).get("level"))
            conn.execute(
                f"""
                UPDATE {TABLE}
                   SET level = ?,
                       dimensions = CAST(? AS JSONB),
                       last_change_reason = ?,
                       changed_at = NOW()
                 WHERE action_type = ?
                """,
                (new_level, dims_json, change_reason, action),
            )
        else:
            conn.execute(
                f"""
                INSERT INTO {TABLE}
                    (action_type, level, dimensions, sample_count, last_change_reason)
                VALUES (?, ?, CAST(? AS JSONB), 0, ?)
                """,
                (action, new_level, dims_json, change_reason),
            )
        conn.commit()
    except Exception:
        _safe_rollback(conn)
        raise

    result = current_level(action)
    result["override"] = True
    result["previous_level"] = previous_level
    return result


def license_status_snapshot() -> dict[str, Any]:
    """纯读驾照现状快照(2026-08-30 点火件③,日报/面板消费):每张驾照
    level/sample_count/hit_rate + 离晋升门槛还差多少(样本差/命中率差/封顶原因)。

    只读不写、零 LLM、永不 raise;台账读数守卫(list_licenses 内已带),数据缺席诚实
    None 绝不编数。promotion_ready=True 也只意味着「每日评估会产建议」——晋升永远走人审。
    """
    try:
        listing = list_licenses()
    except Exception as exc:  # noqa: BLE001 — 契约:永不 raise
        logger.warning("license_status_snapshot failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300], "items": []}

    items: list[dict[str, Any]] = []
    for raw in listing.get("items") or []:
        ledger = raw.get("ledger") if isinstance(raw.get("ledger"), dict) else {}
        hit_rate = _float_or_none(ledger.get("hit_rate"))
        sample = _int_or_none(ledger.get("sample_count")) or 0
        level = _clamp_level(raw.get("level"))
        samples_needed = max(0, PROMOTE_MIN_SAMPLE - sample)
        hit_rate_gap = None if hit_rate is None else round(max(0.0, PROMOTE_HIT_RATE - hit_rate), 4)
        if level >= AUTO_PROMOTE_CEILING:
            blocked = f"已到自动晋升封顶 L{AUTO_PROMOTE_CEILING},L4 仅人工 override"
        elif str(ledger.get("status") or "") != "ok" or hit_rate is None:
            blocked = f"台账无可用命中率读数(status={str(ledger.get('status') or 'unavailable')}),按 hold"
        elif samples_needed > 0:
            blocked = f"样本还差 {samples_needed} 个(现 {sample}/{PROMOTE_MIN_SAMPLE})"
        elif hit_rate < PROMOTE_HIT_RATE:
            blocked = f"命中率还差 {hit_rate_gap:.4f}(现 {hit_rate:.4f}/晋升线 {PROMOTE_HIT_RATE})"
        else:
            blocked = ""  # 门槛已达标:等每日评估产 inbox 建议 → 人审 → override 端点执行
        items.append(
            {
                "action_type": str(raw.get("action_type") or ""),
                "level": level,
                "level_label": LEVEL_LABELS.get(level, str(level)),
                "sample_count": sample,
                "hit_rate": hit_rate,
                "ledger_status": str(ledger.get("status") or ""),
                "promotion_gap": {
                    "samples_needed": samples_needed,
                    "hit_rate_gap": hit_rate_gap,
                    "threshold": {"hit_rate": PROMOTE_HIT_RATE, "min_sample": PROMOTE_MIN_SAMPLE},
                },
                "promotion_blocked_reason": blocked,
                "promotion_ready": blocked == "",
            }
        )
    return {
        "status": str(listing.get("status") or "ready"),
        "reason": str(listing.get("reason") or "") or None,
        "items": items,
        "rules": _rules_block(),
        "note": "纯读快照;晋升永远走人审(inbox 建议→人批→override 端点),绝无自我提权;影响评分维度永久禁止。",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_license_promotion_scan() -> dict[str, Any]:
    """每日驾照晋升评估 job 入口(2026-08-30 点火件②;注册与 env 闸见
    services/scheduler/jobs_license_promotion.py,默认 OFF)。

    恒 dry_run=True:evaluate_promotions 只出建议、本函数绝不落库改级;promote/demote
    建议幂等写 vkpi_action_inbox(category=license_promotion,dedupe_key 含级别迁移,
    人工 dismissed/snoozed/approved 的行绝不复活),人审通过后由既有端点
    POST /api/admin/vkpi/autonomy/licenses/{action_type}/override 执行。
    红线:晋升永远走人审,绝无自我提权;L4 永不自动授予;零 LLM;失败诚实回报不炸调度器。
    """
    report = evaluate_promotions(dry_run=True)
    items = (report.get("items") or []) if isinstance(report, dict) else []
    proposals = [it for it in items if isinstance(it, dict) and it.get("decision") in ("promote", "demote")]
    persisted = 0
    persist_error = ""
    if proposals:
        try:
            from app.domains.actions.inbox import persist_suggestions
            from app.domains.actions.producers import make_suggestion

            suggestions: list[dict[str, Any]] = []
            for it in proposals:
                action = str(it.get("action_type") or "").strip()
                if not action:
                    continue
                verb = "晋升" if it.get("decision") == "promote" else "降级"
                cur = _clamp_level(it.get("current_level"))
                proposed = _clamp_level(it.get("proposed_level"))
                suggestions.append(
                    make_suggestion(
                        category="license_promotion",
                        dedupe_key=f"license_promotion:{action}:L{cur}toL{proposed}",
                        title=f"驾照{verb}建议:{action} L{cur}→L{proposed}",
                        detail=str(it.get("reason") or "")[:500],
                        reason=(
                            f"prediction_ledger 纯规则判定(hit_rate={it.get('hit_rate')}, "
                            f"sample={it.get('sample_count')}, miss_streak={it.get('miss_streak')});"
                            "人审通过才执行,绝无自我提权"
                        ),
                        priority="medium",
                        entity_type="autonomy_license",
                        entity_id=action,
                        suggested_endpoint=f"/api/admin/vkpi/autonomy/licenses/{action}/override",
                        requires_approval=True,
                        payload={
                            "decision": it.get("decision"),
                            "current_level": cur,
                            "proposed_level": proposed,
                            "hit_rate": it.get("hit_rate"),
                            "sample_count": it.get("sample_count"),
                            "miss_streak": it.get("miss_streak"),
                            "evaluated_at": report.get("evaluated_at"),
                        },
                    )
                )
            persisted = persist_suggestions(suggestions)
        except Exception as exc:  # noqa: BLE001 — 建议落格失败只记账,不拖垮调度器
            logger.warning("license promotion suggestions persist failed: %s", exc)
            persist_error = str(exc)[:200]
    result: dict[str, Any] = {
        "status": str(report.get("status") or "error") if isinstance(report, dict) else "error",
        "dry_run": True,
        "evaluated": len(items),
        "proposals": len(proposals),
        "persisted": persisted,
        "inbox_category": "license_promotion",
        "note": "晋升永远走人审:建议进 vkpi_action_inbox,人批后经 override 端点执行;本 job 绝不改级。",
    }
    if persist_error:
        result["persist_error"] = persist_error
    return result
