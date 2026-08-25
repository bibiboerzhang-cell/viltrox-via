"""会话完成度口径 + 卡住项续补:线上实测数字钉成断言。

用户诉求(2026-08-25):
  1.「陆续不齐全的数据也要去补齐,而不是中途二分」
  2. 状态口径要诚实 —— 29/30 完成和 0/30 完成不许共用一个词。

线上只读探针(prod a05e48dd3,2026-08-25)的关键数字全部写进断言:
  * 104 个 partial 会话里:空会话 13 / 全无结果 47 / 真部分 41 / 其实全好 1
  * 242 条卡住项:profile 143 + summary 99;判档 T1 210 / T3 29 / T4 3
  * 242 条里在跑的 job:0;kol_pool_id 指向不存在行的:0
  * 既有 result_state 会说谎:3 个标 ready 实际 0 结果、1 个标 empty 实际全好
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "backend", ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.domains.kol.search_session_stuck_followup import (  # noqa: E402
    DISPOSITION_ADVANCED,
    DISPOSITION_RETRY,
    DISPOSITION_TERMINAL,
    FOLLOWUP_BACKOFF_HOURS,
    FOLLOWUP_MAX_ATTEMPTS,
    FOLLOWUP_SCHEMA,
    FOLLOWUP_TASK_KEY,
    MAX_BATCH_LIMIT,
    REASON_BLOCKED_NOT_RETRYABLE,
    REASON_NEEDS_HUMAN_CHOICE,
    REASON_NEVER_MATERIALIZED,
    REASON_PROFILE_COMPLETE,
    REASON_PROFILE_CRAWL_FAILED,
    REASON_RETRY_EXHAUSTED,
    classify_stuck_item,
    optional_enrichment_gaps,
    run_session_stuck_followup,
    run_summary_line,
)
from app.domains.kol.search_sessions_completion import (  # noqa: E402
    COMPLETION_SHAPE_ALL_COMPLETE,
    COMPLETION_SHAPE_CANDIDATES_ONLY,
    COMPLETION_SHAPE_EMPTY_SESSION,
    COMPLETION_SHAPE_NO_RESULTS,
    COMPLETION_SHAPE_PARTIAL,
    SESSION_COMPLETION_SCHEMA,
    completion_from_rows,
    session_completion_breakdown,
)

MIGRATION_UP = ROOT / "migrations/302_vkpi_session_stuck_item_followup.sql"
MIGRATION_DOWN = ROOT / "migrations/302_vkpi_session_stuck_item_followup_down.sql"
SESSION_SCHEMA_SQL = ROOT / "migrations/103_vkpi_kol_search_sessions.sql"
JOBS_REGISTRY = ROOT / "backend/app/services/scheduler/jobs_registry.py"
ITEMS_MODULE = ROOT / "backend/app/domains/kol/search_sessions_items.py"


# ==========================================================================
# A. 完成度口径:五种形态互斥可分辨(用户拍板第 1 条)
# ==========================================================================


def test_empty_session_and_full_session_are_distinguishable() -> None:
    """线上最误导的一点:#1146(0 条结果)与 #1144(29/30)都叫 partial。"""
    empty = completion_from_rows([])
    full_but_one = completion_from_rows([("ready", "profile", 17), ("ready", "summary", 12), ("partial", "summary", 1)])
    assert empty["shape"] == COMPLETION_SHAPE_EMPTY_SESSION
    assert full_but_one["shape"] == COMPLETION_SHAPE_PARTIAL
    assert empty["shape"] != full_but_one["shape"]
    # #1144 实测:30 行 = ready 17(profile)+ ready 12(summary)+ partial 1(summary)
    assert full_but_one["total"] == 30
    assert full_but_one["ready"] == 29
    assert full_but_one["stuck"] == 1
    assert full_but_one["stuck_by_stage"] == {"summary": 1}


def test_headline_speaks_plain_chinese_without_internal_jargon() -> None:
    """门面直接念的那句话:说人话,不出现内部术语。"""
    line = completion_from_rows([("ready", "profile", 29), ("partial", "summary", 1)])["headline"]
    assert line == "29 人已出结果,1 人资料补全中"
    for jargon in ("stage", "profile", "summary", "partial", "LLM", "payload"):
        assert jargon not in line


