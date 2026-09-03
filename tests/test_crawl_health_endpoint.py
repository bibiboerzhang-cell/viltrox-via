"""抓取健康只读端点 + 原因码表的钉子(LE 车道)。

钉住四件容易悄悄退化的事:
1. **码表覆盖率**:本刀存在的理由就是最大的三个失败桶(``url_unknown_unsupported`` 202 /
   ``non_video_post_no_video_signal`` 14 / ``llm_json_malformed`` 12)在现成映射器里全落
   「原因待排查」。这里不只钉这三个具名码,还钉一条**覆盖率门槛**:真库全时窗抽出来的
   40 种真实 last_error 里,落兜底句的必须 ≤5%(实测 0)。只钉三个码会让 15% 的长尾
   悄悄退回「待排查」而闸全绿。
2. **诚实空态**:窗口零行必须 ``rows=[]`` + ``success 率 None`` + ``empty_reason``,
   绝不用 0 或 100% 冒充。
3. **零写**:端点跑完,假连接上看到的每一条语句都得是 SELECT。
4. **SQL 兼容层**:占位符只能是 ``?``,零字面百分号,聚合列必须 AS 命名。
"""
from __future__ import annotations

import os
import sys
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "backend")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.api.routers import ADMIN_ROUTER_MODULES  # noqa: E402
from app.api.routers import vkpi_crawl_health as ch  # noqa: E402
from app.core.release_validation import release_validation_request_allowed  # noqa: E402

ENDPOINT = "/api/admin/vkpi/ops/crawl-health"

# 门面禁内部术语:响应里的中文一句不得出现这些词(照 tests/test_video_analysis_account_progress.py
# 的断言形状;另外加了「视频」——本码表要做任务类型中立措辞,不许对主页抓取说「视频」)。
BANNED_SUBSTRINGS = (
    "llm", "gemini", "apify", "qdrant", "embedding", "rule_v0", "词表", "payload",
    "fence", "final_v1", "json", "provider", "worker", "视频",
)

# 真库全时窗(2950 行 apify_jobs)里出现过的 last_error 形态,逐条抄自实跑结果。
# 覆盖率门槛就打在这张表上:落兜底句的比例必须 ≤5%。
REAL_ERRORS: tuple[tuple[str, str], ...] = (
    ("kol_profile_deep_crawl", "url_unknown_unsupported"),
    ("video", "RuntimeError: Gemini video analysis failed: yt-dlp video download failed for Gemini analysis"),
    ("video", "RuntimeError: media_resolve_failed: media_resolve_failed"),
    ("video", '{"reason":"media_resolve_failed:instagram:scraped_no_downloadable_url"}'),
    ("kol_auto_poll", "ValueError: payload must include target_type and target_id"),
    ("video", "budget_guard_blocked"),
    ("video", "non_video_post_no_video_signal"),
    ("kol_content_fit_analysis", "llm_json_malformed"),
    ("video", '{"reason":"video_analysis_authorization_fence_required"}'),
    ("kol_profile_deep_crawl", "NameError: name 'profile_crawl_source' is not defined"),
    ("video", "RuntimeError: Gemini video analysis failed: Gemini File API upload failed: [SSL: SSLV3_ALERT]"),
    ("kol_content_fit_analysis", "budget_blocked"),
    ("video", "RuntimeError: media_resolve_failed: media_resolve_timeout"),
    ("video", "precheck: youtube oEmbed 404/410 (deleted_or_private)"),
    ("video", "RuntimeError: media_resolve_failed: apify resolver returned no JSON"),
    ("smart_search_profile_advance", "ModuleNotFoundError: No module named 'qdrant_client'"),
    ("video", "stale_running_reclaimed: worker process exited while resolving media"),
    ("video", "RuntimeError: Gemini video analysis failed: Gemini file ACTIVE timeout after 90s"),
    ("kol_content_fit_analysis", "fallback_to_rule"),
    ("video", "RuntimeError: Gemini video analysis failed: Server disconnected without sending a response"),
    ("video", "RuntimeError: Gemini video analysis failed: Expecting ',' delimiter: line 198 column 40"),
    ("video", "RuntimeError: Gemini video analysis failed: 'NoneType' object has no attribute 'strip'"),
    ("video", "RuntimeError: direct_video_download_failed: direct video download failed: <urlopen error>"),
    ("video", "RuntimeError: Gemini video analysis failed: NameError: name '_truthy' is not defined"),
    ("kol_content_fit_analysis", "no_ready_video_analysis"),
    ("video", "cancelled_by_scope: A5 retry limited to NUasmeh6uo8"),
    ("project_contract_extract", "ScopeDenied: project scope denied"),
    ("smart_search_profile_advance", "APITimeoutError: Request timed out."),
    ("kol_outreach_draft", 'UndefinedColumn: column "campaign_goal" does not exist'),
    ("account_dossier_extract", "AdminShutdown: terminating connection due to administrator command"),
    ("kol_audience_stats_refresh", "DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes"),
)


