"""预估 ↔ 召回腿 **逐组对拍**(2026-08-26)。

对抗复核坐实:预估只照着逐人三态判定算数,而真搜一遍时候选**先**要被库内取数的那条
SQL 腿捞出来。两条腿口径不一样,于是预估报出的数字搜索兑现不了 ——「放宽语言能多回来
85 人」真搜一个也回不来。

本文件是那件事的**验收**,做法刻意笨:

1. 造一个把所有坑都摆进去的固定盘(同义词落差 United States / USA / 美国 / US、
   语言地区后缀 en-US、首尾带空白的值、国家没填、语言没填、粉丝没填、平台没填);
2. 每一组筛选**真跑一遍召回腿**:调线上那个 ``lexical_recall_candidates``
   (它自己拼硬筛 SQL),再把捞回来的人逐个过线上那个 ``_candidate_filter_verdict``;
3. 拿这个人数与 ``estimate_pool_yield`` 报的数**逐组对拍**,对不上就红。

另有两道 pin:召回腿 SQL 的字面片段、以及 ``profile_recall`` 里构造绑值的那段表达式。
召回腿一改,这里当场失败 —— 而不是继续报一个对不上的数。
"""
from __future__ import annotations

import inspect
import sqlite3
from typing import Any

import pytest

from app.domains.kol import pool_yield_recall_parity as parity
from app.domains.kol import profile_recall as recall_module
from app.domains.kol import profile_recall_precision as precision
from app.domains.kol import profile_recall_storage as storage
from app.domains.kol.pool_yield_estimate import estimate_pool_yield
from app.domains.kol.profile_recall_projection import (
    _candidate_filter_verdict,
    _country_match_key,
    _language_match_key,
    _normalize_recall_filters,
)


_QUERY = "vlogger"

#: 固定盘:(条数, 平台, 国家, 语言, 粉丝)。每一行都对着一个具体的坑。
_FIXTURE: tuple[tuple[int, str, str, str, Any], ...] = (
    (20, "youtube", "United States", "en", 80000),      # 同义词落差:点 US 取不到
    (12, "youtube", "US", "English", 90000),            # 正字面
    (8, "instagram", "us", "en-US", 60000),             # 语言带地区后缀
    (10, "youtube", "", "en", 70000),                   # 国家没填
    (10, "tiktok", "US", "", 70000),                    # 语言没填
    (15, "youtube", "Japan", "ja", 120000),             # 确认别的国家
    (6, "bilibili", "美国", "zh", 40000),                # 中文国名
    (5, "youtube", "US", "en", 1000),                   # 粉丝够不上
    (4, "youtube", "US", "en", None),                   # 粉丝没填
    (3, " YouTube ", " US ", " en ", 55000),            # 首尾空白:取数腿捞不到
    (12, "", "", "", 30000),                            # 全空
    (10, "instagram", "United Kingdom", "en-GB", 200000),
)

#: 对拍用的筛选组合。刻意覆盖:空组合、四种国家写法、两种语言写法、平台、
#: 三态两档、粉丝上下限、多维叠加、内容方向。
_COMBINATIONS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("01 空组合", {}),
    ("02 国家=US", {"countries": ["US"]}),
    ("03 国家=USA", {"countries": ["USA"]}),
    ("04 国家=United States", {"countries": ["United States"]}),
    ("05 国家=美国", {"countries": ["美国"]}),
    ("06 语言=English", {"languages": ["English"]}),
    ("07 语言=en", {"languages": ["en"]}),
    ("08 平台=youtube", {"platforms": ["youtube"]}),
    ("09 US+English+5万粉", {"countries": ["US"], "languages": ["English"], "followers_min": 50000}),
    ("10 国家=US·含未知", {"countries": {"values": ["US"], "mode": "include_unknown"}}),
    (
        "11 US+语言含未知",
        {"countries": ["US"], "languages": {"values": ["en"], "mode": "include_unknown"}},
    ),
    ("12 国家=US·排除", {"countries": {"values": ["US"], "mode": "exclude"}}),
    ("13 粉丝 5万~10万", {"followers_min": 50000, "followers_max": 100000}),
    (
        "14 双平台+双国家+5万粉",
        {
            "platforms": ["youtube", "instagram"],
            "countries": ["US", "Japan"],
            "followers_min": 50000,
        },
    ),
    ("15 日本+日语", {"countries": ["Japan"], "languages": ["Japanese"]}),
    ("16 英国+en", {"countries": ["United Kingdom"], "languages": ["en"]}),
    ("17 内容方向=摄影", {"verticals": ["摄影"], "countries": ["US"]}),
)


