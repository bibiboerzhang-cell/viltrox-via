"""CB1 官号内容计划器(Owned Media Planner)—— Channel Brain 层纯读实现。

官号不是 dashboard,是渠道动作。本模块读三张真表:
  · vkpi_employee_channels  —— 官号名册(官方口径筛选,与 dashboard.summary_company 同源)
  · vkpi_channel_metrics    —— 官号粉丝/曝光日快照(取每号最新一条)
  · vkpi_channel_post_metrics —— 官号逐帖指标(多快照,取每帖最新一条去重)
用「词表法」(零 LLM)把每帖标题回打成内容形式(product_launch/spec_showcase/
sample_footage/tutorial_tips/promo_offer/community_ugc,识别不了如实 unclassified),
把发帖 UTC 小时分成四时段,按平均播放/互动算出「每个官号哪种形式 × 哪个时段最灵」,
再产出「哪个号该发什么形式配合哪个 SKU」的动作建议。

对外两个入口:
  · official_content_plan(sku=None, category=None) —— 全官号内容计划(可按 SKU/品类聚焦)
  · channel_performance(channel_id)               —— 单号内容表现侧写

红线(CB1 专属):
  - 纯读、零写库、零 LLM、零采集;绝不写 viltrox_fit_score / 不碰 rule_v0;不碰 .env。
  - compat SQL:? 占位、无字面 %;内容形式分类的词表全部在 Python 侧回打(SQL 不做 LIKE)。
  - 数据缺失诚实:官号 0 / 帖子 0 一律 status=data_missing/empty + note;每个数字带 basis;
    时段口径基于发帖 UTC 小时,前端按浏览者时区换算(不硬编码本地时段名)。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

METHOD = "official_content_plan_v1(词表法回打内容形式,零 LLM 零采集零写库,纯读聚合)"
TIME_BASIS = "时段基于发帖 UTC 小时分四档;前端按浏览者时区换算展示(不硬编码本地时段名)"

# 官方账号筛选:与 dashboard.summary_company / metric_maturity 同源(唯一口径,勿另立)。
# compat 规则:无 ? 无字面 %,POSITION 做包含判定(f-string 内联常量,与 gtm_plan_preview 同法)。
from app.domains.dashboard.metric_maturity import _OFFICIAL_CHANNEL_FILTER_SQL

# 小样本闸:某形式/时段帖数不足此值不判「最灵」,避免单帖噪声误导(诚实标 low_sample)。
_MIN_BUCKET_SAMPLE = 3
# 每号取近多少帖参与形式/时段聚合(按最新快照去重后,再按发帖时间倒序截断)。
_MAX_POSTS_PER_CHANNEL = 240
# sparkline 取最近多少帖(按发帖时间正序,旧→新)。
_SPARK_POINTS = 16
_TOP_POSTS = 6

# ── 内容形式词表(零成本回打;宁缺毋滥,命中不了如实 unclassified)──────────
# 命中按登记顺序取首个(优先级:发布 > 促销 > 教程 > 社区 > 样片 > 规格),
# 规格词最泛放最后兜底,避免把「新品发布」误判成「规格硬核」。
_FORM_LABELS: dict[str, str] = {
    "product_launch": "新品发布",
    "promo_offer": "促销转化",
    "tutorial_tips": "教程干货",
    "community_ugc": "社区共创",
    "sample_footage": "样片实拍",
    "spec_showcase": "规格硬核",
    "unclassified": "未分类",
}
_FORM_ORDER: tuple[str, ...] = (
    "product_launch", "promo_offer", "tutorial_tips",
    "community_ugc", "sample_footage", "spec_showcase",
)
_FORM_TERMS: dict[str, tuple[str, ...]] = {
    "product_launch": (
        "introducing", "meet the", "meet ", "now available", "available now",
        "launch", "launching", "coming soon", "first look", "unveil", "announc",
        "drops", "new release", "just dropped", "new lens", "新品", "发布", "上市", "首发",
    ),
    "promo_offer": (
        "% off", "sale", "deal", "discount", "coupon", "promo", "code ", "shop now",
        "buy now", "order now", "black friday", "cyber monday", "限时", "优惠", "折扣", "促销",
    ),
    "tutorial_tips": (
        "how to", "how-to", "tutorial", "tips", "guide", "settings", "setup",
        "learn ", "master ", "hack", "trick", "step by step", "教程", "教学", "技巧", "参数",
    ),
    "community_ugc": (
        "giveaway", "contest", "featured", "shoutout", "shout out", "repost",
        "thanks to", "tag a", "tag your", "win a", "winner", "congrats", "community",
        "creator spotlight", "共创", "征集", "抽奖", "转发",
    ),
    "sample_footage": (
        "shot on", "filmed on", "sample", "footage", "cinematic", "behind the scenes",
        "bts", "short film", "real world", "test shot", "样片", "实拍", "花絮",
    ),
    "spec_showcase": (
        "aperture", "bokeh", "sharp", "resolution", "full frame", "autofocus",
        "af ", "lab", "evo", "f0.", "f1.", "f2.", "f4", "mm ", "mm\n", "mm.",
        "焦段", "光圈", "锐度",
    ),
}

# 品类/型号词兜底时的停用词(镜头名里遍地都是的泛词,留着会把 SKU 聚焦稀释成全命中)。
_STOP_TOKENS = {
    "the", "for", "and", "with", "lens", "viltrox", "your", "you", "our",
    "this", "that", "now", "new", "all", "get", "af", "fe", "mount", "full",
    "frame", "sony", "nikon", "fujifilm", "canon", "aps", "apsc", "apc", "air-frame",
}
# 型号线后缀白名单(高信号:这些词在标题里就是产品线标识,可精准配对)。
_MODEL_SUFFIX = ("lab", "evo", "air", "pro", "cine")


# ── 小工具 ──────────────────────────────────────────────────────────


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


from app.core.coerce import _int0


def _num(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _text(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _to_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _daypart(hour: int) -> tuple[str, str]:
    """发帖 UTC 小时 → 四时段(bucket_key, 中文标签,标 UTC 口径)。"""
    if 0 <= hour < 6:
        return "utc_00_06", "深夜 00–06(UTC)"
    if 6 <= hour < 12:
        return "utc_06_12", "上午 06–12(UTC)"
    if 12 <= hour < 18:
        return "utc_12_18", "下午 12–18(UTC)"
    return "utc_18_24", "晚间 18–24(UTC)"


_DAYPART_ORDER: tuple[tuple[str, str], ...] = (
    ("utc_00_06", "深夜 00–06(UTC)"),
    ("utc_06_12", "上午 06–12(UTC)"),
    ("utc_12_18", "下午 12–18(UTC)"),
    ("utc_18_24", "晚间 18–24(UTC)"),
)


def _classify_form(title: str) -> str:
    """标题 → 内容形式(词表命中,按 _FORM_ORDER 优先级取首个;命中不了 unclassified)。"""
    text = (title or "").lower()
    if not text.strip():
        return "unclassified"
    for form in _FORM_ORDER:
        for term in _FORM_TERMS[form]:
            if term in text:
                return form
    return "unclassified"


def _engagement_rate(likes: int, comments: int, shares: int, views: int) -> float:
    if views <= 0:
        return 0.0
    return round((likes + comments + shares) / float(views), 4)


def _round1(value: float) -> float:
    return round(float(value), 1)


# ── 数据装载(全部纯读;compat ? 占位,无字面 %)─────────────────────


def _load_official_channels(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT c.id AS channel_id, LOWER(c.platform) AS platform,
               c.account_handle, COALESCE(c.account_display_name, '') AS display_name,
               COALESCE(c.account_url, '') AS url
        FROM vkpi_employee_channels c
        WHERE {_OFFICIAL_CHANNEL_FILTER_SQL}
        ORDER BY c.id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _load_latest_followers(conn: Any, channel_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not channel_ids:
        return {}
    placeholders = ", ".join("?" for _ in channel_ids)
    rows = conn.execute(
        f"""
        SELECT m.channel_id,
               COALESCE(m.followers, 0) AS followers,
               COALESCE(m.engagement_rate, 0) AS engagement_rate,
               m.snapshot_date, m.captured_at
        FROM vkpi_channel_metrics m
        WHERE m.id = (
            SELECT mm.id FROM vkpi_channel_metrics mm
            WHERE mm.channel_id = m.channel_id
            ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC
            LIMIT 1
        )
        AND m.channel_id IN ({placeholders})
        """,
        list(channel_ids),
    ).fetchall()
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        out[_int0(d.get("channel_id"))] = {
            "followers": _int0(d.get("followers")),
            "engagement_rate": _num(d.get("engagement_rate")),
            "snapshot_date": d.get("snapshot_date"),
            "captured_at": d.get("captured_at"),
        }
    return out


def _load_latest_posts(conn: Any, channel_ids: list[int]) -> list[dict[str, Any]]:
    """每帖取最新快照去重(DISTINCT ON channel_id, post_uid),累计口径取最终值。"""
    if not channel_ids:
        return []
    placeholders = ", ".join("?" for _ in channel_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT ON (pm.channel_id, pm.post_uid)
               pm.channel_id, pm.post_uid,
               COALESCE(pm.title, '') AS title, COALESCE(pm.post_url, '') AS post_url,
               pm.posted_at, pm.snapshot_date, pm.captured_at,
               COALESCE(pm.views, 0) AS views, COALESCE(pm.likes, 0) AS likes,
               COALESCE(pm.comments, 0) AS comments, COALESCE(pm.shares, 0) AS shares
        FROM vkpi_channel_post_metrics pm
        WHERE pm.channel_id IN ({placeholders})
        ORDER BY pm.channel_id, pm.post_uid, pm.snapshot_date DESC, pm.captured_at DESC
        """,
        list(channel_ids),
    ).fetchall()
    posts: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        posts.append({
            "channel_id": _int0(d.get("channel_id")),
            "post_uid": str(d.get("post_uid") or ""),
            "title": str(d.get("title") or ""),
            "post_url": str(d.get("post_url") or ""),
            "posted_at": d.get("posted_at"),
            "snapshot_date": d.get("snapshot_date"),
            "captured_at": d.get("captured_at"),
            "views": _int0(d.get("views")),
            "likes": _int0(d.get("likes")),
            "comments": _int0(d.get("comments")),
            "shares": _int0(d.get("shares")),
        })
    return posts


