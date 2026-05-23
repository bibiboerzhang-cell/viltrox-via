from __future__ import annotations

from app.services.vkpi import channel_comments


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *_args, **_kwargs):
        return _Rows(self.rows)


def test_comment_contract_marks_partial_cached_bodies():
    contract = channel_comments._comment_contract(
        declared=120,
        cached=30,
        cap=300,
        collect_supported=True,
    )

    assert contract == {
        "declared": 120,
        "cached": 30,
        "cap": 300,
        "status": "partial",
    }


def test_comment_contract_marks_cap_without_claiming_complete():
    contract = channel_comments._comment_contract(
        declared=500,
        cached=300,
        cap=300,
        collect_supported=True,
    )

    assert contract["status"] == "capped"
    assert contract["declared"] == 500
    assert contract["cached"] == 300


def test_comment_contract_distinguishes_unsupported_from_empty():
    unsupported = channel_comments._comment_contract(
        declared=42,
        cached=0,
        cap=300,
        collect_supported=False,
    )
    empty = channel_comments._comment_contract(
        declared=0,
        cached=0,
        cap=300,
        collect_supported=True,
    )

    assert unsupported["status"] == "not_supported"
    assert empty["status"] == "none_declared"


def test_channel_post_comments_exposes_contract_fields(monkeypatch):
    from app.services.vkpi import comments_collector

    monkeypatch.setattr(comments_collector, "ensure_vkpi_comments_schema", lambda: None)
    monkeypatch.setattr(channel_comments.channels, "_latest_channel_row", lambda *_args, **_kwargs: {"id": 1, "platform": "instagram"})
    monkeypatch.setattr(channel_comments.channels, "_all_posts_for_channel", lambda _row: ([], "snapshot_sample", ""))
    monkeypatch.setattr(
        channel_comments.channels,
        "_match_post",
        lambda *_args, **_kwargs: {"id": "post-1", "source_id": "post-1", "comments": 120},
    )
    monkeypatch.setattr(
        channel_comments,
        "get_conn",
        lambda: _Conn(
            [
                {
                    "id": 1,
                    "external_comment_id": "comment-1",
                    "external_post_id": "post-1",
                    "comment_text": "cached body",
                    "author_handle": "viewer",
                    "likes_count": 0,
                    "reply_count": 0,
                    "depth": 0,
                    "parent_comment_id": "",
                    "is_op": False,
                    "created_at": "2026-05-23T00:00:00Z",
                    "fetched_at": "2026-05-23T00:01:00Z",
                    "raw_data_json": "{}",
                }
            ]
        ),
    )

    payload = channel_comments.channel_post_comments(1, post_id="post-1", limit=50, staff={"id": 1})

    assert payload["comment_count"] == 1
    assert payload["declared_count"] == 120
    assert payload["cached_count"] == 1
    assert payload["comment_cap"] == 50
    assert payload["coverage_status"] == "partial"
    assert payload["comment_contract"] == {
        "declared": 120,
        "cached": 1,
        "cap": 50,
        "status": "partial",
    }
