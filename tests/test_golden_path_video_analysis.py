"""C12 黄金路径真链测试:owner 从 UI 入队 → worker 认领/处理 → 台账双写 → cache ready → 进度可读。

2026-08-22 复盘:两个线上级 bug(台账 FK 炸只在子进程 stderr;owner 点深析一直 blocked 却显示"排队中")
6270 个单测都没拦住,因为没有一条端到端真链。本文件走隔离 PG(vkpi_closeout_test)的真表真 SQL:

正例:owner staff 经 enqueue_final_v1_video_analysis_batch(enforce_target_write=True) 入队 →
      worker._claim_job + _process_claimed_job(只把分析子进程换成零网络假体,假体按真子进程口径
      经 llm_gateway.record_call(force_cost_ledger=True) 记账)→ 断言 apify_jobs done、
      vkpi_analysis_cache ready、vkpi_llm_calls / vkpi_ai_cost_ledger 各一行且 staff_id=owner。
反例:staff=None 入队(无授权围栏)→ worker blocked,last_error_category='authorization',
      账号进度 failure_category=authorization + 中文可读原因。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.pg

_FINAL_V1_DERIVE = "video_analysis_final_v1"
_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
# 黄金路径直接依赖的迁移:289 给 vkpi_analysis_cache 加 prompt_version/model_family(worker 写 cache 用)。
# 隔离 test 库落后于主隔离库时,按 runtime 同款记账(schema_migrations + advisory lock)幂等补齐。
_REQUIRED_MIGRATION_PREFIXES = ("289_",)


def _ensure_required_migrations(raw: Any) -> None:
    with raw.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version_key TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        cur.execute("SELECT version_key FROM schema_migrations")
        applied = {row[0] for row in cur.fetchall()}
        for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            if path.name.endswith("_down.sql") or not path.name.startswith(_REQUIRED_MIGRATION_PREFIXES):
                continue
            if path.name in applied:
                continue
            cur.execute("SELECT pg_advisory_lock(hashtext('viltrox_schema_migrations'))")
            try:
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO schema_migrations(version_key) VALUES (%s) ON CONFLICT DO NOTHING", (path.name,))
            finally:
                cur.execute("SELECT pg_advisory_unlock(hashtext('viltrox_schema_migrations'))")


def _valid_final_v1_payload() -> dict[str, Any]:
    return {
        "layer1_visual_content": {
            "content_summary": "Golden path: creator reviews a Viltrox AF 35mm lens on camera.",
            "scene_timeline": [{"timestamp": "00:05", "what": "Lens unboxing on desk"}],
            "product_presence": {"products": ["Viltrox AF 35mm"], "notes": "visible"},
        },
        "layer2_viewer_emotion": {"evidence": ["upbeat narration"]},
        "layer3_three_values": {},
        "layer4_attribution": {},
        "layer5_recommendations": {},
        "layer6_flags_and_scores": {},
    }


class _World:
    """隔离库里的一组唯一身份/数据行;teardown 按标记精确删除,不碰别的测试的数据。"""

    def __init__(self, pg_dsn: str) -> None:
        import psycopg

        self.dsn = pg_dsn
        self.tag = uuid.uuid4().hex[:10]
        self.raw = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)
        _ensure_required_migrations(self.raw)
        base = 9_000_000 + int(self.tag[:5], 16) % 900_000
        self.user_id = base
        self.staff_id = base
        self.pool_id = base
        self.evidence_id = base
        with self.raw.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (id, email, password_hash, name, status, role, email_verified)
                VALUES (%s, %s, '!golden-path-test!', 'Golden Owner', 'active', 'creator', 1)
                """,
                (self.user_id, f"golden-{self.tag}@example.invalid"),
            )
            cur.execute(
                """
                INSERT INTO staff (id, user_id, role, permissions_json, active, is_owner, accepted_at)
                VALUES (%s, %s, 'owner', '{}', 1, 1, NOW())
                """,
                (self.staff_id, self.user_id),
            )
            cur.execute(
                """
                INSERT INTO vkpi_kol_pool (id, pool_uid, platform, handle, profile_url, display_name, created_at, updated_at)
                VALUES (%s, %s, 'youtube', %s, %s, 'Golden KOL', NOW(), NOW())
                """,
                (self.pool_id, f"golden-{self.tag}", f"golden_{self.tag}", f"https://www.youtube.com/@golden_{self.tag}"),
            )
            cur.execute(
                """
                INSERT INTO vkpi_kol_video_evidence
                    (id, kol_pool_id, content_url, platform, title, source, is_active, evidence_type, created_at, updated_at)
                VALUES (%s, %s, %s, 'youtube', 'Golden video', 'golden_test', TRUE, 'video', NOW(), NOW())
                """,
                (self.evidence_id, self.pool_id, f"https://www.youtube.com/watch?v=g{self.tag}a"),
            )

    def staff(self) -> dict[str, Any]:
        return {
            "id": self.staff_id,
            "user_id": self.user_id,
            "role": "owner",
            "is_owner": 1,
            "active": 1,
            "permissions_json": "{}",
            "suspended_at": None,
            "user_status": "active",
        }

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        from psycopg.rows import dict_row

        with self.raw.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row

        with self.raw.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        try:
            with self.raw.cursor() as cur:
                cur.execute("DELETE FROM vkpi_ai_cost_ledger WHERE staff_id=%s OR metadata_json LIKE %s", (self.staff_id, f'%"target_id": "{self.evidence_id}"%'))
                cur.execute("DELETE FROM vkpi_llm_calls WHERE created_by_staff_id=%s OR metadata_json LIKE %s", (self.staff_id, f'%"target_id": "{self.evidence_id}"%'))
                cur.execute("DELETE FROM vkpi_analysis_cache WHERE target_type='video' AND target_id=%s", (str(self.evidence_id),))
                cur.execute("DELETE FROM apify_jobs WHERE payload->>'kol_pool_id'=%s OR payload->>'target_id'=%s", (str(self.pool_id), str(self.evidence_id)))
                cur.execute("DELETE FROM vkpi_kol_video_evidence WHERE id=%s", (self.evidence_id,))
                cur.execute("DELETE FROM vkpi_kol_pool WHERE id=%s", (self.pool_id,))
                cur.execute("DELETE FROM staff WHERE id=%s", (self.staff_id,))
                cur.execute("DELETE FROM users WHERE id=%s", (self.user_id,))
        finally:
            self.raw.close()


