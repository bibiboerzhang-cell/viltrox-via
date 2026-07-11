"""市场之声「声量告警」(voice_alerts · V0f)——抱怨类别 × 时间窗负面声量阈值告警。

规则(用户已验收范式 board-paradigm V0f,2026-07-11):
  窗口   近 window_hours 小时(默认 8h)的 vkpi_comments;时间口径与
         market_voice._load_comments 同款:COALESCE(created_at, fetched_at)
         (实测部分行 created_at 为空,fetched_at 兜底,不丢声音)。
  判据   完全复用 market_voice 的 lexicon_v0 抱怨口径:话题词(COMPLAINT_CATEGORIES
         六类:对焦/画质/做工品控/价格/兼容性固件/体积重量)+ 负面线索词(NEGATIVE_CUES)
         同条命中才算负面提及——不自造分类,宁缺毋滥。
  权重   官号帖下的评论(vkpi_comments.post_table='vkpi_employee_channels')权重 ×2
         (OWNED_WEIGHT;官号评论区炸负面=更高优先级),其余 ×1;
         类别加权分 ≥ threshold(默认 3)触发。
  推送   触发项走「今日该做什么」现成通道:actions.producers.make_suggestion →
         actions.inbox.persist_suggestions → vkpi_action_inbox(category='voice_alert',
         纯提醒:无执行端点/不需审批/不写业务表);不做铃铛通知
         (无用户通知落表已核实,铃铛=后置加强项)。
  幂等   dedupe_key = 'voice_alert:{类别}:{UTC日期}' —— 同类别同日只落一行
         (vkpi_action_inbox.dedupe_key UNIQUE;ON CONFLICT 只刷新仍 'suggested' 的行,
         已被人工 dismiss/snooze/approve 的绝不复活)。

红线:纯读 vkpi_comments;唯一写 = vkpi_action_inbox 自身台账(走 inbox 现成入口,
非业务表);零 LLM / 零外调 / 零成本;零触评分域——绝不写 viltrox_fit_score、
不碰 rule_v0(本模块不产生任何评分,输出仅供「今日该做什么」提醒)。
显示层宪法对齐:引文样本不带 author_handle / author_id 等个人字段,溯源用 comment_id。

compat 约定:SQL 占位符用 ?;SQL 字符串零字面 percent(词表匹配全在 Python);
时间戳读回可能是 str;conn 可注入(测试),缺省懒 import get_conn。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.domains.market.market_voice import (
    COMPLAINT_CATEGORIES,
    NEGATIVE_CUES,
    VOICE_METHOD,
    _int0,
    _matches_any,
    _text,
)

logger = get_logger(__name__)

# ── 口径常量(测试直接断言)─────────────────────────────────────────────
WINDOW_HOURS_DEFAULT = 8      # 时间窗(小时)
THRESHOLD_DEFAULT = 3         # 触发阈值(加权分)
OWNED_POST_TABLE = "vkpi_employee_channels"  # 官号帖标记(voice_feed 同口径)
OWNED_WEIGHT = 2              # 官号帖评论权重
ALERT_CATEGORY = "voice_alert"  # vkpi_action_inbox.category(无 CHECK 约束,新类合法)
QUOTE_LIMIT = 3               # 每类别携带引文样本条数
SCAN_LIMIT = 5000             # 窗口内最多扫描条数(护栏,与 market_voice 同款)

# 全参数化;零字面 percent;窗口过滤下推 SQL(与 market_voice._load_comments 同口径)。
ALERT_SELECT_SQL = """
    SELECT id, platform, comment_text, post_table, likes_count,
           COALESCE(created_at, fetched_at) AS at_ts
    FROM vkpi_comments
    WHERE comment_text IS NOT NULL AND comment_text <> ''
      AND COALESCE(created_at, fetched_at) >= CAST(? AS TIMESTAMPTZ)
      AND COALESCE(created_at, fetched_at) < CAST(? AS TIMESTAMPTZ)
    ORDER BY likes_count DESC NULLS LAST, id DESC
    LIMIT ?
