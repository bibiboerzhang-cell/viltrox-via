"""迸发⑤:市场之声监听扩源 —— X(Twitter)+ 摄影论坛(Reddit),默认 OFF。

背景(2026-07-12 立项骨架;2026-07-16 开闸刀接线):监听覆盖卡五源之外,
X 与摄影论坛曾是「未接入·盲区」。骨架把结构、字段映射、闸、状态上报铺好;
开闸刀(listening_executors.py)接上真执行体:Reddit 公开 JSON(免费)+
X Apify pay-per-result actor(预算闸内),帖级实体落 vkpi_market_sources /
vkpi_market_mentions,喂「近期市场热词」(hashtag_trends)。

闸(config-gate,三态,运行时读 os.environ 非 import 时冻结值):
  VKPI_X_COLLECT_ENABLED      X 监听闸(默认 0)
  VKPI_FORUM_COLLECT_ENABLED  摄影论坛监听闸(默认 0)
  - 键不存在  → gate_state="absent"  → 覆盖卡「未接入 · 盲区」
  - 键在但假  → gate_state="off"     → 覆盖卡「骨架就绪 · 待开闸」
  - 键为真    → gate_state="on"      → 调 listening_executors 真采集
                (dry_run=True 只出执行计划,保持零网络零写库)

选型结论(设计单全文见交付报告;此处只留代码级要点):
  X:Apify pay-per-result actor 路线(apidojo/tweet-scraper V2 $0.40/1k 或
     kaitoeasyapi $0.25/1k),官方 X API pay-per-use $0.005/read 贵 ~12-30 倍
     且 legacy Basic($200/月)已关新签。搜索词 = 品牌 + 竞品 + 品类词族,
     复用 competitor_brands.json / signal_taxonomy(见 x_listen_query_terms)。
  论坛:Reddit 是唯一自动化通道(PRAW/public JSON 免费,watchlist 复用
     reddit_stability_strategy);DPReview 论坛(主站对非浏览器 UA 403,
     反自动化姿态明确)与 FredMiranda(无 robots.txt ≠ 许可,ToS 未明示允许)
     按「合规全球最严」口径列为 不抓(no_scrape);DPReview 内容走既有
     RSS 白名单通道(external_signal_smoke ALLOWED_HOSTS 已含 dpreview.com)。

表设计结论:复用既有表,零新表零迁移。帖级实体落 vkpi_market_sources /
vkpi_market_mentions(P5.67/P5.68 建好);评论/回复(后续刀)落 vkpi_comments
(post_table=LISTEN_POST_TABLE, post_id=mention id,platform 枚举 "x"/"forum"
已在 collector 映射)。vkpi_comments.post_table 为 VARCHAR(50) 无 CHECK 约束。

红线:闸 absent/off 的任何路径零网络零写库;dry_run=True 同样零网络零写库;
不触 viltrox_fit_score / rule_v0;覆盖卡展示读真闸态,禁装。
compat 约定:SQL 占位符用 ?;BOOLEAN 读回可能是 int;懒 import get_conn。
"""
from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── 闸键(与 app/core/config.py 中央登记同名)────────────────────────────
GATE_X = "VKPI_X_COLLECT_ENABLED"
GATE_FORUM = "VKPI_FORUM_COLLECT_ENABLED"

_TRUTHY = {"1", "true", "yes", "on"}

# 监听采集的评论落 vkpi_comments 时的 post_table 枚举(帖级实体=市场提及行)
LISTEN_POST_TABLE = "vkpi_market_mentions"

# 平台枚举(collector._standardize_comment 的映射键;x 既有,forum 本刀新增)
X_PLATFORM = "x"
FORUM_PLATFORM = "forum"

# 单日硬上限(执行体尊重;成本按 2026-07-16 实测口径:apidojo 结算 $2.56/1k)
X_LISTEN_DAILY_CAP = 1000       # 条/天 绝对封顶(实测口径 ≈ $2.56/天);日常轮次远低于此
FORUM_LISTEN_DAILY_CAP = 500    # Reddit 公开 JSON 免费;Apify 兜底单 run 计时计费另有 600s 封顶