def test_all_complete_and_no_results_are_separate_shapes() -> None:
    all_done = completion_from_rows([("ready", "summary", 12)])
    none_done = completion_from_rows([("partial", "profile", 22)])
    assert all_done["shape"] == COMPLETION_SHAPE_ALL_COMPLETE
    assert all_done["headline"] == "12 人全部完成"
    # 线上 #1134:22 行全 partial,一条结果都没有。
    assert none_done["shape"] == COMPLETION_SHAPE_NO_RESULTS
    assert none_done["ready"] == 0
    assert none_done["stuck"] == 22


def test_recall_candidates_are_not_reported_as_stuck() -> None:
    """召回会话把人选写成 identified/matched 停着等人挑(prod 全库 832 + 1094 行),
    那是正常的等人,不是卡住。混成一桶会让几乎每个会话都显示「未完成」。"""
    shape = completion_from_rows([("matched", "identified", 20), ("identified", "identified", 10)])
    assert shape["shape"] == COMPLETION_SHAPE_CANDIDATES_ONLY
    assert shape["stuck"] == 0
    assert shape["candidate"] == 30
    assert shape["headline"] == "30 人已列出,尚未开始补全资料"


def test_unknown_item_status_never_counted_as_done() -> None:
    """没登记过的 status 不许谎报成已完成,也不许谎报成卡住去制造假告警。"""
    shape = completion_from_rows([("some_new_status", "profile", 3)])
    assert shape["ready"] == 0
    assert shape["stuck"] == 0
    assert shape["candidate"] == 3


def test_stuck_stage_distribution_matches_prod_counts() -> None:
    """线上 242 条卡住项的 stage 分布:profile 143 / summary 99,门面不用自己数。"""
    shape = completion_from_rows([("partial", "profile", 143), ("partial", "summary", 99), ("ready", "profile", 1349)])
    assert shape["stuck"] == 242
    assert shape["stuck_by_stage"] == {"profile": 143, "summary": 99}
    assert shape["stuck_by_stage_label"] == {"基础资料": 143, "资料补全": 99}


def test_completion_never_invents_a_session_status_value() -> None:
    """形态是 status 之外的正交维度:迁移 103 的 status CHECK 一个字不动。"""
    schema_sql = SESSION_SCHEMA_SQL.read_text(encoding="utf-8")
    assert "CHECK (status IN ('planned', 'running', 'ready', 'partial', 'failed', 'cancelled'))" in schema_sql
    for text in (MIGRATION_UP.read_text(encoding="utf-8"), MIGRATION_DOWN.read_text(encoding="utf-8")):
        assert "chk_vkpi_kol_search_sessions_status" not in text
        assert "chk_vkpi_kol_search_session_items_status" not in text


# ==========================================================================
# B. 完成度读端:一条 GROUP BY,聚合列带别名
# ==========================================================================


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows]

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self._rows[0]) if self._rows else None


class _FakeConn:
    def __init__(self, responses: list[list[dict[str, Any]]] | None = None) -> None:
        self.sql: list[str] = []
        self.params: list[tuple] = []
        self._responses = list(responses or [])
        self.committed = 0

    def execute(self, sql: str, params: tuple = ()) -> _FakeCursor:
        self.sql.append(sql)
        self.params.append(params)
        rows = self._responses.pop(0) if self._responses else []
        return _FakeCursor(rows)

    def commit(self) -> None:
        self.committed += 1


def test_breakdown_uses_one_grouped_read_with_aliases() -> None:
    conn = _FakeConn([[{"item_status": "ready", "item_stage": "profile", "item_count": 29},
                       {"item_status": "partial", "item_stage": "summary", "item_count": 1}]])
    result = session_completion_breakdown(conn, 1144)
    assert len(conn.sql) == 1
    assert "GROUP BY status, stage" in conn.sql[0]
    # compat 读回按名取值:聚合列必须带 AS 别名。
    assert "COUNT(*) AS item_count" in conn.sql[0]
    assert "status AS item_status" in conn.sql[0]
    assert conn.params[0] == (1144,)
    assert result["schema"] == SESSION_COMPLETION_SCHEMA
    assert result["ready"] == 29 and result["stuck"] == 1


def test_items_writer_persists_completion_next_to_origin() -> None:
    """result_summary_json 的唯一写入口一并落完成度,不留可漂的快照。"""
    source = ITEMS_MODULE.read_text(encoding="utf-8")
    assert 'persisted_summary["completion"] = session_completion_breakdown(conn, int(session_id))' in source
    assert "from app.domains.kol.search_sessions_completion import session_completion_breakdown" in source


# ==========================================================================
# C. 判档:242 条卡住项的三个真实档位(用户拍板第 1 条的补充「不能中途二分」)
# ==========================================================================


