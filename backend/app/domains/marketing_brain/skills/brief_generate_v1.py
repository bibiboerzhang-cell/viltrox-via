"""Skill · brief_generate_v1 —— 给某个 KOL × 产品出一份可编辑的合作 brief(thin wrapper)。

形式化:本文件是 thin wrapper,不重写算法。素材来自既有服务/数据:
  - KOL 档案:复用 projects.outreach._load_creators / _creator_label(读 vkpi_kol_pool,
    拿 platform/handle/display_name/primary_topic/followers),不另起查询。
  - SOW/deliverables 基线:复用 projects.outreach._template_sow 的 deliverables 模板(确定性、
    不承诺价格),作为 brief.deliverables 的兜底。
  - hook / talking_points / do / dont 的文案:LLM 步骤经可注入 model_fn(input_dict)->dict;
    默认 model_fn=None 时【不真烧 LLM】,走确定性模板规则(对齐被墙环境 + 离线可测)。

run(input, *, model_fn=None, record=True):
  ① 校验/取 input(kol_pool_id 必填;product 必填;angle 可选)
  ② 调既有服务取素材(KOL 档案 + SOW deliverables 基线)
  ③ 形状化成 OUTPUT_SCHEMA:{brief:{hook, talking_points[], do[], dont[], deliverables[]}, editable:true}
  ④ record=True → skill_registry.record_skill_run 落一行(best-effort,缺表/异常不拖垮)
  ⑤ return

红线:本 skill 纯生成草案 + 落运行账本,绝不触 viltrox_fit_score、绝不写业务表、绝不承诺价格、
不真烧 LLM(model_fn 默认 None)。brief 永远 editable=true,供人审后改。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from app.core.logging import get_logger
from app.domains.marketing_brain import skill_registry
from app.domains.projects import outreach as _outreach

logger = get_logger(__name__)

SKILL_NAME = "brief_generate"
SKILL_VERSION = "v1"

# 可注入 LLM 步骤签名:input_dict(含 kol/product/angle 素材)-> dict(可含 hook/talking_points/do/dont)
ModelFn = Callable[[dict[str, Any]], dict[str, Any]]

INPUT_SCHEMA: dict[str, Any] = {
    "kol_pool_id": "int (required) — vkpi_kol_pool.id,定位要合作的 KOL",
    "product": "dict|str (required) — 产品信息;dict 取 product_name/product_sku,str 直接当名",
    "angle": "str (optional) — 内容切角/卖点提示,影响 hook 与 talking_points",
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "brief": {
        "hook": "str — 开场钩子(一句话)",
        "talking_points": "list[str] — 内容要点",
        "do": "list[str] — 建议做的事",
        "dont": "list[str] — 避免做的事",
        "deliverables": "list[str] — 交付物清单(沿用 SOW 基线,确定性)",
    },
    "editable": "bool — 恒为 True,供人审后修改",
}


def _as_str(value: Any) -> str:
    return str(value or "").strip()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_product(product: Any) -> dict[str, Any]:
    """product 容忍 dict / str:统一成 {product_name, product_sku}。"""
    if isinstance(product, dict):
        name = _as_str(product.get("product_name") or product.get("name"))
        sku = _as_str(product.get("product_sku") or product.get("sku"))
        return {"product_name": name, "product_sku": sku}
    return {"product_name": _as_str(product), "product_sku": ""}


def _load_kol_profile(kol_pool_id: int) -> dict[str, Any]:
    """复用 outreach._load_creators 读 KOL 档案(不另起查询)。缺则返回 {}。"""
    try:
        creators, _missing = _outreach._load_creators([kol_pool_id])
    except Exception:  # 活 DB 不可用/缺表 → 离线兜底为空档案(仍能出模板 brief)
        logger.debug("brief_generate_v1: _load_creators failed, using empty profile", exc_info=True)
        return {}
    return dict(creators[0]) if creators else {}


def _str_list(value: Any, limit: int = 12) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = _as_str(item)
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _template_brief(kol: dict[str, Any], product: dict[str, Any], angle: str) -> dict[str, Any]:
    """确定性模板(model_fn=None 时走这里,不真烧 LLM)。

    deliverables 复用 outreach._template_sow 的基线清单,保证与 SOW 草案一致、不承诺价格。
    """
    label = _outreach._creator_label(kol) if kol else "this creator"
    platform = _as_str(kol.get("platform")) or "their channel"
    topic = _as_str(kol.get("primary_topic"))
    product_name = product.get("product_name") or "Viltrox imaging gear"
    angle_clean = _as_str(angle)

    hook = (
        f"Show your {platform} audience how {product_name} fits real "
        f"{topic or 'creative'} work"
    )
    if angle_clean:
        hook = f"{hook} — angle: {angle_clean}"

    talking_points = [
        f"Why {product_name} suits {topic or 'your content'} and {label}'s style",
        "One concrete shot/scenario where it shines (hands-on, not spec-reading)",
        "Honest pros and any trade-offs — credibility over hype",
    ]
    if angle_clean:
        talking_points.insert(0, f"Lead with the angle: {angle_clean}")

    do = [
        "Use the product in your own real workflow before filming",
        "Disclose the partnership clearly (sponsored / gifted)",
        f"Tag Viltrox and reference {platform} community where natural",
    ]
    dont = [
        "Don't fabricate stats or read the spec sheet verbatim",
        "Don't over-script — keep your authentic voice",
        "Don't promise pricing/availability — leave commercials to Viltrox",
    ]
    # SOW 基线 deliverables(确定性,不承诺价格)。
    sow = _outreach._template_sow(product_name, topic or "target creator", 1)
    deliverables = _str_list(sow.get("deliverables")) or [
        "1× dedicated review/feature video",
        "1× social post with product tag",
    ]
    return {
        "hook": hook,
        "talking_points": talking_points,
        "do": do,
        "dont": dont,
        "deliverables": deliverables,
    }


def _shape_brief(raw: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    """把任意来源(model_fn 或模板)收敛到 OUTPUT_SCHEMA 的 brief 形状;缺字段用 fallback 补。"""
    raw = raw if isinstance(raw, dict) else {}
    hook = _as_str(raw.get("hook")) or _as_str(fallback.get("hook"))
    talking_points = _str_list(raw.get("talking_points")) or _str_list(fallback.get("talking_points"))
    do = _str_list(raw.get("do")) or _str_list(fallback.get("do"))
    dont = _str_list(raw.get("dont")) or _str_list(fallback.get("dont"))
    deliverables = _str_list(raw.get("deliverables")) or _str_list(fallback.get("deliverables"))
    return {
        "hook": hook,
        "talking_points": talking_points,
        "do": do,
        "dont": dont,
        "deliverables": deliverables,
    }


def run(
    input: dict[str, Any],
    *,
    model_fn: Optional[ModelFn] = None,
    record: bool = True,
) -> dict[str, Any]:
    """生成一份可编辑的合作 brief。

    model_fn=None(默认)→ 走确定性模板,不真烧 LLM;
    model_fn 提供 → 用它产出 hook/talking_points/do/dont,缺字段回落模板,deliverables 永远走 SOW 基线。
    record=True → record_skill_run 落账(best-effort)。
    """
    t0 = time.monotonic()
    data = input if isinstance(input, dict) else {}
    kol_pool_id = _int_or_none(data.get("kol_pool_id"))
    if not kol_pool_id:
        result = {
            "ok": False,
            "reason": "kol_pool_id required",
            "brief": {"hook": "", "talking_points": [], "do": [], "dont": [], "deliverables": []},
            "editable": True,
        }
        return result

    product = _normalize_product(data.get("product"))
    if not product["product_name"]:
        return {
            "ok": False,
            "reason": "product required",
            "brief": {"hook": "", "talking_points": [], "do": [], "dont": [], "deliverables": []},
            "editable": True,
        }
    angle = _as_str(data.get("angle"))

    # ② 取素材(既有服务):KOL 档案。
    kol = _load_kol_profile(kol_pool_id)

    # 模板兜底永远算出来(既是 model_fn=None 的结果,也是 model_fn 缺字段的回落)。
    template = _template_brief(kol, product, angle)

    model_used = "rule_v0"
    raw_brief: dict[str, Any] = template
    if model_fn is not None:
        material = {
            "kol_pool_id": kol_pool_id,
            "kol": kol,
            "product": product,
            "angle": angle,
            "template": template,
        }
        try:
            produced = model_fn(material)
            if isinstance(produced, dict) and produced:
                raw_brief = produced
                model_used = _as_str(produced.get("_model")) or "model_fn"
        except Exception:  # 注入模型抛错 → 诚实回落模板,不拖垮 skill
            logger.warning("brief_generate_v1: model_fn raised, falling back to template", exc_info=True)
            raw_brief = template
            model_used = "rule_v0_fallback"

    # ③ 形状化成 OUTPUT_SCHEMA;deliverables 始终来自 SOW 基线(确定性),不让模型乱发承诺。
    brief = _shape_brief(raw_brief, template)
    brief["deliverables"] = template["deliverables"]

    result: dict[str, Any] = {
        "ok": True,
        "brief": brief,
        "editable": True,
        "model_used": model_used,
        "kol_pool_id": kol_pool_id,
        "note": "草案仅供人审后编辑;不自动外发、不承诺价格、零触 viltrox_fit_score。",
    }

    # ④ 落运行账本(best-effort)。
    if record:
        latency_ms = int((time.monotonic() - t0) * 1000)
        try:
            skill_registry.record_skill_run(
                skill_name=SKILL_NAME,
                skill_version=SKILL_VERSION,
                input_schema={
                    "kol_pool_id": kol_pool_id,
                    "product": product,
                    "angle": angle,
                },
                model_used=model_used,
                retrieved_context={"kol_resolved": bool(kol)},
                output=result,
                cost_cents=0,  # 默认走模板/可注入函数,不真烧 LLM
                latency_ms=latency_ms,
            )
        except Exception:  # 落账失败绝不影响主返回
            logger.debug("brief_generate_v1: record_skill_run failed (best-effort)", exc_info=True)

    return result


# ---------------------------------------------------------------------------
# EVAL_CASES —— 用 marketing_brain.evals.run_eval 可评。
# skill_fn 包装:input -> brief 的字段键集合(评结构完整性,不评活 DB)。
# 评测时 record=False(纯离线),model_fn=None(不真烧 LLM)。
# ---------------------------------------------------------------------------
from app.domains.marketing_brain.evals import EvalCase  # noqa: E402


def _eval_skill_fn(case_input: dict[str, Any]) -> set[str]:
    """评测用:跑 run,回 brief 的非空字段名集合(结构完整 = 全 5 字段都有值)。"""
    out = run(case_input, model_fn=None, record=False)
    brief = out.get("brief") or {}
    present: set[str] = set()
    for key in ("hook", "talking_points", "do", "dont", "deliverables"):
        val = brief.get(key)
        if val:  # 非空字符串 / 非空 list
            present.add(key)
    return present


def _brief_completeness_metric(expected: Any, actual: Any) -> tuple[bool, float]:
    """期望字段集 ⊆ 实际字段集即 hit;score = 覆盖率。"""
    exp = set(expected or [])
    act = set(actual or [])
    if not exp:
        return True, 1.0
    covered = len(exp & act) / len(exp)
    return exp.issubset(act), covered


_FULL = {"hook", "talking_points", "do", "dont", "deliverables"}

EVAL_CASES: list[EvalCase] = [
    EvalCase(
        name="lens_with_angle",
        input={"kol_pool_id": 1, "product": {"product_name": "Viltrox AF 85mm"}, "angle": "low-light portraits"},
        expected=_FULL,
        metric=_brief_completeness_metric,
    ),
    EvalCase(
        name="product_as_string",
        input={"kol_pool_id": 2, "product": "Viltrox AF 27mm"},
        expected=_FULL,
        metric=_brief_completeness_metric,
    ),
    EvalCase(
        name="no_angle_still_complete",
        input={"kol_pool_id": 3, "product": {"product_name": "Viltrox AF 35mm", "product_sku": "VTX-35"}},
        expected=_FULL,
        metric=_brief_completeness_metric,
    ),
    EvalCase(
        name="deliverables_always_present",
        input={"kol_pool_id": 4, "product": "Viltrox drone gimbal", "angle": "outdoor vlog"},
        expected={"deliverables", "hook"},
        metric=_brief_completeness_metric,
    ),
]


def evaluate(*, suite: str = "brief_generate_v1") -> dict[str, Any]:
    """跑 EVAL_CASES,返回 EvalReport.to_dict()。

    brief 走确定性模板(model_fn=None);_load_kol_profile 缺活 DB 时离线兜底为空档案,仍出完整模板 brief。
    故 hit_rate 诚实可复现(与活库内容无关)。零真 LLM、record=False、零触 viltrox_fit_score。
    """
    from app.domains.marketing_brain.evals import run_eval

    report = run_eval(_eval_skill_fn, EVAL_CASES, default_metric=_brief_completeness_metric, suite=suite)
    return report.to_dict()
