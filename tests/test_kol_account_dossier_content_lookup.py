import sqlite3

from app.services.kol import account_dossier
# 重构后 _json_text_expr(方言分支)搬到 account_dossier_lookup,读该模块自己的 is_postgres_runtime;
# 测试需在新位置也 patch,否则 SQLite 用例会走 Postgres ::text 分支炸 "unrecognized token :"。
from app.services.kol import account_dossier_lookup


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            platform TEXT,
            handle TEXT,
            display_name TEXT,
            profile_url TEXT,
            linked_main_kol_id INTEGER
        );
        CREATE TABLE kols (
            id INTEGER PRIMARY KEY,
            platform TEXT,
            channel_name TEXT,
            channel_url TEXT,
            profile_url TEXT
        );
        CREATE TABLE kol_posts (
            id INTEGER PRIMARY KEY,
            kol_id INTEGER,
            snapshot_id INTEGER,
            platform TEXT,
            post_url TEXT,
            title TEXT,
            thumbnail_url TEXT,
            published_at TEXT,
            content_type TEXT,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            brand_mentions_json TEXT,
            competitor_mentions_json TEXT,
            comment_sentiment TEXT,
            raw_json TEXT,
            created_at TEXT
        );
        CREATE TABLE kol_comments (
            id INTEGER PRIMARY KEY,
            kol_id INTEGER,
            post_id INTEGER,
            platform TEXT,
            post_url TEXT,
            author_handle TEXT,
            comment_text TEXT,
            like_count INTEGER,
            sentiment TEXT,
            intent_tags_json TEXT,
            created_at TEXT
        );
        CREATE TABLE vkpi_content_posts (
            id INTEGER PRIMARY KEY,
            kol_id INTEGER,
            platform TEXT,
            post_url TEXT,
            title TEXT,
            thumbnail_url TEXT,
            published_at TEXT,
            content_type TEXT,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            shares INTEGER,
            metadata_json TEXT,
            created_at TEXT
        );
        """
    )
    return conn


def test_pool_id_does_not_fall_back_to_same_number_main_kol(monkeypatch):
    conn = _conn()
    conn.execute(
        "INSERT INTO vkpi_kol_pool (id, platform, handle, display_name, profile_url, linked_main_kol_id) VALUES (?,?,?,?,?,?)",
        (3603, "youtube", "nomatch", "No Match", "https://example.com/no-match", None),
    )
    conn.execute(
        "INSERT INTO kols (id, platform, channel_name, channel_url, profile_url) VALUES (?,?,?,?,?)",
        (3603, "youtube", "Other Creator", "https://example.com/other", "https://example.com/other"),
    )
    conn.execute(
        """
        INSERT INTO kol_posts
            (id, kol_id, snapshot_id, platform, post_url, title, thumbnail_url, published_at, content_type,
             views, likes, comments, shares, brand_mentions_json, competitor_mentions_json, comment_sentiment, raw_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (1, 3603, None, "youtube", "https://example.com/wrong", "wrong account", "", "", "video", 10, 0, 0, 0, "[]", "[]", "unknown", "{}", "2026-01-01"),
    )
    monkeypatch.setattr(account_dossier, "get_conn", lambda: conn)
    monkeypatch.setattr(account_dossier, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(account_dossier_lookup, "is_postgres_runtime", lambda: False)

    result = account_dossier.list_kol_posts(3603, limit=5)

    assert result["page"]["total"] == 0
    assert result["items"] == []


def test_kol_route_can_prefer_same_number_main_kol(monkeypatch):
    conn = _conn()
    conn.execute(
        "INSERT INTO vkpi_kol_pool (id, platform, handle, display_name, profile_url, linked_main_kol_id) VALUES (?,?,?,?,?,?)",
        (3603, "youtube", "nomatch", "No Match", "https://example.com/no-match", None),
    )
    conn.execute(
        "INSERT INTO kols (id, platform, channel_name, channel_url, profile_url) VALUES (?,?,?,?,?)",
        (3603, "instagram", "owen.wang12", "https://instagram.com/owen.wang12", "https://instagram.com/owen.wang12"),
    )
    conn.execute(
        """
        INSERT INTO kol_posts
            (id, kol_id, snapshot_id, platform, post_url, title, thumbnail_url, published_at, content_type,
             views, likes, comments, shares, brand_mentions_json, competitor_mentions_json, comment_sentiment, raw_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (1, 3603, None, "instagram", "https://example.com/right", "Viltrox reel", "", "", "video", 10, 0, 0, 0, '["viltrox"]', "[]", "unknown", "{}", "2026-01-01"),
    )
    monkeypatch.setattr(account_dossier, "get_conn", lambda: conn)
    monkeypatch.setattr(account_dossier, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(account_dossier_lookup, "is_postgres_runtime", lambda: False)

    result = account_dossier.list_kol_posts(3603, limit=5, prefer_main_id=True)

    assert result["page"]["total"] == 1
    assert result["items"][0]["title"] == "Viltrox reel"


def test_pool_id_resolves_linked_main_kol_and_filters_comments_by_post_url(monkeypatch):
    conn = _conn()
    conn.execute(
        "INSERT INTO vkpi_kol_pool (id, platform, handle, display_name, profile_url, linked_main_kol_id) VALUES (?,?,?,?,?,?)",
        (2742, "instagram", "viltrox", "Viltrox", "https://instagram.com/viltrox", 110),
    )
    conn.execute(
        "INSERT INTO kols (id, platform, channel_name, channel_url, profile_url) VALUES (?,?,?,?,?)",
        (110, "instagram", "Viltrox", "https://instagram.com/viltrox", "https://instagram.com/viltrox"),
    )
    conn.execute(
        """
        INSERT INTO kol_posts
            (id, kol_id, snapshot_id, platform, post_url, title, thumbnail_url, published_at, content_type,
             views, likes, comments, shares, brand_mentions_json, competitor_mentions_json, comment_sentiment, raw_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (2, 110, None, "instagram", "https://example.com/a", "Viltrox LAB", "", "", "image", 20, 2, 1, 0, '["viltrox"]', "[]", "positive", "{}", "2026-01-02"),
    )
    conn.executemany(
        """
        INSERT INTO kol_comments
            (id, kol_id, post_id, platform, post_url, author_handle, comment_text, like_count, sentiment, intent_tags_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (10, 110, 2, "instagram", "https://example.com/a", "user_a", "Nice lens", 3, "positive", "[]", "2026-01-03"),
            (11, 110, 2, "instagram", "https://example.com/b", "user_b", "Other post", 9, "neutral", "[]", "2026-01-04"),
        ],
    )
    monkeypatch.setattr(account_dossier, "get_conn", lambda: conn)
    monkeypatch.setattr(account_dossier, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(account_dossier_lookup, "is_postgres_runtime", lambda: False)

    posts = account_dossier.list_kol_posts(2742, limit=5)
    comments = account_dossier.list_kol_comments(2742, limit=5, post_url="https://example.com/a/")

    assert posts["page"]["total"] == 1
    assert posts["items"][0]["title"] == "Viltrox LAB"
    assert comments["page"]["total"] == 1
    assert comments["items"][0]["comment_text"] == "Nice lens"


def test_content_post_fallback_is_sqlite_compatible(monkeypatch):
    conn = _conn()
    conn.execute(
        "INSERT INTO vkpi_kol_pool (id, platform, handle, display_name, profile_url, linked_main_kol_id) VALUES (?,?,?,?,?,?)",
        (3015, "youtube", "viltrox", "Viltrox", "https://youtube.com/@viltrox", 500),
    )
    conn.execute(
        "INSERT INTO vkpi_content_posts (id, kol_id, platform, post_url, title, thumbnail_url, published_at, content_type, views, likes, comments, shares, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (20, 500, "youtube", "https://example.com/video", "Viltrox launch video", "", "2026-01-05", "video", 100, 8, 2, 1, "{}", "2026-01-05"),
    )
    monkeypatch.setattr(account_dossier, "get_conn", lambda: conn)
    monkeypatch.setattr(account_dossier, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(account_dossier_lookup, "is_postgres_runtime", lambda: False)

    result = account_dossier.list_kol_posts(3015, limit=5)

    assert result["page"]["total"] == 1
    assert result["items"][0]["brand_mentions_json"] == '["viltrox"]'
