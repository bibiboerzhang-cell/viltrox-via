from __future__ import annotations

from app.domains.content import creative_segments


def _segment(row_id: int, *, focal: str = "") -> dict:
    return {
        "segment_id": f"{row_id}:scene",
        "segment_type": "scene",
        "description": f"segment {row_id}",
        "timestamp": None,
        "opening_types": [],
        "styles": [],
        "video_styles": [],
        "focals": [focal] if focal else [],
        "source": {"evidence_id": row_id},
        "video": {"view_count": 10_000 - row_id, "platform": "youtube"},
        "kol": {},
        "_blob": f"segment {row_id}",
    }


def test_segment_top_items_stops_after_exact_leading_slice(monkeypatch) -> None:
    rows = [{"evidence_id": row_id} for row_id in range(1, 41)]
    calls: list[tuple[int, int]] = []
    thumbnail_flags: list[bool] = []

    monkeypatch.setattr(creative_segments, "_count_final_v1_rows", lambda _conn: len(rows))

    def load(_conn, scan_limit: int, *, offset: int = 0):
        calls.append((scan_limit, offset))
        return rows[offset : offset + scan_limit]

    def decompose(row, *, include_thumbnails: bool = True):
        thumbnail_flags.append(include_thumbnails)
        return [_segment(int(row["evidence_id"]))]

    monkeypatch.setattr(creative_segments, "_load_final_v1_rows", load)
    monkeypatch.setattr(creative_segments, "_decompose_video", decompose)
    monkeypatch.setattr("app.db.connection.get_conn", lambda: object())

    result = creative_segments.segment_top_items(limit=3)

    assert result["selection"] == "exact_top_n"
    assert result["scanned_videos"] == 40
    assert [item["segment_id"] for item in result["items"]] == [
        "1:scene",
        "2:scene",
        "3:scene",
    ]
    assert calls == [(16, 0)]
    assert thumbnail_flags == [False, False, False]
    assert all("_blob" not in item for item in result["items"])


def test_segment_top_items_pages_until_filtered_match(monkeypatch) -> None:
    rows = [{"evidence_id": row_id} for row_id in range(1, 131)]
    offsets: list[int] = []

    monkeypatch.setattr(creative_segments, "_count_final_v1_rows", lambda _conn: len(rows))

    def load(_conn, scan_limit: int, *, offset: int = 0):
        offsets.append(offset)
        return rows[offset : offset + scan_limit]

    def decompose(row, *, include_thumbnails: bool = True):
        row_id = int(row["evidence_id"])
        return [_segment(row_id, focal="135mm" if row_id == 100 else "85mm")]

    monkeypatch.setattr(creative_segments, "_load_final_v1_rows", load)
    monkeypatch.setattr(creative_segments, "_decompose_video", decompose)
    monkeypatch.setattr("app.db.connection.get_conn", lambda: object())

    result = creative_segments.segment_top_items(focal="135", limit=1)

    assert [item["segment_id"] for item in result["items"]] == ["100:scene"]
    assert offsets == [0, 64]
    assert "matched" not in result
