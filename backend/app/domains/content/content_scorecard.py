"""内容记分卡三平台换轴(E2 content_scorecard v0,GTM-2 记分卡刀)。

两个入口(全部纯读聚合已有数据,零新采集、零 LLM、零写库、零迁移):
  score_video(evidence_id)        单条 KOL 视频证据按平台北极星轴判档(A/B/C/淘汰/不可判);
  score_channel_posts(channel_id) 官号(vkpi_employee_channels)全部帖子判档 + 分布聚合。

规则库消费闭环(本模块的存在意义):
  所有判档阈值一律从 growth_playbook.rules() 读条目(threshold.value),不硬编数字;
  每个判档输出 rule_refs[](rule_id + role + statement),judgement 可溯源到规则册。
  role 语义:applied=该规则真实参与了本次判档;axis_unavailable=平台北极星轴规则
  存在但所需数据(完播率/2s留存/CTR/sends)公开抓取拿不到,诚实标注不可用;
  gate=统计功效闸(样本不足禁下结论)。

三平台轴(2b 蓝图口径)与诚实态:
  TT  完播四档+hook 档 —— 库内无完播率/2s留存数据 → 两轴 status=unknown,
      判档退化为互动率+账号内播放分位双代理(proxy=true,逐条 proxy_notes);
  IG  sends/saves 北极星 —— 公开数据拿不到 → status=unavailable,
      用互动结构(评论占比等)+ 可保存实用型词表(对已析文本回打标签,零成本)代理;
  YT  CTR×留存双门槛 —— 拿不到 → 保持双门槛结构,用互动门+分位门双代理并标 proxy。

compat 约定:SQL 占位符用 ?;SQL 文本禁裸 percent 与多余 ASCII 问号;BOOLEAN 过滤
沿用库内 is_active = TRUE 写法(读回值不直接参与 Python 真值判断);懒 import get_conn。
红线:纯读,绝不写库;绝不触碰 viltrox_fit_score / rule_v0;不重析视频、不入队。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.domains.market_brain import growth_playbook

logger = get_logger(__name__)

METHOD = "content_scorecard_v0"
FINAL_ANALYSIS_KIND = "video_final_v1"

# 判档档位(键为机器码,label 为展示名);unrated=统计功效闸不足样本,诚实不判。
TIER_LABELS: dict[str, str] = {
    "A": "A档",
    "B": "B档",
    "C": "C档",
    "eliminate": "淘汰",
    "unrated": "不可判",
}
TIER_ORDER: tuple[str, ...] = ("A", "B", "C", "eliminate", "unrated")

# ── 消费的规则 id 清单(全部必须真实存在于 growth_playbook;冒烟有存在性断言)──
RULE_STAT_EXPOSURE = "stat_power_min_exposure"
RULE_STAT_SAMPLE = "stat_power_min_sample"
RULE_ENG_ANCHOR = "funnel_stage3_engagement"
RULE_PCT_TOP = "boost_trigger_organic_top25"
RULES_TT_COMPLETION: tuple[str, ...] = (
    "tt_completion_tier_viral",
    "tt_completion_tier_strong",
    "tt_completion_tier_average",
    "tt_completion_tier_weak",
)
RULES_TT_HOOK: tuple[str, ...] = ("tt_hook_2s_a_tier", "tt_hook_2s_floor")
RULE_IG_SENDS = "ig_sends_weight"
RULE_IG_SAVEABLE = "ig_saveable_utility"
RULE_YT_GATE = "yt_double_gate"
RULE_EMOTION = "emotion_gear_four_labels"

# ── 词表(零成本回打标签用;宁缺毋滥,识别不了如实 unclassified)────────────
# IG 可保存实用型(ig_saveable_utility 的 content_format 词表化落地)
SAVEABLE_TERMS: tuple[str, ...] = (
    "tutorial", "how to", "settings", "setup", "guide", "tips",
    "教程", "教学", "参数", "设置", "技巧", "构图", "步骤",
)
# 器材四情绪(emotion_gear_four_labels;只对 final_v1 已析文本打,不对裸标题硬贴)
EMOTION_TERMS: dict[str, tuple[str, ...]] = {
    "awe": ("震撼", "惊叹", "惊艳", "壮观", "奇观", "大片感", "电影感", "stunning", "breathtaking", "cinematic"),
    "identity": ("创作者身份", "人设", "身份认同", "自我表达", "vlogger", "filmmaker", "creator"),
    "transformation": ("前后对比", "对比测试", "升级", "解锁", "蜕变", "before", "after", "upgrade"),
    "trust": ("专业", "严谨", "实测", "评测", "测评", "可信", "review", "professional"),
}


# ── 小工具 ──────────────────────────────────────────────────────────


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _loads(value: Any) -> Any:
    """JSONB 经 compat 层可能回 dict 也可能回 str,双态容错。"""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _text(value: Any, limit: int = 200) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _norm_platform(value: Any) -> str:
    p = str(value or "").strip().lower()
    if p in ("tiktok", "instagram", "youtube"):
        return p
    return p or "other"


# ── 规则册消费层(唯一取数口;绝不在别处写死阈值)───────────────────


_RULE_INDEX: dict[str, dict[str, Any]] | None = None


def _rule_index() -> dict[str, dict[str, Any]]:
    """加载规则册为 rule_id 索引(进程内缓存;纯内存读,零 DB)。"""
    global _RULE_INDEX
    if _RULE_INDEX is None:
        payload = growth_playbook.rules()
        _RULE_INDEX = {str(r.get("rule_id")): r for r in payload.get("rules", [])}
    return _RULE_INDEX


def _rule(rule_id: str) -> dict[str, Any]:
    item = _rule_index().get(rule_id)
    if not item:
        raise LookupError(f"growth_playbook rule not found: {rule_id}")
    return item


def _rule_value(rule_id: str) -> Any:
    """读某规则 threshold.value(判档阈值的唯一来源)。"""
    return _as_dict(_rule(rule_id).get("threshold")).get("value")


def _ref(rule_id: str, role: str, note: str) -> dict[str, Any]:
    """构造一条 rule 引用(rule_id 不存在直接抛错,判档绝不引用幽灵规则)。"""
    item = _rule(rule_id)
    return {
        "rule_id": rule_id,
        "role": role,
        "note": _text(note, 160),
        "statement": _text(item.get("statement"), 160),
        "confidence": item.get("confidence"),
        "source": _text(item.get("source"), 80),
    }


# ── 轴级分类器(消费规则条目的 threshold 结构;数据缺失回 None=unknown)──


def _completion_tier(completion_rate: float | None) -> str | None:
    """TT 完播四档:逐条消费 tt_completion_tier_* 的 threshold(op+value+tier)。"""
    if completion_rate is None:
        return None
    for rule_id in RULES_TT_COMPLETION:
        th = _as_dict(_rule(rule_id).get("threshold"))
        op, value, tier = th.get("op"), th.get("value"), str(th.get("tier") or "")
        if op == ">=" and completion_rate >= float(value):
            return tier
        if op == "<" and completion_rate < float(value):
            return tier
        if op == "between" and isinstance(value, list) and len(value) == 2:
            if float(value[0]) <= completion_rate < float(value[1]):
                return tier
    return None


def _hook_tier(retention_2s: float | None) -> str | None:
    """TT 2 秒 hook 档:消费 tt_hook_2s_*;两锚之间规则册未定义,诚实回 between_anchors。"""
    if retention_2s is None:
        return None
    for rule_id in RULES_TT_HOOK:
        th = _as_dict(_rule(rule_id).get("threshold"))
        op, value, tier = th.get("op"), th.get("value"), str(th.get("tier") or "")
        if op == ">=" and retention_2s >= float(value):
            return tier
        if op == "<" and retention_2s < float(value):
            return tier
    return "between_anchors"


def _engagement_rate(views: int | None, likes: int | None, comments: int | None, shares: int | None) -> float | None:
    """(赞+评+转发)/播放;播放缺失或为 0 诚实回 None,不除零不外推。"""
    if not views or views <= 0:
        return None
    inter = (likes or 0) + (comments or 0) + (shares or 0)
    return round(inter / views, 5)


def _percentile(cohort: list[int], value: int) -> float | None:
    """value 在 cohort(含自身)里的播放分位:严格小于数/(n-1);n<2 回 None。"""
    n = len(cohort)
    if n < 2:
        return None
    below = sum(1 for v in cohort if v < value)
    return round(below / (n - 1), 4)


def _match_any(blob: str, terms: tuple[str, ...]) -> list[str]:
    low = blob.lower()
    return [t for t in terms if t.lower() in low]


# ── 判档引擎(三平台共用骨架 + 平台轴差异;全代理判档 proxy=true)──────


def _grade(
    platform: str,
    views: int | None,
    likes: int | None,
    comments: int | None,
    shares: int | None,
    cohort_views: list[int],
    text_blob: str,
) -> dict[str, Any]:
    """核心判档:返回 {tier, tier_label, tier_basis, signals, axes, proxy_notes, rule_refs}。

    北极星真数据(完播/2s留存/CTR/sends)库内不存在 → 各平台轴诚实 unknown/unavailable,
    判档走互动率锚(funnel_stage3_engagement)+ 账号内播放分位(boost_trigger_organic_top25)
    双代理;样本不足由统计功效闸(stat_power_min_exposure / stat_power_min_sample)拦住。
    """
    refs: list[dict[str, Any]] = []
    notes: list[str] = []

    # —— 阈值全部现场从规则册读(别处不许写死)——
    min_views = int(_rule_value(RULE_STAT_EXPOSURE) or 0)
    eng_anchor = float(_rule_value(RULE_ENG_ANCHOR) or 0)
    top_pct = float(_rule_value(RULE_PCT_TOP) or 0)
    bottom_pct = round(1.0 - top_pct, 4)  # 由同一条 boost 规则镜像导出(底部分位=1-顶部分位)
    min_cohort = int(_rule_value(RULE_STAT_SAMPLE) or 0)

    eng_rate = _engagement_rate(views, likes, comments, shares)
    pct = _percentile(cohort_views, int(views)) if (views and views > 0) else None
    cohort_ok = len(cohort_views) >= min_cohort
    if not cohort_ok:
        pct = None

    signals: dict[str, Any] = {
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "engagement_rate": eng_rate,
        "engagement_basis": "(赞+评" + ("+转发" if shares is not None else ",转发缺失") + ")/播放",
        "account_percentile": pct,
        "cohort_size": len(cohort_views),
        "proxy": True,
    }

    # —— 平台北极星轴(诚实态)——
    axes: dict[str, Any] = {}
    if platform == "tiktok":
        axes["completion"] = {
            "status": "unknown",
            "tier": _completion_tier(None),
            "reason": "公开抓取拿不到完播率,四档判档暂不可用(schema 已备,数据到位即启用)",
            "rules": list(RULES_TT_COMPLETION),
        }
        axes["hook_2s"] = {
            "status": "unknown",
            "tier": _hook_tier(None),
            "reason": "公开抓取拿不到 2 秒留存,hook 档暂不可用",
            "rules": list(RULES_TT_HOOK),
        }
        for rid in RULES_TT_COMPLETION:
            refs.append(_ref(rid, "axis_unavailable", "完播率数据缺失,该档规则本次未参与判档"))
        for rid in RULES_TT_HOOK:
            refs.append(_ref(rid, "axis_unavailable", "2 秒留存数据缺失,该档规则本次未参与判档"))
        notes.append("TT 完播四档+hook 档均无真数据:判档为互动率+账号分位双代理(proxy)")
    elif platform == "instagram":
        axes["sends"] = {
            "status": "unavailable",
            "reason": "sends(私发分享)是 IG 私有信号,公开数据拿不到;以互动结构代理",
            "rules": [RULE_IG_SENDS],
        }
        axes["saves"] = {
            "status": "unavailable",
            "reason": "saves 公开数据拿不到;以「可保存实用型」词表对已析文本回打标签代理",
            "rules": [RULE_IG_SAVEABLE],
        }
        refs.append(_ref(RULE_IG_SENDS, "axis_unavailable", "sends 不可得,互动结构代理"))
        total_inter = (likes or 0) + (comments or 0)
        comment_share = round((comments or 0) / total_inter, 4) if total_inter > 0 else None
        signals["comment_share_of_interactions"] = comment_share
        saveable_hits = _match_any(text_blob, SAVEABLE_TERMS) if text_blob else []
        signals["saveable_format_terms"] = saveable_hits
        if saveable_hits:
            refs.append(_ref(RULE_IG_SAVEABLE, "applied", "词表命中可保存实用型:" + ",".join(saveable_hits[:4])))
        notes.append("IG sends/saves 均不可得:判档用互动率+账号分位代理,互动结构与词表仅作旁证")
    elif platform == "youtube":
        axes["double_gate"] = {
            "status": "proxy",
            "reason": "CTR 与平均留存是 YT Studio 私有数据,公开抓取拿不到;保持双门槛结构,用互动门+分位门双代理",
            "rules": [RULE_YT_GATE],
        }
        refs.append(_ref(RULE_YT_GATE, "applied", "双门槛结构以互动门+分位门双代理执行(CTR/留存不可得)"))
        notes.append("YT CTR×留存双门槛不可得:以互动率门+账号分位门保持双门槛结构(proxy)")
    else:
        axes["generic"] = {
            "status": "proxy",
            "reason": "该平台无专用北极星轴规则,仅按全平台漏斗锚+分位代理判档",
            "rules": [RULE_ENG_ANCHOR, RULE_PCT_TOP],
        }
        notes.append("非三大平台:仅漏斗互动锚+账号分位代理判档")

    # —— 统计功效闸(样本不足禁下结论;引用即闸真实拦截)——
    if views is None or views <= 0:
        refs.append(_ref(RULE_STAT_EXPOSURE, "gate", "播放数缺失,曝光代理不可得,禁下档位结论"))
        return {
            "tier": "unrated",
            "tier_label": TIER_LABELS["unrated"],
            "tier_basis": "播放数缺失(曝光代理不可得),统计功效闸禁判",
            "signals": signals,
            "axes": axes,
            "proxy_notes": notes + ["播放数缺失:不判档、不淘汰,绝不编数"],
            "rule_refs": refs,
        }
    if views < min_views:
        refs.append(_ref(RULE_STAT_EXPOSURE, "gate", f"播放 {views} 低于最小曝光 {min_views}(以播放代理曝光),禁下档位结论"))
        return {
            "tier": "unrated",
            "tier_label": TIER_LABELS["unrated"],
            "tier_basis": f"播放 {views} 未过统计功效闸(最小曝光 {min_views},播放代理),不下结论",
            "signals": signals,
            "axes": axes,
            "proxy_notes": notes + ["样本不足:小样本判优劣大概率是噪声,如实标不可判"],
            "rule_refs": refs,
        }

    # —— 双代理判档 ——
    eng_ok = eng_rate is not None and eng_rate >= eng_anchor
    refs.append(_ref(RULE_ENG_ANCHOR, "applied", f"互动率 {eng_rate} 对锚 {eng_anchor}(代理判定 {'过' if eng_ok else '未过'})"))
    pct_top = pct is not None and pct >= top_pct
    pct_bottom = pct is not None and pct < bottom_pct
    if pct is not None:
        refs.append(_ref(RULE_PCT_TOP, "applied", f"账号内播放分位 {pct} 对顶部线 {top_pct}(全量历史代替30天窗,底部线取 1-顶部={bottom_pct})"))
        notes.append("分位口径:该账号同平台全量历史播放分布(库内无30天滚动窗,诚实代理)")
    else:
        refs.append(_ref(RULE_STAT_SAMPLE, "gate", f"账号同平台可比样本 {len(cohort_views)} 条不足 {min_cohort},分位信号弃用"))
        notes.append("账号内可比样本不足:分位信号不可用,判档仅剩互动锚单代理(不判淘汰)")

    if eng_ok and pct_top:
        tier, basis = "A", "互动率过锚且播放进账号头部分位(双代理同时通过)"
    elif pct is not None and (not eng_ok) and pct_bottom:
        tier, basis = "eliminate", "互动率未过锚且播放落账号底部分位(双代理同时失败)"
    elif eng_ok or pct_top:
        tier, basis = "B", "双代理过其一(互动锚或头部分位)"
    else:
        tier, basis = "C", "双代理均未过但未落底部分位(维持观察,不加码)"

    return {
        "tier": tier,
        "tier_label": TIER_LABELS[tier],
        "tier_basis": basis + ";北极星真数据缺失,全程代理判档",
        "signals": signals,
        "axes": axes,
        "proxy_notes": notes,
        "rule_refs": refs,
    }


# ── 情绪回打标签(仅对 final_v1 已析文本;零成本词表法,非重析)─────────


def _emotion_block(deep_text: str) -> dict[str, Any]:
    if not deep_text:
        return {"status": "no_text", "reason": "该视频无 final_v1 深析文本,不对裸元数据硬贴情绪标签"}
    hits: dict[str, list[str]] = {}
    for label, terms in EMOTION_TERMS.items():
        matched = _match_any(deep_text, terms)
        if matched:
            hits[label] = matched
    if not hits:
        return {
            "status": "unclassified",
            "reason": "词表未命中四情绪(宁缺毋滥,不硬贴)",
            "method": "lexicon_v0(对已析文本回打标签,零成本非重析)",
            "rule_refs": [_ref(RULE_EMOTION, "axis_unavailable", "四情绪词表零命中,本条不贴标签")],
        }
    primary = max(hits.items(), key=lambda kv: len(kv[1]))[0]
    return {
        "status": "tagged",
        "primary_emotion": primary,
        "matched": {k: v[:4] for k, v in hits.items()},
        "method": "lexicon_v0(对已析文本回打标签,零成本非重析)",
        "rule_refs": [_ref(RULE_EMOTION, "applied", f"词表命中主情绪 {primary}(多标签命中取词数最多)")],
    }


# ── 数据装载(全只读)────────────────────────────────────────────────


def _load_evidence(conn: Any, evidence_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, kol_pool_id, platform, COALESCE(video_title, title, '') AS title,
               content_url, view_count, like_count, comment_count, share_count,
               published_at_norm
        FROM vkpi_kol_video_evidence
        WHERE id = ?
        """,
        (int(evidence_id),),
    ).fetchone()
    return dict(row) if row else None


