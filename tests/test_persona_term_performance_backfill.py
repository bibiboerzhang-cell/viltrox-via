"""persona 词效回填链的契约测试(迁移 306 的读端 + 写端)。

三件事钉死:

1. ``per_sku_term_performance`` 复用 v1/v2 归一 shim(同一个 ``normalize_term_row`` /
   ``_fold_term``):v1 行 youtube_search_calls 由 search_calls 推导、v2 行原样收下、
   同 (platform, anchor_source, term) 跨版本合并——裸按 v2 键聚合会把存量行读空。
2. 载荷口径:top_terms 只收真换回过合格新人的词且至多 5 条;exhausted_terms 是
   已抓干词清单;样本荒 ``low_sample=True``;配额为零产出率是 None 不是 0;
   读不出 → ``status='probe_failed'``,绝不伪装零产出空账。
3. 写端只有一条路:``build_product_personas._upsert``(重放 --execute 路径)顺带
   UPDATE ``term_performance_json``;无 scheduler、无触发器、零 LLM。persona 正文
   INSERT 先独立 commit,词效回填失败不回滚正文。
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from typing import Any

from app.domains.kol import discovery_term_yield as yield_mod

import scripts_local.build_product_personas as persona_script

SKU = "EPIC-65-MACRO"


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConn:
    """纯读假连接:记录 SQL 与参数,只放行 SELECT。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.seen: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: Any = ()) -> _FakeCursor:
        self.seen.append((sql, tuple(params)))
        assert sql.strip().upper().startswith("SELECT"), "聚合读端只允许 SELECT"
        return _FakeCursor(self.rows)


class _ReplayConn:
    """重放写路假连接:SELECT 回会话行,其余语句连同 commit 逐条记流水。"""

    def __init__(self, session_rows: list[dict[str, Any]]) -> None:
        self._session_rows = session_rows
        self.calls: list[tuple[str, ...]] = []

    def execute(self, sql: str, params: Any = ()) -> _FakeCursor:
        squashed = " ".join(str(sql).split())
        self.calls.append(("execute", squashed, tuple(params)))
        if squashed.upper().startswith("SELECT"):
            return _FakeCursor(self._session_rows)
        return _FakeCursor([])

    def commit(self) -> None:
        self.calls.append(("commit",))


def _v1_term(**overrides: Any) -> dict[str, Any]:
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
    row = _v1_term(quota_units=200, search_calls=2, qualified_new=1, candidates_returned=11)
    row.update({"youtube_search_calls": 2, "quota_units_deprecated": True})
    row.update(overrides)
    return row


def _session(sid: int, created_at: str, schema: str, terms: list[dict[str, Any]]) -> dict[str, Any]:
    evidence = {
        "schema": schema,
        "lane": "online_strict",
        "product_anchor": {"kind": "sku", "sku": SKU},
        "terms": terms,
    }
    return {"session_id": sid, "created_at": created_at, "term_evidence": evidence}


def _mixed_rows() -> list[dict[str, Any]]:
    """v1 + v2 会话:同词跨版本、v1 独有零配额 IG 腿、v2 独有已抓干 skip 词。"""
    ig = _v1_term(
        term="viltrox creator", platform="instagram", anchored=False,
        anchor="", quota_units=0, search_calls=1, qualified_new=1,
        candidates_returned=None, attribution="shared_round",
    )
    ig.pop("anchor_source")
    drained = _v2_term(
        term="fx mount lens review", anchor="fx mount", anchor_source="mount_category",
        quota_units=0, search_calls=1, youtube_search_calls=0, qualified_new=0,
        candidates_returned=0, exhausted=True, skipped="budget_gate",
    )
    return [
        _session(1118, "2026-08-28T02:05:39", "discovery_term_evidence_v1", [
            _v1_term(),
            _v1_term(term="Viltrox lens test", qualified_new=0, candidates_returned=7),
            ig,
        ]),
        _session(2001, "2026-08-29T12:00:00", "discovery_term_evidence_v2", [
            _v2_term(),
            drained,
        ]),
    ]


# ── ② SQL 兼容层与查询参数 ────────────────────────────────────────────────────
def test_sku_query_obeys_compat_layer_rules() -> None:
    conn = _FakeConn(_mixed_rows())
    yield_mod.per_sku_term_performance(SKU, conn, days=45)
    assert len(conn.seen) == 1
    sql, params = conn.seen[0]
    assert params == (SKU, 45)  # ? 占位:sku + 窗口两参
    assert sql.count("?") == 2
    assert "jsonb_exists(result_summary_json, 'discovery_term_evidence')" in sql
    assert "-> 'product_anchor' ->> 'sku'" in sql  # 取值走 -> 路径,不用 ? 算子
    assert " LIKE " not in sql.upper()
    assert "AS term_evidence" in sql and "AS session_id" in sql
    assert "make_interval(days => ?)" in sql


