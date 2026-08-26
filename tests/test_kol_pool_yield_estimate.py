"""产量预估的契约(2026-08-26)。

用户原话:「别总是 00000」。这套用例钉住的正是「为什么会 0」以及「该松哪一刀」:

* 三态计数必须分开报 —— 「语言」这一刀砍掉的人里,**没填**的和**确认不是**的
  必须各归各账。把两者混成一个「不符合」正是 58% 人群被误杀的根因。
* 阶梯必须单调不增,且逐条松绑的表必须按「能回来多少人」排好 —— 自动放宽只许
  采信这张表。
* 一次预估**只准发 SELECT**:零 provider、零 LLM、零写库。这一条由录制式连接
  逐条断言,不是靠注释保证。
* 空组合与全维度组合两个边界都要给出诚实结果。
"""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from app.domains.kol import pool_yield_estimate as yield_estimate
from app.domains.kol.pool_yield_estimate import estimate_pool_yield


# ── 固定盘:刻意复刻线上那条阶梯的形状(国家 -> 粉丝 -> 语言 逐级坍塌)────────────
#
# 100 人:美国 20(其中 ≥5 万粉 10:英语 2 / 没填 7 / 日语 1)、日本 30、国家没填 50。
# 于是「美国 + 5 万粉 + 英语」= 2 人,而被语言砍掉的 8 人里 **7 人只是没填**。

_US = "United States"
_LIFESTYLE_BIO = "travel and food lifestyle vlog"
_CAMERA_BIO = "mirrorless camera body reviews"


def _fixture_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(**kwargs: Any) -> None:
        row = {
            "platform": "youtube",
            "country": "",
            "language": "",
            "followers": 80000,
            "bio": "",
            "duplicate_of_id": None,
        }
        row.update(kwargs)
        rows.append(row)

    add(country=_US, language="en", bio=_LIFESTYLE_BIO)
    add(country=_US, language="en", bio=_CAMERA_BIO)
    for _ in range(7):
        add(country=_US, language="")
    add(country=_US, language="ja")
    for _ in range(10):
        add(country=_US, language="", followers=1000)
    for _ in range(30):
        add(country="Japan", language="ja")
    for index in range(50):
        add(country="", language="", platform="" if index < 2 else "youtube")
    return rows


class _RecordingConnection:
    """录制式只读连接:每条 SQL 都留档,任何非 SELECT 当场炸掉。"""

    def __init__(self, raw: sqlite3.Connection) -> None:
        self._raw = raw
        self.statements: list[str] = []

    def execute(self, sql: str, params: Any = ()) -> Any:
        head = " ".join(str(sql).split())
        self.statements.append(head)
        if not head.upper().lstrip("( ").startswith("SELECT"):
            raise AssertionError(f"产量预估只准发 SELECT,却发了:{head[:120]}")
        return self._raw.execute(sql, tuple(params))


