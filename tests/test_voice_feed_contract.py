"""市场之声「反馈流」契约测试(零 DB 依赖)。

两层断言:
  1. SQL 常量静态审查:参数化占位(?)/ LIMIT 封顶常量 / identity CASE 三分支 /
     显示层宪法(author_handle、author_id、raw_data_json 绝不入 SELECT)/
     compat 红线(SQL 零字面 percent);
  2. mock conn 跑 get_voice_feed:返回体 {items,total,offset,limit} 键完整、
     item 契约键逐一齐全、limit 封顶 50、offset 负数归 0、identity 过滤
     参数化下推、非法 identity 抛 ValueError。
红线:纯读契约,不触真库,不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.market import voice_feed  # noqa: E402


# ── mock conn(仿 tests 现有 _FakeConn 风格,记录每次 execute 的 SQL+参数)──


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, rows=None, total=0):
        self.rows = rows or []
        self.total = total
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        if "COUNT(" in sql:
            return _FakeResult([{"total": self.total}])
        return _FakeResult(self.rows)


def _sample_row(**overrides):
    row = {
        "id": 123,
        "platform": "youtube",
        "comment_text": "Great lens, autofocus is fast.",
        "language_detected": "en",
        "likes_count": 12,
        "created_at": datetime(2026, 7, 11, 2, 41, 0, tzinfo=timezone.utc),
        "fetched_at": datetime(2026, 7, 11, 3, 0, 0, tzinfo=timezone.utc),
        "post_table": "vkpi_kol_video_evidence",
        "post_id": 456,
        "identity": "kol",
        "identity_ref": "some_kol_handle",
        "post_url": "https://youtube.com/watch?v=abc",
        # 内部列混进行里也不许泄漏(显示层宪法回归)
        "author_handle": "should_never_leak",
        "author_id": "u-999",
        "raw_data_json": "{}",
    }
    row.update(overrides)
    return row


ALL_SQL = (
    voice_feed.FEED_SELECT_SQL + voice_feed.FEED_COUNT_SQL + voice_feed.FEED_ORDER_SQL
)


# ── 1. SQL 常量静态审查 ─────────────────────────────────────────────────


def test_sql_constants_are_parameterized():
    assert "LIMIT ? OFFSET ?" in voice_feed.FEED_ORDER_SQL
    for fragment in voice_feed._IDENTITY_FILTER_SQL.values():
        assert "?" in fragment
    assert "?" in voice_feed._SENTIMENT_FILTER_SQL
    # compat 红线:SQL 零字面 percent(不用 LIKE 关键字;likes_count 列名不算)
    assert "%" not in ALL_SQL
    assert " LIKE " not in f" {ALL_SQL.upper()} ".replace("\n", " ")


def test_sql_identity_case_branches():
    sql = voice_feed.FEED_SELECT_SQL
    assert "CASE" in sql
    assert "WHEN c.post_table = 'vkpi_employee_channels' THEN 'owned'" in sql
    assert "WHEN c.post_table = 'vkpi_kol_video_evidence' THEN 'kol'" in sql
    assert "ELSE 'user'" in sql
    # 三路 JOIN 齐:官号 / 视频证据 / KOL 池(identity_ref 数据链)
    assert "LEFT JOIN vkpi_employee_channels" in sql
    assert "LEFT JOIN vkpi_kol_video_evidence" in sql
    assert "LEFT JOIN vkpi_kol_pool" in sql


def test_sql_never_selects_private_columns():
    """显示层宪法:author_handle / author_id / raw_data_json 不进 SELECT。"""
    for forbidden in ("author_handle", "author_id", "raw_data_json"):
        assert forbidden not in ALL_SQL


def test_limit_cap_constant():
    assert voice_feed.MAX_LIMIT == 50
    assert voice_feed.DEFAULT_LIMIT == 20


# ── 2. mock conn 跑 get_voice_feed ──────────────────────────────────────


def test_feed_response_and_item_contract_keys():
    conn = _FakeConn(rows=[_sample_row()], total=12703)
    body = voice_feed.get_voice_feed(offset=0, limit=20, conn=conn)

    assert set(body.keys()) == {"items", "total", "offset", "limit"}
    assert body["total"] == 12703
    assert body["offset"] == 0
    assert body["limit"] == 20

    item = body["items"][0]
    assert set(item.keys()) == {
        "id", "source_table", "platform", "text", "language",
        "identity", "identity_ref", "post_url", "likes", "created_at", "prov",
    }
    assert item["id"] == 123
    assert item["source_table"] == "vkpi_comments"
    assert item["platform"] == "youtube"
    assert item["language"] == "en"
    assert item["identity"] == "kol"
    assert item["identity_ref"] == "some_kol_handle"
    assert item["post_url"] == "https://youtube.com/watch?v=abc"
    assert item["likes"] == 12
    assert item["created_at"] == "2026-07-11T02:41:00Z"
    assert set(item["prov"].keys()) == {"fetched_at", "post_table", "post_id"}
    assert item["prov"]["post_table"] == "vkpi_kol_video_evidence"
    assert item["prov"]["post_id"] == 456
    assert item["prov"]["fetched_at"] == "2026-07-11T03:00:00Z"


def test_feed_item_never_leaks_private_fields():
    conn = _FakeConn(rows=[_sample_row()], total=1)
    item = voice_feed.get_voice_feed(conn=conn)["items"][0]
    flat = repr(item)
    assert "should_never_leak" not in flat
    assert "u-999" not in flat
    assert "raw_data_json" not in flat


def test_feed_text_truncated_to_400_chars():
    conn = _FakeConn(rows=[_sample_row(comment_text="x" * 1000)], total=1)
    item = voice_feed.get_voice_feed(conn=conn)["items"][0]
    assert len(item["text"]) == voice_feed.TEXT_MAX_CHARS == 400


def test_feed_post_url_empty_becomes_null():
    conn = _FakeConn(rows=[_sample_row(post_url=None), _sample_row(post_url="")], total=2)
    items = voice_feed.get_voice_feed(conn=conn)["items"]
    assert items[0]["post_url"] is None
    assert items[1]["post_url"] is None


def test_limit_capped_at_50_and_offset_floor_zero():
    conn = _FakeConn(rows=[], total=0)
    body = voice_feed.get_voice_feed(offset=-5, limit=999, conn=conn)
    assert body["limit"] == 50
    assert body["offset"] == 0
    # 下推到 SQL 的分页参数也必须是封顶后的值
    select_sql, select_params = conn.calls[-1]
    assert "LIMIT ? OFFSET ?" in select_sql
    assert select_params[-2:] == (50, 0)


def test_identity_filter_pushed_down_parameterized():
    conn = _FakeConn(rows=[], total=0)
    voice_feed.get_voice_feed(identity="owned", conn=conn)
    count_sql, count_params = conn.calls[0]
    select_sql, select_params = conn.calls[1]
    for sql in (count_sql, select_sql):
        assert "c.post_table = ?" in sql
    assert "vkpi_employee_channels" in count_params
    assert "vkpi_employee_channels" in select_params

    conn2 = _FakeConn(rows=[], total=0)
    voice_feed.get_voice_feed(identity="user", conn=conn2)
    _, params = conn2.calls[0]
    assert "vkpi_employee_channels" in params and "vkpi_kol_video_evidence" in params


def test_sentiment_filter_pushed_down_parameterized():
    conn = _FakeConn(rows=[], total=0)
    body = voice_feed.get_voice_feed(sentiment="positive", conn=conn)
    count_sql, count_params = conn.calls[0]
    assert "s.sentiment = ?" in count_sql
    assert "positive" in count_params
    # 情感表当前为空 → 如实空结果,不编造
    assert body["items"] == [] and body["total"] == 0


def test_invalid_identity_raises_value_error():
    conn = _FakeConn()
    with pytest.raises(ValueError):
        voice_feed.get_voice_feed(identity="alien", conn=conn)
    assert conn.calls == []  # 非法值挡在 SQL 之前
