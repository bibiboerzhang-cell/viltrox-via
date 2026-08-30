"""Characterization lock for snapshot_collector._insert_posts (CC 降复杂度车道).

Golden values below were captured by executing the ORIGINAL implementation
(HEAD 939f9f5d4) with a recording connection. The refactor must reproduce every
tuple byte-for-byte: same SQL, same parameter order, same or-chain fallback
semantics (0/"" skipped, last operand wins), same commit cadence.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.industry import snapshot_collector


FIXED_NOW = "2026-08-30T00:00:00Z"

EXPECTED_SQL = (
    "INSERT INTO vkpi_industry_posts (post_uid, account_id, platform, platform_post_id, "
    "post_url, thumbnail_url, video_url, media_type, duration_seconds, video_source, "
    "title, caption, published_at, views, likes, comments, shares, saves, hashtags_json, "
    "mentions_json, detected_products_json, content_pillar, sentiment, raw_platform_data, "
    "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(post_uid) DO UPDATE SET post_url=excluded.post_url, "
    "thumbnail_url=excluded.thumbnail_url, video_url=excluded.video_url, "
    "media_type=excluded.media_type, duration_seconds=excluded.duration_seconds, "
    "video_source=excluded.video_source, title=excluded.title, caption=excluded.caption, "
    "published_at=excluded.published_at, views=excluded.views, likes=excluded.likes, "
    "comments=excluded.comments, shares=excluded.shares, saves=excluded.saves, "
    "raw_platform_data=excluded.raw_platform_data"
)


class _RecordingConn:
    def __init__(self) -> None:
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: Any = ()) -> "_RecordingConn":
        self.executes.append((" ".join(sql.split()), tuple(params)))
        return self

    def commit(self) -> None:
        self.commits += 1

    def fetchone(self) -> Any:
        return None

    def fetchall(self) -> list[Any]:
        return []


@pytest.fixture()
def rig(monkeypatch: pytest.MonkeyPatch):
    conn = _RecordingConn()
    ensured: list[Any] = []
    monkeypatch.setattr(snapshot_collector, "get_conn", lambda: conn)
    monkeypatch.setattr(snapshot_collector, "_utcnow", lambda: FIXED_NOW)
    monkeypatch.setattr(snapshot_collector, "_ensure_post_media_columns", lambda c: ensured.append(c))
    monkeypatch.setattr(snapshot_collector.secrets, "token_hex", lambda n: f"tok{n:02d}fixed")
    return conn, ensured


YT_VIDEO = {
    "id": {"videoId": "yt-001"},
    "snippet": {
        "title": "Viltrox 85mm Review #AF85",
        "description": "Best #LENS-2026 ever 你好",
        "publishedAt": "2026-08-01T00:00:00Z",
        "thumbnails": {
            "high": {"url": "https://img.example/high.jpg"},
            "default": {"url": "https://img.example/default.jpg"},
        },
    },
    "statistics": {"viewCount": "1200", "likeCount": "34", "commentCount": "5"},
    "contentDetails": {"duration": "PT1H2M3S"},
}
EMPTY_VIDEO = {"foo": "bar"}
EDGE_VIDEO = {
    "id": "edge-005",
    "statistics": {
        "viewCount": 0,
        "views": "",
        "view": None,
        "view_count": 250,
        "likeCount": 0,
        "likes": 0,
        "like_count": 0,
        "likesCount": 0,
        "likedCount": 0,
        "diggCount": 0,
    },
    "post_url": "https://example.com/p/edge-005",
    "media_type": "carousel",
}
TT_VIDEO = {
    "id": "tt-002",
    "title": "tt title",
    "caption": "tt caption #TT-01",
    "timestamp": 1700000000,
    "stats": {"playCount": 999, "diggCount": 88, "shareCount": 7, "collectCount": 3, "commentCount": 0},
    "video": {"playAddr": "https://cdn.example/tt.mp4", "duration": 15},
    "media_type": "short",
    "url": "https://tiktok.example/@x/video/tt-002",
}
IG_VIDEO = {
    "videoId": "ig-003",
    "caption": "ig cap",
    "displayUrl": "https://img.example/ig.jpg",
    "likesCount": 55,
    "commentsCount": 6,
    "videoViewCount": 777,
    "type": "photo",
    "published_at": "2026-07-31",
    "video_source": "manual",
    "duration_seconds": "44",
}

YT_RAW_JSON = (
    '{"id": {"videoId": "yt-001"}, "snippet": {"title": "Viltrox 85mm Review #AF85", '
    '"description": "Best #LENS-2026 ever 你好", "publishedAt": "2026-08-01T00:00:00Z", '
    '"thumbnails": {"high": {"url": "https://img.example/high.jpg"}, '
    '"default": {"url": "https://img.example/default.jpg"}}}, '
    '"statistics": {"viewCount": "1200", "likeCount": "34", "commentCount": "5"}, '
    '"contentDetails": {"duration": "PT1H2M3S"}}'
)
EDGE_RAW_JSON = (
    '{"id": "edge-005", "statistics": {"viewCount": 0, "views": "", "view": null, '
    '"view_count": 250, "likeCount": 0, "likes": 0, "like_count": 0, "likesCount": 0, '
    '"likedCount": 0, "diggCount": 0}, "post_url": "https://example.com/p/edge-005", '
    '"media_type": "carousel"}'
)
TT_RAW_JSON = (
    '{"id": "tt-002", "title": "tt title", "caption": "tt caption #TT-01", '
    '"timestamp": 1700000000, "stats": {"playCount": 999, "diggCount": 88, "shareCount": 7, '
    '"collectCount": 3, "commentCount": 0}, "video": {"playAddr": "https://cdn.example/tt.mp4", '
    '"duration": 15}, "media_type": "short", "url": "https://tiktok.example/@x/video/tt-002"}'
)
IG_RAW_JSON = (
    '{"videoId": "ig-003", "caption": "ig cap", "displayUrl": "https://img.example/ig.jpg", '
    '"likesCount": 55, "commentsCount": 6, "videoViewCount": 777, "type": "photo", '
    '"published_at": "2026-07-31", "video_source": "manual", "duration_seconds": "44"}'
)


def test_youtube_rows_locked(rig) -> None:
    conn, ensured = rig
    count = snapshot_collector._insert_posts(
        {"id": 41, "platform": "youtube"},
        {"videos": [YT_VIDEO, EMPTY_VIDEO, EDGE_VIDEO]},
    )
    assert count == 3
    assert conn.commits == 1
    assert ensured == [conn]
    assert [sql for sql, _ in conn.executes] == [EXPECTED_SQL] * 3
    assert conn.executes[0][1] == (
        "post-youtube-41-yt-001", 41, "youtube", "yt-001",
        "https://www.youtube.com/watch?v=yt-001", "https://img.example/high.jpg",
        "", "image", 3723, "",
        "Viltrox 85mm Review #AF85", "Best #LENS-2026 ever 你好",
        "2026-08-01T00:00:00Z", 1200, 34, 5, None, None,
        '["#AF85", "#LENS"]', "{}", "{}", "", "", YT_RAW_JSON, FIXED_NOW,
    )
    # 空视频:platform_post_id 走 secrets.token_hex(8) 兜底,全部字段落默认。
    assert conn.executes[1][1] == (
        "post-youtube-41-tok08fixed", 41, "youtube", "tok08fixed",
        "https://www.youtube.com/watch?v=tok08fixed", "", "", "", None, "",
        "", "", "", None, None, None, None, None,
        "{}", "{}", "{}", "", "", '{"foo": "bar"}', FIXED_NOW,
    )
    # or 链语义:0/"" 被跳过(views 取 view_count=250),全 0 时取最后一个操作数(likes=0),
    # 完全缺席时归 None(comments/shares/saves)。
    assert conn.executes[2][1] == (
        "post-youtube-41-edge-005", 41, "youtube", "edge-005",
        "https://example.com/p/edge-005", "", "", "carousel", None, "",
        "", "", "", 250, 0, None, None, None,
        "{}", "{}", "{}", "", "", EDGE_RAW_JSON, FIXED_NOW,
    )


def test_tiktok_and_flat_rows_locked(rig) -> None:
    conn, _ = rig
    count = snapshot_collector._insert_posts(
        {"id": 7, "platform": "tiktok"},
        {"videos": [TT_VIDEO, IG_VIDEO]},
    )
    assert count == 2
    assert conn.commits == 1
    assert conn.executes[0][1] == (
        "post-tiktok-7-tt-002", 7, "tiktok", "tt-002",
        "https://tiktok.example/@x/video/tt-002", "",
        "https://cdn.example/tt.mp4", "video", 15, "apify_cdn",
        "tt title", "tt caption #TT-01", 1700000000,
        999, 88, None, 7, 3,
        '["#TT"]', "{}", "{}", "", "", TT_RAW_JSON, FIXED_NOW,
    )
    assert conn.executes[1][1] == (
        "post-tiktok-7-ig-003", 7, "tiktok", "ig-003",
        "", "https://img.example/ig.jpg", "", "image", 44, "manual",
        "", "ig cap", "2026-07-31", 777, 55, 6, None, None,
        "{}", "{}", "{}", "", "", IG_RAW_JSON, FIXED_NOW,
    )


def test_missing_account_fields_default_platform_and_id(rig) -> None:
    conn, _ = rig
    count = snapshot_collector._insert_posts(
        {"id": None, "platform": None},
        {"videos": [IG_VIDEO]},
    )
    assert count == 1
    assert conn.executes[0][1] == (
        "post-youtube-0-ig-003", 0, "youtube", "ig-003",
        "https://www.youtube.com/watch?v=ig-003", "https://img.example/ig.jpg",
        "", "image", 44, "manual",
        "", "ig cap", "2026-07-31", 777, 55, 6, None, None,
        "{}", "{}", "{}", "", "", IG_RAW_JSON, FIXED_NOW,
    )


def test_limit_semantics_locked(rig) -> None:
    conn, ensured = rig
    # limit<0 → 0 条,连 get_conn/_ensure_post_media_columns 都不触。
    assert snapshot_collector._insert_posts({"id": 41, "platform": "youtube"}, {"videos": [YT_VIDEO] * 5}, limit=-1) == 0
    assert conn.executes == [] and conn.commits == 0 and ensured == []
    # limit>200 clamp 到 200;整批只 commit 一次。
    many = {"videos": [dict(TT_VIDEO, id=f"v{i}") for i in range(250)]}
    assert snapshot_collector._insert_posts({"id": 41, "platform": "youtube"}, many, limit=300) == 200
    assert len(conn.executes) == 200 and conn.commits == 1
    # limit=0 是 falsy → 回落默认 100。
    conn2 = _RecordingConn()
    some = {"videos": [dict(TT_VIDEO, id=f"v{i}") for i in range(150)]}
    prior = len(conn.executes)
    assert snapshot_collector._insert_posts({"id": 41, "platform": "youtube"}, some, limit=0) == 100
    assert len(conn.executes) == prior + 100
    del conn2


def test_empty_video_list_returns_zero(rig) -> None:
    conn, ensured = rig
    assert snapshot_collector._insert_posts({"id": 41, "platform": "youtube"}, {"videos": []}) == 0
    assert conn.executes == [] and conn.commits == 0 and ensured == []
