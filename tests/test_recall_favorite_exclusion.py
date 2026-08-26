"""全局排除「已被关注的人」的契约(用户裁决 2026-08-25 · 第 2 条)。

四条主张各有断言:

1. **口径是全局、按人不按员工** —— 任意一名员工收藏过,任何人搜索都不再看到他;
2. **排除的是「已被收藏的人」,不是「已在池子里的人」** —— 没人收藏的池内成员一个不许少;
3. **去重行不许算错** —— canonical/alias 两层的四支等价类都要摘干净,SQL 用真 sqlite 跑;
4. **计数与缺口如实透出** —— 摘了几个人要能读到,凑不满就照实缺着,绝不拿别人补位。

外加一条失败方向的红线:缺表 / 连接异常 / 查询异常一律**不排除**(保持现状),
绝不因为查不到收藏就把人误杀。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.kol import profile_recall  # noqa: E402
from app.domains.kol import recall_favorite_exclusion as fav  # noqa: E402


# ── 真 sqlite 夹具:migration 107 的等价建表,SQL 逐字跑真引擎 ──────────────────


@pytest.fixture()
def pool_conn(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE vkpi_kol_pool ("
        " id INTEGER PRIMARY KEY, platform TEXT, handle TEXT,"
        " display_name TEXT, followers INTEGER, duplicate_of_id INTEGER DEFAULT NULL)"
    )
    conn.execute(
        "CREATE TABLE vkpi_kol_pool_favorites ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " kol_pool_id INTEGER NOT NULL, staff_id INTEGER NOT NULL,"
        " note TEXT, created_at TEXT, UNIQUE (kol_pool_id, staff_id))"
    )
    monkeypatch.setattr(fav, "get_conn", lambda: conn)
    monkeypatch.setattr(fav, "table_exists", lambda _name: True)
    return conn


def _add_pool(
    conn: sqlite3.Connection,
    pool_id: int,
    *,
    platform: str = "youtube",
    handle: str | None = None,
    duplicate_of_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO vkpi_kol_pool (id, platform, handle, display_name, followers,"
        " duplicate_of_id) VALUES (?,?,?,?,?,?)",
        (
            pool_id,
            platform,
            handle if handle is not None else f"creator{pool_id}",
            f"Creator {pool_id}",
            10_000 + pool_id,
            duplicate_of_id,
        ),
    )
    conn.commit()


def _favorite(conn: sqlite3.Connection, pool_id: int, staff_id: int) -> None:
    conn.execute(
        "INSERT INTO vkpi_kol_pool_favorites (kol_pool_id, staff_id) VALUES (?,?)",
        (pool_id, staff_id),
    )
    conn.commit()


# ── 主张 1:任意员工收藏即算(全局,不按员工)────────────────────────────────


def test_any_staff_favorite_excludes_for_everyone(pool_conn: sqlite3.Connection) -> None:
    """员工 7 收藏的人、员工 9 收藏的人,都进同一个排除集 —— 口径不带 staff_id。"""

    for pool_id in (1, 2, 3, 4):
        _add_pool(pool_conn, pool_id)
    _favorite(pool_conn, 1, staff_id=7)
    _favorite(pool_conn, 2, staff_id=9)

    assert fav.favorited_pool_ids([1, 2, 3, 4]) == {1, 2}


def test_same_person_favorited_by_two_staff_counts_once(pool_conn: sqlite3.Connection) -> None:
    """两名员工收藏同一个人 —— 排除集是「人」的集合,不会因为两行收藏数成两个。"""

    _add_pool(pool_conn, 1)
    _add_pool(pool_conn, 2)
    _favorite(pool_conn, 1, staff_id=7)
    _favorite(pool_conn, 1, staff_id=9)

    hits = [profile_recall.RecallHit(1, 0.9, "q1"), profile_recall.RecallHit(2, 0.8, "q2")]
    survivors, block = fav.exclude_favorited_hits(hits)

    assert [hit.kol_pool_id for hit in survivors] == [2]
    assert block["excluded_count"] == 1
    assert block["excluded_ids"] == [1]


# ── 主张 2:排除「已被收藏的人」≠ 排除「池子里的人」────────────────────────


def test_pool_membership_alone_never_excludes(pool_conn: sqlite3.Connection) -> None:
    """池子里 100 个人一个都没被收藏 —— 一个都不许排除。两者天差地别。"""

    for pool_id in range(1, 101):
        _add_pool(pool_conn, pool_id)

    assert fav.favorited_pool_ids(range(1, 101)) == set()

    hits = [profile_recall.RecallHit(pool_id, 0.5, f"q{pool_id}") for pool_id in range(1, 101)]
    survivors, block = fav.exclude_favorited_hits(hits)
    assert len(survivors) == 100
    assert block["excluded_count"] == 0
    assert block["available"] is True


# ── 主张 3:去重行的四支等价类 ──────────────────────────────────────────────


def test_duplicate_alias_of_a_favorited_canonical_is_excluded(pool_conn: sqlite3.Connection) -> None:
    """候选是 alias、canonical 被收藏 → 也要摘。"""

    _add_pool(pool_conn, 10)                        # canonical
    _add_pool(pool_conn, 11, duplicate_of_id=10)    # alias
    _favorite(pool_conn, 10, staff_id=7)

    assert fav.favorited_pool_ids([10, 11]) == {10, 11}


def test_favorited_alias_excludes_its_canonical(pool_conn: sqlite3.Connection) -> None:
    """收藏落在 alias 行上,而召回只吐 canonical(duplicate_of_id IS NULL)→ canonical 也要摘。

    这正是「只比 id 会漏」的那一刀:不修这支,收藏过的人换个身份又冒出来。
    """

    _add_pool(pool_conn, 20)                        # canonical(召回会吐这行)
    _add_pool(pool_conn, 21, duplicate_of_id=20)    # alias(收藏落在这行)
    _favorite(pool_conn, 21, staff_id=9)

    assert fav.favorited_pool_ids([20]) == {20}


def test_sibling_alias_of_a_favorited_alias_is_excluded(pool_conn: sqlite3.Connection) -> None:
    """同一 canonical 下的兄弟 alias:A 被收藏,B 也是同一个人 → B 也要摘。"""

    _add_pool(pool_conn, 30)                        # canonical
    _add_pool(pool_conn, 31, duplicate_of_id=30)    # alias A(被收藏)
    _add_pool(pool_conn, 32, duplicate_of_id=30)    # alias B(候选)
    _favorite(pool_conn, 31, staff_id=7)

    assert fav.favorited_pool_ids([32]) == {32}


def test_unrelated_duplicate_chains_do_not_bleed(pool_conn: sqlite3.Connection) -> None:
    """两条互不相干的去重链:一条被收藏,另一条一个人都不许被牵连。"""

    _add_pool(pool_conn, 40)
    _add_pool(pool_conn, 41, duplicate_of_id=40)
    _add_pool(pool_conn, 50)
    _add_pool(pool_conn, 51, duplicate_of_id=50)
    _favorite(pool_conn, 41, staff_id=7)

    assert fav.favorited_pool_ids([40, 41, 50, 51]) == {40, 41}


def test_null_duplicate_of_id_rows_are_not_treated_as_siblings(pool_conn: sqlite3.Connection) -> None:
    """全是 canonical(duplicate_of_id 全 NULL)时,不许因为「都为 NULL」被当成兄弟。

    这是第 ④ 支最容易写错的地方:NULL = NULL 若被当成命中,一次收藏会清空整个池子。
    """

    for pool_id in (60, 61, 62):
        _add_pool(pool_conn, pool_id)
    _favorite(pool_conn, 60, staff_id=7)

    assert fav.favorited_pool_ids([60, 61, 62]) == {60}


# ── 主张 4:计数、缺口如实透出 ──────────────────────────────────────────────


def _install_recall_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: dict[int, dict[str, Any]],
    hits: list[Any],
) -> None:
    monkeypatch.setenv("RECALL_LLM_RERANK_ENABLED", "0")
    monkeypatch.setattr(
        profile_recall,
        "resolve_query_text",
        lambda **_kwargs: ("camera reviewer", {"query_profile": ""}),
    )
    monkeypatch.setattr(profile_recall, "_embed_query", lambda _text: ([0.1], {}))
    monkeypatch.setattr(profile_recall, "_search_qdrant", lambda _vector, _limit: hits)
    monkeypatch.setattr(
        profile_recall,
        "_entry_rows",
        lambda ids: {item_id: dict(rows[item_id]) for item_id in ids if item_id in rows},
    )
    monkeypatch.setattr(profile_recall, "_evidence_summaries", lambda _ids: {})
    monkeypatch.setattr(profile_recall, "_pool_rows_fallback", lambda _ids: {})
    monkeypatch.setattr(profile_recall, "_pool_text_fallback_hits", lambda *_a, **_k: [])
    monkeypatch.setattr(profile_recall, "_adoption_profile", lambda: {})


def _row(item_id: int) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "handle": f"creator{item_id}",
        "display_name": f"Creator {item_id}",
        "platform": "youtube",
        "profile_type": "creator",
        "creator_type_score": 80,
        "reviewer_type_score": 40,
        "followers": 10_000 + item_id,
        "country": "US",
        "language": "en",
        "primary_topic": "camera lens review",
        "bio": "Camera gear reviewer and filmmaker",
    }


def test_recall_hides_favorited_people_and_reports_the_count(
    monkeypatch: pytest.MonkeyPatch, pool_conn: sqlite3.Connection
) -> None:
    """端到端:同事收藏过的人从结果里消失,并且诊断如实说摘了几个。"""

    rows = {item_id: _row(item_id) for item_id in range(1, 7)}
    for item_id in rows:
        _add_pool(pool_conn, item_id)
    _favorite(pool_conn, 2, staff_id=7)
    _favorite(pool_conn, 5, staff_id=9)

    hits = [profile_recall.RecallHit(item_id, 0.9 - item_id / 100, f"q{item_id}") for item_id in rows]
    _install_recall_fixture(monkeypatch, rows=rows, hits=hits)

    result = profile_recall.recall_kol_profiles(
        query_text="camera reviewer",
        candidate_limit=6,
        limit=6,
        creator_quota=6,
        reviewer_quota=6,
    )
    diagnostics = result["diagnostics"]

    assert {item["kol_pool_id"] for item in result["items"]} == {1, 3, 4, 6}
    assert diagnostics["favorite_excluded_count"] == 2
    assert diagnostics["retrieved_candidate_count"] == 6
    assert diagnostics["candidate_count"] == 4
    assert sorted(diagnostics["favorite_exclusion"]["excluded_ids"]) == [2, 5]
    assert diagnostics["favorite_exclusion"]["available"] is True
    assert diagnostics["favorite_exclusion"]["scope"] == "any_staff_favorite_global"
    assert diagnostics["favorite_exclusion"]["reason_code"] == "already_favorited_by_team"


def test_recall_reports_the_gap_honestly_and_never_fills_it(
    monkeypatch: pytest.MonkeyPatch, pool_conn: sqlite3.Connection
) -> None:
    """排除导致凑不满 —— 缺口照实说,而且绝不拿别人补位。"""

    rows = {item_id: _row(item_id) for item_id in range(1, 7)}
    for item_id in rows:
        _add_pool(pool_conn, item_id)
    for favorited in (2, 5):
        _favorite(pool_conn, favorited, staff_id=7)

    hits = [profile_recall.RecallHit(item_id, 0.9 - item_id / 100, f"q{item_id}") for item_id in rows]
    _install_recall_fixture(monkeypatch, rows=rows, hits=hits)

    result = profile_recall.recall_kol_profiles(
        query_text="camera reviewer",
        candidate_limit=6,
        limit=6,
        creator_quota=6,
        reviewer_quota=6,
    )
    diagnostics = result["diagnostics"]

    assert diagnostics["returned_count"] == 4
    assert diagnostics["requested_count"] == 6
    assert diagnostics["shortfall"] == 2
    assert diagnostics["result_contract_satisfied"] is False
    # 缺口没被任何人填上:返回的人全部来自「没被收藏」的那一批。
    assert {item["kol_pool_id"] for item in result["items"]} <= {1, 3, 4, 6}
    note = diagnostics["favorite_exclusion_note"]
    assert "已排除 2" in note and "仍缺 2" in note


def test_recall_note_is_silent_when_nothing_was_excluded(
    monkeypatch: pytest.MonkeyPatch, pool_conn: sqlite3.Connection
) -> None:
    """没排除任何人就不许说「已排除」——诚实空态,不许无中生有。"""

    rows = {item_id: _row(item_id) for item_id in range(1, 4)}
    for item_id in rows:
        _add_pool(pool_conn, item_id)
    hits = [profile_recall.RecallHit(item_id, 0.9, f"q{item_id}") for item_id in rows]
    _install_recall_fixture(monkeypatch, rows=rows, hits=hits)

    result = profile_recall.recall_kol_profiles(
        query_text="camera reviewer",
        candidate_limit=3,
        limit=3,
        creator_quota=3,
        reviewer_quota=3,
    )
    assert result["diagnostics"]["favorite_excluded_count"] == 0
    assert result["diagnostics"]["favorite_exclusion_note"] == ""


# ── 失败方向:查不到收藏 → 不排除任何人(绝不误杀)──────────────────────────


def test_missing_favorites_table_excludes_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fav, "table_exists", lambda _name: False)
    hits = [profile_recall.RecallHit(pool_id, 0.5, f"q{pool_id}") for pool_id in (1, 2, 3)]

    survivors, block = fav.exclude_favorited_hits(hits)

    assert [hit.kol_pool_id for hit in survivors] == [1, 2, 3]
    assert block["available"] is False
    assert block["unavailable_reason"] == "favorites_table_missing"
    assert block["excluded_count"] == 0


def test_query_failure_excludes_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom:
        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("db down")

    monkeypatch.setattr(fav, "table_exists", lambda _name: True)
    monkeypatch.setattr(fav, "get_conn", _Boom)
    hits = [profile_recall.RecallHit(pool_id, 0.5, f"q{pool_id}") for pool_id in (1, 2, 3)]

    survivors, block = fav.exclude_favorited_hits(hits)

    assert [hit.kol_pool_id for hit in survivors] == [1, 2, 3]
    assert block["excluded_count"] == 0


def test_connection_failure_excludes_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_conn() -> Any:
        raise RuntimeError("pool closed")

    monkeypatch.setattr(fav, "table_exists", lambda _name: True)
    monkeypatch.setattr(fav, "get_conn", _no_conn)

    assert fav.favorited_pool_ids([1, 2, 3]) == set()
    assert fav.favorited_identity_keys() == set()


# ── 在线腿:身份键排除 ──────────────────────────────────────────────────────


def test_online_identity_keys_cover_the_duplicate_equivalence(pool_conn: sqlite3.Connection) -> None:
    _add_pool(pool_conn, 70, platform="youtube", handle="AlphaShooter")
    _add_pool(pool_conn, 71, platform="youtube", handle="AlphaShooterAlt", duplicate_of_id=70)
    _add_pool(pool_conn, 80, platform="tiktok", handle="untouched")
    _favorite(pool_conn, 71, staff_id=7)

    keys = fav.favorited_identity_keys()

    assert ("youtube", "alphashooter") in keys
    assert ("youtube", "alphashooteralt") in keys
    assert ("tiktok", "untouched") not in keys


def test_online_candidates_are_matched_case_and_at_insensitively() -> None:
    keys = {("youtube", "alphashooter")}
    candidates = [
        {"platform": "YouTube", "handle": "@AlphaShooter"},
        {"platform": "youtube", "handle": "someone_else"},
    ]

    survivors, block = fav.exclude_favorited_online_candidates(candidates, identity_keys=keys)

    assert [item["handle"] for item in survivors] == ["someone_else"]
    assert block["excluded_count"] == 1
    assert block["excluded_identity_keys"] == ["youtube:alphashooter"]


def test_online_candidate_without_identity_is_kept() -> None:
    """判不出身份就不许当成命中排除 —— 失败方向永远是不误杀。"""

    keys = {("youtube", "alphashooter")}
    candidates = [{"platform": "youtube", "handle": ""}, {"handle": "alphashooter"}]

    survivors, block = fav.exclude_favorited_online_candidates(candidates, identity_keys=keys)

    assert len(survivors) == 2
    assert block["excluded_count"] == 0


def test_online_empty_favorite_set_keeps_everyone(pool_conn: sqlite3.Connection) -> None:
    _add_pool(pool_conn, 90, platform="youtube", handle="nobody_favorited_me")
    candidates = [{"platform": "youtube", "handle": "nobody_favorited_me"}]

    survivors, block = fav.exclude_favorited_online_candidates(candidates)

    assert len(survivors) == 1
    assert block["excluded_count"] == 0
    assert block["available"] is True


# ── 多轮加总 ────────────────────────────────────────────────────────────────


def test_merge_diagnostics_sums_rounds_and_keeps_availability_honest() -> None:
    round_one = fav._diagnostics(considered=30, excluded=[1, 2])
    round_two = fav._diagnostics(considered=25, excluded=[3])
    merged = fav.merge_diagnostics(round_one, round_two)

    assert merged["considered_count"] == 55
    assert merged["excluded_count"] == 3
    assert merged["excluded_ids"] == [1, 2, 3]
    assert merged["available"] is True

    degraded = fav.merge_diagnostics(round_one, fav._unavailable("favorites_table_missing"))
    assert degraded["available"] is False
    assert degraded["unavailable_reason"] == "favorites_table_missing"
    assert degraded["excluded_count"] == 2


def test_merge_diagnostics_with_no_rounds_is_an_honest_zero() -> None:
    merged = fav.merge_diagnostics()
    assert merged["excluded_count"] == 0
    assert merged["considered_count"] == 0
