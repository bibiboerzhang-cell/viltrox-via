from __future__ import annotations

from app.domains.market_brain import weekly_answers_report


def test_weekly_conclusion_notes_preserve_exact_public_copy() -> None:
    ops = {
        "_bet_brief": lambda row: dict(row),
        "_text": lambda value, _limit: str(value or ""),
        "WIN_DECISIONS": ("validated",),
        "LOSS_DECISIONS": ("failed",),
        "_ITEM_CAP": 30,
        "_sufficient": lambda _groups, **_kwargs: [],
        "_build_what_to_change": lambda _rows: {"status": "empty"},
    }

    worked, failed, _changes = weekly_answers_report.conclusion_sections(
        [{"decision": "validated"}, {"decision": "failed"}],
        {},
        claimable=True,
        ops=ops,
    )

    assert worked["note"] == (
        "对了什么=本期带真实窗口证据的 validated/escalate 裁决"
        " + 样本≥5 且胜率≥60% 的组合。"
    )
    assert failed["note"] == (
        "错了什么=本期带真实窗口证据的 failed/retreat 裁决"
        " + 样本≥5 且胜率≤40% 的组合;lesson 原话随行。"
    )
