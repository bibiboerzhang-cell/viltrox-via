"""Provider-backed creator discovery and enrollment orchestration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.db.connection import get_conn
from app.domains.kol import history_match
from app.domains.kol.discovery_filters import (
    LOW_REACH_FLAG_LIKE_PATTERN,
    _brand_official_verdict,
    _candidate_key,
    _detect_excluded_region,
    _has_camera_signal,
    _int,
    _is_bio_irrelevant,
    _is_discovery_garbage,
    _is_hard_avoid,
    _persona_avoid_terms,
    _persona_positive_terms,
    _persona_relevance,
    _reach_display_state,
    _reach_floor_enabled,
    _reach_floor_reason,
    _staff_user_id,
    _text,
)
from app.domains.kol.profile_discovery_candidates import (
    _candidate_platform_signals,
    _is_own_brand_account,
    _strict_discovery_platforms,
)
from app.domains.kol.profile_discovery_localize import (
    _has_cjk,
    _localize_search_terms,
    _market_to_language,
)
from app.services.intelligence.account_scan_service import search_platform_content

logger = logging.getLogger(__name__)


def discovery_plan(
    *,
    query_text: str,
    platforms: Any = None,
    platform_hint: str = "",
    limit: int = 15,
) -> dict[str, Any]:
    safe_limit = max(1, min(_int(limit, 15), 50))
    resolved_platforms = _strict_discovery_platforms(platforms, fallback=platform_hint)
    if not resolved_platforms:
        return {
            "status": "invalid_platform",
            "query": _text(query_text),
            "platforms": [],
            "limit": safe_limit,
            "provider_calls": False,
            "message": "no supported discovery platform was selected",
        }
    return {
        "status": "planned",
        "query": _text(query_text),
        "platforms": resolved_platforms,
        "limit": safe_limit,
        "provider_calls": False,
        "message": "new discovery is planned only; set execute_new_discovery=true to call platform providers",
    }

def _dedupe_enrolled_row_best_effort(enroll_result: Any) -> None:
    """L6:enroll 落库后跑去重 hook(最佳努力)。

    从 write_kol_profile_basics 返回里取写入行 id,调 pool_merge.dedupe_enrolled_pool_row:
    email 强信号自动合并(走 apply_merge 带 fit 守卫)、模糊信号只进人工清单(不写)。
    env(KOL_AUTO_DEDUP_ENROLL)可关。任何异常静默吞(只 debug),绝不阻断 enroll。
    """
    import os

    if str(os.getenv("KOL_AUTO_DEDUP_ENROLL", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return
    try:
        if not isinstance(enroll_result, dict):
            return
        pool_id = enroll_result.get("kol_pool_id")
        if not pool_id:
            return
        from app.domains.kol.pool_merge import dedupe_enrolled_pool_row

        dedupe_enrolled_pool_row(int(pool_id), auto_merge=True)
    except Exception:
        logger.debug("auto_dedup_enroll skip", exc_info=True)


def _auto_enroll_discoveries(new_creators: list[dict[str, Any]]) -> int:
    """把本次「全网新发现」的人即时轻量入库,治去重根因(用户口径:「抓到自动入库就不会再重复出现」)。

    发现项原本不落库 → 下次同/近似搜索 find_history_match 命中不到 → 反复以「新人」出现在「全网新发现」。
    这里逐个 upsert 到 vkpi_kol_pool(仅 platform/handle/avatar/bio/followers 等 profile-basics),
    下次即被归到「库内已有」、不再重复。
    redline-safe:走 write_kol_profile_basics——其 score 守卫会在任何 fit 变动时回滚,结构上不可能动评分域。
    最佳努力:env(KOL_AUTO_ENROLL_DISCOVERY)可关、单条失败只记日志不抛、绝不阻断发现主流程。返回入库条数。
    """
    import os

    if str(os.getenv("KOL_AUTO_ENROLL_DISCOVERY", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return 0
    if not new_creators:
        return 0
    try:
        from app.domains.kol.profile_basics import write_kol_profile_basics
    except Exception:
        return 0
    enrolled = 0
    full_ignited = 0  # full 档单次入库封顶(防一次大搜索烧穿;超出者降 light,预算闸另兜底)
    _FULL_CAP = 10
    for item in new_creators:
        if item.get("history_kol_pool_id") or item.get("kol_pool_id"):
            continue  # 已是库内行 → 不重复入库
        platform = _text(item.get("platform"))
        handle = _text(item.get("handle") or item.get("channel_handle") or item.get("username"))
        if not platform or not handle:
            continue
        profile_data = {
            "platform": platform,
            "handle": handle,
            # 线上修(2026-07-10):入库行此前不带名字,列表/抽屉只剩 handle(YT=UC 频道 ID 串)。
            "display_name": _text(item.get("display_name") or item.get("name") or item.get("title") or item.get("channel_name")),
            "profile_url": _text(item.get("profile_url") or item.get("channel_url") or item.get("url")),
            "avatar_url": _text(item.get("avatar_url") or item.get("avatar")),
            "bio": _text(item.get("bio") or item.get("description") or item.get("snippet")),
            # 诚实回填(2026-07-12 两粉号案随手修):followers 只写真粉丝族;未知写 NULL,
            # 绝不再拿 avg_views 冒充粉丝数、也不把「未知」编成 0——否则第二道闸
            # (followers 已知才推荐)会被杜撰值穿透。真值由 buildout 深爬回填。
            "followers": _int(item.get("followers") or item.get("subscriber_count") or item.get("follower_count") or 0) or None,
        }
        try:
            _enroll_res = write_kol_profile_basics(None, profile_data, dry_run=False)
            # ⚠不要把 kol_pool_id 回写到会话项! 设计不变量(search_sessions.approve_session 注释):
            # new_creator 入池后会话项 kol_pool_id 必须保持 NULL,否则「会话项交集」会把这些真候选
            # 全误杀 → 全网发现框整组消失(550pro2 监视器搜索 15 个新发现却 0 显示的真因)。
            enrolled += 1
            # L6 去重 hook:落库后立即为该行找跨平台同一人。email 强信号自动合并、模糊只进人工清单。
            # 最佳努力:apply_merge 自带 fit 守卫;任何异常吞掉只记日志,绝不阻断 enroll 主流程。
            _dedupe_enrolled_row_best_effort(_enroll_res)
            # 发现即建档(B+A 合体):按相关度分档自动点火完整档案——高相关 full(深爬3帖+评论,
            # 受众/契合链自动跟),其余 light(深爬1帖)。best-effort 绝不阻断 enroll。
            try:
                _pid = (_enroll_res or {}).get("kol_pool_id") if isinstance(_enroll_res, dict) else None
                if _pid:
                    from app.domains.discovery.buildout import ignite_profile_buildout

                    _score = 0.0
                    for _k in ("recall_rank_score", "relevance_score", "score", "vector_score"):
                        try:
                            _score = float(item.get(_k) or 0)
                        except (TypeError, ValueError):
                            _score = 0.0
                        if _score:
                            break
                    _demoted = _text(item.get("relevance_tier_hint")) == "demote" or _text(item.get("relevance_tier")) == "demote"
                    _res = ignite_profile_buildout(
                        int(_pid),
                        score=_score,
                        demoted=_demoted or full_ignited >= _FULL_CAP,
                        source="smart_search_discovery",
                    )
                    if _res.get("tier") == "full":
                        full_ignited += 1
            except Exception:
                logger.info("discovery buildout ignite skip(不阻断 enroll)", exc_info=True)
        except Exception as exc:
            logger.info("auto_enroll_discovery skip handle=%r: %s", handle, str(exc)[:200])
    if enrolled:
        logger.info("auto_enroll_discovery enrolled=%d into vkpi_kol_pool", enrolled)
    return enrolled


def _warm_discovery_avatar_cache(new_creators: list[dict[str, Any]], *, max_items: int = 15) -> None:
    """新面孔头像预热(2026-07-21 头像灰占位案):库内卡头像能显示的真通路 = image-proxy 磁盘缓存
    已温(入库/浏览时抓过);新面孔第一次渲染必冷抓,签名 CDN 冷抓超时/瞬时失败就只剩占位。
    这里把发现项 avatar_url 后台线程预热进同一份磁盘缓存(cache_image 幂等、短超时、逐条吞错),
    前端首屏即命中缓存。best-effort:线程 daemon、任何异常静默,绝不阻断/拖慢发现主流程。"""
    urls: list[str] = []
    seen: set[str] = set()
    for item in new_creators[: max(0, int(max_items))]:
        url = _text((item or {}).get("avatar_url"))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    if not urls:
        return

    def _warm() -> None:
        try:
            from app.domains.media import cache_image

            for url in urls:
                try:
                    cache_image(url, timeout=4)
                except Exception:
                    continue
        except Exception:
            logger.debug("discovery avatar warmup skipped", exc_info=True)

    try:
        import threading

        threading.Thread(target=_warm, name="discovery-avatar-warmup", daemon=True).start()
    except Exception:
        logger.debug("discovery avatar warmup thread not started", exc_info=True)


def _existing_match_pool_id(item: dict[str, Any]) -> int:
    """「库内已有」发现项的 pool 行 id(history_kol_pool_id 或 historical_match.kol_pool_id;缺 → 0)。"""
    pid = _int(item.get("history_kol_pool_id"))
    if pid > 0:
        return pid
    match = item.get("historical_match")
    if isinstance(match, dict):
        return max(0, _int(match.get("kol_pool_id")))
    return 0


def _triage_existing_matches_reach(
    existing_matches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """「库内已有」发现项的触达分诊(第二道闸读端,2026-07-12 两粉号案)。

    此前 existing_matches 跳过全部闸门 → 补全回填 followers=2 的行(kol_pool 12297)照样回到
    「全网新发现」面。按 pool 行现值走 _reach_display_state 单一真源三态:
    - low_reach(实时判据/low_reach 标命中)→ 丢出结果 + 计 filtered_low_reach(行保留,只挡入口);
    - unknown(followers 未知)→ 丢出展示 + 计 analyzing(分析后再 po),并 best-effort 补点火
      light 档 buildout(复用既有队列,queue 按 URL 自去重,绝不新造抓取);
    - ok → 保留。DB 不可用 → 全放行(fail-open 不误杀)。零触 viltrox_fit_score / rule_v0。
    """
    counts = {"low_reach": 0, "analyzing": 0}
    if not existing_matches or not _reach_floor_enabled():
        return existing_matches, counts
    ids = [_existing_match_pool_id(item) for item in existing_matches]
    want = sorted({pid for pid in ids if pid})
    pool_rows: dict[int, dict[str, Any]] = {}
    if want:
        try:
            placeholders = ",".join(["?"] * len(want))
            rows = get_conn().execute(
                f"""
                SELECT id, followers, avg_views, avg_comments, engagement_rate,
                       (raw_platform_data LIKE ?) AS low_reach_flagged
                FROM vkpi_kol_pool
                WHERE id IN ({placeholders})
                """,
                (LOW_REACH_FLAG_LIKE_PATTERN, *want),
            ).fetchall()
            pool_rows = {int(dict(r)["id"]): dict(r) for r in rows}
        except Exception:
            logger.warning("existing match reach triage skipped(fail-open 不误杀)", exc_info=True)
            return existing_matches, counts
    kept: list[dict[str, Any]] = []
    for item, pid in zip(existing_matches, ids):
        row = pool_rows.get(pid)
        state = _reach_display_state(row) if row else "ok"  # pool 行缺 → 放行(不误杀)
        if state == "low_reach":
            counts["low_reach"] += 1
            logger.debug(
                "discovery_existing_reach_floor_filtered handle=%r kol_pool_id=%s",
                item.get("handle"), pid,
            )
            continue
        if state == "unknown":
            counts["analyzing"] += 1
            # 补全链自动触发:followers 未知的库内行补点火 light 档(幂等;深爬回填 followers
            # 后经 profile_basics 第二道闸重过闸,达标即在会话读端自动放出)。
            try:
                from app.domains.discovery.buildout import ignite_profile_buildout

                ignite_profile_buildout(pid, score=0.0, demoted=True, source="discovery_reach_backfill")
            except Exception:
                logger.info("reach backfill ignite skip kol=%s", pid, exc_info=True)
            continue
        kept.append(item)
    return kept, counts


async def discover_new_creators(
    *,
    query_text: str,
    platforms: Any = None,
    platform_hint: str = "",
    market: str = "",
    limit: int = 15,
    per_platform_limit: int = 15,
    search_query_en: str = "",
    product_focus: Any = None,
    ideal_creator_types: Any = None,
    verticals: Any = None,
    avoid_types: Any = None,
    target_persona: str = "",
    auto_enroll: bool = True,
    exclude_chinese: bool = True,
) -> dict[str, Any]:
    """Search platforms for creator candidates and mark existing KOL matches.

    发现精准修:search_query_en(英文平台检索词,优先于中文 query_text 用于实际平台搜索,治
    中文 query 捞中文圈);product_focus/ideal_creator_types/verticals/avoid_types 供 per-item
    persona 启发式相关度打分(纯本地字符串比对,零 LLM/零 Apify,无需预算闸)。全 default,既有调用不破坏。"""
    query = _text(search_query_en) or _text(query_text)
    # 区域语言本地化:目标市场非英语区 → 英文检索词翻成该区语言搜平台(捞本地达人),relevanceLanguage 同步。
    # 英语区/空 market → search_term=query、relevance_language='en',与现状完全一致(零回归)。
    _relevance_language, _region_code = _market_to_language(market)
    search_term = _localize_search_terms(query, _relevance_language)
    # K2 兜底(session 1089 案):localize 走 LLM,LLM 全灭时中文 query 原样搜 YT/IG →
    # 捞中文圈 → 再被 CN/HK/TW 区域闸清空,全网新发现近乎归零。确定性零 LLM 兜底:
    # 目标语言是英文而检索词仍含 CJK → 用 persona 英文正词(product_focus/ideal_types/
    # verticals,KB 词天然英文)拼检索词;persona 也无英文词才保留原样(诚实降级,绝不杜撰)。
    if _relevance_language == "en" and _has_cjk(search_term):
        _en_fallback = [
            t for t in _persona_positive_terms(product_focus, ideal_creator_types, verticals, "")
            if not _has_cjk(t)
        ]
        if _en_fallback:
            search_term = " ".join(_en_fallback[:8])
    # Strict online mode is provider-internal and never auto-enrolls raw rows.
    # It may retain the concurrent 3×50 platform supply for downstream hard
    # qualification; legacy callers keep the historical 50 cap.
    safe_limit_cap = 150 if not auto_enroll else 50
    safe_limit = max(1, min(_int(limit, 15), safe_limit_cap))
    safe_per_platform = max(1, min(_int(per_platform_limit, 15), 50))
    resolved_platforms = _strict_discovery_platforms(platforms, fallback=platform_hint)
    if not query:
        return {
            "status": "invalid_query",
            "query": query,
            "platforms": resolved_platforms,
            "items": [],
            "new_creators": [],
            "existing_matches": [],
            "provider_calls": False,
            "message": "query is required",
        }
    if not resolved_platforms:
        return {
            "status": "invalid_platform",
            "query": query,
            "platforms": [],
            "items": [],
            "new_creators": [],
            "existing_matches": [],
            "provider_calls": False,
            "message": "no supported discovery platform was selected",
        }

    new_creators: list[dict[str, Any]] = []
    survivors: list[dict[str, Any]] = []  # 全部通过去重/garbage/地区过滤的存活候选,待 relevance 排序后再 top-N 截断
    existing_matches: list[dict[str, Any]] = []
    platform_results: list[dict[str, Any]] = []
    seen: set[str] = set()
    # persona 检索词原料(英文优先;avoid 命中重扣)。helper 内做泛词过滤与归一,空表零影响。
    _pos_terms = _persona_positive_terms(product_focus, ideal_creator_types, verticals, search_query_en or query_text)
    _neg_terms = _persona_avoid_terms(avoid_types)
    errors: list[dict[str, Any]] = []
    # 闸门丢弃计数(可观测,用于调参):brand_official=品牌官号总数(lexicon=词表快路命中/
    # dynamic=零词表动态判据命中,两子计数只供排障),bio_irrelevant=bio 自述明显非视觉职业
    # 且自身零相机信号(askmonitorofficial 类)。
    _gate_dropped = {
        "hard_avoid": 0, "no_camera_signal": 0, "low_reach": 0, "brand_official": 0,
        "brand_official_lexicon": 0, "brand_official_dynamic": 0, "bio_irrelevant": 0,
    }

    async def _search_one_platform(platform: str) -> dict[str, Any]:
        """Run one platform search with error isolation; returns annotated items + meta.

        每平台并发(asyncio.gather)。单平台失败在此捕获、绝不传播,其余平台照常返回。
        """
        try:
            result = await search_platform_content(
                platform,
                search_term,
                market=_text(market).upper(),
                max_results=safe_per_platform,
                relevance_language=_relevance_language,
                strict_evidence=not auto_enroll,
            )
        except Exception as exc:
            logger.warning("profile discovery provider failed platform=%s", platform, exc_info=True)
            return {
                "platform": platform,
                "status": "failed",
                "message": "platform_search_unavailable",
                "annotated": [],
                "error": True,
            }
        raw_items = [dict(item or {}) for item in (result.get("items") or [])]
        strict_items: list[dict[str, Any]] = []
        platform_mismatches = 0
        for item in raw_items:
            platform_signals = _candidate_platform_signals(item)
            if platform_signals and platform_signals != {platform}:
                platform_mismatches += 1
                continue
            item["platform"] = platform
            strict_items.append(item)
        annotated = history_match.annotate_platform_items(strict_items, platform=platform)
        return {
            "platform": platform,
            "status": result.get("status"),
            "message": result.get("message"),
            "metadata": result.get("metadata") or {},
            "annotated": annotated,
            "filtered_platform_mismatch": platform_mismatches,
            "error": result.get("status") not in {"done", "ready"} and not annotated,
        }

    # 并行化:YT/IG/TikTok 同时发,替代旧串行 for(一个接一个 await)。
    # return_exceptions=True 双保险——_search_one_platform 已内部捕获,这里再兜一层防御。
    platform_outcomes = await asyncio.gather(
        *[_search_one_platform(platform) for platform in resolved_platforms],
        return_exceptions=True,
    )

    # 按 resolved_platforms 原顺序合并(gather 保序),保留确定性的去重/limit 语义。
    for platform, outcome in zip(resolved_platforms, platform_outcomes):
        if isinstance(outcome, BaseException):
            errors.append({"platform": platform, "status": "failed", "message": str(outcome)[:500]})
            platform_results.append({"platform": platform, "status": "failed", "returned": 0, "metadata": {}, "message": str(outcome)[:500]})
            continue
        annotated = outcome.get("annotated") or []
        platform_results.append(
            {
                "platform": platform,
                "status": outcome.get("status"),
                "returned": len(annotated),
                "metadata": outcome.get("metadata") or {},
                "message": outcome.get("message"),
                "filtered_platform_mismatch": int(outcome.get("filtered_platform_mismatch") or 0),
            }
        )
        if outcome.get("error"):
            errors.append({"platform": platform, "status": outcome.get("status"), "message": outcome.get("message")})
        for item in annotated:
            key = _candidate_key(item, platform)
            if key in seen:
                continue
            seen.add(key)
            # 品牌官号闸(FEELWORLD 官号混入案 + Panavision/DZOFilm/Thypoch 漏网案):
            # 词表快路 = competitor_brands.json 命中身份字段**并发**官号信号才拦;词表外品牌
            # 走动态判据(官号形态 + bio 企业自述口吻并发,bio 缺=证据不足放行)。两路都
            # 不自动入库、不上新发现墙;「我评测了 FEELWORLD」类正常达人只在标题/内容提品牌,
            # 不命中。库内已有的官号(existing_match)同口径拦,防经发现面回流。
            _brand_gate = _brand_official_verdict(item)
            if _brand_gate:
                _gate_dropped["brand_official"] += 1
                _gate_dropped[f"brand_official_{_brand_gate}"] += 1
                logger.debug(
                    "discovery_brand_official_excluded handle=%r platform=%s via=%s",
                    item.get("handle"), platform, _brand_gate,
                )
                continue
            if item.get("historical_match") or item.get("history_kol_pool_id"):
                existing_matches.append(item)
                continue
            # P0-6 修:地区排除判据改扫**真正带地区信号的文本字段**(sample_title/channel_name/handle);
            # 发现 item 无 per-item country、market 恒=搜索市场 US,旧判据三参全空 → 形同虚设。
            # 口径保留海外华人(马六甲=马来西亚/新加坡/海外华人摄影师不排,只排 CN大陆/HK/TW 强信号)。
            _region = _detect_excluded_region(item) if exclude_chinese else ""
            if _is_discovery_garbage(item) or _region:
                continue
            # 品牌自有账号闸(用户硬要求:Viltrox 官方号不进发现结果)——handle 归一后以 viltrox 打头
            # 即 Viltrox 品牌自营(viltrox.official/viltrox_id/viltrox.global/viltrox.usa/viltrox.cee 等),
            # 官号数据另有专线(vkpi_employee_channels),绝不当外部 KOL 发现。只丢,不入池、不杜撰。
            if _is_own_brand_account(item):
                _gate_dropped["own_brand"] = _gate_dropped.get("own_brand", 0) + 1
                continue
            # 相机/视觉创作者闸门(用户硬要求:得有相机、得需要拍摄)。全新发现(无库内历史匹配,
            # 上方 existing_matches 已先行 continue)零相机信号 → 真丢弃。red line:只丢,绝不杜撰分。
            if _is_hard_avoid(item, _neg_terms):
                _gate_dropped["hard_avoid"] += 1
                continue
            if not _has_camera_signal(item):
                _gate_dropped["no_camera_signal"] += 1
                continue
            # bio 明显无关补强(askmonitorofficial 案):bio 自述非视觉职业 + 自身身份零相机信号
            # 双条件并发才丢(sample_title 是查询词回声不算自证);bio 缺/短 → 证据不足放行,不误杀。
            if _is_bio_irrelevant(item):
                _gate_dropped["bio_irrelevant"] += 1
                logger.debug(
                    "discovery_bio_irrelevant_excluded handle=%r platform=%s",
                    item.get("handle"), platform,
                )
                continue
            # 召回触达门槛(用户裁决 2026-07-11):followers 明确 < 门槛(默认 1000,env 可调)
            # 或互动信号实测全零(views/comments 实测都 0)→ 不进「全网新发现」结果流。
            # 与地区/相机闸同层的候选 FILTER;字段缺 / fast_path 填充 0 一律放行(不误杀);
            # 只挡本入口不删数据。red line:零触 viltrox_fit_score / rule_v0。
            _reach_reason = _reach_floor_reason(item)
            if _reach_reason:
                _gate_dropped["low_reach"] += 1
                logger.debug(
                    "discovery_reach_floor_filtered handle=%r platform=%s reason=%s",
                    item.get("handle"), platform, _reach_reason,
                )
                continue
            # persona 启发式相关度(纯本地零 LLM):写 item['score']/relevance_score/relevance_tier;
            # 先全收集到 survivors,循环外按 relevance 降序排序再 top-N 截断(否则按到达顺序砍掉高相关项)。
            item.update(_persona_relevance(item, pos_terms=_pos_terms, neg_terms=_neg_terms))
            survivors.append(item)

    # 闸门可观测:单行 INFO,丢弃明细(诚实——被丢=结果中静默缺席,非杜撰分)。便于调参。
    _total_dropped = (
        _gate_dropped["hard_avoid"] + _gate_dropped["no_camera_signal"] + _gate_dropped["low_reach"]
        + _gate_dropped["brand_official"] + _gate_dropped["bio_irrelevant"]
    )
    if _total_dropped:
        logger.info(
            "camera_relevance_gate dropped=%d hard_avoid=%d no_camera_signal=%d low_reach=%d brand_official=%d bio_irrelevant=%d survivors=%d query=%r",
            _total_dropped, _gate_dropped["hard_avoid"], _gate_dropped["no_camera_signal"],
            _gate_dropped["low_reach"], _gate_dropped["brand_official"], _gate_dropped["bio_irrelevant"],
            len(survivors), query,
        )
    # relevance 降序排序 → top-N 截断。red line:relevance 是独立展示信号,绝不并入 viltrox_fit_score / rule_v0。
    # Relevance remains primary. When candidates tie, prefer rows with observed
    # engagement so the first 15 profile advances are less likely to be tiny or
    # empty accounts that only reveal their low reach after deep crawl.
    survivors.sort(
        key=lambda it: (
            float(it.get("relevance_score") or 0.0),
            _int(it.get("avg_views") or it.get("views")),
            _int(it.get("comments")),
            _int(it.get("likes")),
        ),
        reverse=True,
    )
    # 平台轮转截断(platform_round_robin,2026-07-02 用户令):此前全局 relevance 排序取 top-N,
    # YouTube 元数据富、分普遍更高 → 单平台屠榜。现按平台分组(组内保持 relevance 降序),
    # YT/IG/TT 轮流各取一个直到装满;某平台弹尽由其余平台自然补位。
    # red line 不变:relevance 是独立展示信号,绝不并入 viltrox_fit_score / rule_v0。
    _by_platform: dict[str, list[dict[str, Any]]] = {}
    for item in survivors:
        _by_platform.setdefault(_text(item.get("platform")).lower(), []).append(item)
    _order = [p for p in resolved_platforms if _by_platform.get(p)]
    for _extra in _by_platform:
        if _extra not in _order:
            _order.append(_extra)
    _cursor = {p: 0 for p in _order}
    while len(new_creators) < safe_limit and _order:
        _progressed = False
        for _p in _order:
            if len(new_creators) >= safe_limit:
                break
            _lst = _by_platform.get(_p) or []
            if _cursor[_p] < len(_lst):
                new_creators.append(_lst[_cursor[_p]])
                _cursor[_p] += 1
                _progressed = True
        if not _progressed:
            break

    # 第二道闸·读端三态(用户裁决 2026-07-12「分析后再 po」):followers 已知达标 → 直接展示;
    # 未知 → 保留在结果里(照样入库+点火补全)但标 reach_status=analyzing,由会话读端
    # (search_sessions 展示闸)折叠为「分析中 ×N」,补全回填达标后自动放出;已知低于门槛
    # 在上方闸门已丢(filtered_low_reach)。红线:落库≠推荐,标注不动任何评分。
    _analyzing_new = 0
    for item in new_creators:
        _state = _reach_display_state(item)
        if _state == "unknown":
            item["reach_status"] = "analyzing"
            _analyzing_new += 1
        else:
            item["reach_status"] = "ok"

    # 「库内已有」同口径分诊(12297 两粉号正是从这条免检通道回流):low_reach 丢 + 计数;
    # followers 未知折叠为分析中并补点火 enrichment;达标保留。
    if auto_enroll:
        existing_matches, _existing_reach = _triage_existing_matches_reach(existing_matches)
    else:
        # Strict online net-new mode is qualification-only until an accepted
        # row is materialized. Existing inventory cannot take quota and must
        # not ignite reach/profile buildout as a side effect of read-only
        # discovery.
        _existing_reach = {"low_reach": 0, "analyzing": 0}
    _gate_dropped["low_reach"] += _existing_reach["low_reach"]
    _analyzing_total = _analyzing_new + _existing_reach["analyzing"]

    # B 去重根治:把本次全网新发现即时轻量入库,下次同/近似搜索归「库内已有」、不再重复
    # (用户口径:「抓到自动入库就不会再出现这个状况」)。best-effort 同步小写,失败不阻断发现。
    # K3 正账(2026-07-03):接住返回值(此前被丢弃)记进 counts.auto_enrolled,
    # attach_new_discovery_result 会把 counts 原样透传进会话 result_summary → 前端显示真实入库数。
    # 注意:reach_status=analyzing 的项也照样入库+buildout 点火(「发现→自动入库→补全→过闸→再 po」)。
    auto_enrolled_count = 0
    if auto_enroll:
        try:
            auto_enrolled_count = _auto_enroll_discoveries(new_creators)
        except Exception as exc:
            logger.info("auto_enroll_discovery batch skipped: %s", str(exc)[:200])

    # 新面孔头像预热(后台线程 best-effort):让首屏卡片命中 image-proxy 磁盘缓存,
    # 与库内卡同一条成功通路;失败静默,绝不阻断发现。
    if auto_enroll:
        try:
            _warm_discovery_avatar_cache(new_creators)
        except Exception:
            logger.debug("discovery avatar warmup call skipped", exc_info=True)

    status = "ready" if new_creators or existing_matches else "empty"
    if errors and (new_creators or existing_matches):
        status = "partial"
    elif errors:
        status = "failed"
    return {
        "status": status,
        "query": query,
        "platforms": resolved_platforms,
        "market": _text(market).upper(),
        "limit": safe_limit,
        "per_platform_limit": safe_per_platform,
        "items": [*existing_matches, *new_creators],
        "new_creators": new_creators,
        "existing_matches": existing_matches,
        "counts": {
            "new_creators": len(new_creators),
            "existing_matches": len(existing_matches),
            # K3:本次真实自动入库条数(_auto_enroll_discoveries 逐条 upsert 的成功数;
            # 缺 handle/入库失败/已在库的项不计)。前端据此显示真数,不再拿发现数冒充入库数。
            "auto_enrolled": auto_enrolled_count,
            # 召回触达门槛命中数(诚实可见,非静默;明细见 debug 日志 discovery_reach_floor_filtered)。
            "filtered_low_reach": _gate_dropped["low_reach"],
            # 品牌官号排除数(诚实可见;门面文案只说「品牌官方账号已排除」,不暴露词表/判据)。
            # 子计数 lexicon(词表快路)/dynamic(零词表动态判据)只供排障,不进门面文案。
            "excluded_brand_official": _gate_dropped["brand_official"],
            "excluded_brand_official_lexicon": _gate_dropped["brand_official_lexicon"],
            "excluded_brand_official_dynamic": _gate_dropped["brand_official_dynamic"],
            # bio 明显无关排除数(askmonitorofficial 类;双条件并发才丢,防误杀)。
            "excluded_bio_irrelevant": _gate_dropped["bio_irrelevant"],
            # 「分析后再 po」折叠计数:followers 未知、已入库并点火补全的候选(会话读端不展示,
            # 前端据此显示「分析中 ×N」;补全回填达标后自动放出)。
            "analyzing": _analyzing_total,
            "platforms": len(resolved_platforms),
            "errors": len(errors),
        },
        "platform_results": platform_results,
        "errors": errors,
        "provider_calls": True,
    }
