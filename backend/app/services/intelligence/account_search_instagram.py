"""
services/intelligence/account_search_instagram.py — IG 发现腿的检索词/收敛/档案富化。

从 account_search_discovery.py 抽出(千行卫兵 + 800 软棘轮:宿主文件在快照里
锁死 843 行,本刀要往 IG 分支加「富化前置过闸 + 富化时间预算」,必须先腾地方)。
行为不变量:
- 三个原有 helper(_instagram_hashtags / _instagram_collapse_owner_posts /
  _instagram_owner_profiles)逐字搬迁,签名与返回值不变;宿主模块 re-export 原名,
  account_scan_service 的 re-export 链与既有 monkeypatch 点全部不动;
- 共享运行时(_run_actor)照旧经 _scan_service() 懒 import 取,防循环 import。

本刀新增(车道 2·A2「IG 富化后置」):
- ``instagram_enrich_targets``:富化前先用**单调闸**筛掉必死候选,只把存活者
  送进 instagram-profile-scraper;
- ``instagram_enrich_budget``:剩余时间不够就诚实跳过富化(候选照常返回、
  followers 未知 → 读端归「分析中」),而不是把整条腿拖爆 deadline。

红线:纯 provider/候选层,零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

# 富化最少需要的剩余预算(秒)。prod 实测 instagram-profile-scraper 单次 run
# 15~32s(9 个会话:15/15/18/20/23/23/29/30/32,中位 23s),启动固定开销占大头。
# 剩余预算低于此值时发 actor 几乎必被 deadline 砍掉——那是「花了钱又拿不到结果」,
# 所以宁可不发(诚实降级:followers 未知,读端归「分析中」)。env 可调。
INSTAGRAM_ENRICH_MIN_BUDGET_SECONDS_ENV = "VKPI_DISCOVERY_IG_ENRICH_MIN_BUDGET_SECONDS"
_INSTAGRAM_ENRICH_MIN_BUDGET_DEFAULT = 15.0

# IG 多词查询拆成多个有意义 hashtag，避免整句拼接成无效 tag。
_INSTAGRAM_HASHTAG_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "your", "you", "are",
    "this", "that", "best", "top", "new", "all", "any", "how", "why",
    "what", "who", "kol", "kols", "influencer", "influencers", "creator",
    "creators", "channel", "channels", "video", "videos", "review",
    "reviews",
})


def _scan_service():
    """懒 import 宿主模块:取共享 _run_actor(防循环 import;保住既有 monkeypatch 点
    —— tests patch account_scan_service._run_actor 依旧生效)。"""
    from app.services.intelligence import account_scan_service as _scan

    return _scan


def instagram_enrich_min_budget_seconds() -> float:
    """富化最小剩余预算(秒);运行时读 env(非 import 时快照),线上改 env 重启即生效。"""
    raw = str(os.environ.get(INSTAGRAM_ENRICH_MIN_BUDGET_SECONDS_ENV, "")).strip()
    try:
        value = float(raw) if raw else _INSTAGRAM_ENRICH_MIN_BUDGET_DEFAULT
    except (TypeError, ValueError):
        return _INSTAGRAM_ENRICH_MIN_BUDGET_DEFAULT
    return value if value > 0 else _INSTAGRAM_ENRICH_MIN_BUDGET_DEFAULT


def _instagram_hashtags(query: str, *, max_tags: int = 5) -> List[str]:
    """Split a (possibly multi-word) query into multiple Instagram hashtags.

    Aligns IG recall with YouTube/TikTok, which pass the full query. Each
    meaningful token becomes its own hashtag (alnum/underscore only, <=80
    chars), stopwords and tiny tokens are dropped, duplicates collapsed, and
    the count is capped at ``max_tags`` to preserve actor throttle/budget.

    Pure function (no Apify call) so it can be unit-tested directly.
    """
    seen: set[str] = set()
    tags: List[str] = []
    fallback: List[str] = []  # short/stopword tokens kept only if nothing else
    for word in (query or "").lower().split():
        token = "".join(ch for ch in word if ch.isalnum() or ch == "_")[:80]
        if not token or token in seen:
            continue
        seen.add(token)
        if len(token) > 2 and token not in _INSTAGRAM_HASHTAG_STOPWORDS:
            tags.append(token)
            if len(tags) >= max_tags:
                break
        elif len(token) > 1:
            fallback.append(token)
    if not tags:
        # No "meaningful" token survived (e.g. very short brand like "
        # dji" already >2, but pure-stopword/short queries) → keep what we
        # have so IG still gets a real hashtag instead of returning empty.
        tags = fallback[:max_tags]
    return tags


def _instagram_collapse_owner_posts(raw_items: List[Dict[str, Any]], safe_limit: int) -> List[Dict[str, Any]]:
    """K2 扩量刀·IG 号主收敛:hashtag 帖子流按 ownerUsername 收敛成「每号主一帖」
    (保 hashtag 排序首帖),再截 safe_limit。治「20 帖只剩 13 号主、槽位被同号主
    多帖吃掉」(funnel 实测收敛比 ~3:2)。无 owner 的帖保序排尾兜底。纯函数零 IO。"""
    by_owner: Dict[str, Dict[str, Any]] = {}
    ownerless: List[Dict[str, Any]] = []
    for item in raw_items:
        row = item if isinstance(item, dict) else {}
        owner = str(row.get("ownerUsername") or row.get("username") or "").strip().lower()
        if not owner:
            ownerless.append(row)
            continue
        if owner not in by_owner:
            by_owner[owner] = row
    merged = list(by_owner.values()) + ownerless
    return merged[: max(1, int(safe_limit or 1))]


def instagram_prefilter_probe(row: Dict[str, Any]) -> Dict[str, Any]:
    """把一条 hashtag 帖投影成「过闸探针」——只用帖子**已有**的字段,零 provider 调用。

    字段名对齐 discovery_filters 的候选口径(sample_title/channel_name/handle/platform),
    这样调用方传下来的闸函数照原样吃,判据一个字不改。bio 此刻还没有(要富化才拿得到),
    留空 —— 所以调用方只许传**单调闸**(见 instagram_enrich_targets 文档)。

    channel_name 这里**刻意**按 search_platform_content IG 分支富化前的同一口径算
    (ownerUsername → username → ownerFullName),让探针文本与「不富化时的候选」逐字一致;
    富化只会把 channel_name 换成 fullName 并追加 bio。
    """
    owner = str(row.get("ownerUsername") or row.get("username") or "").strip()
    channel_name = str(
        row.get("ownerUsername") or row.get("username") or row.get("ownerFullName") or ""
    ).strip()
    return {
        "platform": "instagram",
        "handle": owner,
        "channel_name": channel_name,
        "channel_url": f"https://www.instagram.com/{channel_name}/" if channel_name else "",
        "source_url": str(row.get("url") or "").strip(),
        "sample_title": str(row.get("caption") or row.get("text") or "")[:300],
    }


def instagram_enrich_targets(
    raw_items: List[Dict[str, Any]],
    prefilter: Callable[[Dict[str, Any]], bool] | None,
) -> Tuple[List[str], int]:
    """A2「富化后置」:返回 (要富化的 username 列表, 被前置闸挡掉的条数)。

    ``prefilter(probe) -> True`` 表示「这条候选**无论富化与否都会被下游丢弃**」,
    于是不值得为它烧一次 profile-scraper 配额。

    调用方**只许**传单调闸(monotone gate):判据文本只增不减时结论不会翻转。
    discovery_filters 的 _is_hard_avoid / _detect_excluded_region / _is_discovery_garbage
    都是「在拼接文本里找子串,命中即丢」——富化只会往 blob 里**加** bio/fullName,
    只可能让它们更容易命中,不可能让已命中的变不命中。所以「富化前判丢」⇒「富化后也丢」,
    候选集合逐条等价,质量口径零变化。

    反例(**禁止**传进来):_has_camera_signal 是反单调的——candidate 现在没有相机信号
    不代表富化拿到 bio 后仍然没有("filmmaker" 常只写在 bio 里)。拿它前置会静默杀掉真摄影师。

    失败方向(结构性保证,不依赖单调性):本函数**只决定要不要花一次富化配额**,
    从不从 raw_items 里删条目。判错的最坏后果 = 那条候选照常返回、只是 followers 未知,
    落到既有的「reach_status=analyzing」诚实通路,绝不会少捞一个人。

    prefilter=None 或抛异常 → 全部富化(退回旧行为,失败方向安全:宁可多花钱也不少捞人)。
    """
    names: List[str] = []
    seen: set[str] = set()
    dropped = 0
    for item in raw_items:
        row = item if isinstance(item, dict) else {}
        owner = str(row.get("ownerUsername") or row.get("username") or "").strip().lstrip("@")
        if not owner or owner.lower() in seen:
            continue
        if prefilter is not None:
            try:
                doomed = bool(prefilter(instagram_prefilter_probe(row)))
            except Exception:
                # 闸自身出错绝不静默吞掉结论方向:记一条 warning 并**放行**去富化。
                logger.warning("scanner.instagram_enrich_prefilter_failed", exc_info=True)
                doomed = False
            if doomed:
                dropped += 1
                continue
        seen.add(owner.lower())
        names.append(owner)
    return names[:50], dropped


async def _instagram_owner_profiles(usernames: List[str]) -> Dict[str, Dict[str, Any]]:
    """K2 扩量刀·IG 档案富化:apify/instagram-profile-scraper 批量拉号主档案
    (followersCount/biography/fullName/头像)。治「IG 新面孔恒无 followers →
    会话读端全折叠成分析中,面上永远见不到 IG 新人」。一次 actor run 喂全部
    usernames(≤50 封顶);actor 失败/空 → {}(诚实降级,行为退回旧版 followers 未知)。
    env APIFY_INSTAGRAM_PROFILE_ACTOR_ID 可换 actor(与 douyin/facebook 分支同模式)。"""
    names: List[str] = []
    seen: set[str] = set()
    for username in usernames:
        name = str(username or "").strip().lstrip("@")
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    names = names[:50]
    if not names:
        return {}
    actor_id = (os.getenv("APIFY_INSTAGRAM_PROFILE_ACTOR_ID") or "apify/instagram-profile-scraper").strip()
    rows = await _scan_service()._run_actor(actor_id, {"usernames": names}, timeout=300)
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        uname = str(row.get("username") or "").strip().lower()
        if uname:
            out[uname] = row
    return out
