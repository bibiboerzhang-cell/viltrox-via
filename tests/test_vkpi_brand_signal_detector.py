from __future__ import annotations

from datetime import datetime, timezone

from app.db.connection import get_conn
from app.services.vkpi.brand_signal_detector import commit_brand_signals, detect_viltrox_signals, list_brand_signals


def test_numeric_epoch_published_at_is_normalized_for_db():
    epoch = "1778563607"
    signals = detect_viltrox_signals(
        {
            "id": "post-epoch",
            "published_at": epoch,
            "caption": "New Viltrox AF 35mm F1.2 LAB sample.",
        },
        context={"platform": "instagram", "source_table": "unit", "source_id": 1},
    )

    assert signals
    expected = datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    assert {signal["published_at"] for signal in signals} == {expected}
    assert {signal["analysis_scope"] for signal in signals} == {"current_year"}


def test_unparseable_published_at_is_not_sent_to_timestamp_column():
    signals = detect_viltrox_signals(
        {
            "id": "post-bad-date",
            "published_at": "not-a-date",
            "caption": "Viltrox portrait sample.",
        },
        context={"platform": "instagram", "source_table": "unit", "source_id": 2},
    )

    assert signals
    assert {signal["published_at"] for signal in signals} == {""}
    assert {signal["analysis_scope"] for signal in signals} == {"unknown_date"}


def test_list_brand_signals_returns_total_count_separate_from_page_count():
    conn = get_conn()
    try:
        conn.execute("DELETE FROM vkpi_brand_signal WHERE signal_type=?", ("unit_signal",))
        conn.commit()
    except Exception:
        pass
    signals = []
    for index in range(2):
        signals.append(
            {
                "signal_uid": f"unit-signal-{index}",
                "kol_entity_uid": f"unit-kol-{index}",
                "post_uid": f"unit-post-{index}",
                "source_table": "unit",
                "source_id": index,
                "post_url": "",
                "platform": "youtube",
                "published_at": "2026-05-20T00:00:00Z",
                "analysis_scope": "current_year",
                "signal_type": "unit_signal",
                "brand_name": "unit-brand",
                "brand_role": "self",
                "signal_strength": "medium",
                "evidence_json": '{"match_text":["viltrox"],"match_source":"unit","match_context":"Unit Viltrox context"}',
                "detected_at": "2026-05-20T00:00:00Z",
            }
        )
    try:
        assert commit_brand_signals(signals) == 2
        result = list_brand_signals(status="all", signal_type="unit_signal", limit=1)
        assert result["count"] == 1
        assert result["total_count"] == 2
        assert result["signals"][0]["evidence"]["match_source"] == "unit"
        assert result["signals"][0]["matched_text"] == "viltrox"
        assert result["signals"][0]["match_context"] == "Unit Viltrox context"
    finally:
        conn.execute("DELETE FROM vkpi_brand_signal WHERE signal_type=?", ("unit_signal",))
        conn.commit()
