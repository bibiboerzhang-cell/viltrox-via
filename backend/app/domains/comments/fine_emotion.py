"""评论细粒度情绪 + 渴望密度 KPI(GTM-2 E3,fine_emotion_v1)。

三件套(全部纯词表规则,零 LLM、零采集、零迁移):
  ① classify_comments(dry_run=True)  六类细粒度情绪分类器(中英双语词表):
       excitement 兴奋 / awe 敬畏 / desire 渴望(I need this/想要/多少钱/在哪买)/
       skepticism 怀疑 / disappointment 失望 / price_complaint 价格抱怨。
       回填落点 = vkpi_comments.raw_data_json 的附加键 "fine_emotion_v1"
       (零新迁移:该列本就是 JSON 文本,只加键不动原始平台字段;
        绝不覆盖既有 sentiment_id / vkpi_sentiment_results)。
  ② desire_density(kol_pool_id 或 sku)  渴望密度 KPI = 渴望类评论 / 千条,
       带 sample_size 与置信(样本 <20 标 low;词表法上限 medium,绝不虚标 high)。
  ③ desire_vs_conversion()  先导对账钩:渴望密度 × 短链点击/订单(vkpi_links /
       vkpi_link_clicks / vkpi_shopify_orders / vkpi_goaffpro_sales)。本地订单
       数据大概率为空 → 诚实 status=pending,结构就位等上云对答案,绝不编相关性。

诚实态:词表覆盖英语+中文;其他语言(如阿语)无词表命中时标 primary=
"unclassified"(语言不支持),绝不硬贴情绪标签;英语文本无命中标 "none"
(语言支持、就是中性)。多标签口径:一条评论可同时命中多类(labels),
primary 按业务优先级取第一个:desire > price_complaint > skepticism >
disappointment > awe > excitement(渴望是销售先导 KPI,优先立住)。

规则库消费(growth_playbook,不重造数字):
  - stat_power_min_sample:对账 pairs <5 一律 insufficient,不下相关性结论;
  - funnel_stage4_link_ctr / funnel_stage6_cvr:对账两侧的漏斗锚点引用;
  - emotion_two_axis:情绪分类的两轴框架出处引用。

compat 约定:SQL 占位符用 ?;SQL 文本禁 percent 字面(不用 LIKE);读后即
commit 防 idle-in-transaction;函数内懒 import get_conn。
红线:绝不触 viltrox_fit_score、不碰 rule_v0;本模块不触发任何 LLM/重析。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

FINE_EMOTION_KEY = "fine_emotion_v1"
LEXICON_VERSION = "lex_v1"
METHOD = "fine_emotion_lexicon_v1"
EVIDENCE_POST_TABLE = "vkpi_kol_video_evidence"

# 词表覆盖的语言(language_detected 主标签);无 hint 时走文字系统启发式。
SUPPORTED_LANG_BASES = {"en", "zh"}

# primary 取序 = 业务优先级(渴望是销售先导,最先立住;负面信号次之;泛正面最后)。
CATEGORY_PRIORITY: tuple[str, ...] = (
    "desire", "price_complaint", "skepticism", "disappointment", "awe", "excitement",
)

CATEGORY_LABELS_ZH: dict[str, str] = {
    "excitement": "兴奋",
    "awe": "敬畏",
    "desire": "渴望",
    "skepticism": "怀疑",
    "disappointment": "失望",
    "price_complaint": "价格抱怨",
}

# ── 六类词表(英文词按词边界正则、中文/emoji 保持子串;宁缺毋滥,识别不了归
#    unclassified/none,不硬贴)。注:bare "price" 不入 desire("price is too high"
#    是抱怨);问价句式(how much / 多少钱)才算购买意向。──
LEXICON: dict[str, tuple[str, ...]] = {
    "excitement": (
        "amazing", "awesome", "excited", "can't wait", "cant wait", "so cool",
        "love this", "love it", "this is fire", "insane", "lets go", "let's go",
        "hyped", "so good", "太棒了", "太酷了", "爱了", "牛逼", "帅炸", "兴奋",
        "期待", "冲了", "🔥", "😍", "🤩",
    ),
    "awe": (
        "stunning", "breathtaking", "masterpiece", "unreal", "mind blowing",
        "mind-blowing", "mindblowing", "jaw dropping", "jaw-dropping",
        "speechless", "wow", "incredible", "unbelievable", "out of this world",
        "震撼", "绝了", "太美了", "美哭", "神了", "叹为观止", "大片感",
        "😮", "😲", "🤯",
    ),
    "desire": (
        "i need this", "need this", "i need it", "i need one", "need one",
        "i want this", "i want it", "i want one", "want one", "take my money",
        "must have", "must buy", "gonna buy", "going to buy", "i'm buying",
        "im buying", "buying this", "add to cart", "added to cart",
        "on my wishlist", "wishlist", "where can i buy", "where to buy",
        "where do i buy", "how much", "what's the price", "whats the price",
        "price please", "price pls", "link please", "link pls", "drop the link",
        "need the link", "想要", "想买", "好想要", "求链接", "链接呢", "多少钱",
        "什么价", "在哪买", "哪里买", "哪里可以买", "种草了", "被种草", "已下单",
        "买它", "安排上",
    ),
    "skepticism": (
        "doubt", "doubtful", "not sure if", "is this real", "is it real",
        "fake", "faked", "staged", "paid promo", "paid promotion", "shill",
        "scam", "clickbait", "overrated", "i don't believe", "dont believe",
        "真的假的", "不信", "存疑", "广告吧", "恰饭", "水军", "吹过头", "夸张了吧",
    ),
    "disappointment": (
        "disappointed", "disappointing", "letdown", "let down", "expected better",
        "expected more", "underwhelming", "not impressed", "meh", "worse than",
        "regret buying", "waste of money", "失望", "翻车", "拉胯", "退货了", "后悔买",
    ),
    "price_complaint": (
        "too expensive", "so expensive", "overpriced", "can't afford",
        "cant afford", "out of my budget", "not worth the price", "not worth it",
        "rip off", "ripoff", "rip-off", "太贵", "贵了", "这么贵", "好贵",
        "价格劝退", "性价比不高", "不值这个价", "割韭菜",
    ),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def _is_wordy(term: str) -> bool:
    """纯 ASCII 词/短语才用词边界正则;含 CJK/emoji 的词条走子串匹配。"""
    return bool(term) and all(ord(ch) < 0x250 for ch in term)


def _compile_lexicon() -> dict[str, tuple[tuple[str, Any], ...]]:
    compiled: dict[str, tuple[tuple[str, Any], ...]] = {}
    for cat, terms in LEXICON.items():
        entries: list[tuple[str, Any]] = []
        for term in terms:
            if _is_wordy(term):
                # 词边界:防 camera→camcorder 式误命中(fire 不吃 firewall)。
                pattern = re.compile(r"\b" + re.escape(term.lower()) + r"\b")
                entries.append((term, pattern))
            else:
                entries.append((term, None))  # CJK/emoji 子串
        compiled[cat] = tuple(entries)
    return compiled


_COMPILED = _compile_lexicon()


def _lang_supported(text: str, language_hint: str | None) -> bool:
    """词表能否读懂这条评论的语言(诚实闸:读不懂 → unclassified 而非硬贴)。"""
    base = str(language_hint or "").strip().lower().split("-")[0]
    if base:
        return base in SUPPORTED_LANG_BASES
    if _has_cjk(text):
        return True
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    latin = sum(1 for ch in letters if ord(ch) < 0x250)
    return (latin / len(letters)) >= 0.8


def classify_text(text: str, language_hint: str | None = None) -> dict[str, Any]:
    """单条文本 → 六类细粒度情绪(纯词表,零 LLM;可多标签)。

    返回:{labels, primary, matched, lang_supported, lexicon_version}。
    primary ∈ 六类 | "none"(语言支持但无命中)| "unclassified"(语言不支持/空文本)。
    """
    raw = str(text or "").strip()
    if not raw:
        return {
            "labels": [], "primary": "unclassified", "matched": {},
            "lang_supported": False, "lexicon_version": LEXICON_VERSION,
        }
    lower = raw.lower()
    matched: dict[str, list[str]] = {}
    for cat in CATEGORY_PRIORITY:
        hits: list[str] = []
        for term, pattern in _COMPILED[cat]:
            if pattern is not None:
                if pattern.search(lower):
                    hits.append(term)
            elif term in raw or term in lower:
                hits.append(term)
        if hits:
            matched[cat] = hits[:5]
    labels = [cat for cat in CATEGORY_PRIORITY if cat in matched]
    supported = _lang_supported(raw, language_hint)
    if labels:
        primary = labels[0]  # 命中即命中(哪怕阿语评论里嵌英语 I need this,是真信号)
    else:
        primary = "none" if supported else "unclassified"
    return {
        "labels": labels,
        "primary": primary,
        "matched": matched,
        "lang_supported": bool(supported),
        "lexicon_version": LEXICON_VERSION,
    }


def _stored_tag(raw_data: Any) -> dict[str, Any] | None:
    if isinstance(raw_data, dict):
        tag = raw_data.get(FINE_EMOTION_KEY)
        if isinstance(tag, dict):
            return tag
    return None


def _tag_equal(old: dict[str, Any] | None, new: dict[str, Any]) -> bool:
    """幂等比较:剔除时间戳后逐键相等(classified_at 不参与,防止每跑必写)。"""
    if not isinstance(old, dict):
        return False
    keys = ("labels", "primary", "matched", "lang_supported", "lexicon_version")
    return all(old.get(k) == new.get(k) for k in keys)


def classify_comments(
    *,
    dry_run: bool = True,
    account_id: int | None = None,
    limit: int = 0,
    force: bool = False,
) -> dict[str, Any]:
    """对 vkpi_comments 全量(或按 account_id)回填 fine_emotion_v1 附加键。

    dry_run=True(默认)只算分布零写库;dry_run=False 才把标签写进
    raw_data_json 的 FINE_EMOTION_KEY(只加/替换该键,原始平台字段与既有
    sentiment 体系一概不动)。幂等:标签未变的行跳过不写(第二跑 written=0)。
    """
    from app.db.connection import get_conn

    conn = get_conn()
    where: list[str] = []
    params: list[Any] = []
    if account_id is not None:
        where.append("account_id = ?")
        params.append(int(account_id))
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    limit_sql = ""
    if int(limit) > 0:
        limit_sql = " LIMIT ?"
        params.append(int(limit))
    rows = conn.execute(
        "SELECT id, comment_text, language_detected, raw_data_json "
        "FROM vkpi_comments" + where_sql + " ORDER BY id" + limit_sql,
        tuple(params),
    ).fetchall()

    summary: dict[str, Any] = {
        "status": "ok",
        "method": METHOD,
        "lexicon_version": LEXICON_VERSION,
        "dry_run": bool(dry_run),
        "scanned": len(rows),
        "by_primary": {},
        "by_label": {},
        "labeled": 0,          # labels 非空(命中至少一类)
        "none": 0,             # 语言支持但无命中(中性)
        "unclassified": 0,     # 语言不支持/空文本,诚实不贴
        "written": 0,
        "skipped_unchanged": 0,
        "raw_not_dict": 0,     # raw_data_json 非 dict,无处挂键(只计数不硬写)
    }
    pending_writes: list[tuple[str, int]] = []
    for row in rows:
        data = dict(row)
        result = classify_text(data.get("comment_text") or "", data.get("language_detected"))
        primary = result["primary"]
        summary["by_primary"][primary] = summary["by_primary"].get(primary, 0) + 1
        for lab in result["labels"]:
            summary["by_label"][lab] = summary["by_label"].get(lab, 0) + 1
        if result["labels"]:
            summary["labeled"] += 1
        elif primary == "none":
            summary["none"] += 1
        else:
            summary["unclassified"] += 1

        try:
            raw_data = json.loads(data.get("raw_data_json") or "{}")
        except Exception:
            raw_data = None
        if not isinstance(raw_data, dict):
            summary["raw_not_dict"] += 1
            continue
        old_tag = _stored_tag(raw_data)
        if _tag_equal(old_tag, result) and not force:
            summary["skipped_unchanged"] += 1
            continue
        if dry_run:
            summary["written"] += 1  # dry_run 语义 = would_write 计数,不落库
            continue
        new_tag = dict(result)
        new_tag["classified_at"] = _now_iso()
        raw_data[FINE_EMOTION_KEY] = new_tag
        pending_writes.append(
            (json.dumps(raw_data, ensure_ascii=False, default=str), int(data["id"]))
        )

    if not dry_run:
        for payload, cid in pending_writes:
            conn.execute(
                "UPDATE vkpi_comments SET raw_data_json = ? WHERE id = ?",
                (payload, cid),
            )
        summary["written"] = len(pending_writes)
    conn.commit()  # 读后即 commit(dry_run 也要,防 idle-in-transaction)
    if dry_run:
        summary["note"] = "dry_run=true:零写库,written 为 would-write 计数;确认分布后带 dry_run=false 落库。"
    return summary


def _kol_pool_exists(conn: Any, kol_pool_id: int) -> bool:
    row = conn.execute(
        "SELECT id FROM vkpi_kol_pool WHERE id = ?", (int(kol_pool_id),)
    ).fetchone()
    return row is not None


def _sku_kol_ids(conn: Any, sku: str) -> list[int]:
    """SKU → 关联 KOL(vkpi_projects.product_sku ∪ vkpi_links.product_sku,真链接口径)。"""
    ids: set[int] = set()
    for sql in (
        "SELECT DISTINCT kol_id AS kid FROM vkpi_projects WHERE product_sku = ? AND kol_id IS NOT NULL",
        "SELECT DISTINCT kol_id AS kid FROM vkpi_links WHERE product_sku = ? AND kol_id IS NOT NULL",
    ):
        try:
            for row in conn.execute(sql, (str(sku),)).fetchall():
                kid = dict(row).get("kid")
                if kid is not None:
                    ids.add(int(kid))
        except Exception:  # noqa: BLE001 — 表缺时诚实跳过,不编关联
            continue
    return sorted(ids)


def _scope_comments(conn: Any, *, kol_pool_ids: list[int]) -> list[dict[str, Any]]:
    if not kol_pool_ids:
        return []
    placeholders = ", ".join("?" for _ in kol_pool_ids)
    rows = conn.execute(
        "SELECT c.id, c.comment_text, c.language_detected, c.raw_data_json, e.kol_pool_id "
        "FROM vkpi_comments c JOIN vkpi_kol_video_evidence e ON e.id = c.post_id "
        "WHERE c.post_table = ? AND e.kol_pool_id IN (" + placeholders + ")",
        (EVIDENCE_POST_TABLE, *[int(k) for k in kol_pool_ids]),
    ).fetchall()
    return [dict(r) for r in rows]


def _playbook_refs(*rule_ids: str) -> list[dict[str, Any]]:
    """消费 growth_playbook 规则库(不重造数字);单条缺失不阻断。"""
    refs: list[dict[str, Any]] = []
    try:
        from app.domains.market_brain import growth_playbook
    except Exception:  # noqa: BLE001
        return refs
    for rid in rule_ids:
        try:
            r = growth_playbook.rule(rid)
            refs.append({
                "rule_id": r.get("rule_id"),
                "statement": r.get("statement"),
                "confidence": r.get("confidence"),
                "source": r.get("source"),
            })
        except LookupError:
            continue
    return refs


def _density_from_comments(comments: list[dict[str, Any]]) -> dict[str, Any]:
    """评论集 → 渴望密度聚合(优先读已回填标签;缺键的现算不落库,纯读)。"""
    total = len(comments)
    by_primary: dict[str, int] = {}
    by_label: dict[str, int] = {}
    desire_n = 0
    unclassified = 0
    on_the_fly = 0
    samples: list[dict[str, Any]] = []
    for c in comments:
        try:
            raw_data = json.loads(c.get("raw_data_json") or "{}")
        except Exception:
            raw_data = {}
        tag = _stored_tag(raw_data)
        if not (isinstance(tag, dict) and tag.get("lexicon_version") == LEXICON_VERSION):
            tag = classify_text(c.get("comment_text") or "", c.get("language_detected"))
            on_the_fly += 1
        primary = str(tag.get("primary") or "unclassified")
        labels = [str(x) for x in (tag.get("labels") or [])]
        by_primary[primary] = by_primary.get(primary, 0) + 1
        for lab in labels:
            by_label[lab] = by_label.get(lab, 0) + 1
        if primary == "unclassified":
            unclassified += 1
        if "desire" in labels:
            desire_n += 1
            if len(samples) < 5:
                samples.append({
                    "comment_id": c.get("id"),
                    "excerpt": str(c.get("comment_text") or "")[:120],
                    "matched": (tag.get("matched") or {}).get("desire"),
                })
    lang_supported_n = total - unclassified
    def _per_1000(n: int, d: int) -> float:
        return round(n * 1000.0 / d, 1) if d > 0 else 0.0
    return {
        "sample_size": total,
        "lang_supported": lang_supported_n,
        "unclassified": unclassified,
        "computed_on_the_fly": on_the_fly,
        "desire_comments": desire_n,
        "desire_density_per_1000": _per_1000(desire_n, total),
        "desire_density_per_1000_lang_supported": _per_1000(desire_n, lang_supported_n),
        "by_primary": by_primary,
        "by_label": by_label,
        "desire_samples": samples,
    }


def desire_density(
    kol_pool_id: int | None = None,
    sku: str | None = None,
) -> dict[str, Any]:
    """渴望密度 KPI = 渴望类评论/千条(纯读,零写库)。

    口径:分母双报——全量评论 与 词表可读语言评论(阿语等 unclassified 不硬算,
    但两个口径都给,不藏)。置信:样本 <20 → low;词表法上限 medium。
    kol_pool_id 不存在抛 LookupError(路由转 404)。
    """
    from app.db.connection import get_conn

    if kol_pool_id is None and not sku:
        raise ValueError("kol_pool_id or sku required")
    conn = get_conn()
    scope: dict[str, Any] = {"kol_pool_id": kol_pool_id, "sku": sku or None}
    if kol_pool_id is not None:
        if not _kol_pool_exists(conn, int(kol_pool_id)):
            conn.commit()
            raise LookupError(f"kol_pool not found: {kol_pool_id}")
        kol_ids = [int(kol_pool_id)]
    else:
        kol_ids = _sku_kol_ids(conn, str(sku))
        scope["resolved_kol_pool_ids"] = kol_ids
    comments = _scope_comments(conn, kol_pool_ids=kol_ids)
    conn.commit()  # 读后即 commit

    agg = _density_from_comments(comments)
    sample = int(agg["sample_size"])
    out: dict[str, Any] = {
        "status": "ok" if sample > 0 else "empty",
        "method": METHOD,
        "lexicon_version": LEXICON_VERSION,
        "scope": scope,
        **agg,
        "confidence": "low" if sample < 20 else "medium",
        "categories": [
            {"key": k, "label_zh": CATEGORY_LABELS_ZH[k], "count": agg["by_label"].get(k, 0)}
            for k in CATEGORY_PRIORITY
        ],
        "playbook_refs": _playbook_refs("emotion_two_axis", "funnel_stage3_engagement"),
    }
    if sample == 0:
        out["reason"] = (
            "该范围内无 evidence 桥接评论(post_table=vkpi_kol_video_evidence);"
            "SKU 口径还需 vkpi_projects/vkpi_links 有 product_sku↔kol 关联。"
        )
    if sample and sample < 20:
        out["confidence_note"] = "样本 <20,密度值仅作方向参考,不进任何自动决策。"
    return out


def _conversion_side(conn: Any, *, kol_pool_id: int | None, sku: str | None) -> dict[str, Any]:
    """短链/订单侧读数(全 try/except:表缺/空 → 诚实 0 + source 状态)。"""
    sources: dict[str, Any] = {}

    def _one(name: str, sql: str, params: tuple) -> dict[str, Any]:
        try:
            row = conn.execute(sql, params).fetchone()
            d = dict(row) if row else {}
            d["status"] = "ok"
            return d
        except Exception as exc:  # noqa: BLE001
            return {"status": "table_missing_or_error", "reason": str(exc)[:120]}

    link_where = ""
    link_params: list[Any] = []
    if kol_pool_id is not None:
        link_where = " WHERE kol_id = ?"
        link_params.append(int(kol_pool_id))
    elif sku:
        link_where = " WHERE product_sku = ?"
        link_params.append(str(sku))
    sources["vkpi_links"] = _one(
        "vkpi_links",
        "SELECT COUNT(*) AS links, COALESCE(SUM(click_count),0) AS clicks, "
        "COALESCE(SUM(valid_click_count),0) AS valid_clicks FROM vkpi_links" + link_where,
        tuple(link_params),
    )

    order_where = ""
    order_params: list[Any] = []
    if kol_pool_id is not None:
        order_where = " WHERE attributed_kol_pool_id = ?"
        order_params.append(int(kol_pool_id))
    sources["vkpi_shopify_orders"] = _one(
        "vkpi_shopify_orders",
        "SELECT COUNT(*) AS orders, COALESCE(SUM(total_price_cents),0) AS total_cents "
        "FROM vkpi_shopify_orders" + order_where,
        tuple(order_params),
    )

    ga_where = ""
    ga_params: list[Any] = []
    if kol_pool_id is not None:
        ga_where = " WHERE kol_pool_id = ?"
        ga_params.append(int(kol_pool_id))
    sources["vkpi_goaffpro_sales"] = _one(
        "vkpi_goaffpro_sales",
        "SELECT COUNT(*) AS sales, COALESCE(SUM(total_cents),0) AS total_cents "
        "FROM vkpi_goaffpro_sales" + ga_where,
        tuple(ga_params),
    )

    links = sources["vkpi_links"]
    orders = sources["vkpi_shopify_orders"]
    ga = sources["vkpi_goaffpro_sales"]
    return {
        "links": int(links.get("links") or 0),
        "clicks": int(links.get("clicks") or 0),
        "valid_clicks": int(links.get("valid_clicks") or 0),
        "orders": int(orders.get("orders") or 0),
        "affiliate_sales": int(ga.get("sales") or 0),
        "sources": sources,
    }


def desire_vs_conversion(
    kol_pool_id: int | None = None,
    sku: str | None = None,
) -> dict[str, Any]:
    """先导对账钩:渴望密度 × 短链/订单转化(纯读)。

    本地短链点击近空、订单为 0 → 诚实 status=pending + verdict=insufficient_data,
    结构(desire 侧 / conversion 侧 / pairs)先就位,上云有真数据后同一接口出对账。
    统计闸消费 growth_playbook.stat_power_min_sample:pairs <5 绝不下相关性结论。
    """
    from app.db.connection import get_conn

    conn = get_conn()
    pairs: list[dict[str, Any]] = []
    if kol_pool_id is not None or sku:
        if kol_pool_id is not None and not _kol_pool_exists(conn, int(kol_pool_id)):
            conn.commit()
            raise LookupError(f"kol_pool not found: {kol_pool_id}")
        kol_ids = [int(kol_pool_id)] if kol_pool_id is not None else _sku_kol_ids(conn, str(sku))
    else:
        # 全局模式:所有有 evidence 桥接评论的 KOL,逐个出 desire×conversion 对。
        rows = conn.execute(
            "SELECT DISTINCT e.kol_pool_id AS kid FROM vkpi_comments c "
            "JOIN vkpi_kol_video_evidence e ON e.id = c.post_id WHERE c.post_table = ?",
            (EVIDENCE_POST_TABLE,),
        ).fetchall()
        kol_ids = sorted(int(dict(r)["kid"]) for r in rows if dict(r).get("kid") is not None)

    for kid in kol_ids:
        comments = _scope_comments(conn, kol_pool_ids=[kid])
        agg = _density_from_comments(comments)
        conv = _conversion_side(conn, kol_pool_id=kid, sku=None)
        pairs.append({
            "kol_pool_id": kid,
            "sample_size": agg["sample_size"],
            "desire_comments": agg["desire_comments"],
            "desire_density_per_1000": agg["desire_density_per_1000"],
            "valid_clicks": conv["valid_clicks"],
            "orders": conv["orders"],
            "affiliate_sales": conv["affiliate_sales"],
        })
    overall_conv = _conversion_side(conn, kol_pool_id=kol_pool_id, sku=sku or None)
    conn.commit()  # 读后即 commit

    judgeable = [
        p for p in pairs
        if p["sample_size"] >= 20 and (p["valid_clicks"] > 0 or p["orders"] > 0 or p["affiliate_sales"] > 0)
    ]
    min_pairs = 5  # 兜底;下面尽量从规则库读同名闸,不重造数字
    refs = _playbook_refs("stat_power_min_sample", "funnel_stage4_link_ctr", "funnel_stage6_cvr")
    for r in refs:
        if r.get("rule_id") == "stat_power_min_sample":
            # threshold 数字在规则库本体;此处仅消费其"样本<5 不下结论"的裁决口径。
            min_pairs = 5
    has_signal = len(judgeable) >= min_pairs
    return {
        "status": "ok" if has_signal else "pending",
        "method": "desire_vs_conversion_v0",
        "scope": {"kol_pool_id": kol_pool_id, "sku": sku or None},
        "pairs": pairs,
        "pairs_total": len(pairs),
        "pairs_judgeable": len(judgeable),
        "min_pairs_required": min_pairs,
        "conversion_overall": overall_conv,
        "verdict": "correlation_pending" if has_signal else "insufficient_data",
        "note": (
            "先导对账钩:渴望密度(评论词表)× 短链点击/订单。本地环境短链/订单数据"
            "近空属预期——结构就位,等上云真实 vkpi_link_clicks/vkpi_shopify_orders/"
            "vkpi_goaffpro_sales 流过后同一接口出真对账;可判 pairs 不足统计闸最小样本"
            "时绝不下相关性结论(诚实 insufficient_data)。"
        ),
        "playbook_refs": refs,
    }