def _payload(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


def test_ready_profile_with_open_enrichment_is_advanced() -> None:
    """T1:线上 210 条。profile_execute.status=ready、池行三项俱全,
    缺的只是可选补全 —— 结算成 ready,缺口原样记账不抹掉。"""
    verdict = classify_stuck_item(
        _payload(profile_execute={
            "status": "ready",
            "contact_enrichment": {"status": "no_contacts"},
            "audience_enrichment": {"status": "partial"},
        }),
        kol_pool_id=3590,
        pool_present=True,
        attempts=1,
    )
    assert verdict["disposition"] == DISPOSITION_ADVANCED
    assert verdict["reason"] == REASON_PROFILE_COMPLETE
    assert verdict["terminal"] is False
    # no_contacts 是结论(找过了,没有),不是缺口;audience partial 才是。
    assert verdict["optional_gaps"] == ["audience:partial"]


def test_no_contacts_is_a_conclusion_not_a_gap() -> None:
    """线上 contact 分布:no_contacts 102 / ok 62 / pending_l0 46 / waiting_for_profile 18。
    前两者是了结,后两者才是真缺口。"""
    assert optional_enrichment_gaps({
        "contact_enrichment": {"status": "no_contacts"},
        "audience_enrichment": {"status": "ok"},
    }) == []
    assert optional_enrichment_gaps({
        "contact_enrichment": {"status": "pending_l0"},
        "audience_enrichment": {"status": "waiting_for_evidence"},
    }) == ["contact:pending_l0", "audience:waiting_for_evidence"]
    # 整块缺失要诚实记 missing,不许当成「已了结」。
    assert optional_enrichment_gaps({}) == ["contact:missing", "audience:missing"]


def test_needs_human_choice_is_terminal_and_never_retried() -> None:
    """T3:线上 29 条。身份有多个候选,机器不许替人选,也不许无限重试。"""
    verdict = classify_stuck_item(
        _payload(profile_execute={"status": "needs_human_choice"}),
        kol_pool_id=3929,
        pool_present=True,
        attempts=1,
    )
    assert verdict["disposition"] == DISPOSITION_TERMINAL
    assert verdict["reason"] == REASON_NEEDS_HUMAN_CHOICE
    assert verdict["terminal"] is True
    assert verdict["needs_human"] is True
    assert verdict["needs_paid_recovery"] is False


def test_blocked_job_with_retry_not_allowed_is_terminal() -> None:
    """T4:线上 3 条,job_last_error 是 JSON 文本且 retry_allowed=false。"""
    verdict = classify_stuck_item(
        _payload(
            job_status="blocked",
            job_last_error=json.dumps({
                "provider_calls_performed": None,
                "reason": "search_session_target_drifted",
                "retry_allowed": False,
                "status": "blocked",
            }),
        ),
        kol_pool_id=None,
        pool_present=False,
        attempts=1,
    )
    assert verdict["disposition"] == DISPOSITION_TERMINAL
    assert verdict["reason"] == REASON_BLOCKED_NOT_RETRYABLE
    assert verdict["needs_human"] is True


def test_blocked_job_that_allows_retry_is_not_parked() -> None:
    """失败方向安全:判不出「不可重试」就退避一轮,绝不误判成永不再补。"""
    verdict = classify_stuck_item(
        _payload(job_status="blocked", job_last_error=json.dumps({"retry_allowed": True})),
        kol_pool_id=4001,
        pool_present=True,
        attempts=1,
    )
    assert verdict["disposition"] == DISPOSITION_RETRY
    assert verdict["terminal"] is False


def test_retry_allowed_as_string_false_still_parks() -> None:
    """compat / JSON 两边都可能把布尔写成字符串,``bool('false')`` 是 True 的坑。"""
    verdict = classify_stuck_item(
        _payload(job_status="blocked", job_last_error=json.dumps({"retry_allowed": "false"})),
        kol_pool_id=None,
        pool_present=False,
        attempts=1,
    )
    assert verdict["reason"] == REASON_BLOCKED_NOT_RETRYABLE


def test_crawl_failure_is_parked_for_a_human_and_never_auto_paid() -> None:
    """重抓要花 Apify 的钱:只打标交人裁决,任务自己绝不下单。"""
    verdict = classify_stuck_item(
        _payload(profile_execute={"status": "crawl_failed"}),
        kol_pool_id=4100,
        pool_present=True,
        attempts=1,
    )
    assert verdict["disposition"] == DISPOSITION_TERMINAL
    assert verdict["reason"] == REASON_PROFILE_CRAWL_FAILED
    assert verdict["needs_paid_recovery"] is True


def test_orphan_pool_reference_is_parked() -> None:
    verdict = classify_stuck_item(_payload(profile_execute={"status": "ready"}), kol_pool_id=9999, pool_present=False, attempts=1)
    assert verdict["disposition"] == DISPOSITION_TERMINAL
    assert verdict["reason"] == REASON_NEVER_MATERIALIZED
    assert verdict["needs_paid_recovery"] is True


def test_never_materialized_item_is_parked() -> None:
    verdict = classify_stuck_item(_payload(), kol_pool_id=None, pool_present=False, attempts=1)
    assert verdict["reason"] == REASON_NEVER_MATERIALIZED


def test_retry_is_capped_and_then_parked() -> None:
    """绝不无限重试烧钱:次数用尽判终态并交人。"""
    inflight = _payload(profile_execute={"status": "pending"})
    assert classify_stuck_item(inflight, kol_pool_id=1, pool_present=True, attempts=FOLLOWUP_MAX_ATTEMPTS - 1)["disposition"] == DISPOSITION_RETRY
    exhausted = classify_stuck_item(inflight, kol_pool_id=1, pool_present=True, attempts=FOLLOWUP_MAX_ATTEMPTS)
    assert exhausted["disposition"] == DISPOSITION_TERMINAL
    assert exhausted["reason"] == REASON_RETRY_EXHAUSTED
    assert exhausted["needs_paid_recovery"] is True


def test_backoff_ladder_is_monotonic_and_bounded() -> None:
    assert list(FOLLOWUP_BACKOFF_HOURS) == sorted(FOLLOWUP_BACKOFF_HOURS)
    assert len(FOLLOWUP_BACKOFF_HOURS) == FOLLOWUP_MAX_ATTEMPTS


# ==========================================================================
# D. 跑一轮:幂等、有上限、可关停、零花钱
# ==========================================================================


class _RunConn(_FakeConn):
    """选行 → 写行 → 读会话 → 会话聚合 → 来源聚合 → 写会话。"""

    def __init__(
        self,
        due_rows: list[dict[str, Any]],
        completion_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        self._due = due_rows
        self._completion = completion_rows or [
            {"item_status": "ready", "item_stage": "summary", "item_count": 30}
        ]
        self.writes: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()) -> _FakeCursor:
        self.sql.append(sql)
        self.params.append(params)
        if "FROM vkpi_kol_search_session_items i" in sql and "status='partial'" in sql:
            return _FakeCursor(self._due)
        if sql.strip().upper().startswith("UPDATE"):
            self.writes.append((sql, params))
            return _FakeCursor([])
        if "FROM vkpi_kol_search_sessions" in sql:
            return _FakeCursor([{"status": "partial", "result_summary_json": "{}"}])
        if "GROUP BY status, stage" in sql:
            return _FakeCursor(self._completion)
        if "GROUP BY origin, item_type" in sql:
            return _FakeCursor([])
        return _FakeCursor([])


def _due_row(item_id: int, session_id: int, payload: dict[str, Any], *, pool: int | None = 3590) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "session_id": session_id,
        "item_stage": "summary",
        "kol_pool_id": pool,
        "payload_json": json.dumps(payload),
        "pool_row_id": pool,
    }


