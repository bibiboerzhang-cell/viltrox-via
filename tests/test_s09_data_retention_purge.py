"""S-09:保留期 purge 日任务——默认 dry-run 只报数;闸开才真删;迁移 308 只加列/索引。"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_TS = "%Y-%m-%dT%H:%M:%SZ"
NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
SECRET = "s09-test-suppression-key-0123456789abcdef"  # ≥32 bytes


def _ago(days: int) -> str:
    return (NOW - timedelta(days=days)).strftime(_TS)


def _conn(*, with_purged_column: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    purged = ", payload_purged_at TEXT" if with_purged_column else ""
    conn.executescript(
        f"""
        CREATE TABLE apify_jobs (id INTEGER PRIMARY KEY, job_type TEXT, payload TEXT, status TEXT, created_at TEXT{purged});
        CREATE TABLE vkpi_comments (id INTEGER PRIMARY KEY, comment_text TEXT, created_at TEXT, fetched_at TEXT);
        CREATE TABLE kol_comments (id INTEGER PRIMARY KEY, comment_text TEXT, created_at TEXT);
        CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, email TEXT DEFAULT '');
        CREATE TABLE vkpi_kol_pool_contacts (id INTEGER PRIMARY KEY, kol_pool_id INTEGER, contact_type TEXT, contact_value TEXT);
        CREATE TABLE vkpi_kol_contact_suppressions (
            id INTEGER PRIMARY KEY, brand_scope TEXT, kol_pool_id INTEGER, channel TEXT,
            contact_fingerprint TEXT, fingerprint_key_id TEXT, is_active BOOLEAN DEFAULT TRUE
        );
        """
    )
    conn.executemany(
        "INSERT INTO apify_jobs (job_type, payload, status, created_at) VALUES (?,?,?,?)",
        [
            ("x", '{"raw": 1}', "done", _ago(120)),      # 候选
            ("x", '{"raw": 2}', "failed", _ago(91)),     # 候选
            ("x", '{"raw": 3}', "done", _ago(30)),       # 太新
            ("x", '{"raw": 4}', "queued", _ago(400)),    # 非终态
            ("x", None, "done", _ago(400)),              # 已无 payload
        ],
    )
    conn.executemany(
        "INSERT INTO vkpi_comments (comment_text, created_at, fetched_at) VALUES (?,?,?)",
        [("old", _ago(300), _ago(200)), ("fresh", _ago(300), _ago(10)), ("no-fetch", _ago(181), None)],
    )
    conn.executemany(
        "INSERT INTO kol_comments (comment_text, created_at) VALUES (?,?)",
        [("old", _ago(181)), ("fresh", _ago(179))],
    )
    return conn


def _seed_suppression(conn) -> None:
    from app.domains.kol.contact_suppression import contact_fingerprint

    conn.execute("INSERT INTO vkpi_kol_pool (id, email) VALUES (7, 'Blocked@Example.com')")
    conn.execute("INSERT INTO vkpi_kol_pool (id, email) VALUES (8, 'other@example.com')")
    conn.executemany(
        "INSERT INTO vkpi_kol_pool_contacts (kol_pool_id, contact_type, contact_value) VALUES (?,?,?)",
        [(7, "email", "blocked@example.com"), (7, "business_email", "keep@example.com"), (8, "email", "blocked@example.com")],
    )
    fingerprint = contact_fingerprint(
        brand_scope="viltrox", kol_pool_id=7, channel="email", normalized_value="blocked@example.com", secret=SECRET
    )
    conn.execute(
        "INSERT INTO vkpi_kol_contact_suppressions (brand_scope, kol_pool_id, channel, contact_fingerprint, fingerprint_key_id, is_active)"
        " VALUES ('viltrox', 7, 'email', ?, 'abcdef0123456789', TRUE)",
        (fingerprint,),
    )


def _policy():
    return {"apify_payload_days": 90, "comments_days": 180, "batch_limit": 5000}


def test_dry_run_reports_counts_and_writes_nothing():
    from app.services.scheduler import jobs_retention as jr

    conn = _conn()
    _seed_suppression(conn)
    result = jr.run_retention(conn, execute=False, now=NOW, policy=_policy(), secret=SECRET)
    assert result["dry_run"] is True
    assert result["apify_payload"] == {"candidates": 2, "purged": 0, "executed": False}
    assert result["comments"]["vkpi_comments"] == {"candidates": 2, "purged": 0, "executed": False}
    assert result["comments"]["kol_comments"] == {"candidates": 1, "purged": 0, "executed": False}
    # 池 7:一条 contacts 行 + pool.email 命中;池 8 同邮箱不同池 → 指纹不同,不命中
    assert result["suppressed_contacts"] == {"candidates": 2, "purged": 0, "executed": False}
    assert conn.execute("SELECT COUNT(*) FROM apify_jobs WHERE payload IS NOT NULL").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM vkpi_comments").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM vkpi_kol_pool_contacts").fetchone()[0] == 3
    assert conn.execute("SELECT email FROM vkpi_kol_pool WHERE id=7").fetchone()[0] == "Blocked@Example.com"


def test_execute_purges_within_policy_only():
    from app.services.scheduler import jobs_retention as jr

    conn = _conn()
    _seed_suppression(conn)
    result = jr.run_retention(conn, execute=True, now=NOW, policy=_policy(), secret=SECRET)
    assert result["apify_payload"] == {"candidates": 2, "purged": 2, "executed": True}
    assert result["comments"]["vkpi_comments"]["purged"] == 2
    assert result["comments"]["kol_comments"]["purged"] == 1
    assert result["suppressed_contacts"] == {"candidates": 2, "purged": 2, "executed": True}

    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM apify_jobs").fetchall()}
    assert rows[1]["payload"] is None and rows[1]["payload_purged_at"] == NOW.strftime(_TS)
    assert rows[2]["payload"] is None and rows[2]["payload_purged_at"]
    assert rows[3]["payload"] == '{"raw": 3}' and rows[3]["payload_purged_at"] is None
    assert rows[4]["payload"] == '{"raw": 4}'
    assert len(rows) == 5  # 行本身保留(FK/记账证据)
    assert [r[0] for r in conn.execute("SELECT comment_text FROM vkpi_comments").fetchall()] == ["fresh"]
    assert [r[0] for r in conn.execute("SELECT comment_text FROM kol_comments").fetchall()] == ["fresh"]
    contacts = [tuple(r) for r in conn.execute(
        "SELECT kol_pool_id, contact_value FROM vkpi_kol_pool_contacts ORDER BY id"
    ).fetchall()]
    assert contacts == [(7, "keep@example.com"), (8, "blocked@example.com")]
    assert conn.execute("SELECT email FROM vkpi_kol_pool WHERE id=7").fetchone()[0] == ""
    assert conn.execute("SELECT email FROM vkpi_kol_pool WHERE id=8").fetchone()[0] == "other@example.com"

    # 第二轮幂等:已盖章的行不再是候选
    again = jr.run_retention(conn, execute=True, now=NOW, policy=_policy(), secret=SECRET)
    assert again["apify_payload"]["candidates"] == 0
    assert again["suppressed_contacts"]["candidates"] == 0


def test_batch_limit_bounds_each_run():
    from app.services.scheduler import jobs_retention as jr

    conn = _conn()
    policy = dict(_policy(), batch_limit=1)
    result = jr.run_retention(conn, execute=True, now=NOW, policy=policy)
    assert result["apify_payload"] == {"candidates": 2, "purged": 1, "executed": True}
    assert result["comments"]["vkpi_comments"] == {"candidates": 2, "purged": 1, "executed": True}


def test_missing_308_column_skips_apify_bucket_honestly():
    from app.services.scheduler import jobs_retention as jr

    conn = _conn(with_purged_column=False)
    result = jr.run_retention(conn, execute=True, now=NOW, policy=_policy())
    bucket = result["apify_payload"]
    assert bucket["executed"] is False and bucket["candidates"] == 0
    assert "308" in bucket["note"]
    assert conn.execute("SELECT COUNT(*) FROM apify_jobs WHERE payload IS NOT NULL").fetchone()[0] == 4


def test_suppression_key_missing_fails_closed(monkeypatch):
    from app.services.scheduler import jobs_retention as jr

    monkeypatch.delenv("VKPI_CONTACT_SUPPRESSION_HMAC_KEY", raising=False)
    conn = _conn()
    _seed_suppression(conn)
    result = jr.run_retention(conn, execute=True, now=NOW, policy=_policy())  # secret=None → 读 env → 缺
    bucket = result["suppressed_contacts"]
    assert bucket["executed"] is False and "fail-closed" in bucket["note"]
    assert conn.execute("SELECT COUNT(*) FROM vkpi_kol_pool_contacts").fetchone()[0] == 3


def test_gate_defaults_off_and_env_opens_it(monkeypatch):
    from app.services.scheduler import jobs_retention as jr

    monkeypatch.delenv("VKPI_DATA_RETENTION_PURGE", raising=False)
    monkeypatch.setattr(jr, "_registry_enabled", lambda: False)
    assert jr.purge_enabled() is False
    monkeypatch.setenv("VKPI_DATA_RETENTION_PURGE", "1")
    assert jr.purge_enabled() is True
    monkeypatch.setenv("VKPI_DATA_RETENTION_PURGE", "0")
    monkeypatch.setattr(jr, "_registry_enabled", lambda: True)
    assert jr.purge_enabled() is True


def test_policy_env_overrides_with_fallback(monkeypatch):
    from app.services.scheduler import jobs_retention as jr

    for key in ("VKPI_RETENTION_APIFY_PAYLOAD_DAYS", "VKPI_RETENTION_COMMENTS_DAYS", "VKPI_RETENTION_BATCH_LIMIT"):
        monkeypatch.delenv(key, raising=False)
    assert jr.retention_policy() == {"apify_payload_days": 90, "comments_days": 180, "batch_limit": 5000}
    monkeypatch.setenv("VKPI_RETENTION_APIFY_PAYLOAD_DAYS", "45")
    monkeypatch.setenv("VKPI_RETENTION_COMMENTS_DAYS", "bogus")
    monkeypatch.setenv("VKPI_RETENTION_BATCH_LIMIT", "-1")
    assert jr.retention_policy() == {"apify_payload_days": 45, "comments_days": 180, "batch_limit": 5000}


def test_job_wrapper_logs_counts_only_and_never_raises(monkeypatch):
    from app.services.scheduler import jobs_retention as jr

    recorded: list[dict] = []
    monkeypatch.setattr(jr, "_record", lambda **kw: recorded.append(kw))
    monkeypatch.setattr(jr, "purge_enabled", lambda: False)
    fake = {"dry_run": True, "apify_payload": {"candidates": 3, "purged": 0}, "comments": {}, "suppressed_contacts": {"candidates": 1, "purged": 0}}
    monkeypatch.setattr(jr, "run_retention", lambda **kw: fake)
    assert asyncio.run(jr.job_vkpi_data_retention_purge()) == fake
    assert recorded == [{"ok": True}]
    summary = jr._summary(fake)
    assert summary["apify_candidates"] == 3 and summary["contact_candidates"] == 1
    assert all(not isinstance(v, str) or "@" not in v for v in summary.values())

    def _boom(**kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(jr, "run_retention", _boom)
    assert asyncio.run(jr.job_vkpi_data_retention_purge()) is None
    assert recorded[-1]["ok"] is False


def test_job_is_registered_and_module_stays_out_of_scheduler_cycle():
    jobs_src = (BACKEND_ROOT / "app" / "services" / "scheduler" / "jobs.py").read_text(encoding="utf-8")
    assert 'id="vkpi_data_retention_purge"' in jobs_src
    assert "from app.services.scheduler.jobs_retention import job_vkpi_data_retention_purge" in jobs_src
    module_src = (BACKEND_ROOT / "app" / "services" / "scheduler" / "jobs_retention.py").read_text(encoding="utf-8")
    assert "from app.services.scheduler" not in module_src
    assert "from .jobs" not in module_src


def test_migration_308_only_adds_columns_and_indexes():
    up = (ROOT / "migrations" / "308_vkpi_privacy_retention_columns.sql").read_text(encoding="utf-8")
    down = (ROOT / "migrations" / "308_vkpi_privacy_retention_columns_down.sql").read_text(encoding="utf-8")
    code = "\n".join(l for l in up.splitlines() if not l.strip().startswith("--"))
    statements = [s.strip().upper() for s in code.split(";") if s.strip()]
    assert len(statements) == 8
    for body in statements:
        assert body.startswith(("ALTER TABLE", "CREATE INDEX IF NOT EXISTS", "COMMENT ON COLUMN")), body[:60]
        if body.startswith("ALTER TABLE"):
            assert "ADD COLUMN IF NOT EXISTS" in body and "DROP" not in body
    assert "vkpi_kol_portal_tokens" in up and "expires_at" in up
    assert "apify_jobs" in up and "payload_purged_at" in up
    for forbidden in ("DELETE FROM", "UPDATE ", "DROP ", "TRUNCATE"):
        assert forbidden not in code.upper(), forbidden
    assert "DROP COLUMN IF EXISTS payload_purged_at" in down
    assert "DROP COLUMN IF EXISTS expires_at" in down
    assert "version_key = '308_vkpi_privacy_retention_columns.sql'" in down
