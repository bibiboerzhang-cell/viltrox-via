"""会话项来源(origin)口径:一处推断、写端落库、汇总统计、历史回填。

用户诉求(2026-08-25):搜索结果要一眼看出「哪些人是自有库里捞的、哪些是本次现场从
平台上新找到的」,而且数据要在写端做扎实,不许前端猜。

线上只读探针(2026-08-25,3939 行)坐实的陷阱写成断言钉在这里:
``existing_kol`` 427/427 行 payload 里也带 ``source=platform_discovery``,
所以 payload 标记绝不能压过 ``item_type``。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "backend", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.domains.kol import search_sessions_items  # noqa: E402
from app.domains.kol.search_sessions_item_origin import (  # noqa: E402
    ITEM_ORIGIN_LABELS,
    ITEM_ORIGIN_LOCAL_POOL,
    ITEM_ORIGIN_ONLINE_NEW,
    ITEM_ORIGIN_OPERATOR_URL,
    ITEM_ORIGIN_UNKNOWN,
    ITEM_ORIGIN_UNLABELED,
    ITEM_ORIGIN_VALUES,
    apply_item_origin_to_payload,
    explain_item_origin,
    infer_item_origin,
    origin_breakdown_from_pairs,
    session_origin_breakdown,
)
from scripts.ops import backfill_item_origin as backfill  # noqa: E402

MIGRATION_UP = ROOT / "migrations/301_vkpi_search_session_item_origin.sql"
MIGRATION_DOWN = ROOT / "migrations/301_vkpi_search_session_item_origin_down.sql"


class _Rows:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self.rowcount = len(self._rows)

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows]

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self._rows[0]) if self._rows else None


# --------------------------------------------------------------------------
# A. 推断规则:五种 item_type 各自正确,判不出返回 unknown 而不是猜
# --------------------------------------------------------------------------


def test_recall_candidate_is_local_pool() -> None:
    # 线上占比最大的一类(1401 条),此前一条都没有来源标记。
    assert infer_item_origin("recall_candidate", {}) == ITEM_ORIGIN_LOCAL_POOL


def test_existing_kol_stays_local_pool_despite_platform_discovery_source() -> None:
    # prod 427/427 条 existing_kol 都带 source=platform_discovery:那描述的是
    # 「这轮又在平台上碰到了这个人」,不是「这个人是新的」。payload 标记不许压过 item_type。
    verdict = explain_item_origin("existing_kol", {"source": "platform_discovery"})
    assert verdict["origin"] == ITEM_ORIGIN_LOCAL_POOL
    assert verdict["reason"] == "item_type:existing_kol"


def test_new_creator_with_discovery_source_is_online_new_with_double_evidence() -> None:
    verdict = explain_item_origin("new_creator", {"source": "platform_discovery"})
    assert verdict["origin"] == ITEM_ORIGIN_ONLINE_NEW
    assert verdict["reason"] == "item_type:new_creator+payload_marker"


def test_new_creator_without_marker_is_still_online_new_on_item_type_alone() -> None:
    verdict = explain_item_origin("new_creator", {})
    assert verdict["origin"] == ITEM_ORIGIN_ONLINE_NEW
    assert verdict["reason"] == "item_type:new_creator"


def test_online_qualified_candidate_recognizes_origin_lane_marker() -> None:
    verdict = explain_item_origin(
        "online_qualified_candidate",
        {"origin_lane": "online", "source": "platform_discovery_strict"},
    )
    assert verdict["origin"] == ITEM_ORIGIN_ONLINE_NEW
    assert verdict["reason"] == "item_type:online_qualified_candidate+payload_marker"
    assert verdict["markers"] == {
        "origin_lane": "online",
        "source": "platform_discovery_strict",
        "url_type": "",
    }


def test_url_item_types_are_operator_url() -> None:
    assert infer_item_origin("url_profile", {}) == ITEM_ORIGIN_OPERATOR_URL
    assert infer_item_origin("url_video", {"in_pool": True}) == ITEM_ORIGIN_OPERATOR_URL


def test_unknown_item_type_without_evidence_is_unknown_not_a_guess() -> None:
    # kol_pool_id / handle 是间接信号,不是来源判据 —— 拿它们硬猜就是编。
    verdict = explain_item_origin("unknown", {"kol_pool_id": 123, "handle": "someone"})
    assert verdict["origin"] == ITEM_ORIGIN_UNKNOWN
    assert verdict["reason"] == "no_origin_evidence"


def test_unknown_item_type_with_url_type_is_operator_url_not_unknown() -> None:
    # 隔离库(prod 数据副本,3806 行)实测:4 条 item_type='unknown' 全部带 url_type,
    # 且整表只有贴链接那条路径写 url_type(url_profile 951 / url_video 43 / unknown 4)。
    # 所以它们不是「来路不明的人」,而是「贴了个我们认不出平台的链接」。
    # 这条同时是读写口径的对齐点:前端也按 url_type 判「你提供的」,少了它两边打架。
    verdict = explain_item_origin(
        "unknown",
        {"in_pool": False, "url_type": "unknown", "video_flow": {}, "profile_flow": {}},
    )
    assert verdict["origin"] == ITEM_ORIGIN_OPERATOR_URL
    assert verdict["reason"] == "payload_url_type"


def test_unmapped_item_type_falls_back_to_explicit_payload_markers_only() -> None:
    assert explain_item_origin("weird_new_lane", {"origin_lane": "online"}) == {
        "origin": ITEM_ORIGIN_ONLINE_NEW,
        "reason": "payload_origin_lane",
        "item_type": "weird_new_lane",
        "markers": {"origin_lane": "online", "source": "", "url_type": ""},
    }
    # 现场发现的旁证优先于贴链接旁证:两个都在时不许把「本次新发现」降级成「手动录入」。
    assert (
        infer_item_origin("weird_new_lane", {"origin_lane": "online", "url_type": "profile"})
        == ITEM_ORIGIN_ONLINE_NEW
    )
    assert (
        explain_item_origin("weird_new_lane", {"source": "platform_discovery"})["reason"]
        == "payload_source"
    )
    assert infer_item_origin("weird_new_lane", {"source": "csv_import"}) == ITEM_ORIGIN_UNKNOWN


def test_every_live_item_type_maps_into_the_declared_value_set() -> None:
    live_item_types = (
        "recall_candidate",
        "new_creator",
        "url_profile",
        "existing_kol",
        "url_video",
        "online_qualified_candidate",
        "unknown",
    )
    for item_type in live_item_types:
        assert infer_item_origin(item_type, {}) in ITEM_ORIGIN_VALUES
    assert infer_item_origin(None, None) == ITEM_ORIGIN_UNKNOWN
    assert set(ITEM_ORIGIN_LABELS) == set(ITEM_ORIGIN_VALUES) | {ITEM_ORIGIN_UNLABELED}


def test_payload_patch_is_pure_and_self_describing() -> None:
    original = {"handle": "alice", "source": "platform_discovery"}
    patched = apply_item_origin_to_payload("new_creator", original)
    assert patched["origin"] == ITEM_ORIGIN_ONLINE_NEW
    assert patched["origin_reason"] == "item_type:new_creator+payload_marker"
    assert original == {"handle": "alice", "source": "platform_discovery"}


# --------------------------------------------------------------------------
# B. 迁移 301:字面同步、可重放、无 ASCII 问号
# --------------------------------------------------------------------------


def test_migration_301_check_literals_match_the_python_value_set() -> None:
    sql = MIGRATION_UP.read_text(encoding="utf-8")
    match = re.search(r"chk_vkpi_kol_search_session_items_origin\s+CHECK \(([^;]*)\)", sql)
    assert match, "origin CHECK constraint not found"
    literals = {value.strip("' ") for value in re.findall(r"'([a-z_]+)'", match.group(1))}
    assert literals == set(ITEM_ORIGIN_VALUES)
    assert "origin IS NULL OR origin IN" in match.group(1)


def test_migration_301_has_no_ascii_question_mark() -> None:
    # 兼容适配器把 ASCII ? 当占位符,注释里出现一个就炸 apply。
    for path in (MIGRATION_UP, MIGRATION_DOWN):
        assert "?" not in path.read_text(encoding="utf-8"), path.name


def test_migration_301_is_replayable_and_runner_transaction_safe() -> None:
    up = MIGRATION_UP.read_text(encoding="utf-8")
    down = MIGRATION_DOWN.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS origin" in up
    assert up.count("DROP CONSTRAINT IF EXISTS chk_vkpi_kol_search_session_items_origin") == 1
    assert up.count("CREATE INDEX IF NOT EXISTS") == 2
    # 迁移 234 起正向迁移必须跑在 runner 自己的事务里,不许自带事务控制。
    assert not re.search(r"(?mi)^\s*(BEGIN|COMMIT)\b", up)
    for index_name in re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", up):
        assert f"DROP INDEX IF EXISTS {index_name}" in down, index_name
    assert "DROP COLUMN IF EXISTS origin" in down


# --------------------------------------------------------------------------
# C. 汇总分布:NULL 报「尚未标注」,不冒充 unknown
# --------------------------------------------------------------------------


def test_breakdown_separates_unlabeled_rows_from_unknown_verdicts() -> None:
    breakdown = origin_breakdown_from_pairs(
        [
            (ITEM_ORIGIN_LOCAL_POOL, "recall_candidate", 12),
            (ITEM_ORIGIN_ONLINE_NEW, "new_creator", 5),
            (ITEM_ORIGIN_UNKNOWN, "unknown", 1),
            (None, "recall_candidate", 3),
        ]
    )
    assert breakdown["total"] == 21
    assert breakdown["counts"] == {
        ITEM_ORIGIN_LOCAL_POOL: 12,
        ITEM_ORIGIN_ONLINE_NEW: 5,
        ITEM_ORIGIN_OPERATOR_URL: 0,
        ITEM_ORIGIN_UNKNOWN: 1,
        ITEM_ORIGIN_UNLABELED: 3,
    }
    assert breakdown["by_item_type"]["recall_candidate"] == {
        ITEM_ORIGIN_LOCAL_POOL: 12,
        ITEM_ORIGIN_UNLABELED: 3,
    }
    assert breakdown["schema"] == "session_item_origin_v1"


def test_session_breakdown_reads_one_group_by_from_the_items_table() -> None:
    seen: list[tuple[str, tuple[Any, ...]]] = []

    class _Conn:
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
            seen.append((" ".join(sql.split()), params))
            return _Rows(
                [
                    {"origin": ITEM_ORIGIN_LOCAL_POOL, "item_type": "recall_candidate", "item_count": 30},
                    {"origin": ITEM_ORIGIN_ONLINE_NEW, "item_type": "new_creator", "item_count": 7},
                ]
            )

    breakdown = session_origin_breakdown(_Conn(), 1129)
    assert len(seen) == 1
    sql, params = seen[0]
    assert "GROUP BY origin, item_type" in sql
    assert "COUNT(*) AS item_count" in sql
    assert params == (1129,)
    assert breakdown["counts"][ITEM_ORIGIN_LOCAL_POOL] == 30
    assert breakdown["counts"][ITEM_ORIGIN_ONLINE_NEW] == 7
    assert breakdown["total"] == 37


# --------------------------------------------------------------------------
# D. 写端:落库即带来源,重写不许把已判定的来源降级成 unknown
# --------------------------------------------------------------------------


class _UpsertConn:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.params: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
        self.sql.append(" ".join(sql.split()))
        self.params.append(params)
        payload = params[12]
        return _Rows(
            [
                {
                    "id": 5,
                    "session_id": params[0],
                    "dedupe_key": params[1],
                    "item_type": params[2],
                    "status": params[3],
                    "stage": params[4],
                    "rank": params[5],
                    "score": params[6],
                    "kol_pool_id": params[7],
                    "evidence_id": params[8],
                    "job_id": params[9],
                    "source_url": params[10],
                    "origin": params[11],
                    "payload_json": payload,
                    "created_at": "2026-08-25T00:00:00Z",
                    "updated_at": "2026-08-25T00:00:00Z",
                }
            ]
        )


def test_upsert_item_persists_origin_column_and_self_describing_payload() -> None:
    conn = _UpsertConn()
    item = search_sessions_items._upsert_item(
        conn,
        1129,
        {
            "dedupe_key": "recall:4242",
            "item_type": "recall_candidate",
            "status": "matched",
            "stage": "identified",
            "kol_pool_id": 4242,
            "payload": {"platform": "youtube", "handle": "alice"},
        },
    )
    sql = conn.sql[0]
    params = conn.params[0]
    assert "source_url, origin, payload_json)" in sql
    assert params[11] == ITEM_ORIGIN_LOCAL_POOL
    payload = json.loads(params[12])
    assert payload["origin"] == ITEM_ORIGIN_LOCAL_POOL
    assert payload["origin_reason"] == "item_type:recall_candidate"
    # 读回的 item 也带着自描述来源,读端不用再猜。
    assert item["payload"]["origin"] == ITEM_ORIGIN_LOCAL_POOL


def test_upsert_conflict_never_downgrades_a_known_origin_to_unknown() -> None:
    conn = _UpsertConn()
    search_sessions_items._upsert_item(
        conn,
        1129,
        {"dedupe_key": "x:1", "item_type": "unknown", "payload": {}},
    )
    sql = conn.sql[0]
    assert (
        "origin=COALESCE(NULLIF(EXCLUDED.origin, ?), "
        "vkpi_kol_search_session_items.origin, EXCLUDED.origin)" in sql
    )
    # ON CONFLICT 子句的占位符排在 VALUES 之后,必须是最后一个入参。
    assert conn.params[0][-1] == ITEM_ORIGIN_UNKNOWN
    assert conn.params[0][11] == ITEM_ORIGIN_UNKNOWN


def test_update_session_always_persists_a_fresh_origin_breakdown() -> None:
    updates: list[tuple[str, tuple[Any, ...]]] = []

    class _Conn:
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
            flat = " ".join(sql.split())
            if flat.startswith("SELECT origin AS origin"):
                return _Rows(
                    [
                        {"origin": ITEM_ORIGIN_LOCAL_POOL, "item_type": "recall_candidate", "item_count": 30},
                        {"origin": ITEM_ORIGIN_ONLINE_NEW, "item_type": "new_creator", "item_count": 7},
                        {"origin": None, "item_type": "url_profile", "item_count": 2},
                    ]
                )
            # 完成度与来源同为「每次持久化都从行里现算」的口径,
            # 同一个写入口一并落库(见 search_sessions_completion)。
            if flat.startswith("SELECT status AS item_status"):
                return _Rows(
                    [
                        {"item_status": "ready", "item_stage": "profile", "item_count": 37},
                        {"item_status": "partial", "item_stage": "summary", "item_count": 2},
                    ]
                )
            if flat.startswith("SELECT * FROM vkpi_kol_search_session_items"):
                return _Rows(
                    [
                        {
                            "id": index,
                            "session_id": 1129,
                            "item_type": "recall_candidate",
                            "status": "ready",
                            "rank": index,
                            "kol_pool_id": index,
                            "source_url": f"https://youtube.com/@local-{index}",
                            "payload_json": {"platform": "youtube", "handle": f"local-{index}"},
                        }
                        for index in range(1, 31)
                    ]
                    + [
                        {
                            "id": 30 + index,
                            "session_id": 1129,
                            "item_type": "new_creator",
                            "status": "identified",
                            "rank": 30 + index,
                            "source_url": f"https://instagram.com/online-{index}",
                            "payload_json": {"platform": "instagram", "handle": f"online-{index}"},
                        }
                        for index in range(1, 8)
                    ]
                )
            updates.append((flat, params))
            return _Rows()

    caller_summary = {"kind": "kol_recall", "items_written": 30}
    search_sessions_items._update_session(
        _Conn(),
        1129,
        status="ready",
        summary=caller_summary,
    )
    assert len(updates) == 1
    persisted = json.loads(updates[0][1][1])
    breakdown = persisted["origin_breakdown"]
    assert breakdown["counts"][ITEM_ORIGIN_LOCAL_POOL] == 30
    assert breakdown["counts"][ITEM_ORIGIN_ONLINE_NEW] == 7
    assert breakdown["counts"][ITEM_ORIGIN_UNLABELED] == 2
    assert breakdown["labels"][ITEM_ORIGIN_ONLINE_NEW] == "本次新发现"
    assert persisted["kind"] == "kol_recall"
    assert persisted["items_count"] == 37
    assert persisted["returned_count"] == 37
    assert persisted["match_status"] == "matched"
    assert persisted["diagnostics"]["returned_count"] == 37
    assert persisted["result_projection"]["by_lane"] == {
        "recall": 30,
        "discovery": 7,
        "online": 0,
    }
    # 同一次写入也落完成度:37 人已出结果 / 2 人还卡在资料补全。
    completion = persisted["completion"]
    assert completion["ready"] == 37
    assert completion["stuck"] == 2
    assert completion["stuck_by_stage"] == {"summary": 2}
    assert completion["headline"] == "37 人已出结果,2 人资料补全中"
    # 不许就地改调用方传进来的 summary。
    assert "origin_breakdown" not in caller_summary


# --------------------------------------------------------------------------
# E. 回填脚本:默认 dry-run 零写入、只填空值、冲突行跳过
# --------------------------------------------------------------------------


class _BackfillConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.selects: list[tuple[str, tuple[Any, ...]]] = []
        self.updates: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
        flat = " ".join(sql.split())
        if flat.startswith("SELECT"):
            self.selects.append((flat, params))
            after_id = params[-2]
            take = params[-1]
            pending = [row for row in self._rows if int(row["id"]) > int(after_id)]
            if "session_id = ?" in flat:
                pending = [row for row in pending if int(row["session_id"]) == int(params[0])]
            return _Rows(pending[: int(take)])
        self.updates.append((flat, params))
        return _Rows([{"id": params[-1]}])

    def commit(self) -> None:
        self.commits += 1


_SAMPLE_ROWS = [
    {
        "id": 1,
        "session_id": 1129,
        "item_type": "recall_candidate",
        "payload_json": json.dumps({"handle": "alice"}),
    },
    {
        "id": 2,
        "session_id": 1129,
        "item_type": "new_creator",
        "payload_json": json.dumps({"source": "platform_discovery", "handle": "bob"}),
    },
    {
        "id": 3,
        "session_id": 1129,
        "item_type": "existing_kol",
        "payload_json": json.dumps({"source": "platform_discovery", "handle": "carol"}),
    },
    {
        "id": 4,
        "session_id": 1130,
        "item_type": "url_profile",
        "payload_json": json.dumps({"url_type": "profile"}),
    },
    {
        "id": 5,
        "session_id": 1130,
        "item_type": "unknown",
        "payload_json": json.dumps({"handle": "mystery"}),
    },
]


def _run_backfill(monkeypatch, rows: list[dict[str, Any]], **kwargs: Any):
    conn = _BackfillConn([dict(row) for row in rows])
    monkeypatch.setattr(backfill, "get_conn", lambda: conn)
    options = {"apply_changes": False, "limit": None, "batch_size": 2, "session_id": None}
    options.update(kwargs)
    return conn, backfill.run(**options)


def test_backfill_dry_run_writes_nothing_and_reports_the_real_distribution(monkeypatch) -> None:
    conn, result = _run_backfill(monkeypatch, _SAMPLE_ROWS)
    assert conn.updates == []
    assert conn.commits == 0
    assert result["mode"] == "dry_run"
    assert result["written_rows"] == 0
    assert result["scanned_null_origin_rows"] == 5
    assert result["by_origin"] == {
        ITEM_ORIGIN_LOCAL_POOL: 2,
        ITEM_ORIGIN_ONLINE_NEW: 1,
        ITEM_ORIGIN_OPERATOR_URL: 1,
        ITEM_ORIGIN_UNKNOWN: 1,
    }
    assert result["by_item_type_and_origin"]["existing_kol/local_pool"] == 1
    assert result["by_reason"]["no_origin_evidence"] == 1


def test_backfill_apply_only_fills_empty_values_and_leaves_updated_at_alone(monkeypatch) -> None:
    conn, result = _run_backfill(monkeypatch, _SAMPLE_ROWS, apply_changes=True)
    assert result["mode"] == "apply"
    assert result["written_rows"] == 5
    assert conn.commits == 1
    assert len(conn.updates) == 5
    for sql, params in conn.updates:
        assert "WHERE id=? AND origin IS NULL" in sql
        assert "updated_at" not in sql
        assert params[0] in ITEM_ORIGIN_VALUES
    first_payload = json.loads(conn.updates[0][1][1])
    assert first_payload["origin"] == ITEM_ORIGIN_LOCAL_POOL
    assert first_payload["origin_reason"] == "item_type:recall_candidate"
    assert first_payload["handle"] == "alice"


def test_backfill_skips_rows_whose_payload_origin_disagrees(monkeypatch) -> None:
    rows = [
        {
            "id": 9,
            "session_id": 1131,
            "item_type": "recall_candidate",
            "payload_json": json.dumps({"origin": ITEM_ORIGIN_ONLINE_NEW, "handle": "dave"}),
        }
    ]
    conn, result = _run_backfill(monkeypatch, rows, apply_changes=True)
    assert conn.updates == []
    assert result["conflict_rows_skipped"] == 1
    assert result["planned_fill_rows"] == 0
    assert result["conflict_samples"][0]["payload_origin"] == ITEM_ORIGIN_ONLINE_NEW
    assert result["conflict_samples"][0]["origin"] == ITEM_ORIGIN_LOCAL_POOL


def test_backfill_keeps_an_agreeing_payload_origin_untouched(monkeypatch) -> None:
    rows = [
        {
            "id": 11,
            "session_id": 1131,
            "item_type": "recall_candidate",
            "payload_json": json.dumps(
                {"origin": ITEM_ORIGIN_LOCAL_POOL, "origin_reason": "item_type:recall_candidate"}
            ),
        }
    ]
    conn, result = _run_backfill(monkeypatch, rows, apply_changes=True)
    assert result["conflict_rows_skipped"] == 0
    assert result["payload_origin_field_added_rows"] == 0
    assert len(conn.updates) == 1


def test_backfill_scope_filters_by_session(monkeypatch) -> None:
    conn, result = _run_backfill(monkeypatch, _SAMPLE_ROWS, session_id=1129)
    select_sql, select_params = conn.selects[0]
    assert "WHERE origin IS NULL AND session_id = ? AND id > ? ORDER BY id LIMIT ?" in select_sql
    assert select_params[0] == 1129
    assert result["scope"]["session_id"] == 1129
    assert result["scanned_null_origin_rows"] == 3
    assert conn.updates == []