def test_default_window_is_a_full_year_and_clamped() -> None:
    conn = _FakeConn([])
    yield_mod.per_sku_term_performance(SKU, conn)
    assert conn.seen[0][1] == (SKU, yield_mod.MAX_WINDOW_DAYS)  # persona 知识长命,默认满窗
    conn2 = _FakeConn([])
    yield_mod.per_sku_term_performance(SKU, conn2, days=99999)
    assert conn2.seen[0][1] == (SKU, yield_mod.MAX_WINDOW_DAYS)
    conn3 = _FakeConn([])
    yield_mod.per_sku_term_performance(SKU, conn3, days=0)
    assert conn3.seen[0][1] == (SKU, 1)


# ── ② v1/v2 shim 复用 ─────────────────────────────────────────────────────────
def test_per_sku_reuses_v1_v2_shim_and_merges_across_versions() -> None:
    result = yield_mod.per_sku_term_performance(SKU, _FakeConn(_mixed_rows()), days=30)
    assert result["status"] == "ok"
    assert result["sessions_scanned"] == 2
    assert result["sessions_used"] == 2
    top = result["top_terms"]
    merged = top[0]
    assert (merged["platform"], merged["term"]) == ("youtube", "Viltrox lens review")
    assert merged["sessions_count"] == 2               # v1 + v2 会话合并成一行
    assert merged["quota_units"] == 500                # 300(v1) + 200(v2)
    assert merged["youtube_search_calls"] == 5         # 3(v1 shim 推导) + 2(v2 原值)
    assert merged["qualified_new"] == 8
    assert merged["qualified_per_100_units"] == 1.6    # 8 * 100 / 500
    assert merged["normalized_v1_rows"] == 1           # shim 动过哪里,消费端看得见


def test_top_terms_exclude_zero_yield_and_keep_none_for_zero_quota() -> None:
    result = yield_mod.per_sku_term_performance(SKU, _FakeConn(_mixed_rows()), days=30)
    top = result["top_terms"]
    assert [row["term"] for row in top] == ["Viltrox lens review", "viltrox creator"]
    # 零产出的词(lens test / skip 词)不进高产榜,但计入台账与 totals。
    ig = top[1]
    assert ig["platform"] == "instagram"
    assert ig["anchor_source"] == yield_mod.UNLABELED_ANCHOR_SOURCE  # shim 的 unlabeled 口径
    assert ig["quota_units"] == 0
    assert ig["qualified_per_100_units"] is None  # 没烧配额不是零产出,绝不除零
    assert result["terms_count"] == 4
    assert result["totals"]["quota_units"] == 800
    assert result["totals"]["qualified_new"] == 9
    assert result["totals"]["qualified_per_100_units"] == 1.12


def test_exhausted_terms_list_names_the_drained_words() -> None:
    result = yield_mod.per_sku_term_performance(SKU, _FakeConn(_mixed_rows()), days=30)
    drained = result["exhausted_terms"]
    assert len(drained) == 1
    assert drained[0]["term"] == "fx mount lens review"
    assert drained[0]["platform"] == "youtube"
    assert drained[0]["exhausted_sessions"] == 1
    assert drained[0]["last_seen"] == "2026-08-29T12:00:00"


def test_top_terms_cap_at_five_ordered_by_yield_then_efficiency() -> None:
    terms = [
        _v2_term(term=f"viltrox word {idx}", quota_units=100 * (idx + 1), qualified_new=idx + 1)
        for idx in range(7)
    ]
    rows = [_session(3001, "2026-08-29T13:00:00", "discovery_term_evidence_v2", terms)]
    result = yield_mod.per_sku_term_performance(SKU, _FakeConn(rows), days=30)
    top = result["top_terms"]
    assert len(top) == yield_mod.PER_SKU_TOP_TERMS == 5
    assert [row["qualified_new"] for row in top] == [7, 6, 5, 4, 3]  # 人数降序
    assert result["terms_count"] == 7  # 榜外的词仍在台账口径里(totals 不缺斤短两)


# ── ④ 样本荒与失败方向 ────────────────────────────────────────────────────────
def test_low_sample_flag_is_visible_on_starved_samples() -> None:
    result = yield_mod.per_sku_term_performance(SKU, _FakeConn(_mixed_rows()), days=30)
    assert result["low_sample"] is True  # 2 会话 < 阈值 5:数据不够别当真
    assert result["low_sample_threshold"] == yield_mod.LOW_SAMPLE_SESSIONS