class FakeConn:
    """按 SQL 前缀发预置结果;顺便把执行过的语句录下来给零写断言用。"""

    def __init__(self, counts: list[dict[str, Any]], sample: list[dict[str, Any]]) -> None:
        self.counts = counts
        self.sample = sample
        self.statements: list[str] = []
        self._pending: list[dict[str, Any]] = []

    def execute(self, sql: str, params: Any = ()) -> "FakeConn":
        self.statements.append(sql)
        self._pending = self.sample if "last_error_category" in sql else self.counts
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._pending)


def _install(monkeypatch: pytest.MonkeyPatch, conn: FakeConn) -> None:
    import app.db.connection as db

    monkeypatch.setattr(db, "get_conn", lambda: conn)
    monkeypatch.setattr(db, "table_exists", lambda name: True)


def _count(job_type: str, status: str, n: int) -> dict[str, Any]:
    return {"job_type": job_type, "status": status, "n": n}


def _fail(job_type: str, status: str, last_error: str, category: str = "") -> dict[str, Any]:
    return {
        "job_type": job_type,
        "status": status,
        "last_error_category": category,
        "last_error": last_error,
    }


# ── 1. 码表 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "code",
    ["url_unknown_unsupported", "non_video_post_no_video_signal", "llm_json_malformed"],
)
def test_three_biggest_buckets_are_no_longer_unclassified(code: str) -> None:
    """最大的三个桶(合计 228 条)必须各有专属中文句 —— 这是本刀存在的理由。"""
    reason = ch.crawl_reason(last_error_category="", last_error=code)
    assert reason["classified"] is True
    assert reason["reason_human"] != ch.UNCLASSIFIED_HUMAN
    assert reason["reason_code"] == code


def test_the_three_buckets_do_not_share_one_sentence() -> None:
    """三个桶是三种毛病,不许用一句话糊过去。"""
    humans = {
        ch.crawl_reason(last_error=code)["reason_human"]
        for code in ("url_unknown_unsupported", "non_video_post_no_video_signal", "llm_json_malformed")
    }
    assert len(humans) == 3


def test_real_error_corpus_coverage_threshold() -> None:
    """真库 31 种真实 last_error 里落兜底句的必须 ≤5%(实测 0)。

    只钉三个具名码会让长尾悄悄退回「待排查」而闸全绿 —— 所以这里钉的是覆盖率。
    """
    unresolved = [
        raw
        for _job_type, raw in REAL_ERRORS
        if ch.crawl_reason(last_error=raw)["reason_human"] == ch.UNCLASSIFIED_HUMAN
    ]
    ratio = len(unresolved) / len(REAL_ERRORS)
    assert ratio <= 0.05, f"{len(unresolved)}/{len(REAL_ERRORS)} 条真实失败仍落「原因待排查」:{unresolved}"