def test_dry_run_is_the_default_and_writes_nothing() -> None:
    """任何裸调用都必须是安全的:默认只判档不落库。"""
    conn = _RunConn([_due_row(1284, 300, {"profile_execute": {"status": "ready"}})])
    result = run_session_stuck_followup(get_conn_fn=lambda: conn)
    assert result["dry_run"] is True
    assert result["scanned"] == 1
    assert result["advanced"] == 1
    assert conn.writes == []
    assert conn.committed == 0


def test_execute_run_settles_only_the_advanced_item_status() -> None:
    conn = _RunConn([
        _due_row(1284, 300, {"profile_execute": {"status": "ready", "audience_enrichment": {"status": "partial"}}}),
        _due_row(221, 41, {"profile_execute": {"status": "needs_human_choice"}}),
    ])
    result = run_session_stuck_followup(dry_run=False, get_conn_fn=lambda: conn)
    assert result["advanced"] == 1 and result["terminal"] == 1
    assert result["needs_human"] == 1
    status_writes = [sql for sql, _ in conn.writes if "SET status='ready'" in sql]
    assert len(status_writes) == 1
    # 结算只用迁移 103 既有的取值,且带 status='partial' 守卫(并发安全 + 幂等)。
    assert "status='partial'" in status_writes[0]
    # 判终态的那条不动 status,诚实保留 partial。
    payload_only = [sql for sql, _ in conn.writes if "SET payload_json=?::jsonb" in sql and "status=" not in sql.split("SET")[1].split("WHERE")[0].replace("payload_json", "")]
    assert payload_only, "terminal item must be recorded without touching its status"
    assert conn.committed == 1