# ── SKU / 品类聚焦(词表 token 在 Python 侧匹配标题,SQL 不做 LIKE)──────


def _extract_tokens(*sources: str) -> set[str]:
    """从 SKU/型号/营销名抽取**高信号**判定 token:焦段(75mm)、光圈(f1.8/f18 双形)、
    产品线后缀(lab/evo/air/pro/cine)。这些在标题里就是精准标识,避免用泛词全命中。
    高信号一个都抽不到(如配件类 SKU)时,退回有意义型号词兜底(去泛词)。"""
    tokens: set[str] = set()
    for src in sources:
        low = str(src or "").lower()
        # 焦段:75mm / 135mm(标题常写法)
        for m in re.findall(r"\d{1,3}mm", low):
            tokens.add(m)
        # 型号焦段/光圈写法「35/1.2」「135/1.8」(SKU/型号名无 mm 字面):抽两三位焦段补
        # '35' 与 '35mm' 双形 + 紧凑形 '351.2' + 光圈 'f1.2'/'f12',让聚焦命中标题里的
        # 35mm/f1.2 写法。此前只认带 mm 字面的源,而 vkpi_products.model_name 普遍是
        # 'AF 35/1.2 FE' 斜杠写法(marketing_name 常空)→ 焦段一律落空,match_tokens 只剩
        # 永不命中的 SKU 码片段(如 len072),18 官号 relevance_hits 恒 0。
        for focal, ap in re.findall(r"(?<!\d)(\d{2,3})\s*/\s*(\d+(?:\.\d+)?)", low):
            tokens.add(focal)
            tokens.add(focal + "mm")
            tokens.add(focal + ap)              # 35/1.2 → 351.2(去斜杠紧凑形)
            tokens.add("f" + ap)                # 光圈有点形 f1.2
            tokens.add("f" + ap.replace(".", ""))  # 光圈无点形 f12
        # 光圈:f1.8 / f1.2 / f18(sku 无点写法)→ 同时登记有点与无点两形,兼容标题写法
        for m in re.findall(r"f/?\d+(?:\.\d+)?", low):
            ap = m.replace("f/", "f")
            tokens.add(ap)
            if "." in ap:
                tokens.add(ap.replace(".", ""))          # f1.8 → f18
            elif len(ap) == 3:                            # f18 → f1.8
                tokens.add(ap[0] + ap[1] + "." + ap[2])
        # 产品线后缀白名单
        for suf in _MODEL_SUFFIX:
            if re.search(rf"(?<![a-z]){suf}(?![a-z])", low):
                tokens.add(suf)
    if not tokens:
        # 兜底:配件/无焦段 SKU,用去泛词的型号词(≥3 字符)做相关度,诚实标 word_fallback。
        for src in sources:
            for w in re.split(r"[^a-z0-9]+", str(src or "").lower()):
                if len(w) >= 3 and w not in _STOP_TOKENS:
                    tokens.add(w)
    return tokens