def test_every_sentence_is_jargon_free_and_job_type_neutral() -> None:
    """码表里每一句中文都不带内部术语,也不对非视频任务说「视频」。"""
    sentences = [human for _cat, human in ch._EXACT_REASONS.values()]
    sentences += [human for _markers, _cat, human in ch._TEXT_REASONS]
    sentences += list(ch._CATEGORY_FALLBACK.values())
    sentences += list(ch._JOB_LABELS.values()) + [ch._JOB_LABEL_FALLBACK]
    sentences += list(ch._STATUS_LABELS.values())
    for sentence in sentences:
        lowered = sentence.lower()
        hits = [word for word in BANNED_SUBSTRINGS if word in lowered]
        assert not hits, f"文案「{sentence}」含内部术语 {hits}"


def test_unknown_code_falls_back_honestly() -> None:
    """认不出就诚实说待排查,不许硬贴一个好看的标签。"""
    reason = ch.crawl_reason(last_error="zzz_never_seen_before_marker")
    assert reason["reason_human"] == ch.UNCLASSIFIED_HUMAN
    assert reason["classified"] is False
    assert reason["category"] == "unknown"


def test_categories_stay_inside_the_frozen_six() -> None:
    """类别轴是与前端 failureReason.ts 冻结的六类;多一类前端会静默归成 unknown。"""
    six = {"download", "authorization", "budget", "model", "provider", "unknown"}
    used = {cat for cat, _human in ch._EXACT_REASONS.values()}
    used |= {cat for _markers, cat, _human in ch._TEXT_REASONS}
    used |= set(ch._CATEGORY_FALLBACK)
    assert used <= six, f"越界类别 {used - six}"


def test_reason_code_survives_a_truncated_json_blob() -> None:
    """worker 写 last_error 时截断到 2000 字符;截断 JSON 不许炸成高基数长尾码。"""
    truncated = '{"reason":"media_resolve_failed:instagram:scraped_no_downloadable_url","deta'
    assert ch.reason_code(truncated) == "media_resolve_failed:instagram:scraped_no_downloadable_url"


def test_reason_code_takes_only_the_first_line() -> None:
    assert ch.reason_code("RuntimeError: boom\n  File x\n  File y") == "RuntimeError: boom"
    assert ch.reason_code("") == ""


def test_job_label_never_leaks_the_raw_identifier() -> None:
    assert ch.job_label("kol_profile_deep_crawl") == "创作者主页抓取"
    assert ch.job_label("brand_new_type_2027") == ch._JOB_LABEL_FALLBACK
    assert "_" not in ch.job_label("brand_new_type_2027")


# ── 2. SQL 兼容层 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("sql", [ch._WINDOW_COUNT_SQL, ch._FAILURE_SAMPLE_SQL])
def test_sql_is_compat_layer_safe(sql: str) -> None:
    """字面百分号会被兼容层当参数标记炸掉;LIKE 同理(要匹配子串用 strpos)。"""
    assert "%" not in sql
    assert "LIKE" not in sql.upper()
    assert "?" in sql
    assert sql.lstrip().upper().startswith("SELECT")


def test_aggregate_columns_are_aliased() -> None:
    assert "COUNT(*) AS n" in ch._WINDOW_COUNT_SQL


# ── 3. 投影:诚实空态 / 口径 / 取样截断 ─────────────────────────────────────
def test_empty_window_is_honest_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """窗口零行:成功率必须是 None,绝不用 0 或 100% 冒充。"""
    conn = FakeConn([], [])
    _install(monkeypatch, conn)
    payload = ch.crawl_health_overview(window_days=7)
    assert payload["rows"] == []
    assert payload["completion_rate"] is None
    assert payload["execution_success_rate"] is None
    assert payload["empty_reason"] == "近 7 天没有抓取记录"
    assert payload["totals"] == {}