def _load_deep_row(conn: Any, evidence_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT llm_dimensions_11
        FROM vkpi_kol_llm_deep_analysis_results
        WHERE source_evidence_id = ? AND analysis_kind = ? AND status = 'ready'
              AND llm_dimensions_11 IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(evidence_id), FINAL_ANALYSIS_KIND),
    ).fetchone()
    return _as_dict(_loads(row["llm_dimensions_11"])) if row else {}


def _load_kol_cohort_views(conn: Any, kol_pool_id: int, platform: str) -> list[int]:
    rows = conn.execute(
        """
        SELECT view_count FROM vkpi_kol_video_evidence
        WHERE kol_pool_id = ? AND COALESCE(platform, '') = ? AND is_active = TRUE
              AND view_count IS NOT NULL AND view_count > 0
        LIMIT 2000
        """,
        (int(kol_pool_id), platform),
    ).fetchall()
    return [int(r["view_count"]) for r in rows]


def _deep_text_blob(dim: dict[str, Any]) -> str:
    """final_v1 投影(llm_dimensions_11)里的可回打标签文本:layer1 摘要+钩子+裁决。"""
    l1 = _as_dict(dim.get("layer1_summary"))
    risk = _as_dict(dim.get("risk"))
    parts = [
        l1.get("content_summary"), l1.get("production_observations"),
        l1.get("brand_exposure"), risk.get("key_hook"), risk.get("final_verdict"),
    ]
    return " ".join(_text(p, 600) for p in parts if p)