def test_empty_history_is_honest_ok_not_fabricated() -> None:
    result = yield_mod.per_sku_term_performance(SKU, _FakeConn([]), days=30)
    assert result["status"] == "ok"
    assert result["top_terms"] == []
    assert result["exhausted_terms"] == []
    assert result["low_sample"] is True
    assert result["totals"]["qualified_per_100_units"] is None


def test_probe_failure_does_not_fake_an_empty_ledger() -> None:
    class _Boom:
        def execute(self, sql: str, params: Any = ()) -> Any:
            raise RuntimeError("db down")

    result = yield_mod.per_sku_term_performance(SKU, _Boom(), days=30)
    assert result["status"] == "probe_failed"
    assert "top_terms" not in result  # 读不出来就说读不出来


def test_empty_sku_never_touches_the_database() -> None:
    conn = _FakeConn(_mixed_rows())
    result = yield_mod.per_sku_term_performance("   ", conn, days=30)
    assert result["status"] == "no_sku"
    assert conn.seen == []


# ── ③ 重放写列 ────────────────────────────────────────────────────────────────
def _persona_stub() -> dict[str, Any]:
    return {
        "what_is": "65mm 微距镜头",
        "key_specs_json": {"焦段": "65mm"},
        "ideal_persona": "微距摄影创作者",
        "ideal_creator_types_json": ["macro"],
        "verticals_json": ["photography"],
        "promotion_angles_json": ["1x-2x magnification"],
        "avoid_types_json": ["vlog only"],
    }


def test_replay_upsert_backfills_term_performance_column() -> None:
    conn = _ReplayConn(_mixed_rows())
    persona_script._upsert(conn, SKU, "Macro Extension Tube", _persona_stub(), "gpt-x")

    kinds = [(call[0], call[1].split()[0].upper()) if call[0] == "execute" else call
             for call in conn.calls]
    # persona 正文先 INSERT 并独立 commit,然后才是词效 SELECT + UPDATE + commit。
    assert kinds == [
        ("execute", "INSERT"),
        ("commit",),
        ("execute", "SELECT"),
        ("execute", "UPDATE"),
        ("commit",),
    ]

    update_sql, update_params = next(
        (call[1], call[2]) for call in conn.calls
        if call[0] == "execute" and call[1].upper().startswith("UPDATE")
    )
    assert "UPDATE vkpi_product_persona SET term_performance_json = ?" in update_sql
    assert update_sql.count("?") == 2 and update_params[1] == SKU
    payload = json.loads(update_params[0])
    assert payload["schema"] == yield_mod.PER_SKU_TERM_PERFORMANCE_SCHEMA
    assert payload["sku"] == SKU
    assert payload["status"] == "ok"
    assert payload["low_sample"] is True  # 样本荒标记原样进列
    assert payload["top_terms"][0]["term"] == "Viltrox lens review"


def test_probe_failed_payload_is_stored_verbatim_not_swallowed() -> None:
    class _SelectBoomConn(_ReplayConn):
        def execute(self, sql: str, params: Any = ()) -> _FakeCursor:
            if str(sql).strip().upper().startswith("SELECT"):
                self.calls.append(("execute", "SELECT boom", tuple(params)))
                raise RuntimeError("clone db unreachable")
            return super().execute(sql, params)

    conn = _SelectBoomConn([])
    persona_script._write_term_performance(conn, SKU)
    update_params = next(call[2] for call in conn.calls
                         if call[0] == "execute" and call[1].upper().startswith("UPDATE"))
    payload = json.loads(update_params[0])
    assert payload["status"] == "probe_failed"  # 「没读出来」也要让消费端看见


def test_backfill_write_path_is_replay_only_and_llm_free() -> None:
    """静态钉死:唯一调用点在 _upsert(重放路径);词效链全程不碰 llm_gateway。"""
    source = Path(persona_script.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    call_sites: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "_write_term_performance"
                ):
                    call_sites.append(node.name)
    assert call_sites == ["_upsert"], (
        f"_write_term_performance 只许被重放 upsert 调用,实际调用点: {call_sites}"
    )
    assert "llm_gateway" not in inspect.getsource(yield_mod), (
        "term_performance 是纯 SQL 聚合,聚合模块不许出现 llm_gateway"
    )
    write_src = inspect.getsource(persona_script._write_term_performance)
    assert "llm_gateway" not in write_src and "invoke(" not in write_src