class _Conn:
    """只读连接;顺便把 SQL 留档,好证明对拍两边打的是同一个库。"""

    def __init__(self, raw: sqlite3.Connection) -> None:
        self._raw = raw
        self.statements: list[str] = []

    def execute(self, sql: str, params: Any = ()) -> Any:
        self.statements.append(" ".join(str(sql).split()))
        return self._raw.execute(sql, tuple(params))


@pytest.fixture()
def pool_conn() -> Any:
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.execute(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            duplicate_of_id INTEGER,
            platform TEXT, handle TEXT, display_name TEXT,
            country TEXT, language TEXT, followers INTEGER,
            bio TEXT, primary_topic TEXT, content_style TEXT,
            secondary_topics_json TEXT, topic_details_json TEXT, tagged_brands_json TEXT
        )
        """
    )
    # 空的作品证据表:让「取标题」那一路真跑一遍(而不是掉进取数失败的告警分支)。
    raw.execute(
        "CREATE TABLE vkpi_kol_video_evidence ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, kol_pool_id INTEGER,"
        " title TEXT, video_title TEXT, is_active INTEGER DEFAULT 1)"
    )
    index = 0
    for count, platform, country, language, followers in _FIXTURE:
        for _ in range(count):
            index += 1
            raw.execute(
                "INSERT INTO vkpi_kol_pool (platform, handle, display_name, country, language,"
                " followers, bio, primary_topic, content_style) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    platform,
                    f"handle{index}",
                    f"{_QUERY} {index}",
                    country,
                    language,
                    followers,
                    "portrait photography and camera reviews",
                    "摄影",
                    "review",
                ),
            )
    raw.commit()
    return _Conn(raw)


def _pool_rows(conn: _Conn) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT * FROM vkpi_kol_pool", ()).fetchall()]


# ── 召回腿的真实结果 ────────────────────────────────────────────────────────


def _retrieval_filters(normalized: dict[str, Any]) -> dict[str, Any]:
    """**逐字**照抄 ``profile_recall.recall_kol_profiles`` 构造绑值的那段。

    刻意不复用 ``pool_yield_recall_parity.recall_sql_values`` —— 那正是被测对象,
    拿它当基准就成了自己考自己。这段抄写由 :func:`test_recall_binding_expression_is_pinned`
    钉住,召回腿一改这里当场红。
    """
    retrieval = dict(normalized)
    if normalized.get("countries"):
        retrieval["_country_values"] = sorted({
            value
            for raw in normalized["countries"]
            for value in (str(raw).strip().lower(), _country_match_key(raw).lower())
            if value
        })
    if normalized.get("languages"):
        retrieval["_language_values"] = sorted({
            value
            for raw in normalized["languages"]
            for value in (str(raw).strip().lower(), _language_match_key(raw))
            if value
        })
    return retrieval


def _recall_leg_ids(conn: _Conn, raw_filters: dict[str, Any]) -> set[int]:
    """真跑一遍召回腿:取数腿 SQL 捞人 -> 逐人三态判定。返回搜索真给得出的那批 id。"""
    normalized, _unsupported = _normalize_recall_filters(dict(raw_filters))
    lexical = precision.lexical_recall_candidates(
        _QUERY,
        operator_query=_QUERY,
        candidate_limit=500,
        conn=conn,
        hard_filters=_retrieval_filters(normalized),
    )
    rows = {row["id"]: row for row in _pool_rows(conn)}
    survivors: set[int] = set()
    for item in lexical.get("items") or []:
        pool_id = int(item["kol_pool_id"])
        verdict = _candidate_filter_verdict(rows[pool_id], {}, normalized)
        if verdict[0]:
            survivors.add(pool_id)
    return survivors


def _pairs(conn: _Conn) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    for label, filters in _COMBINATIONS:
        estimated = estimate_pool_yield(dict(filters), get_connection=lambda: conn)["estimated"]
        out.append((label, int(estimated), len(_recall_leg_ids(conn, filters))))
    return out


# ── 对拍 ────────────────────────────────────────────────────────────────────


def test_every_combination_matches_a_real_recall_run(pool_conn: Any) -> None:
    """17 组逐组对拍:预估报的数 == 真跑一遍召回腿拿到的人数。对不上就不算修好。"""
    mismatched = [row for row in _pairs(pool_conn) if row[1] != row[2]]
    assert not mismatched, "预估与召回腿对不上:" + "; ".join(
        f"{label} 预估={estimated} 实搜={actual}" for label, estimated, actual in mismatched
    )


def test_include_unknown_cell_gains_exactly_zero_in_both_legs(pool_conn: Any) -> None:
    """本次谎报的重灾区单独对拍:「含未知」那一格,两边都必须是 0 增益。"""
    strict = {"countries": ["US"]}
    admitted = {"countries": {"values": ["US"], "mode": "include_unknown"}}
    assert (
        estimate_pool_yield(strict, get_connection=lambda: pool_conn)["estimated"]
        == estimate_pool_yield(admitted, get_connection=lambda: pool_conn)["estimated"]
        == len(_recall_leg_ids(pool_conn, strict))
        == len(_recall_leg_ids(pool_conn, admitted))
    )
    result = estimate_pool_yield(admitted, get_connection=lambda: pool_conn)
    assert "unknown_mode_not_recallable" in {item["item"] for item in result["not_estimated"]}


def test_country_synonyms_are_one_shared_key_set(pool_conn: Any) -> None:
    """国家同义词只有一套口径:四种写法各自的预估都与实搜一致,且都取不到别的写法。"""
    for spelling in ("US", "USA", "United States", "美国"):
        filters = {"countries": [spelling]}
        estimated = estimate_pool_yield(filters, get_connection=lambda: pool_conn)["estimated"]
        assert estimated == len(_recall_leg_ids(pool_conn, filters)), spelling


def test_padded_pool_values_are_counted_as_unrecallable_not_qualified(pool_conn: Any) -> None:
    """库里写成 ``" US "`` 的人取数腿捞不到 —— 预估必须跟着不算他,并如实说为什么。"""
    result = estimate_pool_yield({"countries": ["US"]}, get_connection=lambda: pool_conn)
    row = next(item for item in result["tri_state"] if item["filter"] == "countries")
    assert row["unrecallable"] >= 3
    assert "recall_key_gap" in {item["item"] for item in result["not_estimated"]}


def test_estimate_never_promises_more_than_the_recall_leg(pool_conn: Any) -> None:
    """方向性红线:任何一组都不许预估 > 实搜。宁可保守,绝不虚报。"""
    assert [row for row in _pairs(pool_conn) if row[1] > row[2]] == []


# ── 诚实登记 ────────────────────────────────────────────────────────────────


def test_qualification_gates_are_always_declared(pool_conn: Any) -> None:
    """合格线每次都要登记 —— 这个数是硬筛后的可选面,不是最终能拿到几个人。"""
    result = estimate_pool_yield({"countries": ["US"]}, get_connection=lambda: pool_conn)
    items = {item["item"] for item in result["not_estimated"]}
    assert {
        "qualification_evidence",
        "qualification_freshness",
        "qualification_product_anchor",
        "qualification_account_safety",
        "qualification_followers_floor",
        "qualification_dedupe",
    } <= items
    assert result["estimate_basis"] == "hard_filter_only"
    assert "还要过合格线" in result["headline_note"]
    assert all(item["note"] for item in result["not_estimated"])


def test_vertical_reading_is_declared_as_possibly_conservative(pool_conn: Any) -> None:
    """内容方向只看两路信号,比搜索侧窄 —— 必须当面说「这个数可能偏保守」。"""
    result = estimate_pool_yield(
        {"verticals": ["摄影"], "countries": ["US"]}, get_connection=lambda: pool_conn
    )
    note = next(
        item for item in result["not_estimated"] if item["item"] == "verticals_narrower_than_search"
    )
    assert "偏保守" in note["note"]


# ── 与召回腿的字面 pin ──────────────────────────────────────────────────────


def test_recall_sql_predicates_are_pinned(pool_conn: Any) -> None:
    """召回腿两条取数腿的硬筛谓词字面量。改了就必须回来重算预估口径。"""
    lexical_src = inspect.getsource(precision.lexical_recall_candidates)
    backfill_src = inspect.getsource(storage._pool_text_fallback_hits)
    for source in (lexical_src, backfill_src):
        assert parity.RECALL_SQL_PINS["platforms"] in source
        assert parity.RECALL_SQL_PINS["countries"] in source
        assert parity.RECALL_SQL_PINS["languages"] in source
        assert 'f"{value}' + parity.RECALL_SQL_PINS["languages_like_suffix"] + '"' in source


def test_recall_binding_expression_is_pinned() -> None:
    """``_country_values`` / ``_language_values`` 的构造表达式。"""
    source = inspect.getsource(recall_module.recall_kol_profiles)
    for fragment in (
        'retrieval_filters["_country_values"] = sorted({',
        'retrieval_filters["_language_values"] = sorted({',
        "str(raw).strip().lower(),",
        "_country_match_key(raw).lower(),",
        "_language_match_key(raw),",
    ):
        assert fragment in source, fragment


def test_tri_state_is_still_absent_from_the_recall_sql(pool_conn: Any) -> None:
    """本次选「缩小预估」的前提:取数腿仍然不认三态。哪天它认了,这条会红,提醒来放开预估。"""
    source = inspect.getsource(precision.lexical_recall_candidates)
    assert "countries_mode" not in source
    assert "languages_mode" not in source