"""


def _now_utc(now: datetime | None = None) -> datetime:
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _load_window_docs(conn: Any, since_iso: str, until_iso: str) -> list[dict[str, Any]]:
    """窗口内评论 → 统一 doc(带 owned 标记与权重);纯读。"""
    rows = conn.execute(ALERT_SELECT_SQL, (since_iso, until_iso, SCAN_LIMIT)).fetchall()
    docs: list[dict[str, Any]] = []
    for r in rows:
        rec = dict(r)
        text = _text(rec.get("comment_text"), 500)
        if not text:
            continue
        owned = str(rec.get("post_table") or "").strip() == OWNED_POST_TABLE
        docs.append({
            "comment_id": _int0(rec.get("id")),
            "text": text,
            "lower": text.lower(),
            "platform": _text(rec.get("platform"), 30),
            "at": str(rec.get("at_ts") or "")[:19],
            "likes": _int0(rec.get("likes_count")),
            "owned": owned,
            "weight": OWNED_WEIGHT if owned else 1,
        })
    return docs


def _quote(doc: dict[str, Any]) -> dict[str, Any]:
    """引文样本(显示层宪法:零个人字段,溯源用 comment_id → vkpi_comments)。"""
    return {
        "comment_id": doc["comment_id"],
        "text": _text(doc.get("text"), 220),
        "platform": doc.get("platform") or "",
        "at": doc.get("at") or "",
        "likes": _int0(doc.get("likes")),
        "owned": bool(doc.get("owned")),
    }


def _top_quotes(docs: list[dict[str, Any]], n: int = QUOTE_LIMIT) -> list[dict[str, Any]]:
    """官号优先(权重高)→ 赞多 → 时间新(at 是 ISO 前缀串,字典序即时间序)。"""
    ranked = sorted(
        docs,
        key=lambda d: (int(d.get("weight") or 1), _int0(d.get("likes")), str(d.get("at") or "")),
        reverse=True,
    )
    return [_quote(d) for d in ranked[:n]]


def evaluate_voice_alerts(
    window_hours: int = WINDOW_HOURS_DEFAULT,
    threshold: int = THRESHOLD_DEFAULT,
    *,
    conn: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """纯读评估:窗口内 vkpi_comments 按 lexicon_v0 抱怨口径聚类,官号帖 ×2 加权,产出触发清单。

    返回 {status, method, window_hours, threshold, since, until, scanned,
          categories(全六类含 0 分), triggered(加权分≥阈值,按分降序)}。
    零写库、零 LLM;conn 可注入(测试),缺省懒取 get_conn。
    """
    from app.db.connection import get_conn

    window_hours = max(1, _int0(window_hours) or WINDOW_HOURS_DEFAULT)
    threshold = max(1, _int0(threshold) or THRESHOLD_DEFAULT)
    until_dt = _now_utc(now)
    since_dt = until_dt - timedelta(hours=window_hours)
    since_iso, until_iso = since_dt.isoformat(), until_dt.isoformat()

    db = conn or get_conn()
    docs = _load_window_docs(db, since_iso, until_iso)

    categories: list[dict[str, Any]] = []
    for key, label, topic_terms in COMPLAINT_CATEGORIES:
        # lexicon_v0 判据原样复用:话题词 + 负面线索词同条命中(宁缺毋滥)。
        hits = [
            d for d in docs
            if _matches_any(d["lower"], topic_terms) and _matches_any(d["lower"], NEGATIVE_CUES)
        ]
        score = sum(int(d["weight"]) for d in hits)
        owned_count = sum(1 for d in hits if d["owned"])
        categories.append({
            "key": key,
            "label": label,
            "score": score,
            "count": len(hits),
            "owned_count": owned_count,
            "triggered": score >= threshold,
            "quotes": _top_quotes(hits),
        })
    categories.sort(key=lambda c: -int(c["score"]))
    triggered = [c for c in categories if c["triggered"]]

    return {
        "status": "ok",
        "method": f"{VOICE_METHOD}+owned_x{OWNED_WEIGHT}",
        "window_hours": window_hours,
        "threshold": threshold,
        "since": since_iso,
        "until": until_iso,
        "scanned": len(docs),
        "categories": categories,
        "triggered": triggered,
    }


def _build_alert_suggestion(
    cat: dict[str, Any],
    *,
    window_hours: int,
    threshold: int,
    day: str,
    since_iso: str,
    until_iso: str,
) -> dict[str, Any]:
    """触发类别 → 「今日该做什么」建议(照 actions.producers.make_suggestion 现成口径)。"""
    from app.domains.actions.producers import make_suggestion

    key, label = str(cat["key"]), str(cat["label"])
    score, count, owned = int(cat["score"]), int(cat["count"]), int(cat["owned_count"])
    quotes = list(cat.get("quotes") or [])
    sample = f"样本:「{quotes[0]['text']}」" if quotes else ""
    return make_suggestion(
        category=ALERT_CATEGORY,
        # 幂等口径:同类别同日同 key → vkpi_action_inbox.dedupe_key UNIQUE 只落一行。
        dedupe_key=f"voice_alert:{key}:{day}",
        title=f"声量告警 · 「{label}」负面提及 {window_hours}h 加权 {score} 分(阈值 {threshold})",
        detail=(
            f"近 {window_hours}h「{label}」负面提及 {count} 条"
            f"(官号帖 {owned} 条×{OWNED_WEIGHT})加权 {score} 分 ≥ 阈值 {threshold}。{sample}"
        ),
        reason=(
            f"lexicon_v0 抱怨口径(话题词+负面线索同条命中);官号帖评论权重×{OWNED_WEIGHT};"
            "纯提醒——不自动执行任何动作,不做铃铛通知。"
        ),
        priority="high",
        entity_type="voice_category",
        entity_id=key,
        suggested_endpoint="",          # 纯提醒,不给执行端点
        estimated_cost_cents=0,
        writes_business_data=False,     # 硬约束:不写业务表
        uses_llm=False,
        requires_approval=False,        # 纯提醒(与 project_shared_to_you 同款)
        owner_staff_id=None,            # 公司级(管理层可见)
        payload={
            "window_hours": window_hours,
            "threshold": threshold,
            "score": score,
            "count": count,
            "owned_count": owned,
            "owned_weight": OWNED_WEIGHT,
            "method": f"{VOICE_METHOD}+owned_x{OWNED_WEIGHT}",
            "since": since_iso,
            "until": until_iso,
            "quotes": quotes,
        },
        expected_gain="及时响应负面声量(转回复队列/转产品部),避免口碑扩散",
        risk_level="low",
        evidence_refs=[{"type": "vkpi_comments", "id": str(q["comment_id"])} for q in quotes],
        verification_plan=["人工核对样本原声是否确为负面", "处理后同类别声量回落即闭环"],
        affected_tables=[],             # 纯提醒不改任何业务表
    )


def push_voice_alerts(
    window_hours: int = WINDOW_HOURS_DEFAULT,
    threshold: int = THRESHOLD_DEFAULT,
    *,
    conn: Any = None,
    now: datetime | None = None,
    evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """评估 + 把触发项推成「今日该做什么」条目(vkpi_action_inbox,现成 actions 通道)。

    幂等:dedupe_key = 'voice_alert:{类别}:{UTC日期}' —— 同类别同日不重复推
    (UNIQUE + ON CONFLICT 只刷新仍 'suggested' 的行,人工处理过的不复活)。
    唯一写 = inbox.persist_suggestions → vkpi_action_inbox;零业务表、零 LLM、零评分域。
    """
    result = evaluation or evaluate_voice_alerts(window_hours, threshold, conn=conn, now=now)
    triggered = list(result.get("triggered") or [])
    day = _now_utc(now).date().isoformat()

    pushed = 0
    if triggered:
        from app.domains.actions import inbox

        suggestions = [
            _build_alert_suggestion(
                cat,
                window_hours=int(result.get("window_hours") or window_hours),
                threshold=int(result.get("threshold") or threshold),
                day=day,
                since_iso=str(result.get("since") or ""),
                until_iso=str(result.get("until") or ""),
            )
            for cat in triggered
        ]
        pushed = inbox.persist_suggestions(suggestions)

    return {
        "status": "ok",
        "method": result.get("method"),
        "window_hours": result.get("window_hours"),
        "threshold": result.get("threshold"),
        "day": day,
        "scanned": result.get("scanned"),
        "triggered": len(triggered),
        "triggered_categories": [str(c.get("key") or "") for c in triggered],
        "pushed": pushed,
    }