@pytest.fixture()
def pool_conn() -> Any:
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.execute(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT, country TEXT, language TEXT, followers INTEGER,
            bio TEXT, primary_topic TEXT DEFAULT '', content_style TEXT DEFAULT '',
            secondary_topics_json TEXT DEFAULT '[]',
            topic_details_json TEXT, tagged_brands_json TEXT,
            duplicate_of_id INTEGER
        )
        """
    )
    raw.execute(
        """
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER, title TEXT, video_title TEXT, is_active INTEGER DEFAULT 1
        )
        """
    )
    for row in _fixture_rows():
        raw.execute(
            "INSERT INTO vkpi_kol_pool (platform, country, language, followers, bio, duplicate_of_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (row["platform"], row["country"], row["language"], row["followers"], row["bio"], None),
        )
    raw.commit()
    recorder = _RecordingConnection(raw)
    try:
        yield recorder
    finally:
        raw.close()


def _estimate(conn: Any, filters: Any) -> dict[str, Any]:
    return estimate_pool_yield(filters, get_connection=lambda: conn)


def _ladder_counts(result: dict[str, Any]) -> list[int]:
    return [rung["count"] for rung in result["ladder"]]


def _tri(result: dict[str, Any], name: str) -> dict[str, Any]:
    return next(row for row in result["tri_state"] if row["filter"] == name)


def _drop(result: dict[str, Any], name: str) -> dict[str, Any]:
    return next(row for row in result["drop_one"] if row["filter"] == name)


# ── 三态计数 ───────────────────────────────────────────────────────────────


def test_language_cut_reports_unfilled_and_real_mismatch_separately(pool_conn: Any) -> None:
    """核心断言:语言砍掉 8 人,其中 7 人只是**没填**,只有 1 人确认说别的语言。"""
    result = _estimate(
        pool_conn,
        {"countries": [_US], "languages": ["English"], "followers_min": 50000},
    )
    assert result["estimated"] == 2
    language = _tri(result, "languages")
    assert (language["qualified"], language["unknown"], language["mismatch"]) == (2, 7, 1)
    assert language["scope_count"] == 10


def test_country_cut_separates_unfilled_from_confirmed_other_country(pool_conn: Any) -> None:
    result = _estimate(pool_conn, {"countries": [_US]})
    country = _tri(result, "countries")
    assert (country["qualified"], country["unknown"], country["mismatch"]) == (20, 50, 30)


def test_every_dimension_reports_the_same_qualified_number(pool_conn: Any) -> None:
    """不变式:任何一维的「合格」都等于最终产量 —— 否则三态口径就自相矛盾了。"""
    result = _estimate(
        pool_conn,
        {"countries": [_US], "languages": ["English"], "followers_min": 50000, "platforms": ["youtube"]},
    )
    assert result["tri_state"]
    for row in result["tri_state"]:
        assert row["qualified"] == result["estimated"], row


def test_include_unknown_mode_promises_nothing_because_search_cannot_deliver_it(
    pool_conn: Any,
) -> None:
    """「含未知」这一档**一个人都放不回来** —— 而且必须当面说清楚,不许闷着。

    这条用例是本次谎报的重灾区,方向是**反的**:旧口径按逐人三态判定算,报「切到含未知
    能多回来 7 个人」;可真搜一遍时,候选先要被库内取数腿捞出来,而取数腿写的是
    ``LOWER(COALESCE(p.language,'')) IN (...)`` —— 资料没填的人恒等于空串,任何模式下
    都捞不到。所以那 7 个人搜索给不出。预估从此如实报 0 增益,并把这件事登记出来。
    """
    strict = _estimate(
        pool_conn, {"countries": [_US], "languages": ["English"], "followers_min": 50000}
    )
    relaxed = _estimate(
        pool_conn,
        {
            "countries": [_US],
            "languages": {"values": ["English"], "mode": "include_unknown"},
            "followers_min": 50000,
        },
    )
    assert relaxed["estimated"] == strict["estimated"] == 2
    # 那 7 个人没有被藏起来,只是改记在「取数腿够不着」这一档,而不是「放宽就能回来」。
    assert _tri(strict, "languages")["unknown"] == 7
    assert _tri(relaxed, "languages")["unrecallable"] == 7
    assert _tri(relaxed, "languages")["unknown"] == 0
    # 界面必须看得见这句话。
    assert "unknown_mode_not_recallable" in {item["item"] for item in relaxed["not_estimated"]}
    # 而「整条去掉语言」是真兑现得了的 —— 那张表的数字照旧。
    assert next(row for row in relaxed["drop_one"] if row["filter"] == "languages")["gain"] > 0


def test_exclude_mode_is_reported_as_zero_and_says_why(pool_conn: Any) -> None:
    """「排除某国」这一档搜索恒给 0(取数腿只捞点名的人,判定再把他们全排掉)。"""
    result = _estimate(pool_conn, {"countries": {"values": [_US], "mode": "exclude"}})
    assert result["estimated"] == 0
    items = {item["item"] for item in result["not_estimated"]}
    assert "exclude_mode_not_recallable" in items
    assert next(row for row in result["applied"] if row["filter"] == "countries")["mode_recallable"] is False


# ── 产量阶梯 ───────────────────────────────────────────────────────────────


def test_ladder_reproduces_the_collapse_and_never_goes_back_up(pool_conn: Any) -> None:
    result = _estimate(
        pool_conn,
        {"countries": [_US], "languages": ["English"], "followers_min": 50000},
    )
    assert _ladder_counts(result) == [100, 20, 10, 2]
    assert [rung["filter"] for rung in result["ladder"]] == [
        None, "countries", "followers_min", "languages",
    ]
    counts = _ladder_counts(result)
    assert all(later <= earlier for earlier, later in zip(counts, counts[1:]))


def test_ladder_order_is_fixed_regardless_of_input_key_order(pool_conn: Any) -> None:
    """两次估同一组合必须给出同一张阶梯表 —— 不随入参字典序漂移。"""
    forward = _estimate(
        pool_conn, {"countries": [_US], "languages": ["English"], "followers_min": 50000}
    )
    backward = _estimate(
        pool_conn, {"followers_min": 50000, "languages": ["English"], "countries": [_US]}
    )
    assert _ladder_counts(forward) == _ladder_counts(backward)


def test_each_rung_accounts_for_everyone_it_removed(pool_conn: Any) -> None:
    result = _estimate(
        pool_conn, {"countries": [_US], "languages": ["English"], "followers_min": 50000}
    )
    previous = result["ladder"][0]["count"]
    for rung in result["ladder"][1:]:
        assert rung["removed"] == rung["removed_unknown"] + rung["removed_mismatch"]
        assert previous - rung["removed"] == rung["count"]
        previous = rung["count"]


def test_drop_one_ranks_the_filter_worth_loosening_first(pool_conn: Any) -> None:
    result = _estimate(
        pool_conn, {"countries": [_US], "languages": ["English"], "followers_min": 50000}
    )
    gains = [row["gain"] for row in result["drop_one"]]
    assert gains == sorted(gains, reverse=True)
    assert result["drop_one"][0]["filter"] == "languages"
    language = _drop(result, "languages")
    assert (language["count"], language["gain"], language["gain_unknown"], language["gain_mismatch"]) == (
        10, 8, 7, 1,
    )
    # 松国家 / 松粉丝下限一个人都换不回来 —— 该松的只有语言。
    assert _drop(result, "countries")["gain"] == 0
    assert _drop(result, "followers_min")["gain"] == 0


def test_drop_one_count_equals_the_ladder_without_that_filter(pool_conn: Any) -> None:
    """逐条松的数必须与「真的不勾这一项」重估出来的数逐个对上。"""
    full = {"countries": [_US], "languages": ["English"], "followers_min": 50000}
    result = _estimate(pool_conn, full)
    for name in ("countries", "languages", "followers_min"):
        without = {key: value for key, value in full.items() if key != name}
        assert _drop(result, name)["count"] == _estimate(pool_conn, without)["estimated"], name


# ── 零成本 ─────────────────────────────────────────────────────────────────


def test_estimate_only_ever_issues_select_statements(pool_conn: Any) -> None:
    _estimate(
        pool_conn,
        {"countries": [_US], "languages": ["English"], "followers_min": 50000, "verticals": ["lifestyle"]},
    )
    assert pool_conn.statements
    for statement in pool_conn.statements:
        assert statement.upper().startswith("SELECT"), statement
        assert " INSERT " not in f" {statement.upper()} "
        assert " UPDATE " not in f" {statement.upper()} "
        assert " DELETE " not in f" {statement.upper()} "


def test_plain_filter_set_costs_exactly_one_grouped_count(pool_conn: Any) -> None:
    """没勾内容方向时只准发**一条** GROUP BY COUNT —— 便宜是这个功能成立的前提。"""
    result = _estimate(pool_conn, {"countries": [_US], "followers_min": 50000})
    assert len(pool_conn.statements) == 1
    assert "COUNT(*) AS group_count" in pool_conn.statements[0]
    assert "GROUP BY" in pool_conn.statements[0]
    assert result["cost"]["sql_queries"] == 1
    assert result["cost"]["vertical_rows_classified"] == 0


def test_cost_ledger_says_zero_provider_zero_model_zero_write(pool_conn: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.platform.llm_production as llm_production

    def _explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("产量预估绝不许调模型")

    monkeypatch.setattr(llm_production, "generate_text", _explode, raising=False)
    result = _estimate(
        pool_conn,
        {"countries": [_US], "languages": ["English"], "followers_min": 50000, "verticals": ["lifestyle"]},
    )
    assert result["cost"]["provider_calls"] == 0
    assert result["cost"]["llm_calls"] == 0
    assert result["cost"]["writes"] == 0


def test_vertical_pass_only_reads_the_people_the_ladder_actually_needs(pool_conn: Any) -> None:
    """勾了内容方向也不许扫全池:只判「最多差一项」的那批人。"""
    result = _estimate(
        pool_conn,
        {"countries": [_US], "languages": ["English"], "followers_min": 50000, "verticals": ["lifestyle"]},
    )
    classified = result["cost"]["vertical_rows_classified"]
    assert 0 < classified < result["pool_total"]


# ── 边界 ───────────────────────────────────────────────────────────────────


def test_empty_filter_set_is_the_whole_pool_and_claims_nothing_else(pool_conn: Any) -> None:
    result = _estimate(pool_conn, {})
    assert result["estimated"] == result["pool_total"] == 100
    assert result["ladder"] == [
        {"step": 0, "filter": None, "label": "全池(未加任何筛选)", "count": 100}
    ]
    assert result["tri_state"] == []
    assert result["drop_one"] == []
    assert result["applied"] == []


@pytest.mark.parametrize("empty", [None, "", {}])
def test_absent_filters_never_raise(pool_conn: Any, empty: Any) -> None:
    assert _estimate(pool_conn, empty)["estimated"] == 100


def test_every_dimension_at_once_still_balances(pool_conn: Any) -> None:
    result = _estimate(
        pool_conn,
        {
            "platforms": ["youtube"],
            "countries": [_US],
            "languages": ["English"],
            "verticals": ["lifestyle"],
            "followers_min": 1000,
            "followers_max": 1000000,
        },
    )
    assert [row["filter"] for row in result["tri_state"]] == [
        "platforms", "countries", "followers_min", "followers_max", "languages", "verticals",
    ]
    assert result["estimated"] == 1
    for row in result["tri_state"]:
        assert row["qualified"] == result["estimated"]
    counts = _ladder_counts(result)
    assert all(later <= earlier for earlier, later in zip(counts, counts[1:]))


def test_vertical_dimension_splits_into_three_honest_buckets(pool_conn: Any) -> None:
    """两个美国英语大号:一个做生活方式、一个做相机 —— 一个合格一个确认不符,没有未知。"""
    result = _estimate(
        pool_conn,
        {"countries": [_US], "languages": ["English"], "followers_min": 50000, "verticals": ["lifestyle"]},
    )
    verticals = _tri(result, "verticals")
    assert (verticals["qualified"], verticals["unknown"], verticals["mismatch"]) == (1, 0, 1)
    assert result["estimated"] == 1


def test_impossible_combination_says_zero_without_pretending_it_searched(pool_conn: Any) -> None:
    result = _estimate(pool_conn, {"countries": [_US], "languages": ["Korean"]})
    assert result["estimated"] == 0
    assert _drop(result, "languages")["gain"] == 20
    assert result["scope"] == yield_estimate.SCOPE
    assert "联网" in result["scope_note"]


# ── 诚实 ───────────────────────────────────────────────────────────────────


def test_response_always_declares_what_it_did_not_estimate(pool_conn: Any) -> None:
    result = _estimate(pool_conn, {"countries": [_US], "gear_content": "yes"})
    items = [entry["item"] for entry in result["not_estimated"]]
    assert "online_discovery" in items
    assert "favorites_exclusion" in items
    assert "gear_content" in items
    assert all(entry["note"] for entry in result["not_estimated"])


def test_applied_view_shows_the_mode_the_operator_is_actually_on(pool_conn: Any) -> None:
    result = _estimate(
        pool_conn,
        {"countries": {"values": [_US], "mode": "include_unknown"}, "followers_min": 50000},
    )
    applied = {entry["filter"]: entry for entry in result["applied"]}
    assert applied["countries"]["mode"] == "include_unknown"
    assert applied["countries"]["values"] == [_US]
    assert applied["followers_min"]["value"] == 50000


def test_unsupported_filter_keys_are_surfaced_not_swallowed(pool_conn: Any) -> None:
    result = _estimate(pool_conn, {"countries": [_US], "vibes": ["cool"]})
    assert "vibes" in result["unsupported"]


def test_non_object_filters_are_rejected_loudly(pool_conn: Any) -> None:
    with pytest.raises(ValueError):
        _estimate(pool_conn, ["United States"])


def test_labels_stay_in_plain_operator_language(pool_conn: Any) -> None:
    """门面禁术语:标签里不许出现内部字段名或厂商名。"""
    result = _estimate(
        pool_conn,
        {"countries": [_US], "languages": ["English"], "followers_min": 50000, "verticals": ["lifestyle"]},
    )
    labels = [row["label"] for row in result["tri_state"]] + [row["label"] for row in result["ladder"]]
    banned = ("llm", "vertical", "sql", "gemini", "claude", "openai", "apify", "qdrant")
    for label in labels:
        assert not any(word in label.lower() for word in banned), label


# ── 只读端点 ───────────────────────────────────────────────────────────────


def test_endpoint_is_registered_on_the_pool_router() -> None:
    from app.api.routers.vkpi_kol_pool import router as pool_router

    paths = {route.path for route in pool_router.routes}
    assert "/api/admin/vkpi/kol-pool/yield-estimate" in paths


def test_endpoint_is_registered_in_the_release_read_only_whitelist() -> None:
    """新只读 GET 必须登记白名单,否则发布验证期间会被围栏挡掉。"""
    from app.core.release_validation import release_validation_request_allowed

    assert release_validation_request_allowed("GET", "/api/admin/vkpi/kol-pool/yield-estimate")
    assert not release_validation_request_allowed("POST", "/api/admin/vkpi/kol-pool/yield-estimate")


def test_endpoint_accepts_both_repeated_and_comma_joined_values(
    pool_conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.routers import vkpi_kol_pool_yield_route as route

    seen: list[Any] = []

    def _capture(filters: Any) -> dict[str, Any]:
        seen.append(filters)
        return _estimate(pool_conn, filters)

    monkeypatch.setattr(route, "estimate_pool_yield", _capture)
    result = route.estimate_kol_pool_yield(
        countries=["United States,Japan"],
        languages=["English"],
        platforms=["youtube", "instagram"],
        verticals=[],
        countries_mode="include_unknown",
        languages_mode="require",
        followers_min=50000,
        followers_max=None,
        gear_content="any",
        staff={"id": 1},
    )
    assert seen == [
        {
            "countries": {"values": ["United States", "Japan"], "mode": "include_unknown"},
            "languages": {"values": ["English"], "mode": "require"},
            "platforms": ["youtube", "instagram"],
            "followers_min": 50000,
        }
    ]
    assert result["provider_calls"] is False
    assert result["write_db"] is False
    assert result["execution_mode"] == "provider_free_estimate"


# ── 整组三态总账 + 自动放宽车道的取数口 ──────────────────────────────────────


def test_combination_totals_account_for_every_single_person(pool_conn: Any) -> None:
    """三档相加必须等于全池 —— 一个人都不许没着落。"""
    for filters in (
        {},
        {"countries": [_US]},
        {"countries": [_US], "languages": ["English"], "followers_min": 50000},
        {"countries": [_US], "languages": ["Korean"]},
    ):
        totals = _estimate(pool_conn, filters)["totals"]
        assert (
            totals["qualified"]
            + totals["unknown"]
            + totals["unrecallable"]
            + totals["mismatch"]
            == totals["pool_total"]
            == 100
        )


def test_totals_never_sell_the_unfilled_bucket_as_recoverable(pool_conn: Any) -> None:
    """「只差没填」这一档是**诊断**(该去补数据了),不是「切个档就能回来的人」。

    旧口径把它当成后者,自动放宽照着它松,松完一个人也没多。现在总账上直接钉一句
    ``unknown_recoverable_by_mode = False``,并且真去切一遍档,人数一个不多。
    """
    strict = {"countries": [_US], "languages": ["English"], "followers_min": 50000}
    baseline = _estimate(pool_conn, strict)
    totals = baseline["totals"]
    assert totals["unknown"] == 57  # 50 个国家没填 + 7 个语言没填
    assert totals["unknown_recoverable_by_mode"] is False
    admitted = _estimate(
        pool_conn,
        {
            "countries": {"values": [_US], "mode": "include_unknown"},
            "languages": {"values": ["English"], "mode": "include_unknown"},
            "followers_min": 50000,
        },
    )
    assert admitted["estimated"] == baseline["estimated"] == totals["qualified"]


def test_auto_relax_facing_estimator_exposes_the_agreed_keys(pool_conn: Any) -> None:
    """自动放宽车道按名字懒加载这个口子;键名对不上它就会如实报不可用。"""
    from app.domains.kol import search_yield_estimate

    out = search_yield_estimate.estimate_yield(
        {"countries": [_US], "languages": ["English"], "followers_min": 50000},
        get_connection=lambda: pool_conn,
    )
    assert out["qualified"] == out["count"] == 2
    # 全池口径:50 个国家没填 + 7 个语言没填 —— 挡住他们的全部只是「资料缺」。
    assert out["unknown"] == 57
    assert out["mismatch"] == 41
    assert out["pool_total"] == 100
    assert out["cost"]["provider_calls"] == 0 and out["cost"]["llm_calls"] == 0
    assert out["drop_one"][0]["filter"] == "languages"
