"""市场之声「反馈流」契约测试(零 DB 依赖)。

两层断言:
  1. SQL 常量静态审查:参数化占位(?)/ LIMIT 封顶常量 / identity CASE 三分支 /
     显示层宪法(author_handle、author_id、raw_data_json 绝不入 SELECT)/
     compat 红线(SQL 零字面 percent);
  2. mock conn 跑 get_voice_feed:返回体 {items,total,offset,limit} 键完整、
     item 契约键逐一齐全、limit 封顶 50、offset 负数归 0、identity 过滤
     参数化下推、非法 identity 抛 ValueError;
  3. category 词族下钻(纯增量):合法值 = COMPLAINT_CATEGORIES 六类 key +
     'wishlist';Python 层词族过滤 + 双层封顶(SQL 取 CATEGORY_SCAN_LIMIT 条,
     切片在 Python);total=命中数;响应增量回 category 键;非法值 ValueError。
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
        "identity_id": 789,
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
    # 溯源身份跳锚(identity_id):kol=ev.kol_pool_id / owned=ec.id / 其余 NULL
    assert "WHEN c.post_table = 'vkpi_kol_video_evidence' THEN ev.kol_pool_id" in sql
    assert "WHEN c.post_table = 'vkpi_employee_channels' THEN ec.id" in sql
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
        "identity", "identity_ref", "identity_id", "post_url", "likes", "created_at", "prov",
    }
    assert item["id"] == 123
    assert item["source_table"] == "vkpi_comments"
    assert item["platform"] == "youtube"
    assert item["language"] == "en"
    assert item["identity"] == "kol"
    assert item["identity_ref"] == "some_kol_handle"
    # 溯源身份跳锚:kol=vkpi_kol_pool.id / owned=vkpi_employee_channels.id / user=null
    assert item["identity_id"] == 789
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


# ── 3. category 词族下钻(纯增量参数)────────────────────────────────────


def test_category_families_cover_complaints_plus_wishlist():
    from app.domains.market.market_voice import COMPLAINT_CATEGORIES

    families = voice_feed._category_families()
    expected = {key for key, _label, _terms in COMPLAINT_CATEGORIES}
    expected.add(voice_feed.WISHLIST_CATEGORY)
    assert set(families.keys()) == expected
    # 词表来自 market_voice 单一真源(lexicon_v0),不复制不漂移
    for key, _label, terms in COMPLAINT_CATEGORIES:
        assert families[key] == tuple(terms)


def test_category_filter_python_layer_and_double_cap():
    """词族过滤在 Python 层:SQL 下推 CATEGORY_SCAN_LIMIT/0,total=词族命中数。"""
    rows = [
        _sample_row(id=1, comment_text="Autofocus keeps hunting on my a7iv"),
        _sample_row(id=2, comment_text="Beautiful colors, love this lens"),
        _sample_row(id=3, comment_text="对焦有点拉风箱"),
    ]
    conn = _FakeConn(rows=rows, total=999)
    body = voice_feed.get_voice_feed(category="autofocus", limit=20, conn=conn)

    # SQL 层:不做 COUNT(总数由 Python 命中数给出),分页参数=扫描封顶/偏移 0
    assert len(conn.calls) == 1
    select_sql, select_params = conn.calls[0]
    assert "LIMIT ? OFFSET ?" in select_sql
    assert select_params[-2:] == (voice_feed.CATEGORY_SCAN_LIMIT, 0)
    # SQL 零 LIKE / 零字面 percent(词族匹配全在 Python)
    assert "%" not in select_sql
    assert " LIKE " not in f" {select_sql.upper()} ".replace("\n", " ")

    # Python 层:命中 2/3(词族含中英词);响应契约 + 增量 category 键
    assert body["total"] == 2
    assert [it["id"] for it in body["items"]] == [1, 3]
    assert body["category"] == "autofocus"
    assert set(body.keys()) == {"items", "total", "offset", "limit", "category"}


def test_category_filter_offset_slice_in_python():
    rows = [
        _sample_row(id=i, comment_text=f"the price is too expensive #{i}")
        for i in range(1, 6)
    ]
    conn = _FakeConn(rows=rows, total=0)
    body = voice_feed.get_voice_feed(category="price", offset=2, limit=2, conn=conn)
    assert body["total"] == 5
    assert [it["id"] for it in body["items"]] == [3, 4]


def test_category_wishlist_and_combined_filters_push_down():
    """wishlist 词族合法;identity/sentiment 既有过滤照常参数化下推同一条 SQL。"""
    rows = [
        _sample_row(id=1, comment_text="please make a 135mm f1.8"),
        _sample_row(id=2, comment_text="sharp and fast"),
    ]
    conn = _FakeConn(rows=rows, total=0)
    body = voice_feed.get_voice_feed(
        category="wishlist", sentiment="negative", identity="kol", conn=conn
    )
    assert body["total"] == 1 and body["items"][0]["id"] == 1
    select_sql, select_params = conn.calls[0]
    assert "c.post_table = ?" in select_sql and "s.sentiment = ?" in select_sql
    assert "vkpi_kol_video_evidence" in select_params
    assert "negative" in select_params


def test_category_scan_cap_note_honest():
    """扫描触顶 → 响应带 note 如实说明 total 只覆盖封顶范围(绝不装全量)。"""
    rows = [_sample_row(id=1, comment_text="firmware update please")]

    class _CapConn(_FakeConn):
        def execute(self, sql, params=()):
            self.calls.append((sql, tuple(params)))
            return _FakeResult(rows * voice_feed.CATEGORY_SCAN_LIMIT)

    body = voice_feed.get_voice_feed(category="compatibility", conn=_CapConn())
    assert "note" in body and str(voice_feed.CATEGORY_SCAN_LIMIT) in body["note"]


def test_invalid_category_raises_value_error():
    conn = _FakeConn()
    with pytest.raises(ValueError):
        voice_feed.get_voice_feed(category="alien_topic", conn=conn)
    assert conn.calls == []  # 非法值挡在 SQL 之前


def test_no_category_keeps_legacy_contract_shape():
    """不带 category 的旧调用形状零变化(不长出新键,老前端零感知)。"""
    conn = _FakeConn(rows=[_sample_row()], total=1)
    body = voice_feed.get_voice_feed(conn=conn)
    assert set(body.keys()) == {"items", "total", "offset", "limit"}
