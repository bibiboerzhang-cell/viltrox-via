"""ai_today_json_guard 单测(2026-08-24 审计 F7/F9 配套)。

覆盖:
- 截断收口:真实故障形状(max_output_tokens 打断 → "Unterminated string" @char N,
  2026-08-24 opus-5 evidence_strategy 实证)被修复梯救回;
- 既有 2026-07-16 语法修复梯(尾逗号/行尾漏逗号)经 sibling 复用仍生效;
- 旧 _parse_json 行为兼容(围栏/前后说明文字/失败返回 {});
- generate_json_with_parse_retry:仅 parse_failure 重打、封顶 2 发、其余失败不重打;
- ai_today._run_ai_today_evidence_strategy 解析失败重打一次后 ready;
- official_daily_report._generate 同模型第二发救回 + 跨模型兜底顺序不变。
"""
from __future__ import annotations

import json
from typing import Any

from app.domains.market import ai_today
from app.domains.market.ai_today_json_guard import (
    extract_json_object,
    generate_json_with_parse_retry,
)


_STRATEGY_PAYLOAD: dict[str, Any] = {
    "headline": "今日重点:借势海外弱光人像热点",
    "shooting_plans": ["城市夜景人像 + AF 85mm F1.8,面向海外街拍创作者"],
    "hot_topics": ["海外街拍社区本周热议弱光人像玩法"],
    "product_recommendations": ["AF 定焦系列:契合弱光人像热点(见证据 S1)"],
    "content_recommendations": ["夜景人像教学短片,发 YouTube Shorts"],
    "video_recommendations": ["证据包候选视频 1,用作拍法参考"],
}


def _parse_failure_result() -> dict[str, Any]:
    """网关 parse_failure 后的 fallback 形状(_rule_fallback + errors 保留原始 status)。"""
    return {
        "status": "fallback_to_rule",
        "provider": "rule_v0",
        "model": "rule_v0",
        "json": None,
        "fallback_used": True,
        "reason": "all_providers_failed",
        "errors": [
            {
                "provider": "anthropic",
                "status": "parse_failure",
                "error": "Unterminated string starting at: line 1 column 1900 (char 1919)",
            }
        ],
        "failure_code": "schema_failure",
    }


# ── extract_json_object:截断收口(真实 Unterminated string 形状)─────────────


def test_truncated_unterminated_string_is_rescued_dropping_partial_tail() -> None:
    full = json.dumps(_STRATEGY_PAYLOAD, ensure_ascii=False)
    # 模拟 max_output_tokens 打断:切在最后一个字符串值中间(json.loads 报 Unterminated string)。
    truncated = full[: full.rfind("证据包候选视频") + len("证据包候选视频")]

    parsed = extract_json_object(truncated, surface="test")

    assert isinstance(parsed, dict)
    # 截断前的完整字段全部保留。
    assert parsed["headline"] == _STRATEGY_PAYLOAD["headline"]
    assert parsed["shooting_plans"] == _STRATEGY_PAYLOAD["shooting_plans"]
    assert parsed["product_recommendations"] == _STRATEGY_PAYLOAD["product_recommendations"]


def test_truncated_mid_list_keeps_prior_complete_items() -> None:
    truncated = '{"headline": "标题", "plans": ["完整一条", "被打断的半'

    parsed = extract_json_object(truncated, surface="test")

    assert parsed["headline"] == "标题"
    assert parsed["plans"] == ["完整一条"]


def test_complete_json_untouched_by_truncation_rung() -> None:
    payload = {"a": "包含 } 和 ] 的字符串", "b": [1, 2]}

    assert extract_json_object(json.dumps(payload, ensure_ascii=False)) == payload


# ── extract_json_object:2026-07-16 语法修复梯复用 ───────────────────────────


def test_trailing_comma_rescued_by_reused_ladder() -> None:
    assert extract_json_object('{"a": 1, "b": [2, 3,],}') == {"a": 1, "b": [2, 3]}


def test_missing_line_end_comma_rescued_by_reused_ladder() -> None:
    raw = '{\n  "a": "x"\n  "b": "y"\n}'

    assert extract_json_object(raw) == {"a": "x", "b": "y"}


def test_string_literals_never_rewritten_by_ladder() -> None:
    # 保险丝:字符串字面量里的“尾逗号形状”绝不能被改写。
    payload = {"hooks": "[a,], done", "n": 1}

    assert extract_json_object(json.dumps(payload)) == payload


# ── extract_json_object:旧 _parse_json 行为兼容 ─────────────────────────────


def test_fenced_json_still_parses() -> None:
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_prose_wrapped_json_still_parses() -> None:
    raw = '根据搜索结果,输出如下:\n{"a": 1, "b": "x"}\n以上引用了 3 个来源。'

    assert extract_json_object(raw) == {"a": 1, "b": "x"}


def test_unrepairable_or_empty_returns_empty_dict() -> None:
    assert extract_json_object("") == {}
    assert extract_json_object("彻底不是 JSON 的一段话") == {}
    assert extract_json_object("[1, 2, 3]") == {}  # 根必须是对象(旧行为)


def test_ai_today_parse_json_delegates_to_guard() -> None:
    assert ai_today._parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    # 截断形状经修复梯原地闭合救回(升级点);彻底非 JSON 仍如实 {}。
    assert ai_today._parse_json('{"headline": "被打断的半') == {"headline": "被打断的半"}
    assert ai_today._parse_json("彻底不是 JSON") == {}


# ── generate_json_with_parse_retry ──────────────────────────────────────────


