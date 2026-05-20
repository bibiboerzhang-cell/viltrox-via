from __future__ import annotations

from datetime import datetime, timezone

from app.services.vkpi.brand_signal_detector import detect_viltrox_signals


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