def _resolve_sku(conn: Any, sku: str) -> dict[str, Any] | None:
    try:
        row = conn.execute(
            """
            SELECT sku, COALESCE(model_name, '') AS model_name,
                   COALESCE(marketing_name, '') AS marketing_name,
                   COALESCE(category_main, '') AS category_main,
                   COALESCE(category_detail, '') AS category_detail,
                   COALESCE(series, '') AS series
            FROM vkpi_products
            WHERE LOWER(sku) = LOWER(?)
            LIMIT 1
            """,
            [sku],
        ).fetchone()
    except Exception as exc:  # 表缺/字段缺:诚实降级为字面匹配
        logger.warning("official_planner _resolve_sku failed: %s", exc)
        return None
    return dict(row) if row else None


def _build_focus(conn: Any, sku: str | None, category: str | None) -> dict[str, Any]:
    """构造聚焦匹配器:tokens + 是否解析成功 + 展示名 + note。"""
    focus: dict[str, Any] = {
        "sku": _text(sku, 120) if sku else None,
        "category": _text(category, 120) if category else None,
        "resolved_product": None,
        "sku_resolved": False,
        "tokens": [],
        "note": None,
    }
    tokens: set[str] = set()
    if sku:
        prod = _resolve_sku(conn, sku)
        if prod:
            focus["sku_resolved"] = True
            focus["resolved_product"] = {
                "sku": prod.get("sku"),
                "model_name": _text(prod.get("model_name"), 80),
                "marketing_name": _text(prod.get("marketing_name"), 80),
                "category_main": _text(prod.get("category_main"), 40),
            }
            tokens |= _extract_tokens(
                str(prod.get("sku") or ""), str(prod.get("model_name") or ""),
                str(prod.get("marketing_name") or ""), str(prod.get("series") or ""),
            )
        else:
            focus["note"] = "sku 未在 vkpi_products 命中,按字面 token 匹配标题(basis=literal_token)"
            tokens |= _extract_tokens(sku)
    if category:
        cat_tokens = {t for t in re.split(r"[^a-z0-9]+", category.lower()) if len(t) >= 3 and t not in _STOP_TOKENS}
        tokens |= cat_tokens
    focus["tokens"] = sorted(tokens)
    # 相关度判定用「焦段优先」:焦段(75mm/135mm)在标题里唯一到具体镜头(~2% 命中),
    # 而光圈/后缀(f1.8/lab/evo)是产品线泛词(命中 10-25%)会稀释聚焦。
    # 有焦段 token 时只用焦段判相关;无焦段(机身/配件)才退回全部 token。
    focal = sorted(t for t in tokens if re.fullmatch(r"\d{1,3}mm", t))
    focus["match_tokens"] = focal or sorted(tokens)
    focus["match_basis"] = "focal_length" if focal else ("all_tokens" if tokens else "none")
    return focus


