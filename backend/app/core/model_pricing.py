"""
core/model_pricing.py — estimated USD pricing per 1M tokens.

Values are local estimates for SystemTab cost visibility and should be checked
monthly against provider billing exports.

2026-08-22 模型升级刀:价格表按官方页当日核实冻结。新旧 id 都保留行(历史台账行
按旧价回算;prod env 钉回旧模型时也有价)。同一套数值还镜像在
platform/models/runtime._EXACT_CATALOG(分)与 llm_gateway.PROVIDER_CONFIG(分),
三处必须一致(tests/test_model_registry_defaults.py 比对)。
"""
from __future__ import annotations

PRICING_USD_PER_1M_TOKENS = {
    # ── Anthropic ──
    "claude-opus-5": {"input": 5.0, "output": 25.0},
    # Sonnet 5 $2/$10 已是正式价(2026-08-22 官方页核实);batch 1/5。
    "claude-sonnet-5": {"input": 2.0, "output": 10.0},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},  # retire 2026-10-15
    # ── OpenAI ──
    # gpt-5.6-luna:缓存输入 0.02;调用须 reasoning.effort='none'。
    "gpt-5.6-luna": {"input": 0.20, "output": 1.20},
    "gpt-5.6": {"input": 5.0, "output": 30.0},
    "gpt-5.5": {"input": 5.0, "output": 30.0},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
    "gpt-4o": {"input": 2.5, "output": 10.0},  # 历史台账行回算用,无调用方
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    # ── Google ──
    # gemini-3.6-flash 促销价至 2026-12-31(之后 1.50/7.50);缓存输入 0.075;
    # 音频输入无单独价(同 0.75)。到期需同步改 runtime._EXACT_CATALOG /
    # PROVIDER_CONFIG / apify_jobs_cost / decision_ledger。
    "gemini-3.6-flash": {"input": 0.75, "output": 3.75},
    "gemini-3.5-flash": {"input": 1.50, "output": 9.0},
    # 文本/图/视频/音频同价;无缓存折扣。
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
    # Conservative video/multimodal text rates; audio input is reconciled from
    # provider usage metadata by the worker's model-specific cost calculator.
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0},
}


def _pricing_key(model: str) -> str:
    raw = str(model or "").strip().lower()
    if raw in PRICING_USD_PER_1M_TOKENS:
        return raw
    # 子串梯:更长/更具体的 id 必须排在其前缀之前
    # ('gemini-3.5-flash-lite' 含 'gemini-3.5-flash';'gpt-5.6-luna' 含 'gpt-5.6')。
    if "gpt-5.6-luna" in raw:
        return "gpt-5.6-luna"
    if "gpt-5.6" in raw:
        return "gpt-5.6"
    if "gpt-5.4-mini" in raw:
        return "gpt-5.4-mini"
    if "gpt-5.5" in raw:
        return "gpt-5.5"
    if "gemini-3.6-flash" in raw:
        return "gemini-3.6-flash"
    if "gemini-3.5-flash-lite" in raw:
        return "gemini-3.5-flash-lite"
    if "gemini-3.5-flash" in raw:
        return "gemini-3.5-flash"
    if "gpt-4o-mini" in raw:
        return "gpt-4o-mini"
    if "gpt-4o" in raw:
        return "gpt-4o"
    if "gemini" in raw and "pro" in raw:
        return "gemini-2.5-pro"
    if "gemini" in raw and "flash" in raw:
        return "gemini-3.6-flash"
    if "opus" in raw:
        return "claude-opus-5"
    if "haiku" in raw:
        return "claude-haiku-4-5"  # 历史 haiku 行按 haiku 价;2026-10-15 后可并入 sonnet-5
    if "sonnet" in raw or "claude" in raw:
        return "claude-sonnet-5"
    # 未知/新模型(gpt-5.x / o-系列等无关键字命中)→ 不要落 0 价(会把成本估成 0、台账失真),
    # 按家族给当前默认 tier。真上线新模型应在 PRICING_USD_PER_1M_TOKENS 补真价覆盖此兜底。
    if "gpt" in raw or raw.startswith("o1") or raw.startswith("o3") or raw.startswith("o4"):
        return "gpt-5.6-luna"
    if "gemini" in raw:
        return "gemini-3.6-flash"
    return "claude-sonnet-5"


def estimate_cost_usd(model: str, tokens_in: int = 0, tokens_out: int = 0) -> float:
    pricing = PRICING_USD_PER_1M_TOKENS.get(_pricing_key(model), {"input": 0.0, "output": 0.0})
    return round(
        (max(0, int(tokens_in or 0)) / 1_000_000 * float(pricing["input"]))
        + (max(0, int(tokens_out or 0)) / 1_000_000 * float(pricing["output"])),
        8,
    )
