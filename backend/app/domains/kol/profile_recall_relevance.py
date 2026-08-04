"""KOL 召回展示辅助信号(why-fit 规则桥 + 视频/平面判据 + 新人优先展示降权)。
从 profile_recall.py 行为不变搬出(函数体逐字不变)。

红线:这些信号仅用于独立展示字段(display_rank_score / relevance_flags / why_fit),
绝不并入任何评分(viltrox_fit_score / recall_rank_score / vector_score / rule_v0)。"""
from __future__ import annotations

from typing import Any

from app.domains.kol.profile_recall_contract import _clean_text


# why-fit 规则桥(零成本、纯展示):每条 = (人群侧关键词, 该面向的 KOL 画像信号词, 命中后人话短语)。
# 命中逻辑:本次 query 的人群文本(persona/product_focus/target_persona/query_text)命中"人群侧"任一词,
# 且该 KOL 真实信号(profile_text/type_reason/已用器材/垂类标签/bio)命中"画像信号"任一词 → 该理由成立。
# 纯文本,绝不参与任何评分;与 viltrox_fit_score 完全无关。
WHY_FIT_RULES = (
    ("婚礼", ("婚礼", "wedding", "engagement", "新人", "婚纱"), ("婚礼", "wedding", "engagement", "新人", "婚纱"), "拍婚礼/新人"),
    ("人像", ("人像", "portrait", "肖像", "headshot", "model", "时尚", "fashion"), ("人像", "portrait", "肖像", "headshot", "model", "时尚", "fashion", "bokeh"), "做人像/肖像内容"),
    ("棚拍灯光", ("棚拍", "studio", "灯光", "lighting", "闪光", "flash", "strobe", "补光", "off-camera"), ("棚拍", "studio", "灯光", "lighting", "闪光", "flash", "strobe", "补光", "灯", "off-camera"), "常做灯光/棚拍内容"),
    ("街拍", ("街拍", "street", "扫街", "lifestyle", "生活方式"), ("街拍", "street", "扫街", "lifestyle", "生活方式"), "拍街头/生活方式"),
    ("影视", ("影视", "电影", "film", "cinema", "cinematic", "filmmaker", "短片", "narrative"), ("影视", "电影", "film", "cinema", "cinematic", "filmmaker", "短片", "叙事", "电影感", "narrative"), "做影视/电影感内容"),
    ("多机位", ("多机位", "多机", "multi-cam", "multicam", "现场", "监看", "on-set", "rig", "gimbal", "稳定器"), ("多机位", "多机", "multi-cam", "multicam", "监视器", "monitor", "现场", "监看", "rig", "gimbal", "稳定器", "导播"), "现场/多机位拍摄"),
    ("视频", ("视频", "video", "videograph", "拍视频", "vlog", "vlogger", "content creator", "内容创作"), ("视频", "video", "videograph", "vlog", "vlogger", "拍视频", "电影感", "filmmaker"), "持续产出视频内容"),
    ("风光", ("风光", "landscape", "星空", "astro", "城市", "cityscape", "建筑", "real estate", "interior"), ("风光", "landscape", "星空", "astro", "城市", "cityscape", "风景", "建筑", "real estate", "interior"), "拍风光/超广题材"),
    ("旅行", ("旅行", "travel", "旅拍", "vlog"), ("旅行", "travel", "旅拍", "city", "城市", "风光", "landscape"), "做旅行/旅拍内容"),
    ("测评", ("测评", "评测", "review", "对比", "comparison", "unboxing", "gear", "器材"), ("测评", "评测", "review", "对比", "comparison", "unboxing", "gear", "器材"), "做器材测评/对比"),
    ("电影镜头", ("电影镜头", "anamorphic", "变形", "cine", "epic", "广告", "commercial"), ("anamorphic", "变形", "cine", "电影感", "cinematic", "filmmaker", "广告", "commercial"), "拍电影/广告片"),
)

# ── 召回展示辅助信号(全独立,绝不并入 viltrox_fit_score / recall_rank_score / rule_v0)──────
# 「视频向产品」query_profile 白名单:监视器 / 电影镜头(其买家=视频拍摄者,纯平面摄影师契合一般)。
VIDEO_LEANING_PROFILES = frozenset({"monitor_dc550pro2", "cine_epic_anamorphic"})
# 人群侧「视频向」词:planner product_focus / target_persona / persona_text 命中即判产品偏视频。
VIDEO_LEANING_PERSONA_WORDS = (
    "videograph", "filmmaker", "cinematograph", "cinematic", "monitor", "监视器",
    "外接屏", "cine", "电影", "影视", "field monitor", "camera monitor", "dp ", "多机位",
)
# KOL 真实信号里的「视频」证据词:命中任一 → 该候选具备视频信号(不判为纯平面)。
KOL_VIDEO_SIGNAL_WORDS = (
    "video", "videograph", "film", "filmmaker", "cinema", "cinematic", "cine",
    "vlog", "vlogger", "monitor", "监视器", "影视", "电影", "电影感", "短片",
    "footage", "gimbal", "稳定器", "b-roll", "broll", "motion", "拍视频",
)
# KOL 真实信号里的「纯平面摄影」证据词:命中且无视频信号 → 判为以平面摄影为主。
KOL_STILL_PHOTO_WORDS = (
    "photographer", "photography", "photo ", "still photo", "stills", "摄影师",
    "平面", "portrait photographer", "headshot", "拍照", "lightroom", "raw photo",
)
# 合作过载阈值:brand_collaborations_json 条数 ≥ 此值即提示议价/独占性弱(数据缺时不杜撰)。
BRAND_COLLAB_OVERLOAD_N = 6


