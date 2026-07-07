"""E4 · 规则库回归校准报告(growth_playbook 30 条 × 自有已析视频,纯读零成本)。

validate_rules() 用自有数据检验 market_brain.growth_playbook 规则库:
  ① 相关性侦察:对已析 video_final_v1 视频(join evidence 拿真实互动数),
     计算 可得信号(标题 hook 结构词 / 情绪词表命中 / 发布时段 / 时长)与
     互动率、播放量 的 Spearman 秩相关——秩+Fisher-z 全手算,零新依赖,决定性;
  ② 逐条 verdict:每条 playbook 规则出 supported / contradicted / insufficient_data;
     样本 < 30 一律 insufficient(统计功效闸纪律,比 playbook 自身的 5 更严);
     所需指标库内不可得(完播率/2s留存/CTR/sends/佣金/投放预算)诚实 insufficient;
  ③ 校准报告结构 {rule_id, our_sample, our_finding, verdict, confidence},
     供规则库 overridable 机制消费(本模块只出报告,不改规则库、不改权重)。

成本红线:零 LLM、零视频重析、零采集——信号全部来自词表/规则法对已析文本回打,
互动数来自 evidence 表既有列。与 E1/E2/E3 模块零 import 依赖(自己读表);
消费 growth_playbook(纯内存规则册)而非重造。

compat 约定:get_conn() + SQL ? 占位、零字面 percent;jsonb/时间读回宽容解析;
读后即 commit(防 idle-in-transaction)。表缺/空数据诚实空态,异常不 500。
红线:纯读;绝不写 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

DEEP_TABLE = "vkpi_kol_llm_deep_analysis_results"
EVIDENCE_TABLE = "vkpi_kol_video_evidence"
ANALYSIS_KIND = "video_final_v1"

METHOD = "rule_validation_v0"
MIN_SAMPLE = 30   # 任务纪律:样本<30 一律 insufficient(比 playbook stat_power 的 5 更严)
SIG_P = 0.05      # 相关显著性闸(Fisher-z 近似双侧)

VERDICT_SUPPORTED = "supported"
VERDICT_CONTRADICTED = "contradicted"
VERDICT_INSUFFICIENT = "insufficient_data"

# ── 词表(规则法零成本信号;中英混排,已析摘要多为中文) ──────────────
_LEX_AWE = (
    "stunning", "amazing", "incredible", "insane", "unreal", "cinematic", "epic",
    "gorgeous", "breathtaking", "wow", "magic", "惊艳", "震撼", "电影感", "绝美",
    "奇观", "大片",
)
_LEX_IDENTITY = (
    "creator", "photographer", "filmmaker", "videographer", "storyteller",
    "创作者", "摄影师", "摄像师", "身份", "人设",
)
_LEX_TRANSFORMATION = (
    "before", "after", "upgrade", "switch", "transform", "changed",
    "升级", "蜕变", "换镜", "前后对比",
)
_LEX_TRUST = (
    "review", "honest", "test", "tested", "truth", "real world",
    "评测", "实测", "真实", "靠谱", "上手",
)
_LEX_SUPERLATIVE = (
    "best", "most", "ultimate", "perfect", "king", "beast", "cheapest",
    "fastest", "最强", "最好", "王者", "神",
)
_LEX_COMPARISON = ("vs", "versus", "comparison", "compare", "better than", "对比", "打败")
_LEX_TUTORIAL = ("how to", "tutorial", "settings", "guide", "tips", "setup", "教程", "设置", "技巧")

_DIGIT_RE = re.compile(r"\d")


# ── 小工具(读回宽容) ────────────────────────────────────────────────


def _int0(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(value)
    except Exception:
        return 0


def _text(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _loads_safe(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _hits(text: str, words: tuple[str, ...]) -> int:
    low = text.lower()
    return sum(1 for w in words if w in low)


# ── Spearman 秩相关(纯手算,决定性) ─────────────────────────────────


def _ranks(values: list[float]) -> list[float]:
    """平均并列秩;稳定排序 → 同数据双跑逐位一致。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> tuple[float | None, float | None]:
    """返回 (rho, 双侧 p 近似);p 用 Fisher-z(atanh(rho)*sqrt(n-3))+ 正态 CDF(erf)。"""
    n = len(xs)
    if n < 4 or len(ys) != n:
        return None, None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx <= 0 or vy <= 0:
        return None, None  # 常量列无秩相关可言,诚实 None
    rho = max(-0.999999, min(0.999999, cov / math.sqrt(vx * vy)))
    z = math.atanh(rho) * math.sqrt(n - 3)
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return round(rho, 4), round(min(max(p, 0.0), 1.0), 6)