def test_terminal_and_backoff_are_recorded_on_the_item() -> None:
    conn = _RunConn([_due_row(221, 41, {"profile_execute": {"status": "needs_human_choice"}})])
    run_session_stuck_followup(dry_run=False, get_conn_fn=lambda: conn)
    written = json.loads(conn.writes[0][1][0])["followup"]
    assert written["schema"] == FOLLOWUP_SCHEMA
    assert written["terminal"] is True
    assert written["reason"] == REASON_NEEDS_HUMAN_CHOICE
    assert written["attempts"] == 1
    assert written["provider_calls_performed"] is False
    assert written["viltrox_fit_score_untouched"] is True
    # 终态不排下一轮:不再重试。
    assert "next_attempt_after" not in written


def test_retry_records_a_future_backoff_window() -> None:
    now = datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)
    conn = _RunConn([_due_row(500, 60, {"profile_execute": {"status": "pending"}})])
    run_session_stuck_followup(dry_run=False, get_conn_fn=lambda: conn, now=now)
    written = json.loads(conn.writes[0][1][0])["followup"]
    assert written["terminal"] is False
    expected = (now + timedelta(hours=FOLLOWUP_BACKOFF_HOURS[0])).isoformat(timespec="seconds").replace("+00:00", "Z")
    assert written["next_attempt_after"] == expected


def test_attempts_accumulate_across_rounds() -> None:
    prior = {"profile_execute": {"status": "pending"}, "followup": {"attempts": 3}}
    conn = _RunConn([_due_row(500, 60, prior)])
    run_session_stuck_followup(dry_run=False, get_conn_fn=lambda: conn)
    assert json.loads(conn.writes[0][1][0])["followup"]["attempts"] == 4


def test_selection_skips_terminal_and_未到期_rows() -> None:
    conn = _RunConn([])
    run_session_stuck_followup(get_conn_fn=lambda: conn)
    select_sql = conn.sql[0]
    assert "status='partial'" in select_sql
    assert "'{followup,terminal}'" in select_sql and "<> 'true'" in select_sql
    assert "'{followup,next_attempt_after}'" in select_sql
    assert "LIMIT ?" in select_sql
    # 禁 LIKE:整条 SQL 不许出现。
    assert " LIKE " not in select_sql.upper()


def test_batch_limit_is_capped() -> None:
    conn = _RunConn([])
    result = run_session_stuck_followup(limit=100000, get_conn_fn=lambda: conn)
    assert result["limit"] == MAX_BATCH_LIMIT
    assert conn.params[0][1] == MAX_BATCH_LIMIT
    assert run_session_stuck_followup(limit=0, get_conn_fn=lambda: conn)["limit"] == 1


def test_session_is_promoted_only_when_every_row_is_done() -> None:
    """陈旧的 partial 在所有行都完成后才升成 ready;绝不降级、绝不新增取值。"""
    conn = _RunConn([_due_row(1284, 1144, {"profile_execute": {"status": "ready"}})])
    result = run_session_stuck_followup(dry_run=False, get_conn_fn=lambda: conn)
    assert result["sessions_touched"] == 1
    assert result["sessions_promoted"] == 1
    session_write = [(sql, params) for sql, params in conn.writes if "vkpi_kol_search_sessions" in sql]
    assert len(session_write) == 1
    assert session_write[0][1][0] == "ready"
    summary = json.loads(session_write[0][1][1])
    assert summary["completion"]["shape"] == COMPLETION_SHAPE_ALL_COMPLETE