@pytest.fixture()
def world(pg_dsn: str):
    w = _World(pg_dsn)
    try:
        yield w
    finally:
        w.close()


def _allowed_preflight(worker_module: Any):
    binding = f"google/{worker_module.WORKER_GEMINI_MODEL}"

    def fake_preflight(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "provider_gate_reason": "provider_calls_allowed",
            "model_readiness_status": "production_ready",
            "execution_class": "production",
            "providers": [
                {
                    "provider": "google",
                    "provider_calls_allowed": True,
                    "estimated_cost_usd": 0.01,
                    "model": worker_module.WORKER_GEMINI_MODEL,
                    "binding": binding,
                    "execution_class": "production",
                    "authorization_scope": "production",
                    "production_authorized": True,
                    "model_readiness_status": "production_ready",
                    "claim_status": "descriptive_only",
                    "checks": [],
                }
            ],
        }

    return fake_preflight


def _fake_analyzer(worker_module: Any, calls: list[dict[str, Any]]):
    """零网络分析子进程假体:按真子进程口径(llm_production → llm_gateway.record_call)强制记账。"""
    from app.db.connection import db_connection_sync_scope
    from app.platform import llm_gateway

    def run(payload: dict[str, Any], *, job_id: Any, target_id: str, platform: str) -> dict[str, Any]:
        context = payload.get("llm_context") if isinstance(payload.get("llm_context"), dict) else {}
        calls.append({"job_id": job_id, "target_id": target_id, "platform": platform, "llm_context": context, "mode": payload.get("mode")})
        with db_connection_sync_scope():
            receipt = llm_gateway.record_call(
                provider="google",
                model=worker_module.WORKER_GEMINI_MODEL,
                purpose=str(context.get("purpose") or "audit_video_analysis"),
                prompt=f"golden-path:{target_id}",
                input_tokens=1000,
                output_tokens=500,
                cost_micro_usd=1234,
                status="success",
                fallback_used=False,
                cost_tag=str(context.get("cost_tag") or ""),
                triggered_by=context.get("triggered_by"),
                metadata={**(context.get("metadata") or {}), "entrypoint": "llm_production_google_generate_content_v1"},
                update_budget_scopes=False,
                force_cost_ledger=True,
            )
        calls[-1]["receipt"] = receipt
        return {
            "analyzed": True,
            "status": "completed",
            "model": worker_module.WORKER_GEMINI_MODEL,
            "method": f"gemini_youtube_{worker_module.WORKER_GEMINI_MODEL}",
            "cost_authority": "llm_production_google_generate_content_v1",
            "llm_attempts": [{"state": "success", "actual_cost_usd": 0.001234, "input_tokens": 1000, "output_tokens": 500}],
            "video_analysis_final_v1": _valid_final_v1_payload(),
        }

    return run