def _percentile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(q * len(sorted_vals))))
    return sorted_vals[idx]


# ── 数据集:已析视频 join evidence(纯读,读后 commit) ────────────────


def _collect_videos() -> list[dict[str, Any]]:
    from app.db.connection import get_conn

    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        f"""
        SELECT d.id AS deep_id, d.llm_dimensions_11,
               e.platform, e.view_count, e.like_count, e.comment_count,
               e.duration_seconds, e.published_at_norm, e.title, e.video_title
        FROM {DEEP_TABLE} d
        JOIN {EVIDENCE_TABLE} e ON e.id = d.source_evidence_id
        WHERE d.analysis_kind = ? AND d.status = ?
        ORDER BY d.id
        """,
        (ANALYSIS_KIND, "ready"),
    ).fetchall()]
    try:
        conn.commit()  # 读后即 commit,防 idle-in-transaction
    except Exception as exc:
        logger.warning("rule_validation post-read commit failed analysis_kind=%s: %s", ANALYSIS_KIND, exc)

    videos: list[dict[str, Any]] = []
    for r in rows:
        views = _int0(r.get("view_count"))
        if views <= 0:
            continue  # 无播放分母,互动率不可算
        dims = _loads_safe(r.get("llm_dimensions_11"))
        dims = dims if isinstance(dims, dict) else {}
        src = dims.get("source") if isinstance(dims.get("source"), dict) else {}
        l1 = dims.get("layer1_summary") if isinstance(dims.get("layer1_summary"), dict) else {}
        risk = dims.get("risk") if isinstance(dims.get("risk"), dict) else {}
        title = _text(r.get("title") or r.get("video_title") or src.get("title"), 500)
        summary = _text(l1.get("content_summary"), 2000)
        hook = _text(risk.get("key_hook"), 1000)
        corpus = " ".join((title, summary, hook))
        published = _parse_dt(r.get("published_at_norm"))
        likes = _int0(r.get("like_count"))
        comments = _int0(r.get("comment_count"))
        duration = _int0(r.get("duration_seconds"))
        videos.append({
            "deep_id": _int0(r.get("deep_id")),
            "platform": _text(r.get("platform"), 30).lower(),
            "views": views,
            "engagement_rate": (likes + comments) / views,
            "duration_seconds": duration if duration > 0 else None,
            "publish_hour_utc": published.hour if published else None,
            "has_title": bool(title),
            "title_hook_question": 1 if ("?" in title or "？" in title) else 0,
            "title_hook_number": 1 if _DIGIT_RE.search(title) else 0,
            "title_hook_superlative": _hits(title, _LEX_SUPERLATIVE),
            "title_hook_comparison": 1 if _hits(title, _LEX_COMPARISON) else 0,
            "emotion_awe_hits": _hits(corpus, _LEX_AWE),
            "emotion_identity_hits": _hits(corpus, _LEX_IDENTITY),
            "emotion_transformation_hits": _hits(corpus, _LEX_TRANSFORMATION),
            "emotion_trust_hits": _hits(corpus, _LEX_TRUST),
            "tutorial_lexicon": 1 if _hits(" ".join((title, summary)), _LEX_TUTORIAL) else 0,
        })
    return videos


# ── ① 相关性侦察(可得信号 × 互动率/播放量) ─────────────────────────

