"""MY KOL 每日学习摘要(daily_digest v1)—— 收藏 KOL + 公司官号「这段时间发生了什么」。

五块(全部纯聚合已有数据,零新采集、零 LLM):
  🆕 new_videos       新视频:MY KOL 集合内窗口期新增的 evidence(created_at 落窗);
  📈 view_shift       播放异动(环比):按「发布日期」比较 当期 vs 前一期 发布视频的播放合计
                      (method=posted_window_v1,老视频播放有更长累积时间,口径如实标注);
                      附 followers 环比(vkpi_kol_fit_snapshot 日快照,真时间序列);
  💬 viltrox_mentions 提及 Viltrox 的新内容:窗口期新增 evidence 标题/频道名词表匹配
                      (lexicon_v1:viltrox/唯卓仕/唯卓,宁缺毋滥);
  📇 new_contacts     联系方式新获得:vkpi_kol_pool_contacts 窗口期新增(只出类型/来源计数,
                      绝不在摘要里裸露联系方式明文,明文走既有 contact_reveal 门控);
  🏢 official         公司官号:vkpi_channel_metrics 最近一天日快照(需落窗)聚合
                      粉丝/播放/发帖 delta + vkpi_channel_post_metrics 出「最好一条」。

MY KOL 集合口径完全对齐 my_kol_aggregate._pool_favorites:本人收藏(vkpi_kol_pool_favorites)
∪ 共享给本人(vkpi_kol_pool_members),staff_id=0/None 表示管理层全量视角(全员并集)。

诚实态:每块缺数据返回 {status:"empty", reason:...}(本地快照断更时如实报最近可用日期),
绝不外推杜撰;单块内部异常回 {status:"error"} 不炸整个摘要。

compat 约定:SQL 占位符用 ?;SQL 禁 percent 字符(不用 LIKE,词表匹配在 Python 做);
BOOLEAN 读回可能是 int 1/0,判断走 _truthy;日期参数一律传 ISO 字符串。函数内懒 import get_conn。
红线:纯读聚合,绝不写库;零触 viltrox_fit_score(fit_snapshot 只取 followers 列)、不碰 rule_v0。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 提及词表(小写子串;保守宁缺毋滥,识别不了不硬算「提及」)
VILTROX_TERMS: tuple[str, ...] = ("viltrox", "唯卓仕", "唯卓")

_MAX_ITEMS = 8

# MY KOL 集合子查询(口径=my_kol_aggregate._pool_favorites:收藏 ∪ 共享;0=全员并集)。
# 占位符 4 个,一律绑 (sid, sid, sid, sid)。
_MYSET_SUBQUERY = """
        SELECT DISTINCT kp0.id AS kol_pool_id
        FROM vkpi_kol_pool kp0
        LEFT JOIN vkpi_kol_pool_favorites f0
               ON f0.kol_pool_id = kp0.id AND (? = 0 OR f0.staff_id = ?)
        LEFT JOIN vkpi_kol_pool_members sm0
               ON sm0.kol_pool_id = kp0.id AND (? = 0 OR sm0.staff_id = ?)
        WHERE (f0.id IS NOT NULL OR sm0.id IS NOT NULL)
          AND kp0.duplicate_of_id IS NULL