def _relevance_hits(posts: list[dict[str, Any]], tokens: list[str]) -> int:
    if not tokens:
        return 0
    hits = 0
    for p in posts:
        low = p["title"].lower()
        if any(t in low for t in tokens):
            hits += 1
    return hits


# ── 聚合核心(词表回打 + 时段分档 + 最灵档位)────────────────────────


def _bucket_leaders(posts: list[dict[str, Any]]) -> dict[str, Any]:
    """把一批帖子按内容形式 / 时段聚合,算各桶平均播放+互动,返回分布 + 最灵档。"""
    form_stat: dict[str, dict[str, float]] = {}
    daypart_stat: dict[str, dict[str, float]] = {}
    for p in posts:
        form = _classify_form(p["title"])
        views = p["views"]
        er = _engagement_rate(p["likes"], p["comments"], p["shares"], views)
        fs = form_stat.setdefault(form, {"count": 0, "views_sum": 0.0, "er_sum": 0.0})
        fs["count"] += 1
        fs["views_sum"] += views
        fs["er_sum"] += er
        dt = _to_dt(p.get("posted_at"))
        if dt is not None:
            key, _label = _daypart(dt.astimezone(timezone.utc).hour)
            ds = daypart_stat.setdefault(key, {"count": 0, "views_sum": 0.0, "er_sum": 0.0})
            ds["count"] += 1
            ds["views_sum"] += views
            ds["er_sum"] += er

    total = len(posts) or 1
    form_dist = []
    for form in list(_FORM_ORDER) + ["unclassified"]:
        st = form_stat.get(form)
        if not st or st["count"] == 0:
            continue
        n = int(st["count"])
        form_dist.append({
            "form": form,
            "form_label": _FORM_LABELS[form],
            "count": n,
            "share": round(n / total, 3),
            "avg_views": int(round(st["views_sum"] / n)),
            "avg_engagement_rate": round(st["er_sum"] / n, 4),
        })

    daypart_dist = []
    for key, label in _DAYPART_ORDER:
        st = daypart_stat.get(key)
        if not st or st["count"] == 0:
            continue
        n = int(st["count"])
        daypart_dist.append({
            "bucket": key,
            "label": label,
            "count": n,
            "share": round(n / total, 3),
            "avg_views": int(round(st["views_sum"] / n)),
            "avg_engagement_rate": round(st["er_sum"] / n, 4),
        })

    best_form = _pick_leader(form_dist, "form")
    best_daypart = _pick_leader(daypart_dist, "bucket")
    return {
        "form_breakdown": form_dist,
        "daypart_breakdown": daypart_dist,
        "best_form": best_form,
        "best_daypart": best_daypart,
    }


