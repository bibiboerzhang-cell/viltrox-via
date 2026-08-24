"""评论情报(comment_intel v1)—— 纯读评论集出 购买意向/品牌提及/活跃时段/互动质量/铁粉,零成本。

数据源两路(都不新抓、零外调):
  - 本地:vkpi_comments(IG/TT 及已入库评论,经 account_id / evidence 双桥);
  - 内存:YouTube Data API 抽样已带回的评论列表(audience_stats 传入,不落 vkpi_comments)。
分析器 analyze_comments 是纯函数,吃统一评论 dict:
  {text, author, created_at, like_count, is_reply, video_key}
结果并入 vkpi_kol_pool.audience_estimated_json 的 "comment_intel" 子键(写入在 audience_stats)。

SQL 约束:compat 层禁 percent 字符(psycopg 会当占位符),本模块 SQL 不用 LIKE。
红线:纯读统计,绝不写 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 购买意向词表(小写子串匹配;英/中双语,含口语变体)。
PURCHASE_INTENT_TERMS: tuple[str, ...] = (
    "where to buy", "where can i buy", "where do i buy", "where did you buy", "how much", "price",
    "cost?", "how many dollars", "link please", "link pls", "buy link", "purchase link", "shop link",
    "discount", "coupon", "promo code", "discount code", "on sale", "any deal", "amazon link",
    "aliexpress", "add to cart", "i want one", "i need one", "gonna buy", "going to buy", "just ordered",
    "just bought", "take my money", "worth buying", "should i buy", "is it worth",
    "多少钱", "哪里买", "哪里可以买", "在哪买", "怎么买", "求链接", "求购买链接", "链接呢", "有优惠",
    "优惠码", "折扣", "打折", "想买", "准备买", "已下单", "已入手", "种草", "值得买", "链接发一下",
)

# 品牌词表:Viltrox + 竞品(镜头/影像圈)。value=同品牌别名(小写子串)。
BRAND_TERMS: dict[str, tuple[str, ...]] = {
    "Viltrox": ("viltrox", "唯卓仕", "唯卓"),
    "TTArtisan": ("ttartisan", "tt artisan", "铭匠"),
    "Sigma": ("sigma", "适马"),
    "Sony": ("sony", "索尼"),
    "Canon": ("canon", "佳能"),
    "Nikon": ("nikon", "尼康"),
    "Samyang": ("samyang", "森养", "三阳"),
    "Rokinon": ("rokinon",),
    "7Artisans": ("7artisans", "七工匠"),
    "Laowa": ("laowa", "老蛙"),
    "Sirui": ("sirui", "思锐"),
    "Tamron": ("tamron", "腾龙"),
    "Meike": ("meike", "美科"),
}

_WORDISH_RE = re.compile(r"[a-z0-9]")


def _parse_hour_utc(value: Any) -> int | None:
    """created_at(datetime / ISO str)→ UTC 小时;解析不了返回 None(诚实不猜)。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).hour
    text = str(value).strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).hour
    except Exception:
        return None


