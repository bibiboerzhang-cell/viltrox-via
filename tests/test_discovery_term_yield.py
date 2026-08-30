"""discovery_term_yield 的契约测试:v1/v2 归一 shim + 产出率算术 + 样本荒可见。

写端 schema 已从 v1 漂到 v2(单桶 quota_units → 拆 youtube_search_calls/combined),
库存旧行全是 v1;读端裸按 v2 键聚合会在存量行取空。本文件用 v1+v2 混合样本钉住:

1. 归一正确:v1 行 youtube_search_calls 由 search_calls 推导(仅 YouTube 腿),
   v2 行原样收下;同 (platform, anchor_source, term) 跨版本合并成一行。
2. per_100_units 算术:qualified_new * 100 / quota_units,round 2。
3. 零配额诚实置 None —— 「没花钱」不是「零产出」,绝不除零凑数。
4. 样本计数:sessions_count / first_seen / last_seen / low_sample。
5. SQL 兼容层规矩:? 占位、jsonb_exists(不用 ? 算子)、无 LIKE、聚合带 AS。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.domains.kol import discovery_term_yield as yield_mod
from app.domains.kol import profile_discovery_evidence as evidence_mod


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.seen: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: Any = ()) -> _FakeCursor:
        self.seen.append((sql, tuple(params)))
        assert sql.strip().upper().startswith("SELECT"), "读端只允许 SELECT"
        return _FakeCursor(self.rows)


def _v1_term(**overrides: Any) -> dict[str, Any]:
    """v1 落库行形(照抄本地库 session 1118 的真实键形):无 youtube_search_calls。"""
    row = {
        "term": "Viltrox lens review",
        "anchor": "Viltrox",
        "rounds": [1, 2, 3],
        "anchored": True,
        "platform": "youtube",
        "exhausted": False,
        "attribution": "per_item",
        "quota_units": 300,
        "search_calls": 3,
        "anchor_source": "own_brand_category",
        "qualified_new": 7,
        "apify_actor_runs": 0,
        "candidates_returned": 39,
    }
    row.update(overrides)
    return row


def _v2_term(**overrides: Any) -> dict[str, Any]:
    """v2 落库行形(HEAD 写端 build_term_evidence):拆桶 + deprecated 标。"""
    row = _v1_term(quota_units=200, search_calls=2, qualified_new=1, candidates_returned=11)
    row.update({"youtube_search_calls": 2, "quota_units_deprecated": True})
    row.update(overrides)
    return row


def _evidence(schema: str, terms: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema": schema, "lane": "online_strict", "terms": terms}


def _session(sid: int, created_at: Any, evidence: Any) -> dict[str, Any]:
    return {"session_id": sid, "created_at": created_at, "term_evidence": evidence}


def _mixed_rows() -> list[dict[str, Any]]:
    """v1 会话 + v2 会话:同词跨版本、v1 独有 IG 腿零配额、v2 独有 skip 词。"""
    v1 = _evidence("discovery_term_evidence_v1", [
        _v1_term(),
        _v1_term(term="Viltrox lens test", qualified_new=0, candidates_returned=7),
        _v1_term(  # v1 时代的 Apify 腿:零 YouTube 配额,却带 1 个合格新人。
            term="viltrox creator", platform="instagram", anchored=False,
            anchor="", quota_units=0, search_calls=1, qualified_new=1,
            candidates_returned=None, attribution="shared_round",
        ),
    ])
    for row in v1["terms"]:
        if row["platform"] == "instagram":
            row.pop("anchor_source", None)  # 无台账的行连档位键都没有
    v2 = _evidence("discovery_term_evidence_v2", [
        _v2_term(),
        _v2_term(  # v2 的 skip 词:没发出去,零配额零调用。
            term="fx mount lens review", anchor="fx mount",
            anchor_source="mount_category", quota_units=0, search_calls=1,
            youtube_search_calls=0, qualified_new=0, candidates_returned=0,
            exhausted=True, skipped="budget_gate",
        ),
    ])
    return [
        _session(1118, "2026-08-28T02:05:39", v1),
        # v2 会话:evidence 走 JSON 字符串、created_at 走 datetime,双形都要能读。
        _session(2001, datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc), json.dumps(v2)),
    ]


def _by_key(result: dict[str, Any]) -> dict[tuple, dict[str, Any]]:
    return {
        (row["platform"], row["anchor_source"], row["term"]): row
        for row in result["terms"]
    }


# ── 归一 shim ──────────────────────────────────────────────────────────────────
def test_normalize_v1_row_derives_youtube_search_calls() -> None:
    norm = yield_mod.normalize_term_row(_v1_term())
    assert norm is not None
    assert norm["youtube_search_calls"] == 3  # v1 YouTube 腿:occurrence 即 search.list
    assert norm["normalized_from_v1"] is True
    assert norm["quota_units"] == 300


def test_normalize_v1_non_youtube_row_gets_zero_search_calls() -> None:
    norm = yield_mod.normalize_term_row(
        _v1_term(platform="instagram", search_calls=2, quota_units=0)
    )
    assert norm is not None
    assert norm["youtube_search_calls"] == 0  # Apify 腿不吃 YouTube 配额,不许冒领
    assert norm["normalized_from_v1"] is True


def test_normalize_v2_row_takes_stored_calls_verbatim() -> None:
    norm = yield_mod.normalize_term_row(_v2_term(youtube_search_calls=5, search_calls=9))
    assert norm is not None
    assert norm["youtube_search_calls"] == 5  # 有拆桶值就用它,不再推导
    assert norm["normalized_from_v1"] is False


def test_normalize_rejects_unusable_rows_and_labels_missing_anchor_source() -> None:
    assert yield_mod.normalize_term_row({"platform": "youtube"}) is None
    assert yield_mod.normalize_term_row("not-a-dict") is None
    bare = _v1_term()
    bare.pop("anchor_source")
    norm = yield_mod.normalize_term_row(bare)
    assert norm is not None
    assert norm["anchor_source"] == yield_mod.UNLABELED_ANCHOR_SOURCE  # 不杜撰词梯档位


def test_reader_key_matches_writer_contract() -> None:
    assert yield_mod.TERM_EVIDENCE_KEY == evidence_mod.TERM_EVIDENCE_KEY


# ── 聚合 ───────────────────────────────────────────────────────────────────────
def test_mixed_v1_v2_sessions_merge_and_arithmetic() -> None:
    conn = _FakeConn(_mixed_rows())
    result = yield_mod.aggregate_term_yield(conn, days=30)
    assert result["status"] == "ok"
    assert result["sessions_scanned"] == 2
    assert result["sessions_used"] == 2
    assert result["schema_versions"] == {"v1": 1, "v2": 1, "unknown": 0}
    assert result["normalized_v1_rows"] == 3  # v1 会话的 3 行全走了 shim

    rows = _by_key(result)
    merged = rows[("youtube", "own_brand_category", "Viltrox lens review")]
    # 同 (platform, anchor_source, term) 的 v1+v2 会话合并成一行
    assert merged["sessions_count"] == 2
    assert merged["quota_units"] == 500          # 300(v1) + 200(v2)
    assert merged["youtube_search_calls"] == 5   # 3(v1 推导) + 2(v2 原值)
    assert merged["qualified_new"] == 8          # 7 + 1
    assert merged["qualified_per_100_units"] == 1.6  # 8 * 100 / 500
    assert merged["candidates_returned"] == 50   # 39 + 11
    assert merged["first_seen"] == "2026-08-28T02:05:39"
    assert merged["last_seen"] == "2026-08-29T12:00:00+00:00"

    lonely = rows[("youtube", "own_brand_category", "Viltrox lens test")]
    assert lonely["sessions_count"] == 1
    assert lonely["qualified_per_100_units"] == 0.0  # 烧了 300 配额换 0 人:真·零产出


def test_zero_quota_yield_is_none_not_zero() -> None:
    result = yield_mod.aggregate_term_yield(_FakeConn(_mixed_rows()), days=30)
    rows = _by_key(result)
    ig = rows[("instagram", yield_mod.UNLABELED_ANCHOR_SOURCE, "viltrox creator")]
    assert ig["quota_units"] == 0
    assert ig["qualified_new"] == 1
    assert ig["qualified_per_100_units"] is None  # 没烧配额 ≠ 零产出,绝不除零
    assert ig["candidates_unknown_sessions"] == 1  # shared_round 的 None 不算成 0
    skipped = rows[("youtube", "mount_category", "fx mount lens review")]
    assert skipped["qualified_per_100_units"] is None
    assert skipped["exhausted_sessions"] == 1
    assert skipped["skipped_sessions"] == 1


def test_by_anchor_source_tier_rollup() -> None:
    result = yield_mod.aggregate_term_yield(_FakeConn(_mixed_rows()), days=30)
    tiers = result["by_anchor_source"]
    own = tiers["own_brand_category"]
    assert own["terms_count"] == 2               # review + test
    assert own["quota_units"] == 800             # 300 + 300 + 200
    assert own["qualified_new"] == 8
    assert own["qualified_per_100_units"] == 1.0  # 8 * 100 / 800
    assert own["youtube_search_calls"] == 8       # 3 + 3(v1 推导) + 2(v2)
    assert tiers["mount_category"]["exhausted_sessions"] == 1
    assert tiers[yield_mod.UNLABELED_ANCHOR_SOURCE]["qualified_per_100_units"] is None
    totals = result["totals"]
    assert totals["quota_units"] == 800
    assert totals["qualified_new"] == 9
    assert totals["qualified_per_100_units"] == 1.12  # 9 * 100 / 800


def test_low_sample_visibility_and_note() -> None:
    result = yield_mod.aggregate_term_yield(_FakeConn(_mixed_rows()), days=30)
    assert result["low_sample"] is True  # 2 个会话 < 阈值 5:数据不够别当真
    assert result["low_sample_threshold"] == yield_mod.LOW_SAMPLE_SESSIONS
    for row in result["terms"]:
        assert row["sessions_count"] >= 1
        assert row["first_seen"] and row["last_seen"]


def test_unparseable_session_is_counted_not_fatal() -> None:
    rows = _mixed_rows() + [_session(3001, "2026-08-29T13:00:00", "{broken json")]
    result = yield_mod.aggregate_term_yield(_FakeConn(rows), days=30)
    assert result["status"] == "ok"
    assert result["sessions_scanned"] == 3
    assert result["sessions_used"] == 2
    assert result["sessions_unparseable"] == 1


def test_empty_window_is_honest_ok() -> None:
    result = yield_mod.aggregate_term_yield(_FakeConn([]), days=30)
    assert result["status"] == "ok"
    assert result["terms"] == []
    assert result["low_sample"] is True
    assert result["totals"]["qualified_per_100_units"] is None


def test_probe_failure_does_not_fake_an_empty_ledger() -> None:
    class _Boom:
        def execute(self, sql: str, params: Any = ()) -> Any:
            raise RuntimeError("db down")

    result = yield_mod.aggregate_term_yield(_Boom(), days=30)
    assert result["status"] == "probe_failed"
    assert "terms" not in result  # 读不出来就说读不出来,不返回像零产出的空账


def test_sql_obeys_compat_layer_rules() -> None:
    conn = _FakeConn(_mixed_rows())
    yield_mod.aggregate_term_yield(conn, days=45)
    assert len(conn.seen) == 1
    sql, params = conn.seen[0]
    assert params == (45,)  # ? 占位 + 单参数窗口
    assert "jsonb_exists(result_summary_json, 'discovery_term_evidence')" in sql
    assert "->" in sql and "-> '" in sql  # 取值用 -> 路径,存在性判断不用 ? 算子
    assert " LIKE " not in sql.upper()
    assert "AS term_evidence" in sql and "AS session_id" in sql  # 表达式列带 AS
    assert "make_interval(days => ?)" in sql


def test_days_window_is_clamped() -> None:
    conn = _FakeConn([])
    yield_mod.aggregate_term_yield(conn, days=99999)
    assert conn.seen[0][1] == (yield_mod.MAX_WINDOW_DAYS,)
    conn2 = _FakeConn([])
    yield_mod.aggregate_term_yield(conn2, days=0)
    assert conn2.seen[0][1] == (1,)