# ── 主入口①:单视频判档 ─────────────────────────────────────────────


def score_video(evidence_id: int, *, conn: Any = None) -> dict[str, Any]:
    """单条视频证据按平台北极星轴判档;evidence 不存在抛 LookupError(路由转 404)。"""
    from app.db.connection import get_conn

    db = conn or get_conn()
    ev = _load_evidence(db, int(evidence_id))
    if not ev:
        raise LookupError(f"evidence {evidence_id} not found")

    platform = _norm_platform(ev.get("platform"))
    raw_platform = str(ev.get("platform") or "").strip().lower()
    kol_pool_id = _int_or_none(ev.get("kol_pool_id"))
    cohort = _load_kol_cohort_views(db, kol_pool_id, raw_platform) if kol_pool_id is not None else []

    dim = _load_deep_row(db, int(evidence_id))
    deep_text = _deep_text_blob(dim)

    graded = _grade(
        platform,
        _int_or_none(ev.get("view_count")),
        _int_or_none(ev.get("like_count")),
        _int_or_none(ev.get("comment_count")),
        _int_or_none(ev.get("share_count")),
        cohort,
        deep_text or _text(ev.get("title"), 300),
    )
    emotion = _emotion_block(deep_text)

    risk = _as_dict(dim.get("risk"))
    return {
        "status": "ready",
        "method": METHOD,
        "evidence_id": int(evidence_id),
        "platform": platform,
        "title": _text(ev.get("title"), 160),
        "content_url": _text(ev.get("content_url"), 300) or None,
        "posted_at": _iso(ev.get("published_at_norm")),
        "kol_pool_id": kol_pool_id,
        "tier": graded["tier"],
        "tier_label": graded["tier_label"],
        "tier_basis": graded["tier_basis"],
        "signals": graded["signals"],
        "axes": graded["axes"],
        "emotion": emotion,
        "proxy_notes": graded["proxy_notes"],
        "rule_refs": graded["rule_refs"] + list(emotion.get("rule_refs") or []),
        "deep_analysis": {
            "present": bool(dim),
            "key_hook": _text(risk.get("key_hook"), 160) or None,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "规则库代理判档(growth_playbook 消费闭环);独立展示信号,不参与 V6 Fit 评分,不触发任何重析。",
    }


# ── 主入口②:官号帖子分布 ───────────────────────────────────────────


def _load_channel(conn: Any, channel_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, channel_uid, platform, account_handle, account_display_name, account_url
        FROM vkpi_employee_channels
        WHERE id = ? AND deleted_at IS NULL
        """,
        (int(channel_id),),
    ).fetchone()
    return dict(row) if row else None


def _load_latest_posts(conn: Any, channel_id: int) -> list[dict[str, Any]]:
    """该官号每个 post_uid 的最新快照(按 snapshot_date/id 降序去重)。"""
    rows = conn.execute(
        """
        SELECT post_uid, platform, post_url, title, posted_at, views, likes, comments,
               shares, snapshot_date, captured_at
        FROM vkpi_channel_post_metrics
        WHERE channel_id = ?
        ORDER BY snapshot_date DESC, id DESC
        LIMIT 20000
        """,
        (int(channel_id),),
    ).fetchall()
    seen: set[str] = set()
    latest: list[dict[str, Any]] = []
    for r in rows:
        uid = str(r["post_uid"] or "")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        latest.append(dict(r))
    return latest


def score_channel_posts(channel_id: int, *, conn: Any = None) -> dict[str, Any]:
    """官号全部帖子判档 + 档位分布聚合;官号不存在抛 LookupError(路由转 404)。"""
    from app.db.connection import get_conn

    db = conn or get_conn()
    ch = _load_channel(db, int(channel_id))
    if not ch:
        raise LookupError(f"channel {channel_id} not found")

    posts = _load_latest_posts(db, int(channel_id))
    platform = _norm_platform(ch.get("platform"))
    channel_info = {
        "id": int(channel_id),
        "platform": platform,
        "handle": _text(ch.get("account_handle"), 80),
        "display_name": _text(ch.get("account_display_name"), 80),
        "url": _text(ch.get("account_url"), 200) or None,
    }
    if not posts:
        return {
            "status": "empty",
            "reason": "该官号在 vkpi_channel_post_metrics 里没有帖子快照",
            "method": METHOD,
            "channel": channel_info,
        }

    cohort = [int(p["views"]) for p in posts if _int_or_none(p.get("views")) and int(p["views"]) > 0]
    counts: dict[str, int] = {t: 0 for t in TIER_ORDER}
    examples: dict[str, list[dict[str, Any]]] = {t: [] for t in TIER_ORDER}
    refs_by_id: dict[str, dict[str, Any]] = {}
    notes: set[str] = set()
    latest_snapshot: str | None = None
    latest_captured: str | None = None

    for p in posts:
        post_platform = _norm_platform(p.get("platform")) or platform
        graded = _grade(
            post_platform,
            _int_or_none(p.get("views")),
            _int_or_none(p.get("likes")),
            _int_or_none(p.get("comments")),
            _int_or_none(p.get("shares")),
            cohort,
            _text(p.get("title"), 300),
        )
        tier = graded["tier"]
        counts[tier] = counts.get(tier, 0) + 1
        for ref in graded["rule_refs"]:
            rid = str(ref.get("rule_id"))
            prev = refs_by_id.get(rid)
            if prev is None or (prev.get("role") != "applied" and ref.get("role") == "applied"):
                refs_by_id[rid] = ref
        for n in graded["proxy_notes"]:
            notes.add(n)
        snap = _iso(p.get("snapshot_date"))
        cap = _iso(p.get("captured_at"))
        if snap and (latest_snapshot is None or snap > latest_snapshot):
            latest_snapshot = snap
        if cap and (latest_captured is None or cap > latest_captured):
            latest_captured = cap
        examples[tier].append(
            {
                "post_uid": _text(p.get("post_uid"), 80),
                "title": _text(p.get("title"), 120) or None,
                "post_url": _text(p.get("post_url"), 300) or None,
                "posted_at": _iso(p.get("posted_at")),
                "views": _int_or_none(p.get("views")),
                "likes": _int_or_none(p.get("likes")),
                "comments": _int_or_none(p.get("comments")),
                "engagement_rate": graded["signals"].get("engagement_rate"),
                "account_percentile": graded["signals"].get("account_percentile"),
                "tier": tier,
                "tier_basis": graded["tier_basis"],
            }
        )

    total = len(posts)
    for t in TIER_ORDER:
        examples[t] = sorted(examples[t], key=lambda x: (x.get("views") or 0), reverse=True)[:3]
    distribution = [
        {
            "tier": t,
            "tier_label": TIER_LABELS[t],
            "count": counts.get(t, 0),
            "share": round(counts.get(t, 0) / total, 4) if total else 0.0,
        }
        for t in TIER_ORDER
    ]

    return {
        "status": "ready",
        "method": METHOD,
        "channel": channel_info,
        "posts_total": total,
        "posts_judged": total - counts.get("unrated", 0),
        "distribution": distribution,
        "examples": examples,
        "proxy_notes": sorted(notes),
        "rule_refs": list(refs_by_id.values()),
        "latest_snapshot_date": latest_snapshot,
        "latest_captured_at": latest_captured,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "官号内容分布=每帖最新快照按规则库代理判档聚合;纯读,不触发任何采集/重析。",
    }
