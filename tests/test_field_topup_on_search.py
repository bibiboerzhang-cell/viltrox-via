"""车道 1「定点按需抓取」的成本闸与诚实契约(2026-08-25)。

最重要的两条,其余用例都围着它们转:

1. **总开关默认 OFF** —— 任何花钱的动作都不得自动武装。没显式开闸,本车道只出账单、
   零入队;开了闸,``dry_run`` 仍然只算不花。
2. **入队前报得出账单** —— ``planned_fetch_count`` 就是「这一次要花多少次抓取」,
   而且在总开关关着时也算得出来,让人在武装之前先看见价格。

其余:粉丝倒序(有限预算先救大号)、每次上限、每日上限、冷却期、诊断字段契约、
以及「不假装本次就补上了」(``applies_to_this_search`` 恒 False)。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import profile_field_topup_enqueue as topup
from app.domains.kol.profile_recall_funnel import RecallStageLedger


# ── 假库:只回答本模块用到的三条 SELECT,零真实 IO ──────────────────────────


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _FakeConn:
    """按 SQL 特征分派;记录每条查询,便于断言「干跑时一条写都没有」。"""

    def __init__(
        self,
        *,
        pool_rows: list[dict[str, Any]],
        cooling: set[int] | None = None,
        daily_used: int = 0,
    ) -> None:
        self.pool_rows = pool_rows
        self.cooling = cooling or set()
        self.daily_used = daily_used
        self.seen: list[str] = []

    def execute(self, sql: str, params: Any = ()) -> _FakeCursor:
        self.seen.append(sql)
        assert sql.strip().upper().startswith("SELECT"), "本车道只允许 SELECT 探测"
        if "FROM vkpi_kol_pool" in sql:
            wanted = {int(value) for value in params if isinstance(value, int)}
            rows = [row for row in self.pool_rows if int(row["id"]) in wanted]
            rows.sort(key=lambda row: (-(row.get("followers") or -1), row["id"]))
            return _FakeCursor(rows)
        if "vkpi_kol_url_deep_crawl_runs" in sql:
            return _FakeCursor([{"kol_pool_id": pool_id} for pool_id in sorted(self.cooling)])
        if "COUNT(*) AS used" in sql:
            return _FakeCursor([{"used": self.daily_used}])
        if "FROM apify_jobs" in sql:
            return _FakeCursor([])
        raise AssertionError(f"unexpected sql: {sql[:80]}")


def _row(pool_id: int, followers: int, platform: str = "youtube") -> dict[str, Any]:
    return {
        "id": pool_id,
        "platform": platform,
        "handle": f"h{pool_id}",
        "profile_url": f"https://www.youtube.com/@h{pool_id}",
        "followers": followers,
        "country": "",
        "language": "",
    }


def _candidate(pool_id: int, fields: list[str] | None = None) -> dict[str, Any]:
    return {
        "kol_pool_id": pool_id,
        "handle": f"h{pool_id}",
        "platform": "youtube",
        "missing_fields": fields or ["country"],
    }


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "VKPI_FIELD_TOPUP_ENABLED",
        "VKPI_FIELD_TOPUP_PER_SEARCH",
        "VKPI_FIELD_TOPUP_DAILY_MAX",
        "VKPI_FIELD_TOPUP_COOLDOWN_HOURS",
        "VKPI_FIELD_TOPUP_PLATFORMS",
    ):
        monkeypatch.delenv(name, raising=False)


def _bind(monkeypatch: pytest.MonkeyPatch, conn: _FakeConn) -> list[dict[str, Any]]:
    """接上假库,并把真入队器换成记账器 —— 测试里一条 apify_jobs 都不许真写。"""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(topup, "get_conn", lambda: conn)

    def _fake_enqueue(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"url": url, **kwargs})
        return {"status": "queued", "job_id": 900 + len(calls)}

    # 打在真模块的属性上,而不是 sys.modules:``from app.domains.kol import url_deep_crawl``
    # 在包已导入时走的是包属性,替 sys.modules 条目会被绕过(整套测试一起跑时会漏)。
    from app.domains.kol import url_deep_crawl

    monkeypatch.setattr(url_deep_crawl, "enqueue_profile_deep_crawl_job", _fake_enqueue)
    return calls


# ── 1. 总开关默认 OFF ───────────────────────────────────────────────────────


def test_gate_is_off_by_default_and_enqueues_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConn(pool_rows=[_row(1, 900_000), _row(2, 100_000)])
    calls = _bind(monkeypatch, conn)

    assert topup.topup_settings()["enabled"] is False
    result = topup.enqueue_field_topup_for_candidates(
        candidates=[_candidate(1), _candidate(2)], session_id=7
    )

    assert result["status"] == "disabled"
    assert result["enabled"] is False
    assert result["enqueued"] == 0
    assert calls == [], "闸关着不许花任何钱"
    # 关着也要算得出账单:武装之前先看见价格。
    assert result["planned_fetch_count"] == 2


def test_gate_only_opens_on_explicit_truthy_value(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ("0", "", "false", "no", "off", "maybe"):
        monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", value)
        assert topup.topup_settings()["enabled"] is False, value
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", value)
        assert topup.topup_settings()["enabled"] is True, value


# ── 2. 干跑:开着闸也只算不花 ──────────────────────────────────────────────


def test_dry_run_reports_the_bill_without_enqueuing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    conn = _FakeConn(pool_rows=[_row(1, 900_000), _row(2, 100_000), _row(3, 10_000)])
    calls = _bind(monkeypatch, conn)

    result = topup.enqueue_field_topup_for_candidates(
        candidates=[_candidate(1), _candidate(2), _candidate(3)],
        session_id=7,
        dry_run=True,
    )

    assert result["status"] == "dry_run"
    assert result["dry_run"] is True
    assert result["planned_fetch_count"] == 3
    assert result["enqueued"] == 0
    assert calls == []


# ── 3. 上限生效 ────────────────────────────────────────────────────────────


def test_per_search_cap_limits_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    monkeypatch.setenv("VKPI_FIELD_TOPUP_PER_SEARCH", "2")
    rows = [_row(pool_id, 1_000 * pool_id) for pool_id in range(1, 8)]
    conn = _FakeConn(pool_rows=rows)
    calls = _bind(monkeypatch, conn)

    result = topup.enqueue_field_topup_for_candidates(
        candidates=[_candidate(row["id"]) for row in rows], session_id=7
    )

    assert result["planned_fetch_count"] == 2
    assert len(calls) == 2
    assert result["skipped"]["per_search_cap"] == 5
    assert result["skipped_total"] == 5


def test_per_search_cap_cannot_exceed_hard_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_FIELD_TOPUP_PER_SEARCH", "9999")
    assert topup.topup_settings()["per_search_max"] == topup.HARD_CAP_PER_SEARCH
    monkeypatch.setenv("VKPI_FIELD_TOPUP_DAILY_MAX", "9999")
    assert topup.topup_settings()["daily_max"] == topup.HARD_CAP_DAILY


def test_daily_budget_stops_the_lane_and_is_accounted_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    monkeypatch.setenv("VKPI_FIELD_TOPUP_DAILY_MAX", "5")
    rows = [_row(pool_id, 1_000 * pool_id) for pool_id in range(1, 5)]
    conn = _FakeConn(pool_rows=rows, daily_used=4)
    calls = _bind(monkeypatch, conn)

    result = topup.enqueue_field_topup_for_candidates(
        candidates=[_candidate(row["id"]) for row in rows], session_id=7
    )

    assert result["daily"] == {"max": 5, "used": 4, "remaining": 1}
    assert result["planned_fetch_count"] == 1
    assert len(calls) == 1
    assert result["skipped"]["daily_budget"] == 3, "超额的人必须被数出来,不许静默丢"


def test_exhausted_daily_budget_enqueues_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    monkeypatch.setenv("VKPI_FIELD_TOPUP_DAILY_MAX", "3")
    conn = _FakeConn(pool_rows=[_row(1, 900_000)], daily_used=3)
    calls = _bind(monkeypatch, conn)

    result = topup.enqueue_field_topup_for_candidates(candidates=[_candidate(1)], session_id=7)

    assert result["status"] == "no_budget"
    assert result["enqueued"] == 0
    assert calls == []


# ── 4. 倒序正确:有限预算先救大号 ──────────────────────────────────────────


def test_priority_is_followers_descending(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    monkeypatch.setenv("VKPI_FIELD_TOPUP_PER_SEARCH", "3")
    rows = [_row(1, 5_000), _row(2, 4_000_000), _row(3, 120_000), _row(4, 900_000)]
    conn = _FakeConn(pool_rows=rows)
    calls = _bind(monkeypatch, conn)

    # 标记顺序刻意与粉丝顺序相反,证明排序是本模块做的,不是入参顺序凑巧。
    result = topup.enqueue_field_topup_for_candidates(
        candidates=[_candidate(1), _candidate(3), _candidate(4), _candidate(2)],
        session_id=7,
    )

    assert [item["kol_pool_id"] for item in result["planned"]] == [2, 4, 3]
    assert [call["kol_pool_id"] for call in calls] == [2, 4, 3]


# ── 5. 冷却生效 ────────────────────────────────────────────────────────────


def test_cooldown_skips_recently_crawled_people(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    rows = [_row(1, 900_000), _row(2, 800_000), _row(3, 700_000)]
    conn = _FakeConn(pool_rows=rows, cooling={1, 3})
    calls = _bind(monkeypatch, conn)

    result = topup.enqueue_field_topup_for_candidates(
        candidates=[_candidate(1), _candidate(2), _candidate(3)], session_id=7
    )

    assert result["skipped"]["cooldown"] == 2
    assert [call["kol_pool_id"] for call in calls] == [2]


def test_cooldown_never_reads_pool_last_scrape_at(monkeypatch: pytest.MonkeyPatch) -> None:
    """prod 实测 last_scrape_at 全 NULL —— 拿它当冷却闸等于没有闸,这里钉死不许用。"""
    conn = _FakeConn(pool_rows=[_row(1, 900_000)])
    _bind(monkeypatch, conn)
    topup.plan_field_topup([_candidate(1)])
    cooldown_sql = [sql for sql in conn.seen if "make_interval" in sql]
    assert cooldown_sql, "冷却查询必须真的跑了"
    assert all("last_scrape_at" not in sql for sql in conn.seen)


# ── 6. 可抓性与平台白名单 ──────────────────────────────────────────────────


def test_uncrawlable_candidates_are_counted_not_silently_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    conn = _FakeConn(pool_rows=[_row(1, 900_000)])  # 候选 2 在库里查不出来(无 URL / 平台不支持)
    _bind(monkeypatch, conn)

    result = topup.plan_field_topup([_candidate(1), _candidate(2)])

    assert result["marked"] == 2
    assert result["skipped"]["not_crawlable"] == 1
    assert result["planned_fetch_count"] == 1


def test_already_filled_fields_are_not_paid_for_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    """标记是搜索那一刻拍的照。字段在这中间被别的管线补上了,就不该再花这笔钱。"""
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    filled = {**_row(1, 900_000), "country": "US"}
    conn = _FakeConn(pool_rows=[filled, _row(2, 800_000)])
    calls = _bind(monkeypatch, conn)

    result = topup.enqueue_field_topup_for_candidates(
        candidates=[_candidate(1, ["country"]), _candidate(2, ["country"])], session_id=7
    )

    assert result["skipped"]["already_filled"] == 1
    assert [call["kol_pool_id"] for call in calls] == [2]


def test_partially_filled_row_only_asks_for_what_is_still_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    conn = _FakeConn(pool_rows=[{**_row(1, 900_000), "country": "US"}])
    _bind(monkeypatch, conn)

    result = topup.plan_field_topup([_candidate(1, ["country", "language"])])

    assert result["planned"][0]["missing_fields"] == ["language"]


def test_probe_failure_fails_closed_with_zero_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")

    class _Boom:
        def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("db down")

    calls = _bind(monkeypatch, _FakeConn(pool_rows=[]))
    monkeypatch.setattr(topup, "get_conn", lambda: _Boom())

    result = topup.enqueue_field_topup_for_candidates(candidates=[_candidate(1)], session_id=7)

    assert result["status"] == "probe_failed"
    assert result["planned_fetch_count"] == 0
    assert calls == []


# ── 7. 诊断字段契约 + 诚实 ────────────────────────────────────────────────


def test_diagnostics_field_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    monkeypatch.setenv("VKPI_FIELD_TOPUP_PER_SEARCH", "1")
    rows = [_row(1, 900_000), _row(2, 800_000), _row(3, 700_000)]
    conn = _FakeConn(pool_rows=rows, cooling={3})
    _bind(monkeypatch, conn)

    result = topup.enqueue_field_topup_for_candidates(
        candidates=[_candidate(1), _candidate(2), _candidate(3)], session_id=7
    )

    for key in (
        "schema", "status", "enabled", "dry_run", "marked", "eligible",
        "planned_fetch_count", "enqueued", "already_queued", "errors",
        "skipped", "skipped_total", "daily", "settings", "items",
        "applies_to_this_search", "note", "summary_line",
    ):
        assert key in result, key
    assert result["schema"] == topup.FIELD_TOPUP_SCHEMA
    # 「本次标记了 N 人待补、实际入队 M 人、因预算/冷却跳过 K 人」
    assert result["marked"] == 3
    assert result["enqueued"] == 1
    assert result["skipped"]["cooldown"] + result["skipped"]["per_search_cap"] == 2


def test_summary_line_is_plain_language_without_internal_jargon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """门面禁术语:给人看的那句话里不许出现内部词。"""
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    conn = _FakeConn(pool_rows=[_row(1, 900_000)])
    _bind(monkeypatch, conn)

    for dry, expect in ((True, "试算"), (False, "已排队")):
        line = topup.enqueue_field_topup_for_candidates(
            candidates=[_candidate(1)], session_id=7, dry_run=dry
        )["summary_line"]
        assert expect in line
        for jargon in (
            "apify", "Apify", "enqueue", "payload", "kol_pool_id", "cooldown",
            "dry_run", "LLM", "gate",
        ):
            assert jargon not in line, jargon


def test_never_claims_the_topup_applies_to_this_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    conn = _FakeConn(pool_rows=[_row(1, 900_000)])
    _bind(monkeypatch, conn)
    for dry in (True, False):
        result = topup.enqueue_field_topup_for_candidates(
            candidates=[_candidate(1)], session_id=7, dry_run=dry
        )
        assert result["applies_to_this_search"] is False


def test_no_candidates_short_circuits_without_touching_the_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    conn = _FakeConn(pool_rows=[_row(1, 900_000)])
    _bind(monkeypatch, conn)
    result = topup.enqueue_field_topup_for_candidates(candidates=[], session_id=7)
    assert result["status"] == "no_candidates"
    assert conn.seen == [], "没标记就别去打扰数据库"


# ── 8. 入队参数:最小成本档 + 后台泳道 + 复用既有入队器 ────────────────────


def test_enqueue_uses_the_cheapest_background_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    conn = _FakeConn(pool_rows=[_row(1, 900_000)])
    calls = _bind(monkeypatch, conn)

    topup.enqueue_field_topup_for_candidates(candidates=[_candidate(1)], session_id=7)

    call = calls[0]
    assert call["queue_lane"] == "batch", "绝不能挤进 interactive 泳道拖慢首屏"
    assert call["max_posts"] == 1
    assert call["suppress_final_v1"] is True
    assert call["suppress_contact_followup"] is True
    assert call["suppress_profile_followups"] is True
    assert call["source"] == topup.TOPUP_SOURCE
    assert "enforce_target_write" not in call, "库内召回候选不在 My-KOL,开行级围栏会全数 fail-closed"


# ── 9. 账本:标记只登记不抓取,截断可见 ───────────────────────────────────


def test_ledger_records_marks_without_any_fetch() -> None:
    ledger = RecallStageLedger()
    ledger.note_topup_candidates([_candidate(1), _candidate(2)])
    diagnostics = ledger.as_diagnostics(deduped_candidate_count=10)
    assert diagnostics["field_topup_candidate_count"] == 2
    assert [item["kol_pool_id"] for item in diagnostics["field_topup_candidates"]] == [1, 2]
    assert diagnostics["field_topup_truncated"] is False


def test_ledger_truncation_is_visible_not_silent() -> None:
    from app.domains.kol.profile_recall_funnel import TOPUP_CANDIDATE_CAP

    ledger = RecallStageLedger()
    ledger.note_topup_candidates([_candidate(i) for i in range(TOPUP_CANDIDATE_CAP + 25)])
    diagnostics = ledger.as_diagnostics(deduped_candidate_count=500)
    assert diagnostics["field_topup_candidate_count"] == TOPUP_CANDIDATE_CAP + 25
    assert len(diagnostics["field_topup_candidates"]) == TOPUP_CANDIDATE_CAP
    assert diagnostics["field_topup_truncated"] is True


def test_marking_layer_never_reaches_the_fetcher() -> None:
    """标记只是标记:召回判定层与账本都不许碰入队器,否则「只标记不抓取」就是空话。"""
    from pathlib import Path

    from app.domains.kol import profile_recall, profile_recall_filter_modes, profile_recall_funnel

    for module in (profile_recall, profile_recall_filter_modes, profile_recall_funnel):
        # 只看真正的导入 / 调用,注释里提一句「消费方见 X」不算碰。
        code = "\n".join(
            line for line in Path(module.__file__).read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "enqueue_profile_deep_crawl_job" not in code, module.__name__
        assert "import profile_field_topup_enqueue" not in code, module.__name__
        assert "profile_field_topup_enqueue." not in code, module.__name__


# ── 10. 异步不阻塞首屏 ─────────────────────────────────────────────────────


def test_topup_never_calls_a_provider_inline() -> None:
    """非阻塞的物理依据:本车道唯一的外部动作是 INSERT apify_jobs,
    provider 调用发生在 worker 侧、走既有预算闸。"""
    from pathlib import Path

    source = Path(topup.__file__).read_text(encoding="utf-8")
    assert "enqueue_profile_deep_crawl_job" in source
    for forbidden in ("import requests", "import httpx", "ApifyClient", "asyncio", "await "):
        assert forbidden not in source, forbidden


def test_pipeline_consumes_topup_after_the_results_are_assembled() -> None:
    """位置即契约:补齐必须排在结果装配之后,绝不能挪到首屏前面去等它。"""
    from pathlib import Path

    from app.domains.kol import profile_discovery_pipeline

    source = Path(profile_discovery_pipeline.__file__).read_text(encoding="utf-8")
    recall_at = source.index("recall_result = profile_recall.recall_kol_profiles(")
    advance_at = source.index("advance_result = advance_search_session_items(")
    topup_at = source.index("enqueue_field_topup_for_candidates(")
    assert recall_at < advance_at < topup_at


def test_ledger_marks_do_not_change_the_pass_set() -> None:
    """账本只是加法:登记标记不改任何一道闸的计数。"""
    baseline = RecallStageLedger()
    baseline.note_hard_filter(["countries"], ["country"], passed=False)
    with_marks = RecallStageLedger()
    with_marks.note_hard_filter(["countries"], ["country"], passed=False)
    with_marks.note_topup_candidates([_candidate(1)])

    left = baseline.as_diagnostics(deduped_candidate_count=5)
    right = with_marks.as_diagnostics(deduped_candidate_count=5)
    for key in left:
        if key.startswith("field_topup"):
            continue
        assert left[key] == right[key], key
