"""Daily digest Viltrox mentions: final_v1 multimodal evidence + honest fallback."""
from __future__ import annotations

import json
import sqlite3

from app.domains.kol import daily_digest


def _schema(conn: sqlite3.Connection, *, with_cache: bool = True) -> None:
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            handle TEXT,
            display_name TEXT,
            platform TEXT,
            duplicate_of_id INTEGER
        );
        CREATE TABLE vkpi_kol_pool_favorites (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER,
            staff_id INTEGER
        );
        CREATE TABLE vkpi_kol_pool_members (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER,
            staff_id INTEGER
        );
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER,
            video_title TEXT,
            title TEXT,
            channel_name TEXT,
            content_url TEXT,
            media_kind TEXT,
            view_count INTEGER,
            like_count INTEGER,
            comment_count INTEGER,
            is_active INTEGER,
            created_at TEXT,
            publish_date TEXT,
            posted_at TEXT
        );
        """
    )
    if with_cache:
        conn.executescript(
            """
            CREATE TABLE vkpi_analysis_cache (
                id INTEGER PRIMARY KEY,
                target_type TEXT,
                target_id TEXT,
                derive_method TEXT,
                result TEXT,
                status TEXT
            );
            """
        )


def _result(modalities: list[str], *, layer1_only: bool = False) -> str:
    evidence = [
        {
            "modality": modality,
            "timestamp": f"00:0{index}",
            "detail": f"secret-{modality}-{index}",
        }
        for index, modality in enumerate(modalities)
    ]
    brand = {"viltrox_status": "present", "viltrox_evidence": evidence}
    raw = (
        {"video_analysis_final_v1": {"layer1_visual_content": {"brand_product_evidence": brand}}}
        if layer1_only
        else {"brand_product_evidence": brand}
    )
    return json.dumps({"raw_gemini_video": raw}, ensure_ascii=False)


def _populated_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _schema(conn)
    conn.execute(
        "INSERT INTO vkpi_kol_pool (id, handle, display_name, platform) VALUES (1, 'creator', 'Creator', 'youtube')"
    )
    conn.execute("INSERT INTO vkpi_kol_pool_favorites (id, kol_pool_id, staff_id) VALUES (1, 1, 7)")
    rows = [
        (1, "Portrait walk", "Alice", 1000, 1),
        (2, "Viltrox AF 85 review", "Alice", 900, 1),
        (3, "Night street test", "Alice", 800, 1),
        (4, "Generic camera lesson", "Alice", 700, 1),
        (5, "Another camera lesson", "Viltrox Creator", 600, 1),
        (6, "Viltrox inactive old row", "Alice", 500, 0),
    ]
    conn.executemany(
        """
        INSERT INTO vkpi_kol_video_evidence (
            id, kol_pool_id, video_title, title, channel_name, content_url,
            view_count, is_active, created_at
        ) VALUES (?, 1, ?, '', ?, 'https://example.test/video', ?, ?, '2026-08-20T00:00:00Z')
        """,
        rows,
    )
    # Some collectors leave video_title empty while title is populated; the fallback must keep it.
    conn.execute(
        "UPDATE vkpi_kol_video_evidence SET video_title='', title='Viltrox AF 85 review' WHERE id=2"
    )
    conn.executemany(
        """
        INSERT INTO vkpi_analysis_cache (
            id, target_type, target_id, derive_method, result, status
        ) VALUES (?, 'video', ?, 'video_analysis_final_v1', ?, 'ready')
        """,
        [
            (101, "1", _result(["audio", "visual"])),
            (103, "3", _result(["subtitle"], layer1_only=True)),
        ],
    )
    conn.commit()
    return conn


def test_final_v1_projection_batches_the_full_digest_window(monkeypatch) -> None:
    seen: list[list[int]] = []

    def fake_projection(_conn: object, evidence_ids: list[int]) -> dict[int, list[str]]:
        seen.append(evidence_ids)
        return {value: ["visual"] for value in evidence_ids}

    monkeypatch.setattr(daily_digest, "final_v1_modalities_for_evidence", fake_projection)
    projected = daily_digest._final_v1_modalities(object(), list(range(1, 402)))

    assert [len(batch) for batch in seen] == [200, 200, 1]
    assert len(projected) == 401
    assert projected[401] == ["visual"]


def test_viltrox_mentions_includes_visual_subtitle_audio_and_keeps_lexicon_fallback() -> None:
    conn = _populated_conn()
    try:
        changes_before = conn.total_changes
        body = daily_digest._viltrox_mentions(conn, sid=7, since="2026-08-01")

        assert conn.total_changes == changes_before  # pure read
        assert body["status"] == "ready"
        assert body["method"] == "final_v1_modalities_present+lexicon_fallback_v2"
        assert body["count"] == 4
        assert body["sources"] == ["final_v1", "title_lexicon", "channel_lexicon"]
        assert body["modalities"] == ["visual", "subtitle", "audio"]
        assert body["coverage"] == {
            "candidate_count": 5,
            "final_v1_coverage_status": "ready",
            "final_v1_ready_count": 2,
            "without_ready_final_v1_count": 3,
            "multimodal_present_count": 2,
            "lexicon_only_count": 2,
            "not_confirmed_count": 1,
        }

        by_id = {item["evidence_id"]: item for item in body["items"]}
        assert by_id[1]["matched_terms"] == []
        assert by_id[1]["method"] == "final_v1_modalities"
        assert by_id[1]["sources"] == ["final_v1"]
        assert by_id[1]["modalities"] == ["visual", "audio"]
        assert "画面/口播" in by_id[1]["reason"]

        assert by_id[2]["method"] == "lexicon_fallback"
        assert by_id[2]["sources"] == ["title_lexicon"]
        assert by_id[2]["modalities"] == []
        assert by_id[2]["matched_terms"] == ["viltrox"]

        assert by_id[3]["sources"] == ["final_v1"]
        assert by_id[3]["modalities"] == ["subtitle"]
        assert "字幕" in by_id[3]["reason"]

        assert by_id[5]["sources"] == ["channel_lexicon"]
        assert "频道名词表" in by_id[5]["reason"]
        assert 4 not in by_id
        assert 6 not in by_id

        encoded = json.dumps(body, ensure_ascii=False)
        assert "secret-" not in encoded
        assert "timestamp" not in encoded
        assert "未深析内容保留未确认" in body["reason"]
        assert "不判为不相关" in body["reason"]
    finally:
        conn.close()


def test_viltrox_mentions_empty_keeps_unanalyzed_rows_undetermined() -> None:
    conn = _populated_conn()
    try:
        conn.execute("DELETE FROM vkpi_analysis_cache")
        conn.execute("UPDATE vkpi_kol_video_evidence SET video_title='generic', channel_name='generic'")
        conn.commit()
        changes_before = conn.total_changes

        body = daily_digest._viltrox_mentions(conn, sid=7, since="2026-08-01")

        assert conn.total_changes == changes_before
        assert body["status"] == "empty"
        assert body["method"] == "final_v1_modalities_present+lexicon_fallback_v2"
        assert body["sources"] == []
        assert body["modalities"] == []
        assert body["coverage"]["candidate_count"] == 5
        assert body["coverage"]["without_ready_final_v1_count"] == 5
        assert body["coverage"]["not_confirmed_count"] == 5
        assert "5 条尚无 ready final_v1" in body["reason"]
        assert "未深析或深析未就绪" in body["reason"]
        assert "保留未确认" in body["reason"]
        assert "不判为不相关" in body["reason"]
    finally:
        conn.close()


def test_viltrox_mentions_missing_cache_schema_does_not_infer_not_related() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        _schema(conn, with_cache=False)
        conn.execute(
            "INSERT INTO vkpi_kol_pool (id, handle, display_name, platform) VALUES (1, 'creator', 'Creator', 'youtube')"
        )
        conn.execute("INSERT INTO vkpi_kol_pool_favorites (id, kol_pool_id, staff_id) VALUES (1, 1, 7)")
        conn.execute(
            """
            INSERT INTO vkpi_kol_video_evidence (
                id, kol_pool_id, video_title, title, channel_name, content_url,
                view_count, is_active, created_at
            ) VALUES (1, 1, 'generic', '', 'generic', 'https://example.test/video', 1, 1, '2026-08-20')
            """
        )
        conn.commit()
        changes_before = conn.total_changes

        body = daily_digest._viltrox_mentions(conn, sid=7, since="2026-08-01")

        assert conn.total_changes == changes_before
        assert body["status"] == "empty"
        assert body["coverage"]["final_v1_coverage_status"] == "unavailable"
        assert body["coverage"]["final_v1_ready_count"] is None
        assert body["coverage"]["without_ready_final_v1_count"] is None
        assert "覆盖暂无法读取" in body["reason"]
        assert "不判为不相关" in body["reason"]
    finally:
        conn.close()


def test_digest_filters_inactive_rows_before_bounded_limit() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        _schema(conn)
        conn.execute(
            "INSERT INTO vkpi_kol_pool (id, handle, display_name, platform) "
            "VALUES (1, 'watched', 'Watched Creator', 'youtube')"
        )
        conn.execute(
            "INSERT INTO vkpi_kol_pool (id, handle, display_name, platform) "
            "VALUES (2, 'not-watched', 'Other Employee Creator', 'youtube')"
        )
        conn.execute(
            "INSERT INTO vkpi_kol_pool_favorites (id, kol_pool_id, staff_id) VALUES (1, 1, 7)"
        )
        # The old query applied LIMIT 400 before Python's active filter. These
        # high-view inactive rows therefore hid the one valid watched video.
        conn.executemany(
            """
            INSERT INTO vkpi_kol_video_evidence (
                id, kol_pool_id, video_title, title, channel_name, content_url,
                view_count, is_active, created_at
            ) VALUES (?, 1, 'inactive', '', 'generic', 'https://example.test/inactive',
                      ?, 0, '2026-08-20T00:00:00Z')
            """,
            [(value, 10_000 + value) for value in range(2, 402)],
        )
        conn.execute(
            """
            INSERT INTO vkpi_kol_video_evidence (
                id, kol_pool_id, video_title, title, channel_name, content_url,
                view_count, is_active, created_at
            ) VALUES (1, 1, 'Viltrox active review', '', 'generic',
                      'https://example.test/active', 1, 1, '2026-08-20T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO vkpi_kol_video_evidence (
                id, kol_pool_id, video_title, title, channel_name, content_url,
                view_count, is_active, created_at
            ) VALUES (999, 2, 'Viltrox outside this employee scope', '', 'generic',
                      'https://example.test/outside', 999999, 1, '2026-08-20T00:00:00Z')
            """
        )
        conn.commit()

        mentions = daily_digest._viltrox_mentions(conn, sid=7, since="2026-08-01")
        new_videos = daily_digest._new_videos(conn, sid=7, since="2026-08-01")

        assert mentions["status"] == "ready"
        assert mentions["count"] == 1
        assert mentions["coverage"]["candidate_count"] == 1
        assert [item["evidence_id"] for item in mentions["items"]] == [1]
        assert new_videos["status"] == "ready"
        assert new_videos["count"] == 1
        assert [item["evidence_id"] for item in new_videos["items"]] == [1]
    finally:
        conn.close()
