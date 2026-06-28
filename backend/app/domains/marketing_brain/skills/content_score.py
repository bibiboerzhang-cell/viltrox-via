"""Skill: content_score_v1 —— 内容视频打分的 thin wrapper。

形式化规范(对齐 skill 批次铁律):
  - 这是一个 **读取 + schema 化 + 落账本** 的薄封装,**绝不重跑视频分析、绝不真烧 LLM**。
  - 数据源:`vkpi_analysis_cache` 里已产出的 `video_analysis_final_v1`(俗称 final_v1)结果,
    经 `app.domains.analysis.cache_repo.get_analysis_cache_entry` 只读取出。
    这正是 `kol/final_v1_extract.py` 落 `llm_dimensions_11` 的同一份缓存,本 skill 只做读侧投影。
  - LLM 步骤(可选的自然语言总结增强)经可注入 `model_fn`;默认 `None` = 走规则总结,
    不触代理、不烧 token、不依赖外网。
  - record=True 时 best-effort 调 `marketing_brain.skill_registry.record_skill_run` 落一行账本。

输入二选一:
  - `analysis_cache_ref`: {"target_id": <evidence_id>, "derive_method"?: ...} 直接定位缓存行;
  - `video_url`: 仅作回退/标识用途(无 cache_ref 时记入输出,但无缓存则判 unavailable —— 本 skill
    不负责把 URL 跑成分析,那是 gemini_video 管线的职责)。

红线:零写 viltrox_fit_score;读它做展示都不涉及(本 skill 只读 analysis_cache 的 final_v1 维度)。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from app.domains.analysis.cache_repo import get_analysis_cache_entry
from app.domains.marketing_brain import skill_registry
from app.domains.marketing_brain.evals import EvalCase

SKILL_NAME = "content_score"
SKILL_VERSION = "v1"
PROMPT_VERSION = "content_score_v1_rules"

# final_v1 缓存的默认 derive_method(对齐 final_v1_extract.FINAL_DERIVE_METHOD)。
DEFAULT_DERIVE_METHOD = "video_analysis_final_v1"

# OUTPUT_SCHEMA.scores 里我们暴露的标准维度键 → final_v1 layer6.scores 里的候选源键。
# 读侧只做 key 映射,不重算分数。
_SCORE_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "quality": ("content_quality_score", "quality_score", "production_quality_score", "quality"),
    "brand_fit": ("brand_fit_score", "brand_alignment_score", "fit_score", "brand_fit"),
    "hook": ("hook_score", "hook_strength_score", "opening_hook_score", "hook"),
    "marketing_value": ("marketing_value_score", "marketing_potential_score", "marketing_value"),
    "authenticity": ("authenticity_score", "trust_score", "authenticity"),
    "audience_match": ("audience_match_score", "audience_fit_score", "audience_match"),
}

INPUT_SCHEMA: dict[str, Any] = {
    "video_url": "str?  (回退/标识;无 cache_ref 时记入输出但不据此跑分析)",
    "analysis_cache_ref": "dict?  {target_id:str|int, derive_method?:str}  (优先:定位 final_v1 缓存行)",
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "status": "str  ('ok' | 'unavailable' | 'error')",
    "scores": "dict[str,float]  {quality, brand_fit, hook, marketing_value, ...}(仅含缓存里存在的维度)",
    "summary": "str  内容总结(规则拼装,或 model_fn 增强)",
    "marketing_potential": "dict  {score:float|None, band:str, key_hook:str|None, verdict:str|None, risk_flags:list}",
    "source": "dict  {target_id, derive_method, model, video_url, cache_status}",
    "model_used": "str|None",
}


# --------------------------------------------------------------------------- #
# 读侧归一(镜像 final_v1_extract 的取数口径,但只读、不写、不重算)
# --------------------------------------------------------------------------- #
def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _score_number(value: Any) -> Optional[float]:
    """final_v1 的分数可能是裸数,或 {score, confidence, rationale}。只取数,clamp 到 [0,100]。"""
    if isinstance(value, dict):
        value = value.get("score")
    if value in (None, ""):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:  # NaN
        return None
    return max(0.0, min(100.0, num))


def _final_payload(result: Any) -> dict[str, Any]:
    """缓存 result 可能直接是 final_v1 内容,或被包在 {derive_method: {...}} 里。镜像 final_v1_extract。"""
    root = _as_dict(result)
    nested = _as_dict(root.get(DEFAULT_DERIVE_METHOD))
    if _as_dict(nested.get("layer1_visual_content")) or _as_dict(nested.get("layer6_flags_and_scores")):
        return nested
    return root


def _extract_scores(layer6: dict[str, Any]) -> dict[str, float]:
    """把 layer6.scores 投影成标准维度键。只暴露缓存里真实存在的维度。"""
    raw = _as_dict(layer6.get("scores"))
    out: dict[str, float] = {}
    for std_key, aliases in _SCORE_KEY_ALIASES.items():
        for alias in aliases:
            if alias in raw:
                num = _score_number(raw.get(alias))
                if num is not None:
                    out[std_key] = round(num, 3)
                break
    return out


def _band(score: Optional[float]) -> str:
    if score is None:
        return "unknown"
    if score >= 75:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def _rule_summary(*, payload: dict[str, Any], scores: dict[str, float], marketing_score: Optional[float]) -> str:
    """无 model_fn 时的规则总结:拼缓存里已有的文本字段,不烧 LLM。"""
    layer1 = _as_dict(payload.get("layer1_visual_content"))
    layer6 = _as_dict(payload.get("layer6_flags_and_scores"))
    parts: list[str] = []
    content_summary = str(layer1.get("content_summary") or "").strip()
    if content_summary:
        parts.append(content_summary[:600])
    hook = str(layer6.get("key_hook") or "").strip()
    if hook:
        parts.append(f"Hook: {hook[:200]}")
    verdict = str(layer6.get("final_verdict") or "").strip()
    if verdict:
        parts.append(f"Verdict: {verdict[:200]}")
    if marketing_score is not None:
        parts.append(f"Marketing value {marketing_score:.0f}/100 ({_band(marketing_score)}).")
    if scores:
        dims = ", ".join(f"{k}={v:g}" for k, v in scores.items())
        parts.append(f"Dimensions: {dims}.")
    return " ".join(parts).strip() or "No content summary available in cached final_v1 analysis."


def _coerce_input(raw_input: Any) -> dict[str, Any]:
    data = raw_input if isinstance(raw_input, dict) else {}
    ref = _as_dict(data.get("analysis_cache_ref"))
    target_id = ref.get("target_id")
    if target_id in (None, ""):
        target_id = data.get("target_id")  # 容忍扁平传法
    derive_method = ref.get("derive_method") or data.get("derive_method") or DEFAULT_DERIVE_METHOD
    return {
        "video_url": (str(data.get("video_url")).strip() if data.get("video_url") else None),
        "target_id": (str(target_id) if target_id not in (None, "") else None),
        "derive_method": str(derive_method),
    }


def _shape_output(
    *,
    payload: dict[str, Any],
    inp: dict[str, Any],
    entry: dict[str, Any],
    summary: str,
    model_used: Optional[str],
) -> dict[str, Any]:
    layer6 = _as_dict(payload.get("layer6_flags_and_scores"))
    scores = _extract_scores(layer6)
    marketing_score = scores.get("marketing_value")
    if marketing_score is None:
        marketing_score = _score_number(_as_dict(layer6.get("scores")).get("marketing_value_score"))
        if marketing_score is None:
            marketing_score = _score_number(layer6.get("marketing_value_score"))
    return {
        "status": "ok",
        "scores": scores,
        "summary": summary,
        "marketing_potential": {
            "score": (round(marketing_score, 3) if marketing_score is not None else None),
            "band": _band(marketing_score),
            "key_hook": (str(layer6.get("key_hook")) if layer6.get("key_hook") else None),
            "verdict": (str(layer6.get("final_verdict")) if layer6.get("final_verdict") else None),
            "risk_flags": _as_list(layer6.get("risk_flags")),
        },
        "source": {
            "target_id": inp["target_id"],
            "derive_method": inp["derive_method"],
            "model": entry.get("model"),
            "video_url": inp["video_url"],
            "cache_status": entry.get("status"),
        },
        "model_used": model_used,
    }


def run(
    raw_input: Any,
    *,
    model_fn: Optional[Callable[[dict[str, Any]], str]] = None,
    record: bool = True,
) -> dict[str, Any]:
    """读 final_v1 缓存 → 投影成 content score schema → best-effort 落账本 → 返回。

    model_fn(payload_dict) -> summary_str:可注入的 LLM 总结增强;默认 None = 规则总结、不烧 LLM。
    record:True 时 best-effort 落 vkpi_skill_runs(缺表/异常都不抛)。
    """
    started = time.monotonic()
    inp = _coerce_input(raw_input)

    if not inp["target_id"]:
        out = {
            "status": "unavailable",
            "scores": {},
            "summary": "No analysis_cache_ref.target_id supplied; this skill reads cached final_v1, it does not run video analysis.",
            "marketing_potential": {"score": None, "band": "unknown", "key_hook": None, "verdict": None, "risk_flags": []},
            "source": {"target_id": None, "derive_method": inp["derive_method"], "model": None, "video_url": inp["video_url"], "cache_status": "missing_ref"},
            "model_used": None,
        }
        _best_effort_record(out, inp, model_used=None, started=started, record=record)
        return out

    entry: Optional[dict[str, Any]]
    try:
        entry = get_analysis_cache_entry(
            "video", inp["target_id"], derive_method=inp["derive_method"]
        )
    except Exception:
        entry = None

    if not entry or str(entry.get("status") or "") != "ready" or entry.get("result") in (None, ""):
        out = {
            "status": "unavailable",
            "scores": {},
            "summary": "No ready final_v1 cache for this target; run the gemini_video pipeline first.",
            "marketing_potential": {"score": None, "band": "unknown", "key_hook": None, "verdict": None, "risk_flags": []},
            "source": {
                "target_id": inp["target_id"],
                "derive_method": inp["derive_method"],
                "model": (entry.get("model") if entry else None),
                "video_url": inp["video_url"],
                "cache_status": (entry.get("status") if entry else "not_found"),
            },
            "model_used": None,
        }
        _best_effort_record(out, inp, model_used=None, started=started, record=record)
        return out

    payload = _final_payload(entry.get("result"))
    layer6 = _as_dict(payload.get("layer6_flags_and_scores"))
    scores = _extract_scores(layer6)
    marketing_score = scores.get("marketing_value")
    if marketing_score is None:
        marketing_score = _score_number(_as_dict(layer6.get("scores")).get("marketing_value_score"))

    model_used: Optional[str] = None
    summary: str
    if model_fn is not None:
        try:
            summary = str(model_fn(payload)).strip() or _rule_summary(payload=payload, scores=scores, marketing_score=marketing_score)
            model_used = "injected_model_fn"
        except Exception:
            summary = _rule_summary(payload=payload, scores=scores, marketing_score=marketing_score)
            model_used = None
    else:
        summary = _rule_summary(payload=payload, scores=scores, marketing_score=marketing_score)

    out = _shape_output(payload=payload, inp=inp, entry=entry, summary=summary, model_used=model_used)
    _best_effort_record(out, inp, model_used=model_used, started=started, record=record)
    return out


def _best_effort_record(
    out: dict[str, Any], inp: dict[str, Any], *, model_used: Optional[str], started: float, record: bool
) -> None:
    if not record:
        return
    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        skill_registry.record_skill_run(
            skill_name=SKILL_NAME,
            skill_version=SKILL_VERSION,
            input_schema={"target_id": inp.get("target_id"), "derive_method": inp.get("derive_method"), "has_video_url": bool(inp.get("video_url"))},
            model_used=model_used,
            prompt_version=(PROMPT_VERSION if model_used is None else "content_score_v1_model_fn"),
            retrieved_context={"derive_method": inp.get("derive_method"), "cache_status": _as_dict(out.get("source")).get("cache_status")},
            output=out,
            cost_cents=0,
            latency_ms=latency_ms,
        )
    except Exception:
        # best-effort:账本落不进绝不拖垮 skill。
        pass


# --------------------------------------------------------------------------- #
# EVAL_CASES —— 喂假 final_v1 payload，断言投影正确。零真 DB / 零真 LLM。
# skill_fn(input)=对一个 inline payload 跑 _shape 投影;metric 比对关键字段。
# --------------------------------------------------------------------------- #
def _eval_skill_fn(case_input: dict[str, Any]) -> dict[str, Any]:
    """eval 专用被测函数:绕过 DB，直接对内联 final_v1 payload 跑投影逻辑。"""
    payload = _final_payload(case_input.get("result"))
    layer6 = _as_dict(payload.get("layer6_flags_and_scores"))
    scores = _extract_scores(layer6)
    marketing_score = scores.get("marketing_value")
    if marketing_score is None:
        marketing_score = _score_number(_as_dict(layer6.get("scores")).get("marketing_value_score"))
    return {
        "scores": scores,
        "marketing_band": _band(marketing_score),
        "has_summary": bool(_rule_summary(payload=payload, scores=scores, marketing_score=marketing_score)),
    }


def _eval_metric(expected: dict[str, Any], actual: dict[str, Any]) -> tuple[bool, float]:
    checks = []
    checks.append(actual.get("marketing_band") == expected.get("marketing_band"))
    checks.append(actual.get("has_summary") is True)
    exp_scores = expected.get("scores") or {}
    act_scores = actual.get("scores") or {}
    for k, v in exp_scores.items():
        checks.append(abs(float(act_scores.get(k, -999)) - float(v)) < 1e-6)
    hits = sum(1 for c in checks if c)
    score = hits / len(checks) if checks else 1.0
    return (hits == len(checks)), score


def _case(name: str, mv: Optional[float], extra: dict[str, Any], band: str, exp_scores: dict[str, Any]) -> EvalCase:
    layer6_scores: dict[str, Any] = dict(extra)
    if mv is not None:
        layer6_scores["marketing_value_score"] = mv
    result = {
        "layer1_visual_content": {"content_summary": "A product demo video."},
        "layer6_flags_and_scores": {"scores": layer6_scores, "key_hook": "bold opening"},
    }
    return EvalCase(
        name=name,
        input={"result": result},
        expected={"marketing_band": band, "scores": exp_scores},
        metric=_eval_metric,
    )


EVAL_CASES: list[EvalCase] = [
    _case("high_marketing_value", 88.0, {"content_quality_score": 80.0, "hook_score": 75.0},
          "high", {"marketing_value": 88.0, "quality": 80.0, "hook": 75.0}),
    _case("medium_band", 60.0, {"brand_fit_score": 55.0}, "medium",
          {"marketing_value": 60.0, "brand_fit": 55.0}),
    _case("low_band", 30.0, {}, "low", {"marketing_value": 30.0}),
    # 分数是 {score, confidence} 结构 —— 只取 score。
    _case("dict_shaped_score", {"score": 90.0, "confidence": 0.9}, {"quality_score": {"score": 70.0}},
          "high", {"marketing_value": 90.0, "quality": 70.0}),
    # 无 marketing_value_score → band unknown，但仍出 summary。
    _case("no_marketing_score", None, {"hook_score": 40.0}, "unknown", {"hook": 40.0}),
]


def evaluate(*, suite: str = "content_score_v1") -> dict[str, Any]:
    """用 marketing_brain.evals.run_eval 跑 EVAL_CASES,返回 report.to_dict()。"""
    from app.domains.marketing_brain.evals import run_eval

    report = run_eval(_eval_skill_fn, EVAL_CASES, default_metric=_eval_metric, suite=suite)
    return report.to_dict()