# 论坛源合规台账(合规全球最严口径;开闸刀不得越过 no_scrape 行)
FORUM_SOURCE_POLICY: tuple[dict[str, str], ...] = (
    {
        "source": "reddit",
        "policy": "allowed",
        "path": "PRAW OAuth(免费 60 req/min)/ public JSON 既有通道",
        "basis": "官方 Data API;watchlist 复用 reddit_stability_strategy.RECOMMENDED_WATCHLIST",
    },
    {
        "source": "dpreview_forums",
        "policy": "no_scrape",
        "path": "论坛不抓;新闻/评测走既有 RSS 白名单(external_signal_smoke)",
        "basis": "2026-07-12 核验:主站对非浏览器 UA 返回 403,反自动化姿态明确",
    },
    {
        "source": "fredmiranda",
        "policy": "no_scrape",
        "path": "暂不接入(人工阅读不在本系统范围)",
        "basis": "2026-07-12 核验:无 robots.txt;缺失≠许可,ToS 未明示允许自动访问",
    },
)


def gate_state(gate_key: str) -> str:
    """三态闸判定:absent(键不存在)/ off(键在但假)/ on(键为真)。

    运行时读 os.environ(非 config import 冻结值),覆盖卡与采集器共用一个口径。
    """
    raw = os.environ.get(gate_key)
    if raw is None:
        return "absent"
    return "on" if raw.strip().lower() in _TRUTHY else "off"


def _disabled(gate_key: str, state: str, platform: str) -> dict[str, Any]:
    """闸关/键缺 → 直接返回,零网络零写库(采集器统一出口)。"""
    return {
        "status": "disabled",
        "platform": platform,
        "gate": gate_key,
        "gate_state": state,
        "items": [],
        "network_calls": 0,
        "reason": (
            f"{gate_key} 未登记(env 键不存在)—— 未接入·盲区。"
            if state == "absent"
            else f"{gate_key}=off —— 骨架就绪·待开闸(设 1 且开闸刀接线后才会真实采集)。"
        ),
    }


# ── 搜索词族(复用既有 lexicon,零新词表;import 失败降级保守兜底)──────


_FALLBACK_BRAND_TERMS: tuple[str, ...] = ("viltrox", "viltrox lab")
_FALLBACK_COMPETITOR_TERMS: tuple[str, ...] = ("sigma lens", "tamron lens", "samyang lens")
_FALLBACK_CATEGORY_TERMS: tuple[str, ...] = ("prime lens", "cine lens", "autofocus lens")


def x_listen_query_terms(*, max_competitors: int = 8) -> dict[str, list[str]]:
    """X 搜索词族 = 品牌 + 竞品 + 品类(全部复用既有 lexicon,纯读零网络)。

    竞品经 competitor_text.load_competitor_brands(#51 competitor_brands.json 同源);
    品类经 signal_taxonomy 关键词组。兄弟件契约:ImportError 降级自带保守词表。
    """
    brand = list(_FALLBACK_BRAND_TERMS)
    competitors: list[str] = []
    categories: list[str] = []
    try:
        # competitor_brands.json 同源(#51 口径):dict[brand → {keywords, priority, ...}]
        from app.domains.kol.competitor_text import load_competitor_brands

        brands = load_competitor_brands()
        ranked = sorted(
            (str(name).strip().lower() for name in brands if str(name).strip()),
            key=lambda n: (str((brands.get(n) or {}).get("priority") or "tier3"), n),
        )
        competitors = [f"{name} lens" for name in ranked[: max(1, int(max_competitors))]]
    except Exception:
        logger.debug("competitor lexicon unavailable, using fallback terms", exc_info=True)
    try:
        # 品类词族:signal_taxonomy.generic_imaging_terms(五源共用匹配口径,零新词表)
        from app.domains.market.signal_taxonomy import KEYWORD_GROUPS

        for term in KEYWORD_GROUPS.get("generic_imaging_terms", [])[:8]:
            text = str(term or "").strip().lower()
            if text and text not in categories:
                categories.append(text)
    except Exception:
        logger.debug("signal taxonomy unavailable, using fallback terms", exc_info=True)
    return {
        "brand": brand,
        "competitors": competitors or list(_FALLBACK_COMPETITOR_TERMS),
        "categories": (categories or list(_FALLBACK_CATEGORY_TERMS))[:6],
    }