def test_missing_table_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.db.connection as db

    monkeypatch.setattr(db, "table_exists", lambda name: False)
    payload = ch.crawl_health_overview(window_days=30)
    assert payload["completion_rate"] is None
    assert payload["empty_reason"]


def test_two_rates_use_two_declared_denominators(monkeypatch: pytest.MonkeyPatch) -> None:
    """完成率含拦截/待人工,执行成功率不含 —— 两个口径都必须在响应里写明。"""
    counts = [
        _count("video", "done", 6),
        _count("video", "failed", 2),
        _count("video", "blocked", 1),
        _count("video", "triage", 1),
        _count("video", "queued", 5),
    ]
    conn = FakeConn(counts, [_fail("video", "blocked", "budget_blocked")])
    _install(monkeypatch, conn)
    payload = ch.crawl_health_overview(window_days=30)
    assert payload["completion_rate"] == 0.6  # 6 / (6+2+1+1)
    assert payload["execution_success_rate"] == 0.75  # 6 / (6+2)
    assert payload["in_flight"] == 5  # 在途不进任何一个分母
    assert payload["completion_rate_basis"]
    assert payload["execution_success_rate_basis"]


def test_blocked_heavy_job_cannot_hide_behind_the_legacy_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    """真实形状:主页抓取 done 209 / blocked 202。旧口径会报 95.4%,新口径必须说破。"""
    counts = [
        _count("kol_profile_deep_crawl", "done", 209),
        _count("kol_profile_deep_crawl", "blocked", 202),
        _count("kol_profile_deep_crawl", "failed", 10),
    ]
    sample = [_fail("kol_profile_deep_crawl", "blocked", "url_unknown_unsupported")] * 202
    conn = FakeConn(counts, sample)
    _install(monkeypatch, conn)
    payload = ch.crawl_health_overview(window_days=3650)
    assert payload["completion_rate"] == 0.4964
    assert payload["execution_success_rate"] == 0.9543
    assert payload["completion_rate"] < payload["execution_success_rate"]
    top = payload["top_reasons"][0]
    assert top["count"] == 202
    assert top["reason_human"] != ch.UNCLASSIFIED_HUMAN


def test_top_reasons_group_by_sentence_not_by_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """异常文本的码是高基数长尾;按码分组会把一类问题拆成几十行。"""
    sample = [
        _fail("video", "failed", "RuntimeError: Gemini video analysis failed: Expecting ',' delimiter: line 1"),
        _fail("video", "failed", "RuntimeError: Gemini video analysis failed: Expecting ',' delimiter: line 2"),
        _fail("video", "failed", "RuntimeError: Gemini video analysis failed: Expecting ',' delimiter: line 3"),
    ]
    conn = FakeConn([_count("video", "failed", 3)], sample)
    _install(monkeypatch, conn)
    payload = ch.crawl_health_overview(window_days=30)
    assert len(payload["top_reasons"]) == 1
    assert payload["top_reasons"][0]["count"] == 3
    assert payload["unclassified_count"] == 0


def test_sample_truncation_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """命中取样上限就诚实标 truncated,不假装是全量。"""
    sample = [_fail("video", "blocked", "budget_blocked")] * 100
    conn = FakeConn([_count("video", "blocked", 9999)], sample)
    _install(monkeypatch, conn)
    payload = ch.crawl_health_overview(window_days=30, sample_limit=100)
    assert payload["sample_truncated"] is True
    assert payload["sample_size"] == 100
    payload_wide = ch.crawl_health_overview(window_days=30, sample_limit=500)
    assert payload_wide["sample_truncated"] is False


def test_by_job_carries_a_label_and_its_own_top_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    counts = [_count("kol_profile_deep_crawl", "blocked", 2), _count("video", "done", 1)]
    sample = [_fail("kol_profile_deep_crawl", "blocked", "url_unknown_unsupported")] * 2
    conn = FakeConn(counts, sample)
    _install(monkeypatch, conn)
    by_job = {entry["job_type"]: entry for entry in ch.crawl_health_overview(window_days=30)["by_job"]}
    assert by_job["kol_profile_deep_crawl"]["job_label"] == "创作者主页抓取"
    assert by_job["kol_profile_deep_crawl"]["top_reason"] != ch.UNCLASSIFIED_HUMAN
    assert by_job["video"]["top_reason"] is None
    assert by_job["video"]["completion_rate"] == 1.0