"""


# ── 小工具(容错;compat 读回宽容)────────────────────────────────────


from app.core.coerce import _truthy


def _active(value: Any) -> bool:
    """evidence.is_active:NULL 视为有效(历史行未回填),显式 falsy 才剔除。"""
    return True if value is None else _truthy(value)


def _int0(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _day(value: Any) -> str | None:
    text = _iso(value)
    return text[:10] if text else None


def _text(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _pct(cur: int, prev: int) -> float | None:
    """环比百分比;基期为 0 诚实返回 None(不除零、不编「+∞」)。"""
    if prev <= 0:
        return None
    return round((cur - prev) * 100.0 / prev, 1)


def _kol_brief(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "kol_pool_id": _int0(row.get("kol_pool_id")),
        "handle": _text(row.get("handle"), 80),
        "display_name": _text(row.get("display_name"), 80),
        "platform": _text(row.get("platform"), 24).lower(),
    }


def _myset_params(staff_id: int) -> tuple[int, int, int, int]:
    sid = _int0(staff_id)
    return (sid, sid, sid, sid)


# ── 集合基数 ─────────────────────────────────────────────────────────


def _myset_count(conn: Any, sid: int) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM ({_MYSET_SUBQUERY}) ms",  # noqa: S608 — 常量子查询,参数全绑定
        _myset_params(sid),
    ).fetchone()
    return _int0(dict(row).get("c")) if row else 0


# ── 🆕 块一:新视频(evidence 新增)──────────────────────────────────


def _new_videos(conn: Any, sid: int, since: str) -> dict[str, Any]:
    rows = conn.execute(
        f"""
        SELECT e.id AS evidence_id, e.kol_pool_id,
               COALESCE(e.video_title, e.title, '') AS title,
               e.content_url, e.media_kind, e.view_count, e.like_count, e.comment_count,
               e.is_active, e.created_at,
               COALESCE(DATE(e.publish_date), e.posted_at) AS posted_day,
               kp.handle, kp.display_name, kp.platform
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool kp ON kp.id = e.kol_pool_id
        JOIN ({_MYSET_SUBQUERY}) ms ON ms.kol_pool_id = e.kol_pool_id
        WHERE DATE(e.created_at) >= ?
        ORDER BY COALESCE(e.view_count, 0) DESC, e.id DESC
        LIMIT 400
        """,  # noqa: S608 — 常量子查询,参数全绑定
        (*_myset_params(sid), since),
    ).fetchall()
    items = [dict(r) for r in rows if _active(dict(r).get("is_active"))]
    if not items:
        return {"status": "empty", "reason": f"{since} 以来 MY KOL 集合内没有新增视频证据"}
    kols = {(_int0(it.get("kol_pool_id"))) for it in items}
    top = []
    for it in items[:_MAX_ITEMS]:
        top.append({
            **_kol_brief(it),
            "evidence_id": _int0(it.get("evidence_id")),
            "title": _text(it.get("title"), 120),
            "content_url": _text(it.get("content_url"), 400),
            "media_kind": _text(it.get("media_kind"), 24) or None,
            "view_count": it.get("view_count"),
            "like_count": it.get("like_count"),
            "comment_count": it.get("comment_count"),
            "posted_day": _day(it.get("posted_day")),
            "added_day": _day(it.get("created_at")),
        })
    return {"status": "ready", "count": len(items), "kol_count": len(kols), "items": top}


# ── 📈 块二:播放异动(环比)+ followers 环比 ─────────────────────────


def _view_shift(conn: Any, sid: int, since: str, prev_since: str, days: int) -> dict[str, Any]:
    rows = conn.execute(
        f"""
        SELECT e.kol_pool_id, kp.handle, kp.display_name, kp.platform,
               SUM(CASE WHEN COALESCE(DATE(e.publish_date), e.posted_at) >= ?
                        THEN COALESCE(e.view_count, 0) ELSE 0 END) AS cur_views,
               SUM(CASE WHEN COALESCE(DATE(e.publish_date), e.posted_at) >= ?
                        THEN 1 ELSE 0 END) AS cur_count,
               SUM(CASE WHEN COALESCE(DATE(e.publish_date), e.posted_at) >= ?
                         AND COALESCE(DATE(e.publish_date), e.posted_at) < ?
                        THEN COALESCE(e.view_count, 0) ELSE 0 END) AS prev_views,
               SUM(CASE WHEN COALESCE(DATE(e.publish_date), e.posted_at) >= ?
                         AND COALESCE(DATE(e.publish_date), e.posted_at) < ?
                        THEN 1 ELSE 0 END) AS prev_count
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool kp ON kp.id = e.kol_pool_id
        JOIN ({_MYSET_SUBQUERY}) ms ON ms.kol_pool_id = e.kol_pool_id
        WHERE COALESCE(DATE(e.publish_date), e.posted_at) >= ?
          AND COALESCE(e.is_active, TRUE) = TRUE
        GROUP BY e.kol_pool_id, kp.handle, kp.display_name, kp.platform
        """,  # noqa: S608 — 常量子查询,参数全绑定
        # 占位符按 SQL 文本顺序:SELECT 列 7 个(cur×2 / prev_views×2 / prev_count×2 顺序如上)
        # → JOIN 子查询 4 个(sid)→ WHERE 1 个(prev_since 预过滤)。
        (since, since, prev_since, since, prev_since, since, *_myset_params(sid), prev_since),
    ).fetchall()
    movers: list[dict[str, Any]] = []
    for raw in rows:
        r = dict(raw)
        cur_v, prev_v = _int0(r.get("cur_views")), _int0(r.get("prev_views"))
        if cur_v <= 0 and prev_v <= 0:
            continue
        movers.append({
            **_kol_brief(r),
            "cur_views": cur_v,
            "prev_views": prev_v,
            "cur_count": _int0(r.get("cur_count")),
            "prev_count": _int0(r.get("prev_count")),
            "delta_views": cur_v - prev_v,
            "delta_pct": _pct(cur_v, prev_v),
        })
    movers.sort(key=lambda m: abs(_int0(m.get("delta_views"))), reverse=True)

    followers = _followers_shift(conn, sid, days)
    if not movers and followers.get("status") != "ready":
        return {
            "status": "empty",
            "reason": f"近 {days} 天与前一期都没有 MY KOL 集合内带播放数的发布记录,followers 快照亦不足",
            "followers": followers,
        }
    return {
        "status": "ready",
        "method": "posted_window_v1",
        "note": "按发布日期比较两期视频当前累计播放;老视频累积时间更长,只作方向参考",
        "kol_count": len(movers),
        "items": movers[:_MAX_ITEMS],
        "followers": followers,
    }


def _followers_shift(conn: Any, sid: int, days: int) -> dict[str, Any]:
    """followers 环比:vkpi_kol_fit_snapshot 日快照(真时间序列;只取 followers,不读 fit_score)。"""
    latest_row = conn.execute("SELECT MAX(snapshot_date) AS d FROM vkpi_kol_fit_snapshot").fetchone()
    latest = _day(dict(latest_row).get("d")) if latest_row else None
    if not latest:
        return {"status": "empty", "reason": "尚无 KOL 日快照(vkpi_kol_fit_snapshot 空)"}
    base_target = (date.fromisoformat(latest) - timedelta(days=days)).isoformat()
    base_row = conn.execute(
        "SELECT MAX(snapshot_date) AS d FROM vkpi_kol_fit_snapshot WHERE snapshot_date <= ?",
        (base_target,),
    ).fetchone()
    base = _day(dict(base_row).get("d")) if base_row else None
    if not base or base == latest:
        earliest_row = conn.execute("SELECT MIN(snapshot_date) AS d FROM vkpi_kol_fit_snapshot").fetchone()
        earliest = _day(dict(earliest_row).get("d")) if earliest_row else None
        return {"status": "empty", "reason": f"快照天数不足以做 {days} 天环比(快照最早 {earliest or latest},最新 {latest})"}
    rows = conn.execute(
        f"""
        SELECT s.kol_pool_id, s.snapshot_date, s.followers,
               kp.handle, kp.display_name, kp.platform
        FROM vkpi_kol_fit_snapshot s
        JOIN vkpi_kol_pool kp ON kp.id = s.kol_pool_id
        JOIN ({_MYSET_SUBQUERY}) ms ON ms.kol_pool_id = s.kol_pool_id
        WHERE s.snapshot_date IN (?, ?)
        """,  # noqa: S608 — 常量子查询,参数全绑定
        (*_myset_params(sid), latest, base),
    ).fetchall()
    by_kol: dict[int, dict[str, Any]] = {}
    for raw in rows:
        r = dict(raw)
        entry = by_kol.setdefault(_int0(r.get("kol_pool_id")), {**_kol_brief(r)})
        if _day(r.get("snapshot_date")) == latest:
            entry["latest_followers"] = _int0(r.get("followers"))
        else:
            entry["base_followers"] = _int0(r.get("followers"))
    movers = []
    for entry in by_kol.values():
        if "latest_followers" not in entry or "base_followers" not in entry:
            continue  # 只在两期都有快照时才算环比,不拿单边数据硬凑
        entry["delta_followers"] = entry["latest_followers"] - entry["base_followers"]
        entry["delta_pct"] = _pct(entry["latest_followers"], entry["base_followers"])
        if entry["delta_followers"] != 0:
            movers.append(entry)
    if not movers:
        return {"status": "empty", "reason": f"{base} → {latest} 两期快照内 MY KOL 集合 followers 无变动记录"}
    movers.sort(key=lambda m: abs(_int0(m.get("delta_followers"))), reverse=True)
    return {"status": "ready", "as_of": latest, "base_date": base, "items": movers[:_MAX_ITEMS]}


# ── 💬 块三:提及 Viltrox 的新内容(词表)────────────────────────────


def _viltrox_mentions(conn: Any, sid: int, since: str) -> dict[str, Any]:
    rows = conn.execute(
        f"""
        SELECT e.id AS evidence_id, e.kol_pool_id,
               COALESCE(e.video_title, e.title, '') AS title,
               COALESCE(e.channel_name, '') AS channel_name,
               e.content_url, e.view_count, e.is_active, e.created_at,
               kp.handle, kp.display_name, kp.platform
        FROM vkpi_kol_video_evidence e
        JOIN vkpi_kol_pool kp ON kp.id = e.kol_pool_id
        JOIN ({_MYSET_SUBQUERY}) ms ON ms.kol_pool_id = e.kol_pool_id
        WHERE DATE(e.created_at) >= ?
        ORDER BY COALESCE(e.view_count, 0) DESC, e.id DESC
        LIMIT 400
        """,  # noqa: S608 — 常量子查询,参数全绑定
        (*_myset_params(sid), since),
    ).fetchall()
    hits: list[dict[str, Any]] = []
    for raw in rows:
        r = dict(raw)
        if not _active(r.get("is_active")):
            continue
        blob = (_text(r.get("title"), 300) + " " + _text(r.get("channel_name"), 120)).lower()
        matched = [term for term in VILTROX_TERMS if term in blob]
        if not matched:
            continue
        hits.append({
            **_kol_brief(r),
            "evidence_id": _int0(r.get("evidence_id")),
            "title": _text(r.get("title"), 120),
            "content_url": _text(r.get("content_url"), 400),
            "view_count": r.get("view_count"),
            "added_day": _day(r.get("created_at")),
            "matched_terms": matched,
        })
    if not hits:
        return {
            "status": "empty",
            "reason": f"{since} 以来新增内容里没有词表可识别的 Viltrox 提及(lexicon_v1,只看标题/频道名,宁缺毋滥)",
        }
    return {"status": "ready", "method": "lexicon_v1", "count": len(hits), "items": hits[:_MAX_ITEMS]}


# ── 📇 块四:联系方式新获得(不出明文)────────────────────────────────


def _new_contacts(conn: Any, sid: int, since: str) -> dict[str, Any]:
    rows = conn.execute(
        f"""
        SELECT ct.id AS contact_id, ct.kol_pool_id, ct.contact_type, ct.contact_source,
               ct.consent_basis, ct.created_at,
               kp.handle, kp.display_name, kp.platform
        FROM vkpi_kol_pool_contacts ct
        JOIN vkpi_kol_pool kp ON kp.id = ct.kol_pool_id
        JOIN ({_MYSET_SUBQUERY}) ms ON ms.kol_pool_id = ct.kol_pool_id
        WHERE DATE(ct.created_at) >= ?
        ORDER BY ct.created_at DESC, ct.id DESC
        LIMIT 200
        """,  # noqa: S608 — 常量子查询,参数全绑定
        (*_myset_params(sid), since),
    ).fetchall()
    items = [dict(r) for r in rows]
    if not items:
        return {"status": "empty", "reason": f"{since} 以来 MY KOL 集合内没有新入库的联系方式"}
    by_type: dict[str, int] = {}
    out = []
    for it in items:
        ctype = _text(it.get("contact_type"), 32).lower() or "unknown"
        by_type[ctype] = by_type.get(ctype, 0) + 1
        if len(out) < _MAX_ITEMS:
            out.append({
                **_kol_brief(it),
                "contact_type": ctype,
                "contact_source": _text(it.get("contact_source"), 64) or None,
                "consent_basis": _text(it.get("consent_basis"), 64) or None,
                "added_day": _day(it.get("created_at")),
            })
    return {
        "status": "ready",
        "count": len(items),
        "by_type": by_type,
        "items": out,
        "note": "摘要只出类型/来源,联系方式明文走既有 contact_reveal 门控端点",
    }


# ── 🏢 块五:公司官号(昨日表现 + 最好一条)──────────────────────────


def _official(conn: Any, since: str, days: int) -> dict[str, Any]:
    latest_row = conn.execute("SELECT MAX(snapshot_date) AS d FROM vkpi_channel_metrics").fetchone()
    latest = _day(dict(latest_row).get("d")) if latest_row else None
    if not latest:
        return {"status": "empty", "reason": "官号尚无任何日快照(vkpi_channel_metrics 空)"}
    if latest < since:
        return {
            "status": "empty",
            "reason": f"近 {days} 天窗口内没有官号日快照,最近一次是 {latest}(每日刷新在云端跑,本地库可能滞后)",
            "latest_available": latest,
        }
    rows = conn.execute(
        """
        SELECT c.id AS channel_id, c.platform, c.account_handle, c.account_display_name,
               m.followers, m.followers_delta, m.posts_delta, m.total_views,
               m.views_delta_24h, m.engagement_rate
        FROM vkpi_employee_channels c
        JOIN vkpi_channel_metrics m ON m.channel_id = c.id AND m.snapshot_date = ?
        WHERE c.deleted_at IS NULL
        ORDER BY COALESCE(m.views_delta_24h, 0) DESC, c.id ASC
        """,
        (latest,),
    ).fetchall()
    channels = [dict(r) for r in rows]
    if not channels:
        return {"status": "empty", "reason": f"{latest} 当天没有任何官号的快照行", "latest_available": latest}
    totals = {
        "channel_count": len(channels),
        "followers_delta": sum(_int0(c.get("followers_delta")) for c in channels),
        "views_delta": sum(_int0(c.get("views_delta_24h")) for c in channels),
        "posts_delta": sum(_int0(c.get("posts_delta")) for c in channels),
        "followers_total": sum(_int0(c.get("followers")) for c in channels),
    }
    top_channels = [
        {
            "channel_id": _int0(c.get("channel_id")),
            "platform": _text(c.get("platform"), 24).lower(),
            "handle": _text(c.get("account_handle"), 80),
            "display_name": _text(c.get("account_display_name"), 80),
            "followers_delta": _int0(c.get("followers_delta")),
            "views_delta": _int0(c.get("views_delta_24h")),
        }
        for c in channels[:3]
    ]
    return {
        "status": "ready",
        "snapshot_date": latest,
        "totals": totals,
        "top_channels": top_channels,
        "best_post": _official_best_post(conn, latest),
    }


def _official_best_post(conn: Any, metrics_latest: str) -> dict[str, Any]:
    latest_row = conn.execute("SELECT MAX(snapshot_date) AS d FROM vkpi_channel_post_metrics").fetchone()
    latest = _day(dict(latest_row).get("d")) if latest_row else None
    if not latest:
        return {"status": "empty", "reason": "官号尚无逐帖快照(vkpi_channel_post_metrics 空)"}
    rows = conn.execute(
        """
        SELECT p.title, p.post_url, p.platform, p.posted_at, p.views, p.views_delta,
               p.likes, p.comments,
               c.account_handle, c.account_display_name
        FROM vkpi_channel_post_metrics p
        JOIN vkpi_employee_channels c ON c.id = p.channel_id
        WHERE p.snapshot_date = ? AND c.deleted_at IS NULL
        ORDER BY COALESCE(p.views_delta, 0) DESC, COALESCE(p.views, 0) DESC
        LIMIT 3
        """,
        (latest,),
    ).fetchall()
    posts = [dict(r) for r in rows]
    if not posts:
        return {"status": "empty", "reason": f"{latest} 当天没有逐帖快照行"}
    method = "views_delta_v1" if any(_int0(p.get("views_delta")) > 0 for p in posts) else "views_total_fallback"
    items = [
        {
            "title": _text(p.get("title"), 120),
            "post_url": _text(p.get("post_url"), 400),
            "platform": _text(p.get("platform"), 24).lower(),
            "handle": _text(p.get("account_handle"), 80),
            "display_name": _text(p.get("account_display_name"), 80),
            "posted_day": _day(p.get("posted_at")),
            "views": p.get("views"),
            "views_delta": p.get("views_delta"),
            "likes": p.get("likes"),
            "comments": p.get("comments"),
        }
        for p in posts
    ]
    return {
        "status": "ready",
        "snapshot_date": latest,
        "method": method,
        "note": "当日播放增量排序" if method == "views_delta_v1" else "当日无正增量,按累计播放兜底排序(如实标注)",
        "items": items,
    }


# ── 总装 ─────────────────────────────────────────────────────────────


def _block(name: str, fn: Any, *args: Any) -> dict[str, Any]:
    """单块内部异常不炸整个摘要,诚实回 error 原因(前端可安静降级)。"""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 — 增益块失败要可见但非阻塞
        logger.warning("daily_digest block %s failed: %s", name, exc)
        return {"status": "error", "reason": str(exc)[:200]}


def digest(days: int = 1, staff_id: int | None = None) -> dict[str, Any]:
    """MY KOL + 官号 每日学习摘要(纯读聚合,零 LLM,零写库)。

    days:回看窗口天数(1=昨天以来);staff_id:None/0=全员并集(管理层视角),
    否则该 staff 的收藏 ∪ 共享集合。数据不足的块诚实 empty,不外推。
    """
    from app.db.connection import get_conn

    conn = get_conn()
    days = max(1, min(_int0(days) or 1, 90))
    sid = _int0(staff_id)
    today = datetime.now(timezone.utc).date()
    since = (today - timedelta(days=days)).isoformat()
    prev_since = (today - timedelta(days=days * 2)).isoformat()

    kol_count = _myset_count(conn, sid)
    payload: dict[str, Any] = {
        "status": "ready",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {"days": days, "since": since, "prev_since": prev_since, "until": today.isoformat()},
        "scope": {"staff_id": sid or None, "mode": "staff" if sid else "all", "kol_count": kol_count},
    }
    if kol_count <= 0:
        payload["kol"] = {
            "status": "empty",
            "reason": "该范围内还没有 MY KOL(收藏或共享)——先在 KOL Pool 里收藏,摘要才有观察对象",
        }
    else:
        payload["kol"] = {
            "status": "ready",
            "new_videos": _block("new_videos", _new_videos, conn, sid, since),
            "view_shift": _block("view_shift", _view_shift, conn, sid, since, prev_since, days),
            "viltrox_mentions": _block("viltrox_mentions", _viltrox_mentions, conn, sid, since),
            "new_contacts": _block("new_contacts", _new_contacts, conn, sid, since),
        }
    payload["official"] = _block("official", _official, conn, since, days)
    return payload