def _is_video_leaning_product(
    query_meta: dict[str, Any],
    persona_text: str,
    product_focus: Any,
) -> bool:
    """本次 query 是否偏「视频/监视器」人群(监视器/cine 产品线,或人群文本含视频向词)。
    纯展示判据,不写任何评分。"""
    profile_key = str((query_meta or {}).get("query_profile") or "")
    if profile_key in VIDEO_LEANING_PROFILES:
        return True
    blob = str(persona_text or "").lower()
    if isinstance(product_focus, (list, tuple)):
        blob += " " + " ".join(str(item).lower() for item in product_focus if item)
    elif product_focus:
        blob += " " + str(product_focus).lower()
    return any(word in blob for word in VIDEO_LEANING_PERSONA_WORDS)


def _kol_signal_blob(row: dict[str, Any], evidence: dict[str, Any]) -> str:
    """KOL 真实画像信号汇总(画像文本/类型理由/bio/主垂类/内容风格/次垂类/已用器材/标签)。"""
    parts: list[str] = [
        _clean_text(row.get("profile_text"), 600),
        _clean_text(row.get("type_reason"), 400),
        _clean_text(row.get("bio"), 300),
        _clean_text(row.get("primary_topic"), 200),
        _clean_text(row.get("content_style"), 200),
        _clean_text(row.get("secondary_topics_json"), 300),
        " ".join(str(lens) for lens in (evidence.get("used_lenses") or [])),
        " ".join(str(label) for label in (evidence.get("reason_labels") or [])),
    ]
    return " ".join(p for p in parts if p).lower()


def _still_photo_dominant(signal_blob: str) -> bool:
    """命中纯平面摄影词且不含任何视频信号 → 判为以平面摄影为主(数据缺时返回 False,不杜撰)。"""
    if not signal_blob:
        return False
    has_video = any(word in signal_blob for word in KOL_VIDEO_SIGNAL_WORDS)
    has_still = any(word in signal_blob for word in KOL_STILL_PHOTO_WORDS)
    return has_still and not has_video


def _brand_collab_count(row: dict[str, Any]) -> int:
    """brand_collaborations_json 真实合作条数;空/[] / 解析失败 → 0(视为数据缺,不提示过载)。"""
    raw = row.get("brand_collaborations_json")
    if raw in (None, "", "[]"):
        return 0
    value: Any = raw
    if isinstance(raw, (str, bytes)):
        try:
            import json as _json_mod

            value = _json_mod.loads(raw)
        except Exception:
            return 0
    if isinstance(value, dict):
        for key in ("brands", "collaborations", "items", "list"):
            inner = value.get(key)
            if isinstance(inner, list):
                value = inner
                break
        else:
            return 0
    if isinstance(value, list):
        return sum(1 for item in value if item not in (None, "", {}, []))
    return 0


# ── 新人优先(用户令):库内反复用的「饱和大号」(高粉)展示降权,让新鲜/上升期候选浮上来。──
# 仅作用于独立展示分 display_rank_score,绝不并入 viltrox_fit_score / recall_rank_score / vector_score。
# 粉丝数据缺(0)→ 一律不动(不杜撰)。分档让降权可解释、可调。
SATURATED_FOLLOWER_TIERS: tuple[tuple[int, float], ...] = (
    (500_000, -0.18),
    (200_000, -0.12),
    (100_000, -0.08),
)
FRESH_FOLLOWER_CEILING = 30_000  # 粉丝低于此且有内容证据 → 上升期小号,新人优先小幅加权
FRESH_PRIORITY_BOOST = 0.06


def _followers_int(row: dict[str, Any]) -> int:
    try:
        return int(float(row.get("followers") or 0))
    except (TypeError, ValueError):
        return 0


def _relevance_signals(
    row: dict[str, Any],
    evidence: dict[str, Any],
    *,
    video_leaning: bool,
) -> tuple[float, list[str], list[str], str]:
    """产出独立展示信号:(display_relevance_adjust, relevance_flags, note_parts, tier_hint)。
    红线:adjust 仅用于一个独立展示排序字段,绝不并入 viltrox_fit_score / recall_rank_score / vector_score。
    note_parts 为可读短语,拼进 why_fit;数据缺时一律不提示(不杜撰)。"""
    adjust = 0.0
    flags: list[str] = []
    notes: list[str] = []
    tier_hint = ""

    signal_blob = _kol_signal_blob(row, evidence)
    # ① 视频向产品 × 纯平面摄影候选:诚实标注 + 展示相关度下调(下调仅作用于独立展示分)。
    if video_leaning and signal_blob and _still_photo_dominant(signal_blob):
        adjust -= 0.12
        flags.append("平面为主")
        notes.append("以平面摄影为主,与视频/监视器人群契合一般")
        tier_hint = "demote"

    # ② 合作过载:真实合作条数高 → 议价/独占性弱(数据缺不提示)。
    collab_count = _brand_collab_count(row)
    if collab_count >= BRAND_COLLAB_OVERLOAD_N:
        adjust -= 0.04
        flags.append(f"合作{collab_count}+")
        notes.append(f"已合作品牌较多({collab_count}),议价/独占性弱")

    # ③ 粉丝量只用于触达硬闸和最终同分 tie-break，不再进入相关度加减。
    #    保留可读标签帮助运营识别账号规模，但避免不同平台体量差异污染检索排序。
    followers = _followers_int(row)
    if followers > 0:
        for threshold, _penalty in SATURATED_FOLLOWER_TIERS:
            if followers >= threshold:
                flags.append("大号·饱和")
                notes.append(f"库内高粉账号({followers:,}),粉丝量未计入检索相关度")
                break
        else:
            if followers <= FRESH_FOLLOWER_CEILING and signal_blob:
                flags.append("新鲜上升期")
                notes.append("上升期小号,粉丝量仅作同分参考")

    return adjust, flags, notes, tier_hint