def forum_listen_watchlist() -> list[str]:
    """论坛(Reddit)watchlist,复用 P5.67 reddit_stability_strategy;降级保守兜底。"""
    try:
        from app.domains.market.reddit_stability_strategy import RECOMMENDED_WATCHLIST

        return [str(s) for s in RECOMMENDED_WATCHLIST if str(s).strip()]
    except Exception:
        logger.debug("reddit strategy watchlist unavailable, using fallback", exc_info=True)
        return ["photography", "videography", "cinematography"]


# ── 采集器(默认 OFF;闸 on → listening_executors 真采集;dry_run 只出计划)─


def collect_x_listening(
    query_terms: dict[str, list[str]] | None = None,
    *,
    max_items: int = 200,
    staff: dict | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """X 监听采集:品牌+竞品+品类词族搜索 → vkpi_market_sources/vkpi_market_mentions。

    分支语义:
      - 闸 absent/off → status="disabled" 直接返回(零网络零写库);
      - 闸 on + dry_run=True → status="planned" 只出执行计划(零网络零写库);
      - 闸 on → listening_executors.run_x_listening 真采集(Apify pay-per-result
        actor,call_apify_actor 预算预检 + record_apify_run_cost 记账,幂等落表)。
    """
    state = gate_state(GATE_X)
    if state != "on":
        return _disabled(GATE_X, state, X_PLATFORM)

    terms = query_terms or x_listen_query_terms()
    planned_queries = [
        *terms.get("brand", []),
        *terms.get("competitors", []),
        *terms.get("categories", []),
    ]
    capped = max(1, min(int(max_items or 200), X_LISTEN_DAILY_CAP))
    plan = {
        "provider": "apify_pay_per_result_actor",
        "queries": planned_queries,
        "max_items": capped,
        "daily_cap": X_LISTEN_DAILY_CAP,
        "write_targets": ["vkpi_market_sources", "vkpi_market_mentions"],
    }
    if dry_run:
        return {
            "status": "planned",
            "platform": X_PLATFORM,
            "gate": GATE_X,
            "gate_state": state,
            "items": [],
            "network_calls": 0,
            "planned": plan,
            "reason": "dry_run=True:只出执行计划,零网络零写库。",
        }
    from app.domains.comments.listening_executors import run_x_listening

    result = run_x_listening(planned_queries, max_items=capped)
    return {"gate": GATE_X, "gate_state": state, "planned": plan, **result}


def collect_forum_listening(
    watchlist: list[str] | None = None,
    *,
    max_items: int = 200,
    staff: dict | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """摄影论坛监听采集:Reddit watchlist(唯一合规自动化源)→ 市场提及表。

    合规红线:只允许 FORUM_SOURCE_POLICY 中 policy="allowed" 的源;
    dpreview_forums / fredmiranda 为 no_scrape,执行体没有任何路径碰它们。
    分支语义与 collect_x_listening 相同(disabled / planned / 真采集)。
    """
    state = gate_state(GATE_FORUM)
    if state != "on":
        return _disabled(GATE_FORUM, state, FORUM_PLATFORM)

    subs = list(watchlist or [])
    if not subs:
        from app.domains.comments.listening_executors import default_reddit_watchlist

        subs = default_reddit_watchlist()
    allowed_sources = [p["source"] for p in FORUM_SOURCE_POLICY if p["policy"] == "allowed"]
    capped = max(1, min(int(max_items or 200), FORUM_LISTEN_DAILY_CAP))
    plan = {
        "provider": "reddit_public_json",
        "watchlist": subs,
        "allowed_sources": allowed_sources,
        "no_scrape_sources": [p["source"] for p in FORUM_SOURCE_POLICY if p["policy"] == "no_scrape"],
        "max_items": capped,
        "daily_cap": FORUM_LISTEN_DAILY_CAP,
        "write_targets": ["vkpi_market_sources", "vkpi_market_mentions"],
    }
    if dry_run:
        return {
            "status": "planned",
            "platform": FORUM_PLATFORM,
            "gate": GATE_FORUM,
            "gate_state": state,
            "items": [],
            "network_calls": 0,
            "planned": plan,
            "reason": "dry_run=True:只出执行计划,零网络零写库。",
        }
    from app.domains.comments.listening_executors import run_reddit_listening

    result = run_reddit_listening(subs, max_items=capped)
    return {"gate": GATE_FORUM, "gate_state": state, "planned": plan, **result}


# ── 字段映射(薄封装 collector._standardize_comment,x/forum 映射住那边)──


def standardize_x_listen_item(
    raw: dict,
    *,
    post_id: int = 0,
    external_post_id: str = "",
    account_id: int | None = None,
) -> dict[str, Any]:
    """X 监听条目 → vkpi_comments 标准字段(复用 collector 既有 x 平台映射)。"""
    from app.domains.comments.collector import _standardize_comment

    return _standardize_comment(
        raw,
        platform=X_PLATFORM,
        post_id=int(post_id or 0),
        account_id=account_id,
        external_post_id=str(external_post_id or ""),
        post_table=LISTEN_POST_TABLE,
    )


def standardize_forum_listen_item(
    raw: dict,
    *,
    post_id: int = 0,
    external_post_id: str = "",
    account_id: int | None = None,
) -> dict[str, Any]:
    """论坛监听条目 → vkpi_comments 标准字段(forum 平台映射,本刀在 collector 新增)。"""
    from app.domains.comments.collector import _standardize_comment

    return _standardize_comment(
        raw,
        platform=FORUM_PLATFORM,
        post_id=int(post_id or 0),
        account_id=account_id,
        external_post_id=str(external_post_id or ""),
        post_table=LISTEN_POST_TABLE,
    )


# ── 监听覆盖卡状态上报(voice_report sources 的两行;读真闸态,禁装)──────

# 覆盖卡计数用的原生平台枚举(监听落表 platform 列的真实取值)
X_NATIVE_PLATFORM = "x"
FORUM_NATIVE_PLATFORM = "reddit"


def _count_platform_rows(conn: Any, platform: str) -> int | None:
    """监听落表(vkpi_market_mentions × listening 源)该平台行数;查不了诚实返回 None。"""
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_market_mentions m "
            "JOIN vkpi_market_sources s ON s.id = m.source_id "
            "WHERE s.source_type = 'listening_post' AND LOWER(COALESCE(m.platform,'')) = ?",
            (platform,),
        ).fetchone()
        return int(dict(row)["n"] or 0) if row else 0
    except Exception:
        logger.debug("listening cover count failed for %s", platform, exc_info=True)
        return None


def _cover_entry(gate_key: str, platform: str, conn: Any, *, label_hint: str) -> dict[str, Any]:
    state = gate_state(gate_key)
    if state == "absent":
        return {
            "table": f"vkpi_market_mentions · platform={platform}",
            "status": "not_connected",
            "count": 0,
            "gate": gate_key,
            "gate_state": state,
            "note": f"{label_hint}未接入(env 未登记 {gate_key})—— 盲区,如实标注不装。",
        }
    if state == "off":
        return {
            "table": f"vkpi_market_mentions · platform={platform}",
            "status": "scaffold",
            "count": 0,
            "gate": gate_key,
            "gate_state": state,
            "note": f"{label_hint}采集器就绪 · {gate_key}=0 待开闸,未采一条。",
        }
    count = _count_platform_rows(conn, platform)
    n = int(count or 0)
    return {
        "table": f"vkpi_market_mentions · platform={platform}",
        "status": "ready" if n > 0 else "empty",
        "count": n,
        "gate": gate_key,
        "gate_state": state,
        "note": "" if n > 0 else f"{label_hint}闸已开但窗口内尚未采到数据 —— 0 条如实报。",
    }


def listening_cover_sources(conn: Any = None) -> dict[str, dict[str, Any]]:
    """监听覆盖卡的 X / 摄影论坛两行(voice_report sources 增量,键名前端 SOURCE_ORDER 对齐)。"""
    return {
        "x_listen": _cover_entry(GATE_X, X_NATIVE_PLATFORM, conn, label_hint="X 监听"),
        "forum_listen": _cover_entry(GATE_FORUM, FORUM_NATIVE_PLATFORM, conn, label_hint="摄影论坛监听"),
    }