_SIGNALS: tuple[tuple[str, str], ...] = (
    ("title_hook_question", "标题含提问(?)——hook 四结构之「提问」"),
    ("title_hook_number", "标题含数字——具体性/结果前置代理"),
    ("title_hook_superlative", "标题最高级词命中数——「断言」结构代理"),
    ("title_hook_comparison", "标题对比结构(vs/对比)"),
    ("emotion_awe_hits", "awe 情绪词表命中数(标题+摘要+key_hook)"),
    ("emotion_identity_hits", "identity 情绪词表命中数"),
    ("emotion_transformation_hits", "transformation 情绪词表命中数"),
    ("emotion_trust_hits", "trust 情绪词表命中数"),
    ("tutorial_lexicon", "实用教程词表(how to/settings/教程)"),
    ("publish_hour_utc", "发布时段(UTC 小时;粗代理,受众时区未知)"),
    ("duration_seconds", "视频时长(秒;仅时长已知子集)"),
)
_METRICS: tuple[tuple[str, str], ...] = (
    ("engagement_rate", "互动率=(赞+评)/播放(share_count 库内全 0,不计)"),
    ("view_count", "播放量(跨账号体量未归一,秩相关口径下可用)"),
)


def _corr_pairs(videos: list[dict[str, Any]], signal: str, metric_key: str) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for v in videos:
        x = v.get(signal)
        if x is None:
            continue
        if signal.startswith("title_") and not v.get("has_title"):
            continue
        y = v["engagement_rate"] if metric_key == "engagement_rate" else v["views"]
        xs.append(float(x))
        ys.append(float(y))
    return xs, ys