def test_parse_retry_success_first_attempt_calls_once() -> None:
    calls: list[int] = []

    def call() -> dict[str, Any]:
        calls.append(1)
        return {"status": "success", "json": {"ok": True}}

    result, attempts = generate_json_with_parse_retry(call, surface="test", max_attempts=2)

    assert attempts == 1 and len(calls) == 1
    assert result["status"] == "success"


def test_parse_retry_recovers_on_second_attempt() -> None:
    outcomes = [_parse_failure_result(), {"status": "success", "json": {"ok": True}}]

    result, attempts = generate_json_with_parse_retry(
        lambda: outcomes.pop(0), surface="test", max_attempts=2
    )

    assert attempts == 2 and not outcomes
    assert result["status"] == "success"


def test_parse_retry_does_not_retry_validation_failure() -> None:
    calls: list[int] = []

    def call() -> dict[str, Any]:
        calls.append(1)
        return {
            "status": "fallback_to_rule",
            "json": None,
            "errors": [{"provider": "anthropic", "status": "validation_failure", "error": "bad"}],
        }

    result, attempts = generate_json_with_parse_retry(call, surface="test", max_attempts=2)

    assert attempts == 1 and len(calls) == 1
    assert result["status"] == "fallback_to_rule"


def test_parse_retry_bounded_at_two_attempts() -> None:
    calls: list[int] = []

    def call() -> dict[str, Any]:
        calls.append(1)
        return _parse_failure_result()

    result, attempts = generate_json_with_parse_retry(call, surface="test", max_attempts=2)

    assert attempts == 2 and len(calls) == 2
    assert result["status"] == "fallback_to_rule"


# ── ai_today evidence strategy 调用侧集成 ───────────────────────────────────


def test_evidence_strategy_retries_parse_failure_then_ready(monkeypatch) -> None:
    from app.platform import llm_production

    outcomes = [
        _parse_failure_result(),
        {
            "status": "success",
            "provider": "anthropic",
            "model": ai_today._AI_TODAY_STRATEGY_MODEL,
            "json": dict(_STRATEGY_PAYLOAD),
        },
    ]
    calls: list[str] = []

    def fake_generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(str(kwargs.get("purpose")))
        return outcomes.pop(0)

    monkeypatch.setattr(llm_production, "generate_json", fake_generate_json)
    monkeypatch.setattr(ai_today, "_recent_recommended_lines", lambda: [])

    stage = ai_today._run_ai_today_evidence_strategy(
        {"market_signals": ["海外热点 A"], "video_candidates": ["候选视频 1"]},
        [{"source_id": "S1", "title": "证据", "url": "https://example.com/a"}],
    )

    assert len(calls) == 2
    assert stage["status"] == "ready"
    assert stage["content"]["headline"] == _STRATEGY_PAYLOAD["headline"]


def test_evidence_strategy_stays_degraded_after_two_parse_failures(monkeypatch) -> None:
    from app.platform import llm_production

    calls: list[int] = []

    def fake_generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(1)
        return _parse_failure_result()

    monkeypatch.setattr(llm_production, "generate_json", fake_generate_json)
    monkeypatch.setattr(ai_today, "_recent_recommended_lines", lambda: [])

    stage = ai_today._run_ai_today_evidence_strategy(
        {"market_signals": ["海外热点 A"], "video_candidates": ["候选视频 1"]},
        [{"source_id": "S1", "title": "证据", "url": "https://example.com/a"}],
    )

    assert len(calls) == 2  # 封顶 2 发,不无限烧钱
    assert stage["status"] == "degraded"


# ── official_daily_report 调用侧(F9)────────────────────────────────────────


def test_official_report_second_attempt_rescues_parse_failure(monkeypatch) -> None:
    from app.domains.channels import official_daily_report

    usable = {
        "play_performance": "播放整体上行,爆帖《A》带动",
        "comment_insights": "评论正向为主",
        "visual_quality": "真画质分增量分析中",
        "data_trend": "粉丝周增 +120",
        "suggestions": ["多发弱光人像教学"],
        "headline": "今日总评:稳中有升",
    }
    outcomes = [
        _parse_failure_result(),
        {
            "status": "success",
            "provider": "anthropic",
            "model": "claude-test-model",
            "json": dict(usable),
        },
    ]
    calls: list[str] = []

    def fake_generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(str(kwargs.get("provider")))
        return outcomes.pop(0)

    monkeypatch.setattr(official_daily_report.llm_production, "generate_json", fake_generate_json)

    raw, model = official_daily_report._generate("prompt")

    assert calls == ["anthropic", "anthropic"]  # 同模型第二发救回,不必跨模型
    assert model == "claude:claude-test-model"
    assert json.loads(raw)["headline"] == usable["headline"]


def test_official_report_cross_model_fallback_still_works_after_retries(monkeypatch) -> None:
    from app.domains.channels import official_daily_report

    usable = {
        "play_performance": "播放平稳",
        "comment_insights": "咨询类居多",
        "visual_quality": "pending",
        "data_trend": "互动率持平",
        "suggestions": ["回应卡口咨询"],
        "headline": "今日总评:平稳",
    }
    outcomes = [
        _parse_failure_result(),
        _parse_failure_result(),
        {
            "status": "success",
            "provider": "google",
            "model": "gemini-test-model",
            "json": dict(usable),
        },
    ]
    calls: list[str] = []

    def fake_generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(str(kwargs.get("provider")))
        return outcomes.pop(0)

    monkeypatch.setattr(official_daily_report.llm_production, "generate_json", fake_generate_json)

    raw, model = official_daily_report._generate("prompt")

    assert calls == ["anthropic", "anthropic", "google"]
    assert model == "gemini:gemini-test-model"
    assert json.loads(raw)["headline"] == usable["headline"]
