"""库内召回硬筛三态语义的契约(2026-08-25)。

最重要的一条:**缺省行为必须与三态出现之前逐字节一致**。用户裁令是「勾『美国』
就是要美国人」,所以放宽只能由操作员显式选择,系统不许偷偷替他决定。这里把
「零行为漂移」钉死,其余用例覆盖两种分开记账与定点补齐钩子。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import profile_recall_filter_modes as modes
from app.domains.kol.profile_recall_projection import _candidate_filter_verdict


# ── 模式归一:不认识的一律退回 require(失败方向 = 保持现状)──────────────


@pytest.mark.parametrize(
    "raw", [None, "", "  ", "REQUIRE", "unknown_mode", 0, [], "include-unknown"]
)
def test_unrecognised_mode_falls_back_to_require(raw: Any) -> None:
    assert modes.normalize_mode(raw) in modes.TRI_STATE_MODES
    if str(raw or "").strip().lower() not in modes.TRI_STATE_MODES:
        assert modes.normalize_mode(raw) == "require"


def test_declared_modes_are_exactly_three() -> None:
    assert modes.TRI_STATE_MODES == ("require", "include_unknown", "exclude")


# ── 单字段三态判定 ──────────────────────────────────────────────────────────


def test_no_filter_requested_always_passes() -> None:
    for mode in modes.TRI_STATE_MODES:
        assert modes.tri_state_outcome("us", set(), mode) == modes.OUTCOME_PASS
        assert modes.tri_state_outcome("", set(), mode) == modes.OUTCOME_PASS


def test_require_rejects_unknown_exactly_like_before() -> None:
    """缺省档:未知 = 驳回。这是历史行为,用户裁令保留。"""
    assert modes.tri_state_outcome("", {"us"}, "require") == modes.OUTCOME_UNKNOWN
    assert modes.tri_state_outcome("", {"us"}, None) == modes.OUTCOME_UNKNOWN


def test_include_unknown_lets_unknown_through_but_still_rejects_real_mismatch() -> None:
    assert modes.tri_state_outcome("", {"us"}, "include_unknown") == modes.OUTCOME_PASS
    assert modes.tri_state_outcome("jp", {"us"}, "include_unknown") == modes.OUTCOME_MISMATCH
    assert modes.tri_state_outcome("us", {"us"}, "include_unknown") == modes.OUTCOME_PASS


def test_exclude_flips_the_match_and_never_kills_unknown() -> None:
    assert modes.tri_state_outcome("us", {"us"}, "exclude") == modes.OUTCOME_MISMATCH
    assert modes.tri_state_outcome("jp", {"us"}, "exclude") == modes.OUTCOME_PASS
    assert modes.tri_state_outcome("", {"us"}, "exclude") == modes.OUTCOME_PASS


# ── 零行为漂移:缺省档必须等于历史硬筛 ──────────────────────────────────────


def _row(country: str = "", language: str = "", followers: int = 80_000) -> dict[str, Any]:
    return {
        "platform": "youtube",
        "country": country,
        "language": language,
        "followers": followers,
        "bio": "portrait photographer shooting sony e-mount lenses",
    }


_DRIFT_CASES = [
    ("确认是美国+英语", _row("US", "en")),
    ("确认非美国", _row("JP", "ja")),
    ("国家未知", _row("", "en")),
    ("语言未知", _row("US", "")),
    ("两者都未知", _row("", "")),
    ("粉丝不足", _row("US", "en", followers=100)),
]


@pytest.mark.parametrize("label,row", _DRIFT_CASES, ids=[c[0] for c in _DRIFT_CASES])
def test_default_mode_matches_explicit_require(label: str, row: dict[str, Any]) -> None:
    """不传 mode 与显式传 require,判定必须逐字段相同 —— 这是零漂移的定义。"""
    base = {"countries": ["US"], "languages": ["en"], "followers_min": 50_000}
    implicit = _candidate_filter_verdict(row, {}, dict(base))
    explicit = _candidate_filter_verdict(
        row, {}, {**base, "countries_mode": "require", "languages_mode": "require"}
    )
    assert tuple(implicit) == tuple(explicit), label


def test_unknown_country_is_rejected_under_default() -> None:
    """勾『美国』时,国家未知的人仍被驳回 —— 用户明确要的语义。"""
    verdict = _candidate_filter_verdict(_row("", "en"), {}, {"countries": ["US"], "languages": ["en"]})
    passes, rejected, _unknown = verdict
    assert passes is False
    assert "countries" in rejected
    # 未知与「确认不符」记在两本不同的账上,诊断才分得清。
    assert "countries" in verdict.rejected_unknown


def test_include_unknown_admits_the_same_person() -> None:
    """同一个人,操作员显式选『含未知』时才放行 —— 放宽是他的决定,不是系统的。"""
    verdict = _candidate_filter_verdict(
        _row("", "en"), {}, {"countries": ["US"], "languages": ["en"], "countries_mode": "include_unknown"}
    )
    assert verdict[0] is True
    # 放行不等于假装知道:这一档下 countries 不再进驳回账。
    assert "countries" not in verdict.rejected_unknown
    assert "countries" not in verdict[1]


# ── 两本分开的账 ────────────────────────────────────────────────────────────


def test_unknown_and_real_mismatch_are_booked_separately() -> None:
    """诊断必须能区分「他不是美国人」和「我们不知道他是不是」。"""
    verdict_unknown = _candidate_filter_verdict(_row("", "en"), {}, {"countries": ["US"]})
    verdict_mismatch = _candidate_filter_verdict(_row("JP", "en"), {}, {"countries": ["US"]})

    assert "countries" in getattr(verdict_unknown, "rejected_unknown", [])
    assert "countries" not in getattr(verdict_unknown, "rejected_known_mismatch", [])

    assert "countries" in getattr(verdict_mismatch, "rejected_known_mismatch", [])
    assert "countries" not in getattr(verdict_mismatch, "rejected_unknown", [])


def test_verdict_still_unpacks_as_the_historical_triple() -> None:
    """旁挂新账不得破坏既有解包/索引/比较 —— 所有旧调用点必须零改动。"""
    verdict = _candidate_filter_verdict(_row("US", "en"), {}, {"countries": ["US"]})
    passes, rejected, unknown = verdict
    assert isinstance(passes, bool)
    assert verdict[0] is passes and verdict[1] == rejected and verdict[2] == unknown
    assert tuple(verdict) == (passes, rejected, unknown)


# ── 定点补齐钩子:只标记,不抓取 ────────────────────────────────────────────


def test_hook_marks_only_candidates_blocked_solely_by_unknown_fields() -> None:
    """其他维度都合格、只差国家/语言未知的人才该被标出来补。"""
    only_unknown = _candidate_filter_verdict(
        _row("", "en", followers=90_000), {}, {"countries": ["US"], "followers_min": 50_000}
    )
    also_too_small = _candidate_filter_verdict(
        _row("", "en", followers=100), {}, {"countries": ["US"], "followers_min": 50_000}
    )
    assert only_unknown.unknown_field_candidates, "只差未知的应被标记"
    assert [c["missing_fields"] for c in only_unknown.unknown_field_candidates] == [["country"]]
    assert not also_too_small.unknown_field_candidates, "还有别的硬伤就不该标"


def test_hook_never_triggers_collection() -> None:
    """钩子是纯函数:不得有任何采集/入队/网络动作。"""
    from pathlib import Path

    source = Path(modes.__file__).read_text(encoding="utf-8")
    for forbidden in ("requests", "urllib", "httpx", "enqueue", "apify", "get_conn"):
        assert forbidden not in source, f"三态模块不该出现 {forbidden}"
