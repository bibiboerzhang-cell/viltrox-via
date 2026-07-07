"""增长打法规则库(Growth Playbook)——平台数字入册为数据,非系统真理(GTM-1 W2)。

蓝图 v0.2 第六章裁决:平台研究的全部数字一律降格为规则库条目,每条带
basis / source / confidence / sample_size,可被自有数据推翻更新(overridable=true),
绝不硬编码为系统宪法。镜头类目公开基准缺失的条目诚实标注「自建基线待校准」。

红线:纯数据模块,零 DB、零 LLM、零采集、零迁移;不含 viltrox_fit_score、
不碰 rule_v0;数字仅作锚点参考,学习账本(GTM-4)用自有数据持续校准。

读接口:
  rules(platform=None)  → 全量(或按平台过滤)规则清单
  rule(rule_id)         → 单条;不存在抛 LookupError(路由转 404)
"""
from __future__ import annotations

from typing import Any

PLAYBOOK_METHOD = "growth_playbook_v0"
PLAYBOOK_UPDATED_AT = "2026-07-07"

# 来源口径(诚实分级;external = 行业公开研究/平台运营经验,未经自有数据回测)
_SRC_EXTERNAL = "external_research_2025(行业公开研究/平台运营打法汇编,未经自有数据回测)"
_SRC_INTERNAL = "internal_convention(与库内既有统计口径同源)"
_SRC_POLICY = "policy(平台规则/FTC 合规与品牌伦理裁决,非经验数字)"

