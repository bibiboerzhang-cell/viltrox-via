"""Roster 覆盖最大化组合(件D,greedy_setcover_v0)—— 发射台第⑥输出的引擎。

目标:给定候选 KOL id 列表,选出 ≤ max_size 人的组合,最大化「去重触达」。
模型(决定性贪心 set-cover,零 LLM、零外调、零成本):
  1. 每个候选的受众按 地理桶×平台 展开成覆盖单元,单元权重 = 触达基数 × 该桶占比;
     - 触达基数:followers(首选)→ avg_views(降级,单视频口径)→ 0(诚实无基数);
     - 地理桶:audience_estimated_json(ensemble_v1 top_countries,真实受众地理)
       → 创作者国别 country 列代理(creator_country_proxy,创作者国 ≠ 受众地理,诚实降档)
       → 每 KOL 独立 unknown:{kol_id} 桶(缺数据不丢弃候选;独立成桶=缺地理的
         两人不共享覆盖单元、不互相折减,展示层统一折回 UNKNOWN)。
  2. 贪心逐个选边际增益最大者;同单元已被已选成员覆盖时,按 pairwise 重叠分折减:
     - 有实测重叠(评论者集合 jaccard:audience_estimated_json.overlap 存量 + vkpi_comments
       现算,取大)→ method=commenter_jaccard_v0(jaccard 是抽样下界,乘校准系数放大);
     - 无实测 → 地理分布相似度 × 平台系数 做代理 → method=geo_proxy_v0。
  3. 输出 selected(边际触达+入选理由)/ dropped_overlap(与谁重叠多少)/ coverage
     (总去重触达+地理/平台分布),payload 带 basis/method/confidence 可追溯。

跨代理契约:本模块被发射台消费;报价增益段走守卫 import 件B rate_card,缺席诚实降级。
compat 约定:SQL 占位符 ?;SQL 禁 percent 字符(不用 LIKE);函数内懒 import get_conn。
红线:纯读聚合绝不写库;不触 fit 分存储列、不碰 rule_v0;数字全部可追溯,数据不足
诚实 low confidence,绝不杜撰。决定性:同输入同输出(排序 tie-break 用 kol id)。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

METHOD = "greedy_setcover_v0"

# ── 规模护栏(纯本地 SQL,也别无界)──
MAX_CANDIDATES = 60          # 一次最多评估的候选数
MAX_ROSTER_SIZE = 20         # max_size 上限
LIVE_JACCARD_MAX = 25        # 超过此候选数不做现算 pairwise(O(n) 次评论查询),只用存量+代理
MIN_COMMENTER_SET = 5        # 评论者集合小于该值不参与 jaccard(样本太小无意义)
MIN_SHARED_COMMENTERS = 2    # 交集小于 2 不算重叠(与 comment_intel 口径一致)

# ── 折减标定(v0 常数,全部写进 payload.basis 可追溯)──
JACCARD_TO_DUP_FACTOR = 2.5  # 评论抽样 jaccard 系统性低估受众重复,放大后作为折减
OVERLAP_DISCOUNT_CAP = 0.95  # 折减封顶:再重叠也保留 5 个点的边际(避免整段清零)
PROXY_SAME_PLATFORM = 0.30   # 代理折减:同平台且地理完全同分布时,按 30 个点重复估
PROXY_CROSS_PLATFORM = 0.10  # 代理折减:跨平台封顶 10 个点(同一人跨平台重复关注较少)

UNKNOWN_BUCKET = "UNKNOWN"
OTHER_BUCKET = "OTHER"
# 缺地理的候选按 KOL 独立成桶(unknown:{kol_id}):两个「都不知道在哪」的人
# 不该共享覆盖单元互相折减 —— 缺数据不等于同受众。展示层统一折回 UNKNOWN。
UNKNOWN_BUCKET_PREFIX = "unknown:"

# ── 创作者国别列 → 地理桶(库内实际值:中文国名 + 少量 ISO/别名混写)──
COUNTRY_CODE_MAP: dict[str, str] = {
    "美国": "US", "英国": "GB", "UK": "GB", "加拿大": "CA", "德国": "DE", "意大利": "IT",
    "澳大利亚": "AU", "日本": "JP", "西班牙": "ES", "法国": "FR", "泰国": "TH", "巴西": "BR",
    "菲律宾": "PH", "韩国": "KR", "阿联酋": "AE", "迪拜": "AE", "俄罗斯": "RU", "越南": "VN",
    "阿根廷": "AR", "瑞典": "SE", "比利时": "BE", "墨西哥": "MX", "哥伦比亚": "CO",
    "瑞士": "CH", "波兰": "PL", "奥地利": "AT", "台湾": "TW", "罗马尼亚": "RO",
    "新西兰": "NZ", "荷兰": "NL", "新加坡": "SG", "马来西亚": "MY", "印尼": "ID",
    "印度尼西亚": "ID", "爱尔兰": "IE", "葡萄牙": "PT", "斯洛伐克": "SK", "印度": "IN",
    "中国": "CN", "香港": "HK", "土耳其": "TR", "乌克兰": "UA", "希腊": "GR", "捷克": "CZ",
    "丹麦": "DK", "挪威": "NO", "芬兰": "FI", "匈牙利": "HU", "以色列": "IL", "沙特": "SA",
    "沙特阿拉伯": "SA", "埃及": "EG", "南非": "ZA", "尼日利亚": "NG", "智利": "CL",
    "秘鲁": "PE", "巴基斯坦": "PK", "孟加拉国": "BD", "斯里兰卡": "LK", "缅甸": "MM",
    "柬埔寨": "KH", "蒙古": "MN", "哈萨克斯坦": "KZ", "保加利亚": "BG", "克罗地亚": "HR",
    "塞尔维亚": "RS", "立陶宛": "LT", "拉脱维亚": "LV", "爱沙尼亚": "EE", "斯洛文尼亚": "SI",
}


def _normalize_country(raw: Any) -> str:
    """国别值 → 稳定地理桶:映射表命中出码;2 字母码直通;其余保原串(诚实不硬猜)。"""
    text = str(raw or "").strip()
    if not text:
        return UNKNOWN_BUCKET
    if text in COUNTRY_CODE_MAP:
        return COUNTRY_CODE_MAP[text]
    upper = text.upper()
    if upper in COUNTRY_CODE_MAP:
        return COUNTRY_CODE_MAP[upper]
    if len(upper) == 2 and upper.isalpha() and upper.isascii():
        return upper
    return text


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            return {}
    return {}


def _unknown_bucket(kol_id: int) -> str:
    """缺地理候选的专属桶:每 KOL 独立(unknown:{kol_id}),互相不共享覆盖单元。"""
    return UNKNOWN_BUCKET_PREFIX + str(int(kol_id))


def _display_bucket(bucket: Any) -> str:
    """展示口径:内部的 unknown:{kol_id} 独立桶统一折回 UNKNOWN,不外漏内部编号。"""
    text = str(bucket or "")
    return UNKNOWN_BUCKET if text.startswith(UNKNOWN_BUCKET_PREFIX) else text


def _geo_distribution(audience: dict[str, Any], country_col: Any, kol_id: int) -> tuple[dict[str, float], str]:
    """候选的地理桶分布(桶→占比 0..100)+ 来源标签(三档诚实降级)。

    UNKNOWN 一律换成该 KOL 的独立桶(unknown:{kol_id}):缺数据不等于同受众,
    两个缺地理的候选不再共享单元互相折减(修复前二人同落 UNKNOWN 桶按同平台
    30 个点互扣,凭空砍掉边际触达)。
    """
    top_countries = audience.get("top_countries") or []
    sample_size = int(audience.get("sample_size") or 0)
    if isinstance(top_countries, list) and top_countries and sample_size > 0:
        dist: dict[str, float] = {}
        for item in top_countries:
            if not isinstance(item, dict):
                continue
            bucket = _normalize_country(item.get("code"))
            if bucket == UNKNOWN_BUCKET:
                bucket = _unknown_bucket(kol_id)
            try:
                pct = float(item.get("pct") or 0.0)
            except (TypeError, ValueError):
                pct = 0.0
            if bucket and pct > 0:
                dist[bucket] = dist.get(bucket, 0.0) + pct
        listed = sum(dist.values())
        if listed > 0:
            if listed > 100.0:  # 归一防御(pct 累加超 100 时等比缩回)
                dist = {b: round(p * 100.0 / listed, 4) for b, p in dist.items()}
            elif listed < 100.0:
                dist[OTHER_BUCKET] = round(100.0 - listed, 4)
            return dist, "audience_ensemble_v1"
    bucket = _normalize_country(country_col)
    if bucket != UNKNOWN_BUCKET:
        return {bucket: 100.0}, "creator_country_proxy"
    return {_unknown_bucket(kol_id): 100.0}, "none"


def _reach_weight(rec: dict[str, Any]) -> tuple[float, str]:
    """触达基数:followers → avg_views(降级)→ 0(诚实)。"""
    try:
        followers = float(rec.get("followers") or 0)
    except (TypeError, ValueError):
        followers = 0.0
    if followers > 0:
        return followers, "followers"
    try:
        avg_views = float(rec.get("avg_views") or 0)
    except (TypeError, ValueError):
        avg_views = 0.0
    if avg_views > 0:
        return avg_views, "avg_views"
    return 0.0, "none"


def _load_profiles(db: Any, candidate_ids: list[int]) -> tuple[dict[int, dict[str, Any]], list[int]]:
    """一次 IN 查询取候选画像;查不到的 id 进 missing 诚实返回。"""
    placeholders = ",".join("?" for _ in candidate_ids)
    rows = db.execute(
        "SELECT id, platform, handle, display_name, followers, avg_views, country, "
        "audience_estimated_json FROM vkpi_kol_pool WHERE id IN (" + placeholders + ")",
        tuple(int(x) for x in candidate_ids),
    ).fetchall()
    profiles: dict[int, dict[str, Any]] = {}
    for row in rows:
        rec = dict(row)
        kid = int(rec["id"])
        audience = _parse_json_dict(rec.get("audience_estimated_json"))
        geo, geo_source = _geo_distribution(audience, rec.get("country"), kid)
        weight, reach_basis = _reach_weight(rec)
        platform = str(rec.get("platform") or "").strip().lower() or "unknown"
        units = {
            (platform, bucket): weight * pct / 100.0
            for bucket, pct in geo.items()
            if pct > 0
        }
        profiles[kid] = {
            "kol_pool_id": kid,
            "handle": str(rec.get("handle") or ""),
            "display_name": str(rec.get("display_name") or "") or str(rec.get("handle") or ""),
            "platform": platform,
            "reach_weight": weight,
            "reach_basis": reach_basis,
            "geo": geo,
            "geo_source": geo_source,
            "units": units,
            "audience_overlap_items": (audience.get("overlap") or {}).get("items") or [],
        }
    missing = sorted(set(int(x) for x in candidate_ids) - set(profiles.keys()))
    return profiles, missing


def _stored_pair_jaccard(profiles: dict[int, dict[str, Any]]) -> dict[tuple[int, int], float]:
    """存量重叠:audience_estimated_json.overlap.items(comment_intel 产物)里两两在候选集内的 jaccard。"""
    pairs: dict[tuple[int, int], float] = {}
    ids = set(profiles.keys())
    for kid, prof in profiles.items():
        for item in prof.get("audience_overlap_items") or []:
            if not isinstance(item, dict) or str(item.get("kind") or "") != "kol":
                continue
            try:
                peer = int(item.get("peer_id") or 0)
                jac = float(item.get("jaccard") or 0.0)
            except (TypeError, ValueError):
                continue
            if peer in ids and peer != kid and jac > 0:
                key = (min(kid, peer), max(kid, peer))
                pairs[key] = max(pairs.get(key, 0.0), jac)
    return pairs


def _live_pair_jaccard(db: Any, ids: list[int]) -> tuple[dict[tuple[int, int], float], str]:
    """现算重叠:vkpi_comments 评论者集合两两 jaccard(守卫 import,失败诚实降级不阻断)。"""
    if len(ids) > LIVE_JACCARD_MAX:
        return {}, f"候选数超过 {LIVE_JACCARD_MAX},现算 pairwise 跳过(用存量+代理)"
    try:
        from app.domains.kol.comment_intel import _self_commenter_keys, self_commenter_keys_for_kols
    except ImportError:
        return {}, "comment_intel 不可用,现算 pairwise 跳过"
    try:
        sets = self_commenter_keys_for_kols(db, ids)
    except Exception as exc:  # noqa: BLE001 - 批读不可用时保留原逐人隔离语义
        logger.warning("roster live jaccard: batch commenter keys failed: %s", exc)
        sets = {}
    for kid in ids:
        if kid in sets:
            continue
        try:
            sets[kid] = _self_commenter_keys(db, int(kid))
        except Exception as exc:  # noqa: BLE001 — 单个失败不拖垮整体
            logger.warning("roster live jaccard: commenter keys failed kol=%s: %s", kid, exc)
            sets[kid] = set()
    pairs: dict[tuple[int, int], float] = {}
    ordered = sorted(ids)
    for i, a in enumerate(ordered):
        set_a = sets.get(a) or set()
        if len(set_a) < MIN_COMMENTER_SET:
            continue
        for b in ordered[i + 1:]:
            set_b = sets.get(b) or set()
            if len(set_b) < MIN_COMMENTER_SET:
                continue
            shared = len(set_a & set_b)
            if shared < MIN_SHARED_COMMENTERS:
                continue
            union = len(set_a | set_b)
            if union > 0:
                pairs[(a, b)] = round(shared / union, 6)
    return pairs, ""


def _proxy_overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    """代理折减 geo_proxy_v0:地理分布相似度(Σ min 占比)× 平台系数。"""
    geo_a, geo_b = a.get("geo") or {}, b.get("geo") or {}
    sim = sum(min(geo_a.get(bucket, 0.0), geo_b.get(bucket, 0.0)) for bucket in set(geo_a) | set(geo_b)) / 100.0
    factor = PROXY_SAME_PLATFORM if a.get("platform") == b.get("platform") else PROXY_CROSS_PLATFORM
    return round(min(1.0, sim) * factor, 6)


def _pair_overlap(
    a_id: int,
    b_id: int,
    profiles: dict[int, dict[str, Any]],
    measured: dict[tuple[int, int], float],
) -> tuple[float, str]:
    """两两重叠折减分(0..OVERLAP_DISCOUNT_CAP)+ 口径标签。实测优先,代理兜底取大。"""
    key = (min(a_id, b_id), max(a_id, b_id))
    proxy = _proxy_overlap(profiles[a_id], profiles[b_id])
    jac = measured.get(key)
    if jac is not None:
        scaled = min(OVERLAP_DISCOUNT_CAP, round(jac * JACCARD_TO_DUP_FACTOR, 6))
        if scaled >= proxy:
            return scaled, "commenter_jaccard_v0"
    return min(OVERLAP_DISCOUNT_CAP, proxy), "geo_proxy_v0"


def _marginal_gain(
    cand_id: int,
    selected_ids: list[int],
    profiles: dict[int, dict[str, Any]],
    measured: dict[tuple[int, int], float],
) -> tuple[float, dict[str, Any]]:
    """候选相对已选集合的边际去重触达:同单元(平台×地理桶)被已选覆盖时按重叠分折减。"""
    prof = profiles[cand_id]
    gain = 0.0
    worst_discount = 0.0
    worst_against: int | None = None
    worst_method = ""
    new_units: list[tuple[float, str]] = []
    for unit, reach in prof["units"].items():
        discount = 0.0
        against: int | None = None
        method = ""
        for sid in selected_ids:
            if unit in profiles[sid]["units"]:
                score, score_method = _pair_overlap(cand_id, sid, profiles, measured)
                if score > discount:
                    discount, against, method = score, sid, score_method
        discount = min(discount, OVERLAP_DISCOUNT_CAP)
        kept = reach * (1.0 - discount)
        gain += kept
        if discount > worst_discount:
            worst_discount, worst_against, worst_method = discount, against, method
        if against is None and reach > 0:
            new_units.append((reach, f"{unit[0]}×{_display_bucket(unit[1])}"))
    new_units.sort(key=lambda x: (-x[0], x[1]))
    return round(gain, 2), {
        "worst_discount": round(worst_discount, 4),
        "worst_against": worst_against,
        "worst_method": worst_method,
        "top_new_units": [label for _, label in new_units[:3]],
    }


def _selection_reason(prof: dict[str, Any], rank: int, gain: float, detail: dict[str, Any]) -> str:
    parts: list[str] = []
    if rank == 1:
        parts.append("首选:边际触达最大")
    if detail.get("top_new_units"):
        parts.append("新开覆盖单元 " + " / ".join(detail["top_new_units"]))
    if detail.get("worst_against"):
        pct = round(float(detail.get("worst_discount") or 0.0) * 100.0, 1)
        parts.append(f"与已选 #{detail['worst_against']} 重叠折减 {pct} 个点({detail.get('worst_method')})")
    if prof.get("geo_source") == "creator_country_proxy":
        parts.append("地理为创作者国别代理(非实测受众)")
    elif prof.get("geo_source") == "none":
        parts.append("受众地理缺失,归 UNKNOWN 桶")
    if prof.get("reach_basis") == "avg_views":
        parts.append("触达基数用 avg_views 降级口径")
    elif prof.get("reach_basis") == "none":
        parts.append("无粉丝/播放基数,触达计 0")
    return ";".join(parts) if parts else "边际增益最大"


def optimize_roster(candidate_ids: list[int], max_size: int = 8) -> dict[str, Any]:
    """覆盖最大化组合(契约入口):候选 id 列表 → 去重触达最大的 ≤ max_size 人 roster。

    返回键:status / method / selected / dropped_overlap / coverage / basis / confidence。
    决定性(同输入同输出)、零 LLM、纯读不写库;缺数据逐层诚实降级,绝不杜撰。
    """
    from app.db.connection import get_conn

    clean_ids: list[int] = []
    invalid: list[Any] = []
    for raw in candidate_ids or []:
        try:
            kid = int(raw)
        except (TypeError, ValueError):
            invalid.append(raw)
            continue
        if kid > 0 and kid not in clean_ids:
            clean_ids.append(kid)
    if not clean_ids:
        return {
            "status": "empty",
            "method": METHOD,
            "reason": "候选列表为空(或全部非法 id),无从优化",
            "invalid_ids": invalid[:10],
            "selected": [],
            "dropped_overlap": [],
            "coverage": {"total_dedup_reach": 0, "by_geo": [], "by_platform": []},
            "confidence": "low",
        }
    if len(clean_ids) > MAX_CANDIDATES:
        return {
            "status": "too_many_candidates",
            "method": METHOD,
            "reason": f"候选 {len(clean_ids)} 个超过上限 {MAX_CANDIDATES},请先粗筛再进组合优化",
            "selected": [],
            "dropped_overlap": [],
            "coverage": {"total_dedup_reach": 0, "by_geo": [], "by_platform": []},
            "confidence": "low",
        }
    size_cap = max(1, min(int(max_size or 8), MAX_ROSTER_SIZE))

    db = get_conn()
    profiles, missing_ids = _load_profiles(db, clean_ids)
    if not profiles:
        return {
            "status": "no_candidates",
            "method": METHOD,
            "reason": "候选 id 在 vkpi_kol_pool 全部查无此人",
            "missing_ids": missing_ids,
            "selected": [],
            "dropped_overlap": [],
            "coverage": {"total_dedup_reach": 0, "by_geo": [], "by_platform": []},
            "confidence": "low",
        }

    measured = _stored_pair_jaccard(profiles)
    stored_pairs_n = len(measured)
    live_pairs, live_note = _live_pair_jaccard(db, sorted(profiles.keys()))
    for key, jac in live_pairs.items():
        measured[key] = max(measured.get(key, 0.0), jac)

    # ── 贪心主循环(决定性:候选按 id 升序评估,tie-break 取小 id)──
    ordered_ids = sorted(profiles.keys())
    selected_ids: list[int] = []
    selected_entries: list[dict[str, Any]] = []
    covered_units: dict[tuple[str, str], float] = {}
    while len(selected_ids) < size_cap:
        best_id: int | None = None
        best_gain = 0.0
        best_detail: dict[str, Any] = {}
        for cid in ordered_ids:
            if cid in selected_ids:
                continue
            gain, detail = _marginal_gain(cid, selected_ids, profiles, measured)
            if gain > best_gain:
                best_id, best_gain, best_detail = cid, gain, detail
        if best_id is None or best_gain <= 0:
            break
        prof = profiles[best_id]
        # 累计覆盖(与增益同一口径:同单元按最强重叠折减后入账)
        for unit, reach in prof["units"].items():
            discount = 0.0
            for sid in selected_ids:
                if unit in profiles[sid]["units"]:
                    score, _m = _pair_overlap(best_id, sid, profiles, measured)
                    discount = max(discount, score)
            covered_units[unit] = covered_units.get(unit, 0.0) + reach * (1.0 - min(discount, OVERLAP_DISCOUNT_CAP))
        selected_ids.append(best_id)
        rank = len(selected_ids)
        selected_entries.append({
            "rank": rank,
            "kol_pool_id": best_id,
            "handle": prof["handle"],
            "display_name": prof["display_name"],
            "platform": prof["platform"],
            "marginal_reach": round(best_gain, 2),
            "base_reach": round(prof["reach_weight"], 2),
            "reach_basis": prof["reach_basis"],
            "geo_source": prof["geo_source"],
            "geo_top": [
                (_display_bucket(bucket), pct)
                for bucket, pct in sorted(prof["geo"].items(), key=lambda x: (-x[1], x[0]))[:3]
            ],
            "reason": _selection_reason(prof, rank, best_gain, best_detail),
        })

    # ── 落选归因:相对最终已选集合的边际与重叠明细 ──
    dropped: list[dict[str, Any]] = []
    for cid in ordered_ids:
        if cid in selected_ids:
            continue
        prof = profiles[cid]
        gain, _detail = _marginal_gain(cid, selected_ids, profiles, measured)
        overlaps = []
        for sid in selected_ids:
            score, score_method = _pair_overlap(cid, sid, profiles, measured)
            if score > 0:
                overlaps.append({
                    "with_kol_id": sid,
                    "with_handle": profiles[sid]["handle"],
                    "overlap_score": round(score, 4),
                    "method": score_method,
                })
        overlaps.sort(key=lambda x: (-x["overlap_score"], x["with_kol_id"]))
        if prof["reach_weight"] <= 0:
            reason = "无粉丝/播放触达基数,边际增益为 0(补数据后再评)"
        elif len(selected_ids) >= size_cap:
            reason = "roster 已满,且边际增益低于已选成员"
        else:
            reason = "与已选重叠折减后边际增益不足"
        dropped.append({
            "kol_pool_id": cid,
            "handle": prof["handle"],
            "platform": prof["platform"],
            "marginal_reach_if_added": round(gain, 2),
            "top_overlaps": overlaps[:3],
            "reason": reason,
        })
    dropped.sort(key=lambda x: (-x["marginal_reach_if_added"], x["kol_pool_id"]))

    # ── 覆盖汇总(地理桶 / 平台;unknown:{kol_id} 独立桶展示层折回 UNKNOWN)──
    by_geo_map: dict[str, float] = {}
    by_platform_map: dict[str, float] = {}
    for (platform, bucket), reach in covered_units.items():
        display = _display_bucket(bucket)
        by_geo_map[display] = by_geo_map.get(display, 0.0) + reach
        by_platform_map[platform] = by_platform_map.get(platform, 0.0) + reach
    total = sum(covered_units.values())
    raw_total = sum(profiles[sid]["reach_weight"] for sid in selected_ids)
    by_geo = [
        {"bucket": bucket, "reach": round(reach, 2),
         "pct": round(reach * 100.0 / total, 2) if total > 0 else 0.0}
        for bucket, reach in sorted(by_geo_map.items(), key=lambda x: (-x[1], x[0]))
    ]
    by_platform = [
        {"platform": platform, "reach": round(reach, 2),
         "pct": round(reach * 100.0 / total, 2) if total > 0 else 0.0}
        for platform, reach in sorted(by_platform_map.items(), key=lambda x: (-x[1], x[0]))
    ]
    unknown_pct = next((g["pct"] for g in by_geo if g["bucket"] == UNKNOWN_BUCKET), 0.0)

    # ── 可追溯 basis + 诚实置信度 ──
    geo_real = sum(1 for p in profiles.values() if p["geo_source"] == "audience_ensemble_v1")
    geo_proxy = sum(1 for p in profiles.values() if p["geo_source"] == "creator_country_proxy")
    geo_none = sum(1 for p in profiles.values() if p["geo_source"] == "none")
    basis = {
        "candidates_in": len(clean_ids),
        "candidates_found": len(profiles),
        "missing_ids": missing_ids,
        "invalid_ids": invalid[:10],
        "geo_source_counts": {
            "audience_ensemble_v1": geo_real,
            "creator_country_proxy": geo_proxy,
            "unknown": geo_none,
        },
        "reach_basis_counts": {
            "followers": sum(1 for p in profiles.values() if p["reach_basis"] == "followers"),
            "avg_views": sum(1 for p in profiles.values() if p["reach_basis"] == "avg_views"),
            "none": sum(1 for p in profiles.values() if p["reach_basis"] == "none"),
        },
        "overlap_pairs_measured": len(measured),
        "overlap_pairs_stored": stored_pairs_n,
        "overlap_pairs_live": len(live_pairs),
        "live_jaccard_note": live_note,
        "calibration": {
            "jaccard_to_dup_factor": JACCARD_TO_DUP_FACTOR,
            "overlap_discount_cap": OVERLAP_DISCOUNT_CAP,
            "proxy_same_platform": PROXY_SAME_PLATFORM,
            "proxy_cross_platform": PROXY_CROSS_PLATFORM,
        },
    }
    if geo_real >= max(1, len(profiles) // 2) and len(measured) > 0:
        confidence = "medium"
    else:
        confidence = "low"

    payload: dict[str, Any] = {
        "status": "ok" if selected_entries else "no_reach_basis",
        "method": METHOD,
        "max_size": size_cap,
        "selected": selected_entries,
        "dropped_overlap": dropped,
        "coverage": {
            "total_dedup_reach": round(total, 2),
            "raw_reach_sum": round(raw_total, 2),
            "dedup_saved_pct": round((raw_total - total) * 100.0 / raw_total, 2) if raw_total > 0 else 0.0,
            "by_geo": by_geo,
            "by_platform": by_platform,
            "unknown_geo_pct": unknown_pct,
        },
        "basis": basis,
        "confidence": confidence,
        "note": (
            "去重触达为估算:折减用实测评论者 jaccard(commenter_jaccard_v0)优先、"
            "地理×平台相似度(geo_proxy_v0)兜底;创作者国别代理 ≠ 实测受众地理,诚实降档;"
            "受众地理缺失的候选按 KOL 独立 unknown 桶计,彼此不折减(缺数据不等于同受众),"
            "展示层统一折回 UNKNOWN。"
        ),
    }
    if not selected_entries:
        payload["reason"] = "所有候选均无触达基数(followers/avg_views 皆缺),无法优化;补数据后再跑"

    # ── 增益段:件B 报价(守卫 import,兄弟件缺席诚实降级,不阻断主结果)──
    payload["budget"] = _attach_rate_estimates(selected_entries)
    return payload


def _attach_rate_estimates(selected_entries: list[dict[str, Any]]) -> dict[str, Any]:
    """给已选成员挂 rate_card(件B)报价并汇总;模块缺席/单个失败都诚实降级。"""
    if not selected_entries:
        return {"status": "empty", "reason": "无已选成员,报价段缺席"}
    try:
        from app.domains.kol import rate_card
    except ImportError:
        return {"status": "unavailable", "reason": "rate_card 模块未就绪(兄弟件),报价段诚实缺席"}
    ids = [int(entry["kol_pool_id"]) for entry in selected_entries]
    try:
        estimates = rate_card.estimate_rates(ids) if hasattr(rate_card, "estimate_rates") else {}
    except Exception as exc:  # noqa: BLE001 - 批读失败仍可逐人回落
        logger.warning("roster rate_card batch failed: %s", exc)
        estimates = {}
    total_p50 = 0.0
    priced = 0
    for entry in selected_entries:
        try:
            kol_pool_id = int(entry["kol_pool_id"])
            est = estimates.get(kol_pool_id) or rate_card.estimate_rate(kol_pool_id)
        except Exception as exc:  # noqa: BLE001 — 增益段失败不阻断组合结果
            logger.warning("roster rate_card failed kol=%s: %s", entry.get("kol_pool_id"), exc)
            entry["rate_estimate"] = {"status": "error", "reason": str(exc)[:160]}
            continue
        if isinstance(est, dict) and est.get("status") == "ok" and est.get("estimated_usd_p50") is not None:
            entry["rate_estimate"] = {
                key: est.get(key)
                for key in ("status", "estimated_usd_p50", "low", "high", "method", "confidence")
            }
            try:
                total_p50 += float(est["estimated_usd_p50"])
                priced += 1
            except (TypeError, ValueError):
                pass
        else:
            entry["rate_estimate"] = {
                "status": str((est or {}).get("status") or "empty") if isinstance(est, dict) else "error",
                "reason": str((est or {}).get("reason") or "")[:160] if isinstance(est, dict) else "非 dict 返回",
            }
    return {
        "status": "ok" if priced else "empty",
        "priced_count": priced,
        "unpriced_count": len(selected_entries) - priced,
        "roster_est_usd_p50_total": round(total_p50, 2) if priced else None,
        "note": "报价来自 rate_card(件B);缺报价成员不计入合计,合计非全员口径时以 priced_count 为准",
    }
