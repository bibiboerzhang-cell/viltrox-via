"""国家取数腿与闸的**口径对齐**(2026-08-26 · 国家腿同病同修车道)。

对抗复核坐实:语言腿刚治好的那个病,国家腿一字不差地还在犯 —— 闸用
``_country_match_key`` 把 ``USA`` / ``U.S.`` / ``America`` / ``United States`` / ``美国``
统统归一成 ``US`` 再比,而取数腿的 SQL 写的是 ``LOWER(COALESCE(p.country,'')) IN (原值)``,
**在闸之前**就把写法不同的人剔光了。这才是「美国 + 5 万粉 + 英语」兑现不了的真约束
(本地库:``country='美国'`` 324 人 / ``country='US'`` 44 人,门面按规则给出的筛选值是 ``US``)。

本文件逐条钉死:

* **取数腿认归一化闭包**:一种写法点名,全部同义写法都捞得回来;
* **不变式**:取数腿是闸的**超集** —— 闸会放行的人,取数腿一个都不许提前剔掉
  (与语言腿的 ``test_recall_leg_is_a_superset_of_the_gate`` 同款,并且这里连
  「库里写法千奇百怪」的行也一起跑);
* **闸一条没放宽**:确认是别国的人照旧拦;``require`` 下「未知」照旧拦;
* **三态**:``include_unknown`` 不再结构性恒为 0,``exclude`` 不再被下推成「只捞被排除的人」;
* **红线**:SQL 里零字面百分号、零 ``LIKE``,占位符一律 ``?``。

测试全程打真 sqlite,SQL 真执行 —— 不是字符串比对。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domains.kol import profile_recall_precision as precision  # noqa: E402
from app.domains.kol import profile_recall_storage as storage  # noqa: E402
from app.domains.kol.profile_recall_country_gate import (  # noqa: E402
    country_hard_filter,
    country_match_key,
    country_sql_filter,
    country_sql_values,
)
from app.domains.kol.profile_recall_projection import (  # noqa: E402
    _candidate_filter_verdict,
    _country_match_key,
    _normalize_recall_filters,
)

_QUERY = "photography"

#: (id, handle, 库里 country 的**原样**写法)。每一行对着一种真实存在的写法。
_PEOPLE: tuple[tuple[int, str, str], ...] = (
    (1, "code_us", "US"),                      # 国家码:老取数腿唯一捞得到的一种
    (2, "zh_us", "美国"),                       # 中文国名:本地库里最大的一坨(324 人)
    (3, "name_us", "United States"),           # 英文规范名
    (4, "alias_usa", "USA"),                   # 常见缩写
    (5, "dotted_us", "U.S."),                  # 带点缩写
    (6, "america_us", "America"),              # 口语名
    (7, "padded_us", "  United   States  "),   # 带首尾空白 + 内部双空格
    (8, "lower_us", "usa"),                    # 大小写不同
    (9, "gb_person", "英国"),                   # 确认是别国:必须照旧拦
    (10, "no_country", ""),                    # 没填:未知档
    (11, "unknown_land", "Freedonia"),         # 别名表里没有的国名:仍要自洽
)

#: 老取数腿(``LOWER(COALESCE(p.country,'')) IN (原值小写 ∪ 国家码小写)``)在
#: ``countries=["US"]`` 下能捞到的全部人 —— 复核说的「真瓶颈」就是这个集合有多小。
_LEGACY_US_REACH = {"code_us", "lower_us"}


def _make_pool(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY, platform TEXT, handle TEXT, display_name TEXT,
            bio TEXT, primary_topic TEXT, content_style TEXT, secondary_topics_json TEXT,
            topic_details_json TEXT, tagged_brands_json TEXT, avg_likes INTEGER,
            source_type TEXT, source_ref TEXT, real_er REAL, real_er_sample_n INTEGER,
            real_er_computed_at TEXT, real_er_method TEXT, last_seen_at TEXT, updated_at TEXT,
            avatar_url TEXT, profile_url TEXT, avg_views INTEGER, avg_comments INTEGER,
            engagement_rate REAL, followers INTEGER, country TEXT, language TEXT,
            brand_collaborations_json TEXT, duplicate_of_id INTEGER
        );
        CREATE TABLE vkpi_kol_profile_index_entries (
            kol_pool_id INTEGER, collection_name TEXT, method TEXT, status TEXT,
            profile_type TEXT, creator_type_score REAL, reviewer_type_score REAL,
            type_reason TEXT, type_method TEXT, sufficiency TEXT, profile_text TEXT
        );
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY, kol_pool_id INTEGER, title TEXT,
            video_title TEXT, content_url TEXT, is_active INTEGER DEFAULT 1
        );
        """
    )
    for pool_id, handle, country in _PEOPLE:
        conn.execute(
            "INSERT INTO vkpi_kol_pool (id, platform, handle, display_name, bio,"
            " primary_topic, content_style, followers, country, language)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pool_id, "youtube", handle, handle, f"{_QUERY} creator", _QUERY,
             "review", 80000, country, "en"),
        )
        conn.execute(
            "INSERT INTO vkpi_kol_profile_index_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pool_id, "vkpi_kol_profile_index_v1", "vector_recall", "ready", "creator",
             0.8, 0.2, "portrait photography", "rule", "ok", f"{_QUERY} portrait creator"),
        )
    conn.commit()