def _pick_leader(dist: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """从分布里选平均播放最高、且样本 ≥ 闸 的桶;不足样本诚实标 low_sample。"""
    if not dist:
        return None
    # unclassified 不参与「最灵形式」评选(词表识别不了不该当建议)。
    eligible = [d for d in dist if d.get("form") != "unclassified"] if key == "form" else list(dist)
    qualified = [d for d in eligible if int(d.get("count") or 0) >= _MIN_BUCKET_SAMPLE]
    pool = qualified or eligible
    if not pool:
        return None
    winner = max(pool, key=lambda d: (int(d.get("avg_views") or 0), int(d.get("count") or 0)))
    out = dict(winner)
    out["low_sample"] = int(winner.get("count") or 0) < _MIN_BUCKET_SAMPLE
    return out


def _spark(posts: list[dict[str, Any]]) -> list[int]:
    """按发帖时间正序取最近 N 帖的播放,做 sparkline(旧→新);无时间的排后。"""
    ordered = sorted(posts, key=lambda p: (_to_dt(p.get("posted_at")) or datetime.min.replace(tzinfo=timezone.utc)))
    tail = ordered[-_SPARK_POINTS:]
    return [p["views"] for p in tail]


def _recommend_line(handle: str, best_form: dict | None, best_daypart: dict | None,
                    focus: dict[str, Any]) -> str:
    if best_form is None and best_daypart is None:
        return f"官号 {handle}:样本不足,建议先补齐内容快照再定策(basis=insufficient_posts)"
    form_txt = best_form["form_label"] if best_form else "尚无最灵形式"
    slot_txt = best_daypart["label"] if best_daypart else "尚无最灵时段"
    sku_txt = ""
    if focus.get("sku"):
        rp = focus.get("resolved_product") or {}
        name = rp.get("marketing_name") or rp.get("model_name") or focus["sku"]
        sku_txt = f" · 配合 {name}"
    tail = ""
    if best_form and best_form.get("low_sample"):
        tail = "(样本偏少,谨慎参考)"
    return f"官号 {handle} 建议发「{form_txt}」· 最佳时段 {slot_txt}{sku_txt}{tail}"


# ── 对外入口 ────────────────────────────────────────────────────────


def official_content_plan(sku: str | None = None, category: str | None = None) -> dict[str, Any]:
    """全官号内容计划:每号最灵形式 × 最灵时段 → 动作建议(可按 SKU/品类聚焦排序)。

    纯读聚合,零 LLM 零采集零写库;官号 0 / 帖子 0 一律诚实 data_missing。
    """
    from app.db.connection import get_conn

    conn = get_conn()
    channels = _load_official_channels(conn)
    if not channels:
        return {
            "status": "data_missing", "method": METHOD, "generated_at": _utcnow_iso(),
            "official_count": 0, "posts_analyzed": 0, "channels": [],
            "note": "vkpi_employee_channels 官方口径 0 号(basis=official_channel_filter);无官号可规划。",
        }

    channel_ids = [_int0(c["channel_id"]) for c in channels]
    followers = _load_latest_followers(conn, channel_ids)
    all_posts = _load_latest_posts(conn, channel_ids)

    by_channel: dict[int, list[dict[str, Any]]] = {}
    for p in all_posts:
        by_channel.setdefault(p["channel_id"], []).append(p)

    focus = _build_focus(conn, sku, category)
    tokens = list(focus.get("match_tokens") or [])  # 焦段优先的相关度判定 token

    latest_snapshot_dates = [p.get("snapshot_date") for p in all_posts if p.get("snapshot_date")]
    latest_snapshot = max((str(d) for d in latest_snapshot_dates), default=None)

    out_channels: list[dict[str, Any]] = []
    for c in channels:
        cid = _int0(c["channel_id"])
        posts = by_channel.get(cid, [])
        # 只取最近 N 帖参与聚合(按发帖时间倒序)。
        posts_recent = sorted(
            posts, key=lambda p: (_to_dt(p.get("posted_at")) or datetime.min.replace(tzinfo=timezone.utc)),
            reverse=True,
        )[:_MAX_POSTS_PER_CHANNEL]
        fol = followers.get(cid, {})
        n = len(posts_recent)
        avg_views = int(round(sum(p["views"] for p in posts_recent) / n)) if n else 0
        avg_er = round(
            sum(_engagement_rate(p["likes"], p["comments"], p["shares"], p["views"]) for p in posts_recent) / n, 4
        ) if n else 0.0
        agg = _bucket_leaders(posts_recent) if posts_recent else {
            "form_breakdown": [], "daypart_breakdown": [], "best_form": None, "best_daypart": None,
        }
        hits = _relevance_hits(posts_recent, tokens)
        latest_cap = max((str(p.get("captured_at")) for p in posts_recent if p.get("captured_at")), default=None)
        out_channels.append({
            "channel_id": cid,
            "platform": c.get("platform"),
            "handle": c.get("account_handle"),
            "display_name": _text(c.get("display_name"), 80),
            "url": c.get("url") or None,
            "followers": _int0(fol.get("followers")),
            "followers_basis": (
                f"vkpi_channel_metrics 最新快照 {fol.get('snapshot_date')}" if fol else "无粉丝快照(basis=no_metrics_snapshot)"
            ),
            "posts_analyzed": n,
            "avg_views": avg_views,
            "avg_engagement_rate": avg_er,
            "best_form": agg["best_form"],
            "best_daypart": agg["best_daypart"],
            "relevance_hits": hits,
            "recommendation": _recommend_line(str(c.get("account_handle") or ""), agg["best_form"], agg["best_daypart"], focus),
            "spark": _spark(posts_recent),
            "latest_captured_at": latest_cap,
            "note": None if n else "该官号暂无逐帖快照(basis=no_post_metrics)",
        })

    # 排序:有 SKU/品类聚焦 → 相关命中优先,其次触达(avg_views);否则纯按触达。
    focused = bool(tokens)
    out_channels.sort(
        key=lambda c: ((c["relevance_hits"] if focused else 0), c["avg_views"], c["posts_analyzed"]),
        reverse=True,
    )
    for i, c in enumerate(out_channels, 1):
        c["priority_rank"] = i

    # 全官号内容形式榜(哪种形式整体最灵,给个大盘 headline)。
    overall = _bucket_leaders(all_posts) if all_posts else {"best_form": None, "form_breakdown": []}
    headline_form = overall.get("best_form")

    posts_analyzed = len(all_posts)
    status = "ok" if posts_analyzed else "data_missing"
    result = {
        "status": status,
        "method": METHOD,
        "generated_at": _utcnow_iso(),
        "time_basis": TIME_BASIS,
        "focus": focus,
        "focused": focused,
        "official_count": len(channels),
        "posts_analyzed": posts_analyzed,
        "latest_snapshot_date": latest_snapshot,
        "overall_best_form": headline_form,
        "overall_form_breakdown": overall.get("form_breakdown", []),
        "channels": out_channels,
        "basis": (
            f"官号 {len(channels)} 号 · 去重帖 {posts_analyzed} 条(每帖取最新快照);"
            f"内容形式=标题词表回打;时段=发帖 UTC 小时;样本闸={_MIN_BUCKET_SAMPLE} 帖。"
        ),
        "note": None if posts_analyzed else "官号存在但 vkpi_channel_post_metrics 无逐帖数据(basis=no_post_metrics)。",
    }
    return result


def channel_performance(channel_id: int) -> dict[str, Any]:
    """单官号内容表现侧写:形式分布 + 时段分布 + 最灵档 + top 帖 + 趋势线(纯读)。"""
    from app.db.connection import get_conn

    conn = get_conn()
    cid = _int0(channel_id)
    # 该号必须落在官方口径内(非官号不在本计划器职责内,诚实 404 语义)。
    row = conn.execute(
        f"""
        SELECT c.id AS channel_id, LOWER(c.platform) AS platform, c.account_handle,
               COALESCE(c.account_display_name, '') AS display_name, COALESCE(c.account_url, '') AS url
        FROM vkpi_employee_channels c
        WHERE c.id = ? AND ({_OFFICIAL_CHANNEL_FILTER_SQL})
        LIMIT 1
        """,
        [cid],
    ).fetchone()
    if not row:
        return {
            "status": "not_found", "method": METHOD, "generated_at": _utcnow_iso(),
            "channel_id": cid, "note": "该 channel_id 非官方口径官号或不存在(basis=official_channel_filter)。",
        }
    ch = dict(row)
    fol = _load_latest_followers(conn, [cid]).get(cid, {})
    posts = _load_latest_posts(conn, [cid])
    if not posts:
        return {
            "status": "empty", "method": METHOD, "generated_at": _utcnow_iso(),
            "channel": {
                "id": cid, "platform": ch.get("platform"), "handle": ch.get("account_handle"),
                "display_name": _text(ch.get("display_name"), 80), "url": ch.get("url") or None,
                "followers": _int0(fol.get("followers")),
            },
            "posts_analyzed": 0,
            "note": "该官号暂无逐帖快照(basis=no_post_metrics);无从侧写表现。",
        }

    n = len(posts)
    avg_views = int(round(sum(p["views"] for p in posts) / n))
    avg_er = round(sum(_engagement_rate(p["likes"], p["comments"], p["shares"], p["views"]) for p in posts) / n, 4)
    agg = _bucket_leaders(posts)

    top_posts = sorted(posts, key=lambda p: p["views"], reverse=True)[:_TOP_POSTS]
    top_out = []
    for p in top_posts:
        form = _classify_form(p["title"])
        top_out.append({
            "post_uid": p["post_uid"],
            "title": _text(p["title"], 120),
            "post_url": p["post_url"] or None,
            "posted_at": str(p["posted_at"]) if p.get("posted_at") else None,
            "views": p["views"], "likes": p["likes"], "comments": p["comments"], "shares": p["shares"],
            "engagement_rate": _engagement_rate(p["likes"], p["comments"], p["shares"], p["views"]),
            "form": form, "form_label": _FORM_LABELS[form],
        })

    latest_snapshot = max((str(p.get("snapshot_date")) for p in posts if p.get("snapshot_date")), default=None)
    latest_cap = max((str(p.get("captured_at")) for p in posts if p.get("captured_at")), default=None)

    return {
        "status": "ok",
        "method": METHOD,
        "generated_at": _utcnow_iso(),
        "time_basis": TIME_BASIS,
        "channel": {
            "id": cid, "platform": ch.get("platform"), "handle": ch.get("account_handle"),
            "display_name": _text(ch.get("display_name"), 80), "url": ch.get("url") or None,
            "followers": _int0(fol.get("followers")),
            "followers_snapshot_date": str(fol.get("snapshot_date")) if fol.get("snapshot_date") else None,
        },
        "posts_analyzed": n,
        "avg_views": avg_views,
        "avg_engagement_rate": avg_er,
        "form_breakdown": agg["form_breakdown"],
        "daypart_breakdown": agg["daypart_breakdown"],
        "best_form": agg["best_form"],
        "best_daypart": agg["best_daypart"],
        "top_posts": top_out,
        "spark": _spark(posts),
        "recommendation": _recommend_line(str(ch.get("account_handle") or ""), agg["best_form"], agg["best_daypart"], {"sku": None}),
        "latest_snapshot_date": latest_snapshot,
        "latest_captured_at": latest_cap,
        "basis": (
            f"去重帖 {n} 条(每帖取最新快照);内容形式=标题词表回打;时段=发帖 UTC 小时;样本闸={_MIN_BUCKET_SAMPLE} 帖。"
        ),
    }