def _truncate(text: str, limit: int = 80) -> str:
    text = str(text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _matches_any(lower_text: str, terms: tuple[str, ...]) -> bool:
    return any(term in lower_text for term in terms)


def analyze_comments(comments: list[dict[str, Any]]) -> dict[str, Any]:
    """纯函数:统一评论 dict 列表 -> comment_intel 块。空集诚实返回 sample_size=0。

    评论 dict 字段(全部可缺省):text, author, created_at, like_count, is_reply, video_key。
    """
    rows = [c for c in (comments or []) if str((c or {}).get("text") or "").strip()]
    n = len(rows)
    if n == 0:
        return {"sample_size": 0, "note": "无评论可分析(诚实空,不编造)"}

    # ── 购买意向(带证据:最多 5 条原句样本,可下钻)──
    intent_rows = [c for c in rows if _matches_any(str(c.get("text") or "").lower(), PURCHASE_INTENT_TERMS)]
    intent_rows.sort(key=lambda c: int(c.get("like_count") or 0), reverse=True)
    purchase_intent = {
        "count": len(intent_rows),
        "pct": round(100.0 * len(intent_rows) / n, 1),
        "samples": [
            {
                "author_handle": str(c.get("author") or ""),
                "text": _truncate(c.get("text"), 120),
                "created_at": str(c.get("created_at") or "")[:19],
            }
            for c in intent_rows[:5]
        ],
    }

    # ── 品牌提及(每品牌带最多 2 条样本证据)──
    brand_counts: list[dict[str, Any]] = []
    for brand, aliases in BRAND_TERMS.items():
        matched = [c for c in rows if _matches_any(str(c.get("text") or "").lower(), aliases)]
        if matched:
            matched.sort(key=lambda c: int(c.get("like_count") or 0), reverse=True)
            brand_counts.append(
                {
                    "brand": brand,
                    "count": len(matched),
                    "samples": [
                        {"author_handle": str(c.get("author") or ""), "text": _truncate(c.get("text"), 120)}
                        for c in matched[:2]
                    ],
                }
            )
    brand_counts.sort(key=lambda b: (-b["count"], b["brand"]))

    # ── 活跃时段(UTC 24h 直方;附峰值时段评论数作证据)──
    hist = [0] * 24
    timed = 0
    for c in rows:
        hour = _parse_hour_utc(c.get("created_at"))
        if hour is not None:
            hist[hour] += 1
            timed += 1
    top_hours = [h for h, _cnt in sorted(enumerate(hist), key=lambda kv: (-kv[1], kv[0]))[:3] if hist[h] > 0]
    suggestion = ""
    if top_hours:
        lead = top_hours[0]
        suggestion = f"UTC {lead:02d}-{(lead + 2) % 24:02d}时"
    active_hours = {
        "hist": hist,
        "timed_n": timed,
        "top_hours": top_hours,
        "peak_hour_comment_count": hist[top_hours[0]] if top_hours else 0,
        "suggestion": suggestion,
    }

    # ── 互动质量 ──
    video_keys = {str(c.get("video_key")) for c in rows if c.get("video_key") not in (None, "")}
    replies = sum(1 for c in rows if bool(c.get("is_reply")))
    likes = [int(c.get("like_count") or 0) for c in rows]
    engagement = {
        "comments_per_video": round(n / len(video_keys), 1) if video_keys else None,
        "video_n": len(video_keys),
        "reply_pct": round(100.0 * replies / n, 1),
        "likes_median": int(statistics.median(likes)) if likes else 0,
    }

    # ── 铁粉(重复评论者)──
    author_counter: Counter = Counter(
        str(c.get("author") or "").strip() for c in rows if str(c.get("author") or "").strip()
    )
    sample_by_author: dict[str, str] = {}
    for c in rows:
        author = str(c.get("author") or "").strip()
        if author and author not in sample_by_author:
            sample_by_author[author] = _truncate(c.get("text"), 60)
    superfans = [
        {"handle": author, "count": count, "sample": sample_by_author.get(author, "")}
        for author, count in author_counter.most_common(10)
        if count >= 2
    ]

    return {
        "sample_size": n,
        "purchase_intent": purchase_intent,
        "brand_mentions": brand_counts,
        "active_hours": active_hours,
        "engagement": engagement,
        "superfans": superfans,
        "method": "comment_intel_v1",
        "note": "评论集统计(词表/直方),零 LLM 零外调",
    }


def load_local_comments(kol_pool_id: int, *, conn: Any = None, limit: int = 1000) -> list[dict[str, Any]]:
    """vkpi_comments -> 统一评论 dict。双桥:account_id 直连;或 evidence 口径 post_table + post_id。"""
    from app.db.connection import get_conn

    db = conn or get_conn()
    sql_fields = "comment_text, author_handle, created_at, likes_count, parent_comment_id, post_id"
    rows = db.execute(
        f"SELECT {sql_fields} FROM vkpi_comments "
        "WHERE account_id=? AND post_table IN ('evidence','vkpi_kol_video_evidence') LIMIT ?",
        (int(kol_pool_id), int(limit)),
    ).fetchall()
    if not rows:
        ev = db.execute(
            "SELECT id FROM vkpi_kol_video_evidence WHERE kol_pool_id=? LIMIT 100",
            (int(kol_pool_id),),
        ).fetchall()
        eids = [int(dict(e)["id"]) for e in ev]
        if eids:
            placeholders = ",".join(["?"] * len(eids))
            rows = db.execute(
                f"SELECT {sql_fields} FROM vkpi_comments "
                f"WHERE post_table IN ('evidence','vkpi_kol_video_evidence') AND post_id IN ({placeholders}) LIMIT ?",
                (*eids, int(limit)),
            ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        rec = dict(r)
        out.append(
            {
                "text": str(rec.get("comment_text") or ""),
                "author": str(rec.get("author_handle") or ""),
                "created_at": rec.get("created_at"),
                "like_count": int(rec.get("likes_count") or 0),
                "is_reply": bool(str(rec.get("parent_comment_id") or "").strip()),
                "video_key": rec.get("post_id"),
            }
        )
    return out


def comment_intel_for_kol(kol_pool_id: int, *, conn: Any = None, limit: int = 1000) -> dict[str, Any]:
    """入口:读该 KOL 本地评论 -> analyze_comments。无评论 sample_size=0。"""
    comments = load_local_comments(int(kol_pool_id), conn=conn, limit=limit)
    result = analyze_comments(comments)
    result["source"] = "vkpi_comments_pool_evidence"
    result["kol_pool_id"] = int(kol_pool_id)
    return result


# ── 共同粉丝(audience overlap)—— 矩阵投放去重:受众重叠高的 KOL 不必都投 ──

def _has_kol_comments(db: Any) -> bool:
    """kol_comments(account_dossier 评论仓)存在与否;不存在的环境静默跳过该桥。"""
    try:
        row = db.execute("SELECT to_regclass('kol_comments') AS t").fetchone()
        return bool(dict(row or {}).get("t"))
    except Exception:
        return False


def _rescued_author_key(rec: dict) -> str:
    """platform:handle 键,带 TikTok 批次救援链(author_handle 空时依次
    author_id → raw_data_json 的 uniqueId/username/ownerUsername/uid)。
    与 audience_stats.sample_local_commenters 同口径 —— 否则 TikTok KOL 共同粉丝恒为 0。"""
    handle = str(rec.get("author_handle") or "").strip()
    if not handle:
        handle = str(rec.get("author_id") or "").strip()
    if not handle:
        try:
            raw = rec.get("raw_data_json")
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
            if isinstance(data, dict):
                handle = str(
                    data.get("uniqueId") or data.get("username") or data.get("ownerUsername") or data.get("uid") or ""
                ).strip()
        except Exception:
            handle = ""
    if not handle:
        return ""
    return f"{str(rec.get('platform') or '').lower()}:{handle.lower()}"


def _self_commenter_keys(db: Any, kol_pool_id: int) -> set[str]:
    """该 KOL 的评论者集合(键=platform:handle 小写 —— 平台内比对,跨平台同名不算重叠)。

    三路并集:vkpi_comments 的 account_id 桥 + evidence 桥;以及该池行 linked_main_kol_id
    对应的 kol_comments(account_dossier 账号扫描写入的另一评论仓)。
    """
    keys: set[str] = set()
    rows = db.execute(
        "SELECT platform, author_handle, author_id, raw_data_json FROM vkpi_comments "
        "WHERE account_id=? AND post_table IN ('evidence','vkpi_kol_video_evidence')",
        (int(kol_pool_id),),
    ).fetchall()
    for r in rows:
        key = _rescued_author_key(dict(r))
        if key:
            keys.add(key)
    ev_rows = db.execute(
        "SELECT c.platform, c.author_handle, c.author_id, c.raw_data_json FROM vkpi_comments c "
        "JOIN vkpi_kol_video_evidence e ON c.post_table IN ('evidence','vkpi_kol_video_evidence') AND c.post_id=e.id "
        "WHERE e.kol_pool_id=?",
        (int(kol_pool_id),),
    ).fetchall()
    for r in ev_rows:
        key = _rescued_author_key(dict(r))
        if key:
            keys.add(key)
    # 主表桥:该池行已 link 到主 kols 时,把 kol_comments 里同主体的评论者并进来。
    if not _has_kol_comments(db):
        return keys
    linked = db.execute(
        "SELECT linked_main_kol_id FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)
    ).fetchone()
    linked_main = int(dict(linked or {}).get("linked_main_kol_id") or 0)
    if linked_main:
        for r in db.execute(
            "SELECT platform, author_handle FROM kol_comments "
            "WHERE kol_id=? AND author_handle IS NOT NULL AND author_handle<>''",
            (linked_main,),
        ).fetchall():
            rec = dict(r)
            keys.add(f"{str(rec.get('platform') or '').lower()}:{str(rec.get('author_handle') or '').strip().lower()}")
    return keys


def self_commenter_keys_for_kols(db: Any, kol_pool_ids: list[int]) -> dict[int, set[str]]:
    """Batch form of ``_self_commenter_keys`` for roster overlap reads.

    The same three bridges and author rescue rules are used; only the repeated
    per-KOL SELECTs are folded into bounded ``IN`` reads. Callers retain the
    single-item helper as a fail-soft fallback if any batch query is unavailable.
    """
    ids: list[int] = []
    for raw in kol_pool_ids:
        try:
            kol_pool_id = int(raw)
        except (TypeError, ValueError):
            continue
        if kol_pool_id > 0 and kol_pool_id not in ids:
            ids.append(kol_pool_id)
    sets = {kol_pool_id: set() for kol_pool_id in ids}
    if not ids:
        return sets
    placeholders = ",".join("?" for _ in ids)

    account_rows = db.execute(
        "SELECT account_id AS kol_pool_id, platform, author_handle, author_id, raw_data_json "
        "FROM vkpi_comments WHERE account_id IN (" + placeholders + ") "
        "AND post_table IN ('evidence','vkpi_kol_video_evidence')",
        tuple(ids),
    ).fetchall()
    evidence_rows = db.execute(
        "SELECT e.kol_pool_id, c.platform, c.author_handle, c.author_id, c.raw_data_json "
        "FROM vkpi_comments c JOIN vkpi_kol_video_evidence e "
        "ON c.post_table IN ('evidence','vkpi_kol_video_evidence') AND c.post_id=e.id "
        "WHERE e.kol_pool_id IN (" + placeholders + ")",
        tuple(ids),
    ).fetchall()
    for rows in (account_rows, evidence_rows):
        for raw in rows:
            rec = dict(raw)
            kol_pool_id = int(rec.get("kol_pool_id") or 0)
            key = _rescued_author_key(rec)
            if kol_pool_id in sets and key:
                sets[kol_pool_id].add(key)

    if not _has_kol_comments(db):
        return sets
    linked_rows = db.execute(
        "SELECT id, linked_main_kol_id FROM vkpi_kol_pool WHERE id IN (" + placeholders + ")",
        tuple(ids),
    ).fetchall()
    pools_by_main: dict[int, list[int]] = {}
    for raw in linked_rows:
        rec = dict(raw)
        linked_main = int(rec.get("linked_main_kol_id") or 0)
        if linked_main > 0:
            pools_by_main.setdefault(linked_main, []).append(int(rec["id"]))
    if not pools_by_main:
        return sets
    main_ids = list(pools_by_main)
    main_placeholders = ",".join("?" for _ in main_ids)
    main_rows = db.execute(
        "SELECT kol_id, platform, author_handle FROM kol_comments "
        "WHERE kol_id IN (" + main_placeholders + ") "
        "AND author_handle IS NOT NULL AND author_handle<>''",
        tuple(main_ids),
    ).fetchall()
    for raw in main_rows:
        rec = dict(raw)
        linked_main = int(rec.get("kol_id") or 0)
        key = (
            f"{str(rec.get('platform') or '').lower()}:"
            f"{str(rec.get('author_handle') or '').strip().lower()}"
        )
        for kol_pool_id in pools_by_main.get(linked_main, []):
            sets[kol_pool_id].add(key)
    return sets


def compute_audience_overlap(kol_pool_id: int, *, conn: Any = None, top_n: int = 5) -> dict[str, Any]:
    """共同粉丝:该 KOL 评论者集合 vs 库内其他 KOL(evidence/account_id 桥)+ 官号(employee_channels 桥)。

    输出 top5 [{kind, peer_id, handle, display_name, shared_count, jaccard}];交集小于 2 的不列。
    基于 vkpi_comments(author_handle 小写、按平台归一);零外调零成本。SQL 禁 percent 字符。
    """
    from app.db.connection import get_conn

    db = conn or get_conn()
    self_keys = _self_commenter_keys(db, int(kol_pool_id))
    if not self_keys:
        return {
            "items": [],
            "self_commenters": 0,
            "note": "该 KOL 暂无入库评论者,无法算重叠(抓评论后自动补)",
        }

    peer_sets: dict[tuple[str, int], set[str]] = {}
    # 桥 1:evidence -> 其他 KOL(带救援链:TikTok 批次 author_handle 常为空)
    rows = db.execute(
        "SELECT e.kol_pool_id AS peer_id, c.platform, c.author_handle, c.author_id, c.raw_data_json FROM vkpi_comments c "
        "JOIN vkpi_kol_video_evidence e ON c.post_table IN ('evidence','vkpi_kol_video_evidence') AND c.post_id=e.id "
        "WHERE e.kol_pool_id<>?",
        (int(kol_pool_id),),
    ).fetchall()
    for r in rows:
        rec = dict(r)
        key = _rescued_author_key(rec)
        if key:
            peer_sets.setdefault(("kol", int(rec["peer_id"])), set()).add(key)
    # 桥 2:account_id -> 其他 KOL(排除官号表口径)
    rows = db.execute(
        "SELECT c.account_id AS peer_id, c.platform, c.author_handle, c.author_id, c.raw_data_json FROM vkpi_comments c "
        "JOIN vkpi_kol_pool p ON p.id=c.account_id "
        "WHERE c.account_id IS NOT NULL AND c.account_id<>? "
        "AND c.post_table IN ('evidence','vkpi_kol_video_evidence')",
        (int(kol_pool_id),),
    ).fetchall()
    for r in rows:
        rec = dict(r)
        key = _rescued_author_key(rec)
        if key:
            peer_sets.setdefault(("kol", int(rec["peer_id"])), set()).add(key)
    # 桥 3:官号(vkpi_employee_channels)
    rows = db.execute(
        "SELECT c.account_id AS peer_id, c.platform, c.author_handle, c.author_id, c.raw_data_json FROM vkpi_comments c "
        "WHERE c.post_table='vkpi_employee_channels' AND c.account_id IS NOT NULL",
        (),
    ).fetchall()
    for r in rows:
        rec = dict(r)
        key = _rescued_author_key(rec)
        if key:
            peer_sets.setdefault(("official", int(rec["peer_id"])), set()).add(key)
    # 桥 4:kol_comments(account_dossier 评论仓,主表 kols.id 口径;排除自己 link 的主体)
    if _has_kol_comments(db):
        self_linked = db.execute(
            "SELECT linked_main_kol_id FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)
        ).fetchone()
        self_main = int(dict(self_linked or {}).get("linked_main_kol_id") or 0)
        rows = db.execute(
            "SELECT kol_id AS peer_id, platform, author_handle FROM kol_comments "
            "WHERE author_handle IS NOT NULL AND author_handle<>''",
            (),
        ).fetchall()
        for r in rows:
            rec = dict(r)
            peer_id = int(rec["peer_id"] or 0)
            if not peer_id or peer_id == self_main:
                continue
            key = f"{str(rec.get('platform') or '').lower()}:{str(rec.get('author_handle') or '').strip().lower()}"
            peer_sets.setdefault(("kol_main", peer_id), set()).add(key)

    scored: list[dict[str, Any]] = []
    for (kind, peer_id), keys in peer_sets.items():
        shared = len(self_keys & keys)
        if shared < 2:
            continue
        union = len(self_keys | keys)
        scored.append(
            {
                "kind": kind,
                "peer_id": peer_id,
                "shared_count": shared,
                "jaccard": round(shared / union, 3) if union else 0.0,
                "peer_commenters": len(keys),
            }
        )
    scored.sort(key=lambda x: (-x["shared_count"], -x["jaccard"]))
    top = scored[: max(1, int(top_n))]
    # 补名字(各表各查一次,不进循环)
    kol_ids = [s["peer_id"] for s in top if s["kind"] == "kol"]
    official_ids = [s["peer_id"] for s in top if s["kind"] == "official"]
    main_ids = [s["peer_id"] for s in top if s["kind"] == "kol_main"]
    names: dict[tuple[str, int], dict[str, str]] = {}
    if kol_ids:
        placeholders = ",".join(["?"] * len(kol_ids))
        for r in db.execute(
            f"SELECT id, handle, display_name FROM vkpi_kol_pool WHERE id IN ({placeholders})", tuple(kol_ids)
        ).fetchall():
            rec = dict(r)
            names[("kol", int(rec["id"]))] = {
                "handle": str(rec.get("handle") or ""),
                "display_name": str(rec.get("display_name") or ""),
            }
    if official_ids:
        placeholders = ",".join(["?"] * len(official_ids))
        for r in db.execute(
            f"SELECT id, account_handle, account_display_name FROM vkpi_employee_channels WHERE id IN ({placeholders})",
            tuple(official_ids),
        ).fetchall():
            rec = dict(r)
            names[("official", int(rec["id"]))] = {
                "handle": str(rec.get("account_handle") or ""),
                "display_name": str(rec.get("account_display_name") or ""),
            }
    if main_ids:
        placeholders = ",".join(["?"] * len(main_ids))
        # 主表 kols 的名字;若有池行 link 回来,优先池行 handle(前端可直接跳)。
        for r in db.execute(
            f"SELECT id, channel_name FROM kols WHERE id IN ({placeholders})", tuple(main_ids)
        ).fetchall():
            rec = dict(r)
            names[("kol_main", int(rec["id"]))] = {
                "handle": str(rec.get("channel_name") or ""),
                "display_name": str(rec.get("channel_name") or ""),
            }
        for r in db.execute(
            f"SELECT id, handle, display_name, linked_main_kol_id FROM vkpi_kol_pool WHERE linked_main_kol_id IN ({placeholders})",
            tuple(main_ids),
        ).fetchall():
            rec = dict(r)
            entry = names.setdefault(("kol_main", int(rec["linked_main_kol_id"])), {})
            entry["handle"] = str(rec.get("handle") or entry.get("handle") or "")
            entry["display_name"] = str(rec.get("display_name") or entry.get("display_name") or "")
            entry["peer_kol_pool_id"] = str(rec.get("id") or "")
    for s in top:
        info = names.get((s["kind"], s["peer_id"])) or {}
        s["handle"] = info.get("handle") or ""
        s["display_name"] = info.get("display_name") or ""
        if info.get("peer_kol_pool_id"):
            s["peer_kol_pool_id"] = int(info["peer_kol_pool_id"])
    return {
        "items": top,
        "self_commenters": len(self_keys),
        "peers_checked": len(peer_sets),
        "method": "audience_overlap_v1",
        "note": "基于入库评论者交集(平台内 handle 归一);矩阵投放去重参考",
    }