def _pool() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _make_pool(conn)
    return conn


def _retrieval_filters(normalized: dict[str, Any]) -> dict[str, Any]:
    """照抄 ``recall_kol_profiles`` 构造 ``_country_values`` 的那段(绑值形态不变)。"""
    out = dict(normalized)
    if normalized.get("countries"):
        out["_country_values"] = sorted({
            value
            for raw in normalized["countries"]
            for value in (str(raw).strip().lower(), _country_match_key(raw).lower())
            if value
        })
    return out


def _recall_leg_handles(conn: sqlite3.Connection, raw_filters: dict[str, Any]) -> set[str]:
    """取数腿(词法腿)真捞回来了谁。"""
    normalized, _unsupported = _normalize_recall_filters(dict(raw_filters))
    result = precision.lexical_recall_candidates(
        _QUERY, operator_query=_QUERY, candidate_limit=100, conn=conn,
        hard_filters=_retrieval_filters(normalized),
    )
    ids = {int(item["kol_pool_id"]) for item in result.get("items") or []}
    return {handle for pool_id, handle, _country in _PEOPLE if pool_id in ids}


def _broad_leg_handles(conn: sqlite3.Connection, raw_filters: dict[str, Any]) -> set[str]:
    """广度兜底腿真捞回来了谁(第二条取数腿,口径必须与词法腿一致)。"""
    normalized, _unsupported = _normalize_recall_filters(dict(raw_filters))
    hits = storage._pool_text_fallback_hits(
        _QUERY, 100,
        include_relevance_backfill=True,
        operator_query_text=_QUERY,
        filters=_retrieval_filters(normalized),
        get_connection=lambda: conn,
    )
    ids = {int(getattr(hit, "kol_pool_id", 0) or 0) for hit in hits}
    return {handle for pool_id, handle, _country in _PEOPLE if pool_id in ids}


def _gate_handles(raw_filters: dict[str, Any]) -> set[str]:
    """硬筛闸真放行了谁(闸是纯函数,直接喂行)。"""
    normalized, _unsupported = _normalize_recall_filters(dict(raw_filters))
    passed: set[str] = set()
    for pool_id, handle, country in _PEOPLE:
        row = {
            "kol_pool_id": pool_id, "handle": handle, "platform": "youtube",
            "country": country, "followers": 80000, "language": "en",
        }
        if _candidate_filter_verdict(row, {}, normalized)[0]:
            passed.add(handle)
    return passed


# ── 归一化闭包 ───────────────────────────────────────────────────────────────


def test_pushdown_enumerates_every_spelling_that_normalizes_to_the_code() -> None:
    """点一个 ``US``,下推的是「所有会归一成 US 的写法」而不是一个字面量。"""
    values = set(country_sql_values(["US"]))
    assert {"us", "usa", "u.s.", "u.s.a.", "unitedstates", "america", "美国"} <= values
    # 别国的写法一个都不许混进来 —— 变宽只能沿着「同一个国家码」的方向。
    assert not values & {"英国", "unitedkingdom", "gb", "canada"}


def test_pushdown_key_is_the_same_ruler_as_the_gate() -> None:
    """闸与取数腿共用一把尺子:``_country_match_key`` 现在就是取数腿那把。"""
    for raw in ("USA", "u.s.", "美国", "United States", "  America  "):
        assert _country_match_key(raw) == country_match_key(raw) == "US"


# ── 取数腿 ───────────────────────────────────────────────────────────────────


def test_recall_leg_no_longer_culls_the_synonym_spellings() -> None:
    """本波的核心缺陷:操作员点 ``US``,库里写「美国」的人在闸之前就被剔光了。"""
    conn = _pool()
    handles = _recall_leg_handles(conn, {"countries": ["US"]})
    assert {
        "code_us", "zh_us", "name_us", "alias_usa",
        "dotted_us", "america_us", "padded_us", "lower_us",
    } <= handles
    # 修之前只够得着这两个人 —— 这就是复核说的「真瓶颈」。
    assert _LEGACY_US_REACH < handles


def test_both_retrieval_legs_share_the_same_country_reach() -> None:
    """两条取数腿(词法腿 / 广度兜底腿)一个口径,不许一条修好一条还旧。"""
    conn = _pool()
    assert _recall_leg_handles(conn, {"countries": ["US"]}) == _broad_leg_handles(
        conn, {"countries": ["US"]}
    )