def test_response_body_is_jargon_free(monkeypatch: pytest.MonkeyPatch) -> None:
    """整个响应体里给人看的中文字段不得出现内部术语。"""
    conn = FakeConn(
        [_count("kol_profile_deep_crawl", "blocked", 1)],
        [_fail("kol_profile_deep_crawl", "blocked", "url_unknown_unsupported")],
    )
    _install(monkeypatch, conn)
    payload = ch.crawl_health_overview(window_days=30)
    human_text = " ".join(
        [str(payload["completion_rate_basis"]), str(payload["execution_success_rate_basis"])]
        + [str(row["job_label"]) for row in payload["rows"]]
        + [str(item["reason_human"]) for item in payload["top_reasons"]]
        + list(payload["status_labels"].values())
    ).lower()
    assert not [word for word in BANNED_SUBSTRINGS if word in human_text]


# ── 4. 零写 ────────────────────────────────────────────────────────────────
def test_endpoint_never_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn([_count("video", "done", 1)], [])
    _install(monkeypatch, conn)
    ch.crawl_health_overview(window_days=30)
    assert conn.statements, "一条语句都没跑,零写断言就是假绿的"
    for sql in conn.statements:
        assert sql.lstrip().upper().startswith("SELECT"), f"非 SELECT 语句:{sql[:80]}"


def test_query_failure_degrades_to_empty_not_a_500(monkeypatch: pytest.MonkeyPatch) -> None:
    class BoomConn:
        def __init__(self) -> None:
            self.rolled_back = False

        def execute(self, sql: str, params: Any = ()) -> Any:
            raise RuntimeError("relation is gone")

        def rollback(self) -> None:
            self.rolled_back = True

    import app.db.connection as db

    conn = BoomConn()
    monkeypatch.setattr(db, "get_conn", lambda: conn)
    monkeypatch.setattr(db, "table_exists", lambda name: True)
    payload = ch.crawl_health_overview(window_days=30)
    assert payload["completion_rate"] is None
    assert payload["empty_reason"]
    assert conn.rolled_back is True


# ── 5. 路由契约 ────────────────────────────────────────────────────────────
@pytest.fixture()
def client() -> Any:
    app = FastAPI()
    app.include_router(ch.router)
    yield app


def _with_staff(app: FastAPI, staff: dict[str, Any]) -> TestClient:
    route = next(r for r in app.routes if getattr(r, "path", "") == ENDPOINT)
    for dependency in route.dependant.dependencies:
        app.dependency_overrides[dependency.call] = lambda: staff
    return TestClient(app)


def test_route_is_registered_and_get_only() -> None:
    assert "vkpi_crawl_health" in ADMIN_ROUTER_MODULES
    paths = [(r.path, sorted(r.methods)) for r in ch.router.routes if getattr(r, "path", "") == ENDPOINT]
    assert paths == [(ENDPOINT, ["GET"])]


def test_new_read_only_get_is_registered_in_the_release_whitelist() -> None:
    """未登记的新 GET 在发布验收窗口内会回 503 并被浏览器候选闸判死。"""
    assert release_validation_request_allowed("GET", ENDPOINT) is True


def test_non_manager_gets_403(client: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeConn([], []))
    response = _with_staff(client, {"role": "readonly", "is_owner": 0}).get(ENDPOINT)
    assert response.status_code == 403


def test_manager_gets_200(client: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, FakeConn([_count("video", "done", 3)], []))
    response = _with_staff(client, {"role": "admin", "is_owner": 1}).get(f"{ENDPOINT}?window_days=7")
    assert response.status_code == 200
    body = response.json()
    assert body["window_days"] == 7
    assert body["completion_rate"] == 1.0