def test_session_is_not_promoted_while_unselected_candidates_remain() -> None:
    """保守:只有「每一行都完成」才升。还剩没挑的候选就不动 status ——
    那是别的写端的判断,这里只把 completion 说清楚,不替它下结论。
    (线上实测:续补跑完后有 26 个会话属于「请求的全干完、只剩没挑的候选」,
    要不要一并升成 ready 是口径决策,留给人拍板。)"""
    conn = _RunConn(
        [_due_row(700, 19, {"profile_execute": {"status": "ready"}})],
        completion_rows=[
            {"item_status": "ready", "item_stage": "profile", "item_count": 10},
            {"item_status": "matched", "item_stage": "identified", "item_count": 7},
        ],
    )
    result = run_session_stuck_followup(dry_run=False, get_conn_fn=lambda: conn)
    assert result["sessions_touched"] == 1
    assert result["sessions_promoted"] == 0
    session_write = [params for sql, params in conn.writes if "vkpi_kol_search_sessions" in sql]
    assert session_write[0][0] == "partial", "既有 status 不许被改动"
    summary = json.loads(session_write[0][1])
    assert summary["completion"]["shape"] == COMPLETION_SHAPE_PARTIAL
    assert summary["completion"]["candidate"] == 7


def test_second_round_finds_nothing_left_to_do() -> None:
    """幂等:结算过的行 status 已不是 partial,判终态的被 SQL 滤掉,
    退避中的到点才再捞 —— 同一轮重复跑不产生新副作用。"""
    conn = _RunConn([])
    result = run_session_stuck_followup(dry_run=False, get_conn_fn=lambda: conn)
    assert result["scanned"] == 0
    assert conn.writes == []
    assert conn.committed == 0


def test_run_never_performs_provider_or_llm_calls() -> None:
    conn = _RunConn([_due_row(1284, 300, {"profile_execute": {"status": "ready"}})])
    result = run_session_stuck_followup(dry_run=False, get_conn_fn=lambda: conn)
    assert result["provider_calls_performed"] is False
    assert result["llm_calls_performed"] is False
    assert result["viltrox_fit_score_untouched"] is True
    # 只碰会话项与会话两张表,绝不写 kol 池、绝不写 fit。
    for sql, _ in conn.writes:
        assert "vkpi_kol_pool" not in sql
        assert "viltrox_fit_score" not in sql


def test_run_summary_line_carries_the_ledger() -> None:
    line = run_summary_line({
        "scanned": 100, "advanced": 87, "terminal": 11, "retry": 2,
        "needs_human": 8, "needs_paid_recovery": 3,
        "sessions_touched": 34, "sessions_promoted": 6, "dry_run": False,
    })
    assert line == "scanned=100 advanced=87 terminal=11 retry=2 needs_human=8 needs_paid=3 sessions=34 promoted=6"
    assert len(line) <= 500
    assert run_summary_line({"dry_run": True}).startswith("dry_run ")


# ==========================================================================
# E. 注册与开关:默认 OFF,记账列到位
# ==========================================================================


def test_task_is_registered_config_gated_and_seeded_off() -> None:
    registry = JOBS_REGISTRY.read_text(encoding="utf-8")
    assert FOLLOWUP_TASK_KEY in registry
    assert "app.domains.kol.search_session_stuck_followup" in registry
    assert "run_session_stuck_followup_job" in registry
    # 与 D2 三任务同款:_gated_daily_job 统一把守 config-gate。
    assert "_gated_daily_job(task_key, module, entry, **kwargs)" in registry

    migration = MIGRATION_UP.read_text(encoding="utf-8")
    assert f"('{FOLLOWUP_TASK_KEY}'" in migration
    assert "FALSE, 'low'" in migration, "scheduler_tasks 种子必须默认 OFF"
    assert "ON CONFLICT (task_key) DO NOTHING" in migration
    assert "last_run_summary" in migration


def test_migration_is_additive_and_reversible() -> None:
    up = MIGRATION_UP.read_text(encoding="utf-8")
    down = MIGRATION_DOWN.read_text(encoding="utf-8")
    assert "BEGIN" not in up.upper().replace("BEGINS", "") or "BEGIN;" not in up
    assert "ADD COLUMN IF NOT EXISTS last_run_summary" in up
    assert "DROP COLUMN IF EXISTS last_run_summary" in down
    assert f"DELETE FROM scheduler_tasks WHERE task_key = '{FOLLOWUP_TASK_KEY}'" in down
    # 迁移注释里禁 ASCII 问号(compat 适配器会当成占位符炸 apply)。
    for text in (up, down):
        assert "?" not in text


def test_record_run_accepts_an_accounting_note() -> None:
    from app.domains.ops import scheduler_registry

    import inspect

    signature = inspect.signature(scheduler_registry.record_run)
    assert "note" in signature.parameters
    assert signature.parameters["note"].default == ""