def _claim_only(worker_module: Any, monkeypatch: pytest.MonkeyPatch, job_id: int) -> None:
    """隔离库里还有别的 queued 行:把认领 SELECT 钉到本测试的 job,其余 SQL 原样。"""
    sql = worker_module.CLAIM_SELECT_SQL.replace("WHERE status = 'queued'", f"WHERE status = 'queued' AND id = {int(job_id)}")
    assert sql != worker_module.CLAIM_SELECT_SQL
    monkeypatch.setattr(worker_module, "CLAIM_SELECT_SQL", sql)
    monkeypatch.setattr(worker_module, "CLAIM_SELECT_SQL_STEAL", sql)


def _worker_conn(pg_dsn: str):
    import psycopg

    return psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)


def test_golden_path_owner_enqueue_to_done_with_both_ledgers(world: _World, pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import video_analysis_enqueue as enqueue
    from app.platform import llm_gateway
    from app.workers import apify_jobs_worker as worker
    from app.workers import apify_jobs_worker_gemini as gemini_worker

    monkeypatch.setattr(llm_gateway, "budget_preflight", _allowed_preflight(worker))
    monkeypatch.setattr(worker, "_respect_gemini_qps", lambda _conn: None)
    analyzer_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(gemini_worker, "_run_gemini_analyzer_with_timeout", _fake_analyzer(worker, analyzer_calls))

    # ① owner 从 UI 入队(真 assert_target_writable + build_target_fence + apify_jobs 落行)
    result = enqueue.enqueue_final_v1_video_analysis_batch(
        items=[{"kol_pool_id": world.pool_id, "evidence_id": world.evidence_id}],
        staff=world.staff(),
        enforce_target_write=True,
    )
    assert result["queued"] == 1, result
    job_id = int(result["items"][0]["job"]["id"])
    job_row = world.one("SELECT status, payload FROM apify_jobs WHERE id=%s", (job_id,))
    assert job_row and job_row["status"] == "queued"
    payload = job_row["payload"]
    # C2 身份类型化:staff 外键与 user id 各走各键
    assert payload["staff_id"] == world.staff_id and payload["triggered_by_user_id"] == world.user_id
    assert isinstance(payload.get("my_kol_paid_action_fence"), dict)
    assert payload["my_kol_paid_action_fence"]["staff_id"] == world.staff_id

    # ② worker 认领 + 处理(真 _claim_job / _process_claimed_job / 付费围栏复验 / cache 落库)
    _claim_only(worker, monkeypatch, job_id)
    conn = _worker_conn(pg_dsn)
    try:
        claimed = worker._claim_job(conn)
        assert claimed and int(claimed["id"]) == job_id
        assert world.one("SELECT status FROM apify_jobs WHERE id=%s", (job_id,))["status"] == "running"
        worker._process_claimed_job(conn, claimed)
    finally:
        conn.close()

    # ③ 终态:done + cache ready
    done = world.one("SELECT status, last_error, last_error_category FROM apify_jobs WHERE id=%s", (job_id,))
    assert done["status"] == "done", done
    cache = world.one(
        "SELECT status, model, triggered_by_user_id FROM vkpi_analysis_cache WHERE target_type='video' AND target_id=%s AND derive_method=%s",
        (str(world.evidence_id), _FINAL_V1_DERIVE),
    )
    assert cache and cache["status"] == "ready" and cache["model"] == worker.WORKER_GEMINI_MODEL
    assert cache["triggered_by_user_id"] == world.user_id

    # ④ 台账双写各一行且 staff_id=owner(子进程口径:llm_context.triggered_by = payload.staff_id)
    assert len(analyzer_calls) == 1 and analyzer_calls[0]["mode"] == "youtube"
    assert analyzer_calls[0]["llm_context"]["triggered_by"] == world.staff_id
    assert analyzer_calls[0]["receipt"]["cost_ledger"]["recorded"] is True
    llm_rows = world.all("SELECT created_by_staff_id, status, cost_micro_usd FROM vkpi_llm_calls WHERE metadata_json LIKE %s", (f'%"target_id": "{world.evidence_id}"%',))
    assert len(llm_rows) == 1 and llm_rows[0]["created_by_staff_id"] == world.staff_id and llm_rows[0]["status"] == "success"
    ledger_rows = world.all("SELECT staff_id, ai_provider, metadata_json FROM vkpi_ai_cost_ledger WHERE metadata_json LIKE %s", (f'%"target_id": "{world.evidence_id}"%',))
    assert len(ledger_rows) == 1 and ledger_rows[0]["staff_id"] == world.staff_id and ledger_rows[0]["ai_provider"] == "gemini"
    assert "unresolved_staff_id" not in str(ledger_rows[0]["metadata_json"])

    # ⑤ 账号进度:ready 1,无失败项
    from app.db.connection import PostgresCompatConnection
    import psycopg

    raw = psycopg.connect(pg_dsn, connect_timeout=5)
    try:
        progress = enqueue.account_video_analysis_progress(PostgresCompatConnection(raw, pool=None), world.pool_id)
    finally:
        raw.close()
    assert progress["state"] == "done" and progress["completed"] == 1 and progress["failed"] == 0
    assert progress["items"][0]["failure_category"] is None and progress["eta_seconds"] is None


def test_golden_path_unfenced_job_is_blocked_as_authorization(world: _World, pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.kol import video_analysis_enqueue as enqueue
    from app.platform import llm_gateway
    from app.workers import apify_jobs_worker as worker
    from app.workers import apify_jobs_worker_gemini as gemini_worker

    monkeypatch.setattr(llm_gateway, "budget_preflight", _allowed_preflight(worker))
    monkeypatch.setattr(worker, "_respect_gemini_qps", lambda _conn: None)

    def must_not_run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("blocked job must never reach the analyzer")

    monkeypatch.setattr(gemini_worker, "_run_gemini_analyzer_with_timeout", must_not_run)

    # 反例:staff=None(无围栏、无会话血缘)入队——旧式直连任务
    result = enqueue.enqueue_final_v1_video_analysis_batch(
        items=[{"kol_pool_id": world.pool_id, "evidence_id": world.evidence_id}],
        staff=None,
        enforce_target_write=False,
    )
    assert result["queued"] == 1, result
    job_id = int(result["items"][0]["job"]["id"])
    payload = world.one("SELECT payload FROM apify_jobs WHERE id=%s", (job_id,))["payload"]
    assert payload.get("staff_id") is None and payload.get("triggered_by_user_id") is None
    assert "my_kol_paid_action_fence" not in payload

    _claim_only(worker, monkeypatch, job_id)
    conn = _worker_conn(pg_dsn)
    try:
        claimed = worker._claim_job(conn)
        assert claimed and int(claimed["id"]) == job_id
        worker._process_claimed_job(conn, claimed)
    finally:
        conn.close()

    blocked = world.one("SELECT status, last_error, last_error_category FROM apify_jobs WHERE id=%s", (job_id,))
    assert blocked["status"] == "blocked"
    # 任务 5:runtime 把类别写成 authorization(不再恒为 'blocked'),F3 以此列为准
    assert blocked["last_error_category"] == "authorization"
    assert json.loads(blocked["last_error"])["reason"] == "video_analysis_authorization_fence_required"
    assert world.one("SELECT 1 AS x FROM vkpi_analysis_cache WHERE target_type='video' AND target_id=%s", (str(world.evidence_id),)) is None
    assert world.all("SELECT id FROM vkpi_ai_cost_ledger WHERE metadata_json LIKE %s", (f'%"target_id": "{world.evidence_id}"%',)) == []

    from app.db.connection import PostgresCompatConnection
    import psycopg

    raw = psycopg.connect(pg_dsn, connect_timeout=5)
    try:
        progress = enqueue.account_video_analysis_progress(PostgresCompatConnection(raw, pool=None), world.pool_id)
    finally:
        raw.close()
    item = progress["items"][0]
    assert progress["state"] == "partial_failed" and progress["failed"] == 1
    assert item["state"] == "blocked" and item["failure_category"] == "authorization"
    assert item["failure_reason_human"] == "授权围栏缺失:请从 MY KOL 页重新发起"
    assert item["failure_code"] == "video_analysis_authorization_fence_required"


def test_forced_ledger_failure_surfaces_root_cause(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    """C1:真 PG 下强制记账撞 FK/约束时,异常信息与调用行 metadata 都带根因类名+首行(脱敏)。"""
    from app.db.connection import db_connection_sync_scope
    from app.domains.costs import budget_guard
    from app.platform import llm_gateway

    original = budget_guard.record_cost

    def record_cost_with_bad_fk(**kwargs: Any) -> dict[str, Any]:
        # 绕过 staff 外键安全层,复现旧世界的 FK 炸法(staff_id=1 在隔离库不存在)
        monkeypatch.setattr(budget_guard, "_existing_staff_id", lambda _conn, sid: sid)
        try:
            return original(**{**kwargs, "staff_id": 1, "triggered_by": None})
        finally:
            monkeypatch.undo()

    monkeypatch.setattr(budget_guard, "record_cost", record_cost_with_bad_fk)
    assert world.one("SELECT 1 AS x FROM staff WHERE id=1") is None, "isolated db must not have staff id 1"
    with db_connection_sync_scope():
        with pytest.raises(RuntimeError) as caught:
            llm_gateway.record_call(
                provider="google",
                model="gemini-3.6-flash",
                purpose="golden-forced-ledger-fk",
                prompt=f"golden-path:{world.evidence_id}",
                cost_micro_usd=10,
                status="success",
                fallback_used=False,
                cost_tag="cron:vkpi_analysis_worker",
                metadata={"target_id": str(world.evidence_id)},
                update_budget_scopes=False,
                force_cost_ledger=True,
            )
    message = str(caught.value)
    assert message.startswith("forced_ai_cost_ledger_write_failed: ForeignKeyViolation:"), message
    assert "staff_id" in message
    row = world.one("SELECT metadata_json FROM vkpi_llm_calls WHERE purpose='golden-forced-ledger-fk' ORDER BY id DESC LIMIT 1")
    assert row and "cost_ledger_error" in row["metadata_json"] and "ForeignKeyViolation" in row["metadata_json"]
    # 原异常链与 note 都保留根因
    cause = caught.value.__cause__
    assert cause is not None and type(cause).__name__ == "ForeignKeyViolation"
    assert any("cost_ledger_write_failed: ForeignKeyViolation" in note for note in getattr(cause, "__notes__", []))