def test_recall_leg_still_reaches_only_the_country_it_was_asked_for() -> None:
    """接线不等于放行:确认是别国的人、以及没填的人,取数腿照旧不带回来。"""
    conn = _pool()
    handles = _recall_leg_handles(conn, {"countries": ["US"]})
    assert "gb_person" not in handles
    assert "no_country" not in handles
    assert "unknown_land" not in handles


def test_recall_leg_reaches_the_same_people_from_any_spelling_of_the_ask() -> None:
    """操作员写 ``美国`` 还是 ``United States`` 还是 ``USA``,捞回来的是同一批人。"""
    conn = _pool()
    baseline = _recall_leg_handles(conn, {"countries": ["US"]})
    for spelling in ("美国", "United States", "USA", "u.s.", "America"):
        assert _recall_leg_handles(conn, {"countries": [spelling]}) == baseline, spelling


def test_recall_leg_honours_include_unknown_mode() -> None:
    """「含未知」此前在国家腿上结构性失效(那一格的增益永远是 0)。"""
    conn = _pool()
    strict = _recall_leg_handles(conn, {"countries": ["US"]})
    admitted = _recall_leg_handles(
        conn, {"countries": {"values": ["US"], "mode": "include_unknown"}}
    )
    assert "no_country" not in strict
    assert "no_country" in admitted
    assert strict < admitted


def test_recall_leg_stops_sabotaging_exclude_mode() -> None:
    """「排除美国」此前被下推成「只捞美国」,再被闸全排掉,结果恒为 0。"""
    conn = _pool()
    handles = _recall_leg_handles(
        conn, {"countries": {"values": ["US"], "mode": "exclude"}}
    )
    assert {"gb_person", "unknown_land"} <= handles
    assert "zh_us" not in _gate_handles({"countries": {"values": ["US"], "mode": "exclude"}})


# ── 闸:一条都没放宽 ─────────────────────────────────────────────────────────


def test_gate_keeps_a_confirmed_other_country_rejected() -> None:
    """取数腿变宽不等于闸放行:确认是别国的人,闸照旧拦。"""
    assert "gb_person" not in _gate_handles({"countries": ["US"]})


def test_gate_keeps_unknown_rejected_under_require() -> None:
    """``country`` 没填的人是「未知」,缺省 ``require`` 照旧拦 —— 三态语义一格没动。"""
    assert "no_country" not in _gate_handles({"countries": ["US"]})


# ── 不变式:取数腿是闸的超集 ─────────────────────────────────────────────────


def test_recall_leg_is_a_superset_of_the_gate() -> None:
    """本车道最重要的一条:闸会放行的人,取数腿一个都不许提前剔掉。

    与语言腿的同名不变式同款,并且这里把「同一个国家的各种写法」也一起跑 ——
    国家腿犯的正是「写法不同就够不着」这个病。
    """
    conn = _pool()
    for filters in (
        {"countries": ["US"]},
        {"countries": ["美国"]},
        {"countries": ["United States"]},
        {"countries": ["USA", "GB"]},
        {"countries": {"values": ["US"], "mode": "include_unknown"}},
        {"countries": {"values": ["US"], "mode": "exclude"}},
        {"countries": ["Freedonia"]},
        {"countries": ["英国"]},
    ):
        leg = _recall_leg_handles(conn, filters)
        gate = _gate_handles(filters)
        assert gate <= leg, f"{filters}: 闸放行但取数腿够不着 {sorted(gate - leg)}"
        broad = _broad_leg_handles(conn, filters)
        assert gate <= broad, f"{filters}: 闸放行但广度腿够不着 {sorted(gate - broad)}"


def test_country_pushdown_leaves_every_other_dimension_alone() -> None:
    """不筛国家的那一组:前后逐字不变 —— 证明没有别的维度被顺手动过。"""
    conn = _pool()
    assert _recall_leg_handles(conn, {}) == {handle for _id, handle, _c in _PEOPLE}
    # 粉丝下限照旧是硬的:80000 的人全在,80001 起一个都不剩。
    assert _recall_leg_handles(conn, {"followers_min": 80000}) == {
        handle for _id, handle, _c in _PEOPLE
    }
    assert _recall_leg_handles(conn, {"followers_min": 80001}) == set()


# ── 红线 ─────────────────────────────────────────────────────────────────────


def test_country_sql_carries_no_literal_percent_and_no_like() -> None:
    """compat 红线:占位符 ``?``、零字面百分号、零 ``LIKE``。"""
    for kwargs in ({}, {"mode": "include_unknown"}):
        sql, params = country_sql_filter(["US", "GB"], **kwargs)
        assert "%" not in sql
        assert "LIKE" not in sql.upper()
        assert sql.count("?") == len(params)


def test_country_hard_filter_is_a_no_op_without_a_country_ask() -> None:
    """没点国家就不下推 —— 与既有行为逐字一致。"""
    assert country_hard_filter({}) == ("", [])
    assert country_hard_filter({"countries": []}) == ("", [])
    assert country_hard_filter(None) == ("", [])