# 每条规则统一十字段:rule_id/statement/threshold/platform/basis/source/confidence/sample_size/updated_at/overridable
_RULES: tuple[dict[str, Any], ...] = (
    # ── TikTok 完播四档 ─────────────────────────────────────────────
    {
        "rule_id": "tt_completion_tier_viral",
        "statement": "TikTok 完播率 ≥70% 视作病毒级门槛:算法大概率追加分发,该素材优先进放大候选。",
        "threshold": {"metric": "completion_rate", "op": ">=", "value": 0.70, "tier": "viral"},
        "platform": "tiktok",
        "basis": "平台运营圈广泛引用的完播分层经验值;镜头类目无公开基准,需自建基线校准。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "tt_completion_tier_strong",
        "statement": "TikTok 完播率 50%-70% 为强档:表现显著优于均值,可小额验证放大。",
        "threshold": {"metric": "completion_rate", "op": "between", "value": [0.50, 0.70], "tier": "strong"},
        "platform": "tiktok",
        "basis": "完播四档分层第二档;档位边界为行业经验值,非平台官方口径。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "tt_completion_tier_average",
        "statement": "TikTok 完播率 30%-50% 为及格档:维持观察,不投放大预算。",
        "threshold": {"metric": "completion_rate", "op": "between", "value": [0.30, 0.50], "tier": "average"},
        "platform": "tiktok",
        "basis": "完播四档分层第三档;及格线以上仅代表「不淘汰」,不构成加码依据。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "tt_completion_tier_weak",
        "statement": "TikTok 完播率 <30% 为弱档:不进 boost 候选;先复盘开头与节奏再产下一版。",
        "threshold": {"metric": "completion_rate", "op": "<", "value": 0.30, "tier": "weak"},
        "platform": "tiktok",
        "basis": "完播四档分层末档;弱档投放大预算=为坏素材买流量。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    # ── 2 秒 hook 档 ────────────────────────────────────────────────
    {
        "rule_id": "tt_hook_2s_a_tier",
        "statement": "前 2 秒留存 ≥40% 记为 A 档 hook:开头合格,素材可进入放大评估。",
        "threshold": {"metric": "retention_2s", "op": ">=", "value": 0.40, "tier": "A"},
        "platform": "tiktok",
        "basis": "短视频 hook 分层经验值(2 秒留存=开头钩子有效性代理指标)。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "tt_hook_2s_floor",
        "statement": "前 2 秒留存 <30% 判 hook 不合格:先换开头(样片惊叹/问题钩子)再谈任何投放。",
        "threshold": {"metric": "retention_2s", "op": "<", "value": 0.30, "tier": "fail"},
        "platform": "tiktok",
        "basis": "hook 不合格时后续漏斗全部失真,投放数据不可判读。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    # ── Spark / 达人素材 ────────────────────────────────────────────
    {
        "rule_id": "tt_spark_creator_material_only",
        "statement": "Spark Ads 只投达人原生素材,禁自产素材直投:外部研究口径达人素材转化基准约 +43%。",
        "threshold": {"metric": "spark_material_source", "op": "==", "value": "creator_native", "reference_lift": 0.43},
        "platform": "tiktok",
        "basis": "达人原生素材带真实语境与信任背书;+43% 为外部研究引用值,未经自有投放回测。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "tt_us_creator_video_share",
        "statement": "美区 TikTok Shop 约 2/3 销量来自达人短视频挂车:预算优先达人短视频挂车,而非自播或纯官方投流。",
        "threshold": {"metric": "us_shop_gmv_share_creator_video", "op": "~=", "value": 0.67},
        "platform": "tiktok",
        "basis": "美区电商结构外部研究口径;用于预算分配方向判断,非精确预测。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    # ── 佣金锚 ──────────────────────────────────────────────────────
    {
        "rule_id": "commission_base_anchor",
        "statement": "联盟/挂车基础佣金锚 10%-15%:低于 10% 难吸引腰部达人,高于 15% 需绑定明确交付。",
        "threshold": {"metric": "commission_rate", "op": "between", "value": [0.10, 0.15]},
        "platform": "commerce",
        "basis": "消费电子类目联盟佣金常见区间;镜头高客单可承受区间上限。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "commission_ladder",
        "statement": "佣金阶梯激励:超销量目标追加约 +5%(或样机+现金混合),优先绑定已验证的表现者而非广撒。",
        "threshold": {"metric": "commission_bonus_rate", "op": "~=", "value": 0.05, "trigger": "超既定销量目标"},
        "platform": "commerce",
        "basis": "阶梯激励让佣金花在已被数据验证的达人身上,边际效率高于均摊。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    # ── IG sends 权重 ──────────────────────────────────────────────
    {
        "rule_id": "ig_sends_weight",
        "statement": "IG 分发最强信号是 sends(私发分享):非粉触达权重估 3-5×;内容按「值得转发给朋友」设计。",
        "threshold": {"metric": "sends_reach_multiplier", "op": "between", "value": [3, 5]},
        "platform": "instagram",
        "basis": "IG 官方多次公开确认 sends 是核心分发信号;3-5× 为运营圈估算值,非官方数字。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "ig_saveable_utility",
        "statement": "IG Reels 器材内容优先「可保存实用型」(参数设置/构图教程):saves+sends 双信号内容分发上限更高。",
        "threshold": {"metric": "content_format", "op": "in", "value": ["tutorial", "settings", "how_to"]},
        "platform": "instagram",
        "basis": "实用型内容天然触发保存与转发;与 sends 权重规则同一分发逻辑的内容侧应用。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    # ── YouTube 双门槛 ─────────────────────────────────────────────
    {
        "rule_id": "yt_double_gate",
        "statement": "YouTube 素材判「可放大/可引用为爆款证据」须双门槛同时满足:CTR ≥4% 且平均观看留存 ≥40%。",
        "threshold": {"metric": ["ctr", "avg_view_retention"], "op": ">=", "value": [0.04, 0.40], "logic": "AND"},
        "platform": "youtube",
        "basis": "单看 CTR 会奖励标题党、单看留存会奖励小众深水内容;双门槛防单指标欺骗。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    # ── 六段漏斗锚(曝光→转化,全平台口径)─────────────────────────
    {
        "rule_id": "funnel_stage1_hook_rate",
        "statement": "漏斗段1 曝光→3秒观看(hook rate)锚 ≥40%:低于锚先修开头,不看后段数据。",
        "threshold": {"metric": "hook_rate", "op": ">=", "value": 0.40, "stage": 1},
        "platform": "all",
        "basis": "六段漏斗第一闸;与 2s hook A 档同源口径。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "funnel_stage2_completion",
        "statement": "漏斗段2 观看→完播锚 ≥30%(短视频口径):完播是算法续量与心智留存的双重代理。",
        "threshold": {"metric": "completion_rate", "op": ">=", "value": 0.30, "stage": 2},
        "platform": "all",
        "basis": "六段漏斗第二闸;取完播四档及格线为锚。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "funnel_stage3_engagement",
        "statement": "漏斗段3 完播→互动(赞评藏分享合计互动率)锚 ≥4%:互动是购买意向的最早外显。",
        "threshold": {"metric": "engagement_rate", "op": ">=", "value": 0.04, "stage": 3},
        "platform": "all",
        "basis": "器材类内容互动率经验锚;库内既有内容 avg_engagement_rate 可作自建基线替换。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "funnel_stage4_link_ctr",
        "statement": "漏斗段4 互动→短链/挂车点击 CTR 锚 ≥1%:低于锚说明内容种草了但没给出行动出口。",
        "threshold": {"metric": "link_ctr", "op": ">=", "value": 0.01, "stage": 4},
        "platform": "all",
        "basis": "内容电商短链点击经验锚;viltroxvia.com 短链数据积累后自建基线。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "funnel_stage5_landing_stay",
        "statement": "漏斗段5 点击→落地页有效停留(非跳出)锚 ≥40%:低于锚先查落地页与内容的货不对板。",
        "threshold": {"metric": "landing_stay_rate", "op": ">=", "value": 0.40, "stage": 5},
        "platform": "all",
        "basis": "落地承接质量闸;跳出高=流量浪费在页面而非内容。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "funnel_stage6_cvr",
        "statement": "漏斗段6 落地→下单转化 CVR 锚 ≥2%(高客单镜头类目):公开基准缺失,以自有订单数据建基线为准。",
        "threshold": {"metric": "cvr", "op": ">=", "value": 0.02, "stage": 6},
        "platform": "all",
        "basis": "$300-600 客单区间电商 CVR 经验锚;镜头类目无公开基准,此锚仅占位待 Shopify 数据校准。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    # ── boost 触发 ─────────────────────────────────────────────────
    {
        "rule_id": "boost_trigger_organic_top25",
        "statement": "boost 触发:发布 48h 内自然表现进入该账号近 30 天前 25% → 触发 Spark/联盟加码评估(仍需人审)。",
        "threshold": {"metric": "organic_percentile_48h", "op": ">=", "value": 0.75, "window_days": 30},
        "platform": "all",
        "basis": "用账号自身分布做相对判断,规避跨账号体量不可比;与条件化预判的「触发加码」模板同构。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "boost_two_stage_budget",
        "statement": "两段式放大:先 $50-100/条 小额验证,达标(双门槛/完播档)再上主预算;绝不首轮全押。",
        "threshold": {"metric": "stage1_budget_usd", "op": "between", "value": [50, 100]},
        "platform": "all",
        "basis": "小额验证把预算错配风险限制在可弃损失内;是预算三档模板的执行层配套。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    # ── 统计功效闸 ─────────────────────────────────────────────────
    {
        "rule_id": "stat_power_min_exposure",
        "statement": "统计功效闸①:每素材变体 ≥1000 曝光才允许判定优劣;不足样本禁下结论、禁淘汰素材。",
        "threshold": {"metric": "impressions_per_variant", "op": ">=", "value": 1000},
        "platform": "all",
        "basis": "小样本下 CTR/完播差异大概率是噪声;先够曝光再谈 A/B 结论。",
        "source": _SRC_EXTERNAL,
        "confidence": "medium",
        "sample_size": None,
    },
    {
        "rule_id": "stat_power_min_sample",
        "statement": "统计功效闸②:任何动作组裁决样本 <5 一律 insufficient,不得据此升级规则、驾照或改推荐口径。",
        "threshold": {"metric": "judged_sample_count", "op": ">=", "value": 5},
        "platform": "all",
        "basis": "与 prediction_ledger 的 MIN_SAMPLE_FOR_CONFIDENCE 同口径;规则库自身的更新也受本闸约束。",
        "source": _SRC_INTERNAL,
        "confidence": "medium",
        "sample_size": None,
    },
    # ── 情绪两轴 + 器材四情绪 + 三内容模板 ─────────────────────────
    {
        "rule_id": "emotion_two_axis",
        "statement": "情绪两轴(唤醒度×效价):高唤醒×正效价(惊叹/向往)最利分享;高唤醒×负效价(愤怒/焦虑)易传播但伤品牌且触伦理闸。",
        "threshold": {"metric": "emotion_quadrant", "op": "==", "value": "high_arousal_positive"},
        "platform": "all",
        "basis": "情绪传播研究的两轴框架;负效价高唤醒的「有效性」不构成使用许可(见伦理闸)。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "emotion_gear_four_labels",
        "statement": "器材内容四情绪标签:awe(画面惊叹)/identity(身份认同)/transformation(before-after 转变)/trust(专业信任)——每条内容明确主打一种,不混炖。",
        "threshold": {"metric": "primary_emotion", "op": "in", "value": ["awe", "identity", "transformation", "trust"]},
        "platform": "all",
        "basis": "摄影器材购买动机的四情绪归纳;单条内容多情绪混炖会稀释钩子。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "content_template_awe",
        "statement": "内容模板 awe:开场直接上最惊艳样片(成片先行),2 秒内制造「这画面怎么拍的」惊叹,再回讲器材。",
        "threshold": {"metric": "opening_type", "op": "==", "value": "best_shot_first"},
        "platform": "all",
        "basis": "样片惊叹是器材内容最强 hook 来源之一;与 2s hook 档配套使用。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "content_template_identity",
        "statement": "内容模板 identity(身份认同):「用它的人是谁」叙事——把镜头绑定目标 persona 的创作身份与场景,而非参数表。",
        "threshold": {"metric": "narrative_frame", "op": "==", "value": "who_uses_it"},
        "platform": "all",
        "basis": "身份认同驱动的器材内容转化路径更短(观众自我代入即种草);persona 数据来自产品画像表。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    {
        "rule_id": "content_template_before_after",
        "statement": "内容模板 before-after:换镜前后同场景对照,转变即卖点;对照必须真实同机位同光线,禁摆拍造假对比。",
        "threshold": {"metric": "structure", "op": "==", "value": "before_after_contrast"},
        "platform": "all",
        "basis": "transformation 情绪的结构化表达;真实对照是可信度底线(造假对比触伦理闸)。",
        "source": _SRC_EXTERNAL,
        "confidence": "low",
        "sample_size": None,
    },
    # ── 伦理闸 ─────────────────────────────────────────────────────
    {
        "rule_id": "ethics_gate_no_negative_manipulation",
        "statement": "伦理闸①:禁恐惧/羞耻/焦虑等负面情绪操纵营销(「不买你就不专业」类话术零容忍);命中即下架素材、不得放大。",
        "threshold": {"metric": "negative_manipulation", "op": "==", "value": False},
        "platform": "all",
        "basis": "蓝图第八章边界裁决:不做黑帕特情绪操纵;此规则不可被数据推翻(overridable 仅指措辞,底线不变)。",
        "source": _SRC_POLICY,
        "confidence": "high",
        "sample_size": None,
    },
    {
        "rule_id": "ethics_gate_disclosure",
        "statement": "伦理闸②:合作/送样内容必须显著披露(FTC #ad 或平台原生标记);未披露内容不得进 Spark/联盟放大池。",
        "threshold": {"metric": "sponsored_disclosure", "op": "==", "value": True},
        "platform": "all",
        "basis": "FTC 合规与平台广告政策;放大未披露内容=平台处罚与品牌信任双重风险。",
        "source": _SRC_POLICY,
        "confidence": "high",
        "sample_size": None,
    },
)


def _finalize(raw: dict[str, Any]) -> dict[str, Any]:
    """补齐统一字段:updated_at / overridable;返回浅拷贝,调用方改不动册子本体。"""
    item = dict(raw)
    item.setdefault("updated_at", PLAYBOOK_UPDATED_AT)
    item.setdefault("overridable", True)
    return item


def rules(platform: str | None = None) -> dict[str, Any]:
    """全量规则清单(可按 platform 过滤:tiktok/instagram/youtube/commerce/all)。

    纯内存数据读,零 DB 零 LLM;每条自带诚实的 source/confidence/sample_size,
    external 条目未经自有数据回测,仅作锚点,学习账本(GTM-4)负责持续校准。
    """
    wanted = str(platform or "").strip().lower()
    items = [_finalize(r) for r in _RULES]
    if wanted:
        items = [r for r in items if str(r.get("platform")) == wanted or str(r.get("platform")) == "all"]
    platforms: dict[str, int] = {}
    for r in items:
        key = str(r.get("platform"))
        platforms[key] = platforms.get(key, 0) + 1
    return {
        "status": "ok",
        "method": PLAYBOOK_METHOD,
        "count": len(items),
        "filter": {"platform": wanted or None},
        "by_platform": platforms,
        "rules": items,
        "updated_at": PLAYBOOK_UPDATED_AT,
        "note": (
            "规则库=数据非真理:external 条目为行业公开研究/运营经验汇编,未经自有数据回测"
            "(confidence=low,sample_size=null 如实标注);全部 overridable,由学习账本"
            "(GTM-4)用自有 outcome 数据持续校准更新;伦理闸两条为政策底线,不因数据松动。"
        ),
    }


def rule(rule_id: str) -> dict[str, Any]:
    """按 rule_id 取单条;不存在抛 LookupError(路由层转 404)。"""
    wanted = str(rule_id or "").strip()
    for raw in _RULES:
        if str(raw.get("rule_id")) == wanted:
            return _finalize(raw)
    raise LookupError(f"playbook rule not found: {wanted}")