def _correlations(videos: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for signal, signal_note in _SIGNALS:
        for metric_key, _metric_note in _METRICS:
            xs, ys = _corr_pairs(videos, signal, metric_key)
            n = len(xs)
            rho, p = (_spearman(xs, ys) if n >= MIN_SAMPLE else (None, None))
            significant = bool(rho is not None and p is not None and p < SIG_P)
            items.append({
                "signal": signal,
                "signal_note": signal_note,
                "metric": metric_key,
                "n": n,
                "spearman_rho": rho,
                "p_value": p,
                "significant": significant,
                "direction": ("positive" if rho > 0 else "negative") if significant and rho is not None else None,
                "note": (f"样本 {n} < {MIN_SAMPLE},不出相关结论" if n < MIN_SAMPLE else
                         ("" if significant else "无显著相关(p >= 0.05)——诚实空结论")),
            })
    sig_count = sum(1 for it in items if it["significant"])
    return {
        "method": (
            "Spearman 秩相关(平均并列秩)手算;p 值=Fisher-z 正态近似双侧;"
            f"显著闸 p<{SIG_P};样本<{MIN_SAMPLE} 不出结论。相关不是因果,仅作规则校准侦察。"
        ),
        "metrics": [{"key": k, "note": note} for k, note in _METRICS],
        "significant_count": sig_count,
        "items": items,
    }


def _corr_index(corr: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(it["signal"], it["metric"]): it for it in corr["items"]}


# ── ② 逐条规则 verdict ───────────────────────────────────────────────


def _item(rule: dict[str, Any], *, our_sample: int, our_finding: str, verdict: str,
          confidence: str, testable: bool = True) -> dict[str, Any]:
    return {
        "rule_id": str(rule.get("rule_id")),
        "platform": rule.get("platform"),
        "statement": _text(rule.get("statement"), 120),
        "playbook_confidence": rule.get("confidence"),
        "testable": testable,
        "our_sample": int(our_sample),
        "our_finding": our_finding,
        "verdict": verdict,
        "confidence": confidence,
    }


def _verdict_confidence(n: int, p: float | None) -> str:
    if p is not None and p < 0.01 and n >= 100:
        return "medium"
    return "low"


def _corr_verdict(rule: dict[str, Any], corr_item: dict[str, Any], *,
                  finding_prefix: str, expect_positive: bool = True) -> dict[str, Any]:
    """通用「相关性检验」裁决:样本闸 → 显著性 → 方向与规则预期比对。"""
    n = _int0(corr_item.get("n"))
    rho, p = corr_item.get("spearman_rho"), corr_item.get("p_value")
    if n < MIN_SAMPLE:
        return _item(rule, our_sample=n, verdict=VERDICT_INSUFFICIENT, confidence="low",
                     our_finding=f"{finding_prefix};样本 {n} < {MIN_SAMPLE},统计功效闸拦下,不出结论。")
    if rho is None or p is None or p >= SIG_P:
        detail = f"rho={rho}, p={p}" if rho is not None else "信号列无方差"
        return _item(rule, our_sample=n, verdict=VERDICT_INSUFFICIENT, confidence="low",
                     our_finding=f"{finding_prefix};n={n} 无显著相关({detail})——样本足但无信号,暂不支持也不推翻。")
    aligned = (rho > 0) == expect_positive
    verdict = VERDICT_SUPPORTED if aligned else VERDICT_CONTRADICTED
    word = "同向" if aligned else "反向"
    return _item(rule, our_sample=n, verdict=verdict, confidence=_verdict_confidence(n, p),
                 our_finding=(f"{finding_prefix};n={n},rho={rho},p={p}——与规则预期{word},"
                              f"初步 {verdict}(弱效应,词表代理口径)。"))


def _check_engagement_anchor(rule: dict[str, Any], videos: list[dict[str, Any]], _ci: dict) -> dict[str, Any]:
    """funnel_stage3_engagement:4pct 互动率锚放到自有分布里看落点。"""
    ers = sorted(v["engagement_rate"] for v in videos)
    n = len(ers)
    if n < MIN_SAMPLE:
        return _item(rule, our_sample=n, verdict=VERDICT_INSUFFICIENT, confidence="low",
                     our_finding=f"可算互动率样本 {n} < {MIN_SAMPLE}。")
    anchor = 0.04
    p10, p25, median = _percentile(ers, 0.10), _percentile(ers, 0.25), _percentile(ers, 0.50)
    p75, p90 = _percentile(ers, 0.75), _percentile(ers, 0.90)
    share_meeting = round(sum(1 for x in ers if x >= anchor) / n, 3)
    dist = (f"自有分布 median={median:.4f}, p25={p25:.4f}, p75={p75:.4f}, p90={p90:.4f};"
            f"达标(>=0.04)占比 {share_meeting}")
    if p25 is not None and p75 is not None and p25 <= anchor <= p75:
        return _item(rule, our_sample=n, verdict=VERDICT_SUPPORTED, confidence="medium",
                     our_finding=(f"{dist}——锚落在自有分布 p25-p75 区间内,对本类目有区分度"
                                  "(约筛出上半段),作『观察线以上』门槛初步成立。"
                                  "互动率口径=(赞+评)/播放,公开代理非漏斗真第3段。"))
    direction = "过松(几乎全员达标)" if p10 is not None and anchor < p10 else "过严(自有内容几乎无人达标)"
    return _item(rule, our_sample=n, verdict=VERDICT_CONTRADICTED, confidence="low",
                 our_finding=f"{dist}——锚对本类目{direction},建议以自建基线(分位)替代外部锚。")


def _check_platform_absent(rule: dict[str, Any], videos: list[dict[str, Any]],
                           platform: str, metric_note: str) -> dict[str, Any]:
    n = sum(1 for v in videos if v["platform"] == platform)
    return _item(rule, our_sample=n, verdict=VERDICT_INSUFFICIENT, confidence="low",
                 our_finding=f"已析视频中 {platform} 样本 {n};且{metric_note}——不可检验。")


def _check_ig_utility(rule: dict[str, Any], videos: list[dict[str, Any]],
                      ci: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    ig = [v for v in videos if v["platform"] == "instagram"]
    n_utility = sum(1 for v in ig if v["tutorial_lexicon"])
    cross = ci.get(("tutorial_lexicon", "engagement_rate")) or {}
    cross_note = (f"跨平台参考:tutorial 词表×互动率 n={cross.get('n')}, rho={cross.get('spearman_rho')}, "
                  f"p={cross.get('p_value')}")
    if n_utility < MIN_SAMPLE:
        return _item(rule, our_sample=n_utility, verdict=VERDICT_INSUFFICIENT, confidence="low",
                     our_finding=(f"IG 已析样本 {len(ig)},其中实用教程型仅 {n_utility} 条 < {MIN_SAMPLE};"
                                  f"saves/sends 指标公开抓取不可得。{cross_note}(仅参考,不构成 IG 单平台结论)。"))
    xs = [float(v["tutorial_lexicon"]) for v in ig]
    ys = [float(v["engagement_rate"]) for v in ig]
    rho, p = _spearman(xs, ys)
    sub = {"n": len(ig), "spearman_rho": rho, "p_value": p}
    return _corr_verdict(rule, sub, finding_prefix=f"IG 子集教程型信号×互动率;{cross_note}")


def _check_not_testable(rule: dict[str, Any], reason: str) -> dict[str, Any]:
    return _item(rule, our_sample=0, verdict=VERDICT_INSUFFICIENT, confidence="low",
                 testable=False, our_finding=reason)


def _check_metric_unavailable(rule: dict[str, Any], videos: list[dict[str, Any]], missing: str) -> dict[str, Any]:
    return _item(rule, our_sample=0, verdict=VERDICT_INSUFFICIENT, confidence="low",
                 our_finding=(f"所需指标({missing})库内不可得——已析 {len(videos)} 条仅有公开面互动数;"
                              "待相应数据源接入后复验。"))


def _check_four_labels(rule: dict[str, Any], videos: list[dict[str, Any]], _ci: dict) -> dict[str, Any]:
    labels = {
        "awe": "emotion_awe_hits", "identity": "emotion_identity_hits",
        "transformation": "emotion_transformation_hits", "trust": "emotion_trust_hits",
    }
    parts: list[str] = []
    for name, key in labels.items():
        subset = sorted(v["engagement_rate"] for v in videos if v[key] > 0)
        med = _percentile(subset, 0.5)
        parts.append(f"{name}: n={len(subset)}, 互动率中位={med:.4f}" if med is not None else f"{name}: n=0")
    return _item(rule, our_sample=len(videos), verdict=VERDICT_INSUFFICIENT, confidence="low",
                 our_finding=("词表代理覆盖:" + ";".join(parts) +
                              "——词表只测『元素出现』,验不了『每条只主打一种』纪律;"
                              "待深析新维度(emotion_trigger)落库后复验。"))


def _build_checkers() -> dict[str, Callable[[dict[str, Any], list[dict[str, Any]], dict], dict[str, Any]]]:
    """rule_id → 检验器;未注册的规则走通用「指标不可得」。"""
    tiktok_missing = {
        "tt_completion_tier_viral": "完播率非公开指标",
        "tt_completion_tier_strong": "完播率非公开指标",
        "tt_completion_tier_average": "完播率非公开指标",
        "tt_completion_tier_weak": "完播率非公开指标",
        "tt_hook_2s_a_tier": "2 秒留存非公开指标",
        "tt_hook_2s_floor": "2 秒留存非公开指标",
        "tt_spark_creator_material_only": "Spark 投放数据未接入",
        "tt_us_creator_video_share": "美区 GMV 结构数据未接入",
    }
    checkers: dict[str, Callable[[dict[str, Any], list[dict[str, Any]], dict], dict[str, Any]]] = {}
    for rid, note in tiktok_missing.items():
        checkers[rid] = (lambda note_: lambda rule, videos, ci: _check_platform_absent(
            rule, videos, "tiktok", note_))(note)

    for rid, missing in (
        ("commission_base_anchor", "佣金结构数据"),
        ("commission_ladder", "佣金阶梯与销量目标数据"),
        ("ig_sends_weight", "IG sends(私发分享)"),
        ("yt_double_gate", "YT CTR 与平均观看留存(需 Studio 接入;库内 YT 样本充足但指标缺)"),
        ("funnel_stage1_hook_rate", "3 秒观看/hook rate"),
        ("funnel_stage2_completion", "完播率"),
        ("funnel_stage4_link_ctr", "短链/挂车点击"),
        ("funnel_stage5_landing_stay", "落地页停留"),
        ("funnel_stage6_cvr", "下单转化(待 Shopify 归因)"),
        ("boost_trigger_organic_top25", "发布 48h 内快照序列"),
        ("boost_two_stage_budget", "投放预算与分段效果数据"),
    ):
        checkers[rid] = (lambda missing_: lambda rule, videos, ci: _check_metric_unavailable(
            rule, videos, missing_))(missing)

    checkers["stat_power_min_exposure"] = lambda rule, videos, ci: _check_not_testable(
        rule, "纪律条款非经验命题;本报告自身按更严样本闸执行(<30 一律 insufficient)。")
    checkers["stat_power_min_sample"] = lambda rule, videos, ci: _check_not_testable(
        rule, "纪律条款非经验命题;本报告全程执行同源样本闸,与 prediction_ledger 口径一致。")
    checkers["ethics_gate_no_negative_manipulation"] = lambda rule, videos, ci: _check_not_testable(
        rule, "政策底线(伦理闸)不做经验校准——有效性不构成使用许可,不因数据松动。")
    checkers["ethics_gate_disclosure"] = lambda rule, videos, ci: _check_not_testable(
        rule, "政策底线(FTC 合规)不做经验校准,不因数据松动。")

    checkers["funnel_stage3_engagement"] = _check_engagement_anchor
    checkers["emotion_two_axis"] = lambda rule, videos, ci: _corr_verdict(
        rule, ci.get(("emotion_awe_hits", "engagement_rate")) or {"n": 0},
        finding_prefix="高唤醒×正效价代理=awe 词表命中(标题+摘要+key_hook)×互动率")
    checkers["content_template_awe"] = lambda rule, videos, ci: _corr_verdict(
        rule, ci.get(("emotion_awe_hits", "engagement_rate")) or {"n": 0},
        finding_prefix="代理口径:awe 元素出现×互动率(『开场即样片』的开头位置库内不可观测,只验 awe 元素本身)")
    checkers["content_template_identity"] = lambda rule, videos, ci: _corr_verdict(
        rule, ci.get(("emotion_identity_hits", "engagement_rate")) or {"n": 0},
        finding_prefix="identity 词表命中×互动率(叙事框架整体不可观测,词表代理)")
    checkers["content_template_before_after"] = lambda rule, videos, ci: _corr_verdict(
        rule, ci.get(("title_hook_comparison", "engagement_rate")) or {"n": 0},
        finding_prefix="标题对比结构(vs/对比)×互动率(同机位同光线真伪库内不可验)")
    checkers["emotion_gear_four_labels"] = _check_four_labels
    checkers["ig_saveable_utility"] = _check_ig_utility
    return checkers


def _calibration(videos: list[dict[str, Any]], corr: dict[str, Any]) -> dict[str, Any]:
    from app.domains.market_brain import growth_playbook

    playbook = growth_playbook.rules()
    checkers = _build_checkers()
    ci = _corr_index(corr)
    items: list[dict[str, Any]] = []
    for rule in playbook.get("rules", []):
        rid = str(rule.get("rule_id"))
        checker = checkers.get(rid)
        if checker is None:
            items.append(_item(rule, our_sample=0, verdict=VERDICT_INSUFFICIENT, confidence="low",
                               our_finding="本版无对应检验器——诚实未检验。"))
            continue
        try:
            items.append(checker(rule, videos, ci))
        except Exception as exc:  # noqa: BLE001 — 单条检验失败不炸整报告
            logger.warning("rule_validation checker failed rule_id=%s: %s", rid, exc)
            items.append(_item(rule, our_sample=0, verdict=VERDICT_INSUFFICIENT, confidence="low",
                               our_finding=f"检验器异常:{_text(str(exc), 160)}"))
    tally = {VERDICT_SUPPORTED: 0, VERDICT_CONTRADICTED: 0, VERDICT_INSUFFICIENT: 0}
    for it in items:
        tally[it["verdict"]] = tally.get(it["verdict"], 0) + 1
    decided = [
        {k: it[k] for k in ("rule_id", "verdict", "confidence", "our_sample")}
        for it in items if it["verdict"] != VERDICT_INSUFFICIENT
    ]
    return {
        "playbook_method": playbook.get("method"),
        "rules_total": len(items),
        "verdicts": tally,
        "decided": decided,
        "items": items,
        "consumer_note": (
            "结构 {rule_id, our_sample, our_finding, verdict, confidence} 供规则库 overridable "
            "机制消费:本报告只出证据,不改规则库、不改权重;insufficient 占多数是诚实态"
            "(多数规则所需指标尚未接入)。"
        ),
    }


# ── 主入口(纯读) ────────────────────────────────────────────────────


def validate_rules() -> dict[str, Any]:
    """规则库校准报告:①信号×结果 Spearman 侦察 ②逐条 playbook verdict ③消费结构。

    全只读、零 LLM、零重析、零采集;决定性(同数据双跑一致,generated_at 除外)。
    """
    from app.db.connection import table_exists

    now = datetime.now(timezone.utc).isoformat()
    base = {"status": "empty", "method": METHOD, "generated_at": now}
    for table in (DEEP_TABLE, EVIDENCE_TABLE):
        try:
            if not table_exists(table):
                return {**base, "reason": f"{table} 未建——已析视频数据落库后本报告自动出数。"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("rule_validation table_exists failed: %s", exc)
            return {**base, "status": "error", "reason": _text(str(exc), 300)}

    try:
        videos = _collect_videos()
    except Exception as exc:  # noqa: BLE001 — 读库失败诚实回原因,不 500
        logger.warning("rule_validation load failed: %s", exc)
        return {**base, "status": "error", "reason": _text(str(exc), 300)}

    if not videos:
        return {**base, "reason": "无可用已析视频(analysis_kind=video_final_v1 且播放量>0)。"}

    by_platform: dict[str, int] = {}
    for v in videos:
        key = v["platform"] or "(unknown)"
        by_platform[key] = by_platform.get(key, 0) + 1
    ers = sorted(v["engagement_rate"] for v in videos)
    corr = _correlations(videos)
    calibration = _calibration(videos, corr)

    return {
        "status": "ready",
        "method": METHOD,
        "generated_at": now,
        "dataset": {
            "analysis_kind": ANALYSIS_KIND,
            "usable_videos": len(videos),
            "by_platform": dict(sorted(by_platform.items())),
            "with_duration": sum(1 for v in videos if v["duration_seconds"] is not None),
            "with_publish_hour": sum(1 for v in videos if v["publish_hour_utc"] is not None),
            "engagement_rate": {
                "median": round(_percentile(ers, 0.5) or 0, 5),
                "p25": round(_percentile(ers, 0.25) or 0, 5),
                "p75": round(_percentile(ers, 0.75) or 0, 5),
                "p90": round(_percentile(ers, 0.90) or 0, 5),
            },
            "note": "互动率=(赞+评)/播放;share_count 库内全 0 不计;跨账号体量未归一(秩相关口径可用)。",
        },
        "correlations": corr,
        "calibration": calibration,
        "note": (
            "零成本纪律:信号=词表/规则法对已析文本回打,零 LLM 零视频重析;"
            "verdict 是『初步证据』不是新真理——规则库更新仍走 overridable 机制人审。"
        ),
    }


__all__ = ["validate_rules", "MIN_SAMPLE", "SIG_P", "METHOD",
           "VERDICT_SUPPORTED", "VERDICT_CONTRADICTED", "VERDICT_INSUFFICIENT"]