@pytest.mark.parametrize("window_days", [0, -1, ch.MAX_WINDOW_DAYS + 1])
def test_out_of_range_window_is_422(client: FastAPI, monkeypatch: pytest.MonkeyPatch, window_days: int) -> None:
    _install(monkeypatch, FakeConn([], []))
    response = _with_staff(client, {"role": "admin", "is_owner": 1}).get(
        f"{ENDPOINT}?window_days={window_days}"
    )
    assert response.status_code == 422


def test_window_beyond_thirty_days_is_allowed(client: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """本地近 30 天只有 48 行,验收必须能看全时窗;le=30 会把这一刀锁死在空态。"""
    _install(monkeypatch, FakeConn([_count("video", "done", 1)], []))
    response = _with_staff(client, {"role": "admin", "is_owner": 1}).get(f"{ENDPOINT}?window_days=3650")
    assert response.status_code == 200
    assert response.json()["window_days"] == 3650


# ── 6. 验收脚本 crawl_acceptance.py ────────────────────────────────────────
SCRIPT_PATH = os.path.join(ROOT, "backend", "scripts_local", "crawl_acceptance.py")


@pytest.fixture(scope="module")
def script() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("crawl_acceptance_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # 3.14 的 dataclass 按 __module__ 回查 sys.modules
    spec.loader.exec_module(module)
    return module


class ScriptConn:
    """按表名派结果的假连接;录下所有语句用于「dry-run 零写」断言。"""

    def __init__(self, session: dict[str, Any] | None, jobs: list[dict[str, Any]]) -> None:
        self.session = session
        self.jobs = jobs
        self.statements: list[str] = []
        self._pending: list[Any] = []

    def execute(self, sql: str, params: Any = ()) -> "ScriptConn":
        self.statements.append(sql)
        if "apify_jobs" in sql:
            self._pending = list(self.jobs)
        elif "vkpi_kol_search_session_items" in sql:
            self._pending = [{"status": "materialized", "n": 2, "landed": 2}]
        elif "vkpi_kol_search_sessions" in sql:
            self._pending = [self.session] if self.session else []
        else:
            self._pending = []
        return self

    def fetchall(self) -> list[Any]:
        return list(self._pending)

    def rollback(self) -> None:
        self.statements.append("ROLLBACK")


SESSION_ROW: dict[str, Any] = {
    "id": 1124,
    "status": "ready",
    "query_text": "美国 YouTube 镜头评测创作者",
    "created_by": 1,
    "created_at": "2026-09-01T00:00:00Z",
    "result_summary_json": (
        '{"diagnostics": {"returned_count": 20, "stage_funnel": {"entered_reach_gate": 31,'
        ' "survivors": 1, "dropped_by_gate": {"no_match_evidence": 30, "low_reach": 0}}}}'
    ),
}


def _run_script(script: Any, monkeypatch: pytest.MonkeyPatch, argv: list[str], conn: Any) -> int:
    import app.db.connection as db
    from contextlib import contextmanager

    @contextmanager
    def _scope() -> Any:
        yield None

    monkeypatch.setattr(db, "db_connection_sync_scope", _scope)
    monkeypatch.setattr(db, "get_conn", lambda: conn)
    return int(script.run(argv))


def test_script_dry_run_writes_nothing(script: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    """默认 dry-run:只跑 SELECT,零 INSERT/UPDATE/DELETE,零入队。"""
    conn = ScriptConn(SESSION_ROW, [{"job_type": "video", "status": "done", "last_error_category": "", "last_error": ""}])
    monkeypatch.setattr(
        script, "launch", lambda *a, **k: pytest.fail("dry-run 不许发起任何任务")
    )
    code = _run_script(script, monkeypatch, ["-k", "1", "--queries", "recent"], conn)
    assert code == 0
    for sql in conn.statements:
        stripped = sql.lstrip().upper()
        assert stripped.startswith("SELECT") or stripped.startswith("ROLLBACK"), sql[:80]
    assert "ROLLBACK" in conn.statements, "dry-run 结束必须显式回滚,坐实零写"
    printed = capsys.readouterr().out
    assert "DRY-RUN" in printed
    assert "未写库、未取数、未花钱" in printed


def test_script_apply_without_staff_id_refuses(script: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    """没有真身份就跑 --apply,派生任务会全被拦 —— 必须明确报错,不许静默降级。"""
    monkeypatch.setattr(script, "launch", lambda *a, **k: pytest.fail("不该发起"))
    code = script.run(["--apply", "-k", "1"])
    assert code == 2
    assert "--staff-id" in capsys.readouterr().out


def test_script_json_carries_stage_and_reason_axes(script: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    """--json 必须同时给出「每阶段产出」与「原因表」两个轴。"""
    import json as _json

    jobs = [
        {"job_type": "video", "status": "blocked", "last_error_category": "", "last_error": "url_unknown_unsupported"},
        {"job_type": "video", "status": "done", "last_error_category": "", "last_error": ""},
    ]
    code = _run_script(script, monkeypatch, ["-k", "1", "--queries", "recent", "--json"], ScriptConn(SESSION_ROW, jobs))
    assert code == 0
    payload = _json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["header"]["mode"] == "DRY-RUN"
    report = payload["reports"][0]
    assert [row["stage"] for row in report["stages"]] == [
        script.STAGE_SESSION, script.STAGE_RECALL, script.STAGE_PROFILE, script.STAGE_JOBS
    ]
    assert report["stages"][1]["produced"] == 20
    assert "没有能证明相关的作品" in report["stages"][1]["reason"]
    assert report["reasons"][0]["count"] == 1
    assert report["reasons"][0]["reason_human"] != ch.UNCLASSIFIED_HUMAN


def test_script_reason_axis_is_shared_with_the_endpoint(script: Any) -> None:
    """脚本表与运维卡必须同源,否则同一条失败在两处说两句话。"""
    from app.api.routers.vkpi_crawl_health import crawl_reason

    assert script.stage_jobs.__code__.co_names.count("crawl_reason") >= 1
    assert crawl_reason(last_error="url_unknown_unsupported")["classified"] is True


def test_script_batch_cap_is_enforced(script: Any) -> None:
    """--apply 会花钱,批次必须有硬上限;要 999 条也只给上限那么多。"""
    import argparse

    assert script._resolve_batch(argparse.Namespace(limit=999)) == script.MAX_BATCH
    assert script._resolve_batch(argparse.Namespace(limit=2)) == 2
    assert script._resolve_batch(argparse.Namespace(limit=0)) == 1


def test_script_reports_when_the_golden_list_is_shorter_than_asked(script: Any) -> None:
    """文件名写着 60,实际只有 5 条 —— 少给必须说破,不许静默少跑。"""
    queries, note = script.load_queries(script.DEFAULT_QUERY_FILE, 60)
    assert 0 < len(queries) < 60
    assert str(len(queries)) in note


def test_script_stage_reason_text_is_jargon_free(script: Any) -> None:
    sentences = list(script._GATE_LABELS.values()) + [
        script.STAGE_SESSION, script.STAGE_RECALL, script.STAGE_PROFILE, script.STAGE_JOBS
    ]
    for sentence in sentences:
        lowered = sentence.lower()
        assert not [word for word in BANNED_SUBSTRINGS if word in lowered], sentence


def test_script_flags_a_session_with_no_owner(script: Any) -> None:
    """无发起人的会话正是本地 11 条被拦的根因,脚本必须在①节就点破。"""
    row = script.stage_session({**SESSION_ROW, "created_by": None})
    assert "无发起人" in row.reason
    assert script.stage_session(None).produced == 0
