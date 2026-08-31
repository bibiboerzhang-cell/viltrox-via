"""全池邮箱 MX 批验证脚本(scripts/run_email_mx_verification.py)的合同测试。

四条必须钉死的性质:
  ① 配额上限:脚本级 HARD_QUOTA 硬夹,且真实 DNS 查询数永不超过配额;
     配额不够时剩余域名诚实记 unknown,**不记 fail**。
  ② 超时记 unknown 不记 fail:dig 超时 / SERVFAIL / 工具缺席都只是「没查出来」。
  ③ 缓存命中:库缓存(已有确定判定的域名)与进程内域名去重都必须真正省掉查询;
     mx_unknown 绝不进缓存种子(它不是结论)。
  ④ 幂等重跑:evidence 不堆重复行、分布不漂移、第二遍几乎零 DNS。

全部离线:DNS 打桩到 ceq._run_dig(走真实预算/并发/兜底逻辑),库用内存 sqlite。
"""
from __future__ import annotations

import ast
import importlib.util
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from app.domains.kol import contact_email_quality as ceq

ROOT = Path(__file__).resolve().parents[1]


def _load_script_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "run_email_mx_verification", ROOT / "scripts" / "run_email_mx_verification.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))  # stdout_utils
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    return mod


mx = _load_script_module()

_SCHEMA = """
CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY);
CREATE TABLE vkpi_kol_pool_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kol_pool_id INTEGER NOT NULL,
    contact_type TEXT NOT NULL,
    contact_value TEXT NOT NULL,
    contact_source TEXT NOT NULL,
    confidence REAL,
    verification_status TEXT NOT NULL DEFAULT 'observed',
    last_seen_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE vkpi_kol_contact_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL,
    kol_pool_id INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    source_field TEXT NOT NULL DEFAULT '',
    evidence_fingerprint TEXT NOT NULL,
    confidence REAL,
    is_public_declared INTEGER NOT NULL DEFAULT 0,
    consent_basis TEXT NOT NULL DEFAULT 'source_observation',
    consent_at TEXT,
    provider_run_ref TEXT NOT NULL DEFAULT '',
    observed_by_staff_id INTEGER,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(contact_id, evidence_fingerprint)
);
"""

_DIG_MX_OK = ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n;; flags: qr rd ra; QUERY: 1, ANSWER: 2"
_DIG_NXDOMAIN = ";; ->>HEADER<<- opcode: QUERY, status: NXDOMAIN, id: 3\n;; flags: qr rd ra; QUERY: 1, ANSWER: 0"
_DIG_NOERROR_0 = ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 2\n;; flags: qr rd ra; QUERY: 1, ANSWER: 0"


@pytest.fixture()
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO vkpi_kol_pool (id) VALUES (1)")
    return conn


@pytest.fixture(autouse=True)
def _reset_module_state():
    ceq.clear_mx_cache()
    ceq.set_dns_budget(ceq.MAX_DNS_BUDGET)
    yield
    ceq.clear_mx_cache()
    ceq.set_dns_budget(ceq.MAX_DNS_BUDGET)


def _stub_dns(monkeypatch: pytest.MonkeyPatch, mapping: dict[tuple[str, str], tuple[str, str]],
              default: tuple[str, str] = (_DIG_MX_OK, "")) -> list[tuple[str, str]]:
    """打桩 ceq._run_dig:走真实的预算/并发/implicit-A 逻辑,只把网络那一层换掉。"""
    calls: list[tuple[str, str]] = []

    def fake_run_dig(args: list[str], timeout: float) -> tuple[str, str]:
        qtype = "A" if "+short" in args else "MX"
        domain = args[-1]
        calls.append((qtype, domain))
        return mapping.get((qtype, domain), default)

    monkeypatch.setattr(ceq, "_run_dig", fake_run_dig)
    monkeypatch.setattr(ceq, "_mx_via_dnspython", lambda domain, timeout: None)
    return calls


def _insert(db: sqlite3.Connection, value: str, *, source: str = "raw_bio_scan",
            kol: int = 1, ctype: str = "email", status: str = "observed") -> int:
    cur = db.execute(
        """
        INSERT INTO vkpi_kol_pool_contacts
            (kol_pool_id, contact_type, contact_value, contact_source, verification_status, last_seen_at)
        VALUES (?,?,?,?,?,?)
        """,
        (kol, ctype, value, source, status, "2026-08-01T00:00:00+00:00"),
    )
    return int(cur.lastrowid)


def _evidence_rows(db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT contact_id, source_field FROM vkpi_kol_contact_evidence WHERE source_type=? ORDER BY contact_id",
        (ceq.EVIDENCE_SOURCE_TYPE,),
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ ① 配额上限
def test_quota_hard_clamped_to_600() -> None:
    assert mx.HARD_QUOTA == 600
    assert mx.clamp_quota(5000) == 600      # --quota 只能往小调
    assert mx.clamp_quota(120) == 120
    assert mx.clamp_quota(-7) == 0


def test_resolve_never_exceeds_quota_and_starved_are_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_dns(monkeypatch, {})
    domains = [f"d{i}.dev" for i in range(10)]
    outcome = mx.resolve_domains(domains, timeout=2.0, quota=3)
    assert outcome["dns_queries_spent"] <= 3
    assert len(calls) <= 3
    starved = [d for d in domains if outcome["results"][d][1] == "script_quota_exhausted"]
    assert outcome["domains_starved"] == len(starved) > 0
    # 配额不足绝不能被读成「这些域名收不了信」
    assert all(outcome["results"][d][0] == ceq.MX_UNKNOWN for d in starved)
    assert not any(status == ceq.MX_BAD for status, _ in outcome["results"].values())


def test_chunking_never_exhausts_module_budget_mid_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """模块单轮预算硬夹 200;250 个域名必须分轮,且任一域名都不能撞 dns_budget_exhausted。

    撞上就会把 exhausted 写进 ceq 的进程内域名缓存,毒化之后所有轮次。
    """
    _stub_dns(monkeypatch, {})
    domains = [f"d{i}.dev" for i in range(250)]
    outcome = mx.resolve_domains(domains, timeout=2.0, quota=mx.HARD_QUOTA)
    assert outcome["rounds"] == 3 and outcome["domains_resolved"] == 250
    assert mx.CHUNK_DOMAINS * mx.QUERIES_PER_DOMAIN <= ceq.MAX_DNS_BUDGET
    details = {detail for _, detail in outcome["results"].values()}
    assert "dns_budget_exhausted" not in details
    assert outcome["dns_queries_spent"] == 250 <= mx.HARD_QUOTA


def test_concurrency_clamped_to_four(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[int] = []

    class FakePool:
        def __init__(self, max_workers: int) -> None:
            captured.append(max_workers)

        def __enter__(self) -> "FakePool":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def map(self, fn: Any, items: Any) -> list:
            return [fn(item) for item in items]

    monkeypatch.setattr(mx, "ThreadPoolExecutor", FakePool)
    _stub_dns(monkeypatch, {})
    mx.resolve_domains([f"d{i}.dev" for i in range(9)], timeout=2.0, quota=mx.HARD_QUOTA)
    assert captured == [4] and ceq.MAX_DNS_CONCURRENCY == 4


# ------------------------------------------------------------------ ② 超时口径
def test_timeout_is_unknown_not_fail(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(
        monkeypatch,
        {
            ("MX", "slow.dev"): ("", "dig_timeout"),
            ("MX", "flaky.dev"): (";; ->>HEADER<<- status: SERVFAIL", ""),
            ("MX", "gone.dev"): (_DIG_NXDOMAIN, ""),
        },
    )
    _insert(db, "a@slow.dev")
    _insert(db, "b@flaky.dev")
    _insert(db, "c@gone.dev")
    report = mx.run(db, limit=None, quota=mx.HARD_QUOTA, timeout=2.0, do_mx=True)
    assert report["distribution"] == {"mx_ok": 0, "mx_fail": 1, "unknown": 2, "syntax_bad": 0}
    assert report["unknown_reasons"] == {"dig_timeout": 1, "dns_status:SERVFAIL": 1}
    assert report["mx_fail_domains"] == [("gone.dev", 1)]  # 只有 NXDOMAIN 才算 fail
    stored = {row["contact_id"]: row["source_field"] for row in _evidence_rows(db)}
    assert "mx=mx_unknown" in stored[1] and "mx=mx_bad" in stored[3]


def test_no_mx_no_a_is_fail_but_implicit_a_is_ok(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(
        monkeypatch,
        {
            ("MX", "apex.dev"): (_DIG_NOERROR_0, ""), ("A", "apex.dev"): ("93.184.216.34\n", ""),
            ("MX", "hollow.dev"): (_DIG_NOERROR_0, ""), ("A", "hollow.dev"): ("", ""),
        },
    )
    _insert(db, "a@apex.dev")
    _insert(db, "b@hollow.dev")
    report = mx.run(db, limit=None, quota=mx.HARD_QUOTA, timeout=2.0, do_mx=True)
    assert report["distribution"]["mx_ok"] == 1 and report["distribution"]["mx_fail"] == 1
    assert report["dns_queries_spent"] == 4  # 两个域名各消耗 MX + A 兜底


def test_syntax_bad_row_costs_zero_dns(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_dns(monkeypatch, {})
    _insert(db, "someone@example.com")          # 占位域名
    _insert(db, "n@handle.io", source="raw_full_scan")  # \n@提及腐蚀
    report = mx.run(db, limit=None, quota=mx.HARD_QUOTA, timeout=2.0, do_mx=True)
    assert report["distribution"] == {"mx_ok": 0, "mx_fail": 0, "unknown": 0, "syntax_bad": 2}
    assert calls == []  # 语法就坏的行绝不出网
    assert all("mx=mx_unknown" in row["source_field"] for row in _evidence_rows(db))


# ------------------------------------------------------------------ ③ 缓存命中
def test_db_cache_seed_skips_dns(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    cid = _insert(db, "a@known.dev")
    ceq.cache_quality_result(db, contact_id=cid, kol_pool_id=1, email="a@known.dev",
                             syntax_ok=True, syntax_reason="", mx_status=ceq.MX_OK, role=False, detail="mx_records:2")
    calls = _stub_dns(monkeypatch, {})
    report = mx.run(db, limit=None, quota=mx.HARD_QUOTA, timeout=2.0, do_mx=True)
    assert calls == [] and report["dns_queries_spent"] == 0
    assert report["domains_from_db_cache"] == 1
    assert report["distribution"]["mx_ok"] == 1


def test_unknown_verdict_is_not_seeded(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """mx_unknown 是「没查出来」不是结论,下一轮必须重查——否则超时会永久固化。"""
    cid = _insert(db, "a@retry.dev")
    ceq.cache_quality_result(db, contact_id=cid, kol_pool_id=1, email="a@retry.dev",
                             syntax_ok=True, syntax_reason="", mx_status=ceq.MX_UNKNOWN, role=False, detail="dig_timeout")
    calls = _stub_dns(monkeypatch, {("MX", "retry.dev"): (_DIG_MX_OK, "")})
    report = mx.run(db, limit=None, quota=mx.HARD_QUOTA, timeout=2.0, do_mx=True)
    assert calls == [("MX", "retry.dev")]
    assert report["domains_from_db_cache"] == 0
    assert report["distribution"]["mx_ok"] == 1


def test_cache_conflict_forces_requery(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    a = _insert(db, "a@split.dev")
    b = _insert(db, "b@split.dev")
    ceq.cache_quality_result(db, contact_id=a, kol_pool_id=1, email="a@split.dev",
                             syntax_ok=True, syntax_reason="", mx_status=ceq.MX_OK, role=False)
    ceq.cache_quality_result(db, contact_id=b, kol_pool_id=1, email="b@split.dev",
                             syntax_ok=True, syntax_reason="", mx_status=ceq.MX_BAD, role=False)
    calls = _stub_dns(monkeypatch, {})
    report = mx.run(db, limit=None, quota=mx.HARD_QUOTA, timeout=2.0, do_mx=True)
    assert calls == [("MX", "split.dev")]  # 判定打架 -> 宁可重查,不采信脏缓存
    assert report["domains_from_db_cache"] == 0


def test_in_run_domain_dedupe(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(6):
        _insert(db, f"user{i}@gmail.com")
    calls = _stub_dns(monkeypatch, {})
    report = mx.run(db, limit=None, quota=mx.HARD_QUOTA, timeout=2.0, do_mx=True)
    assert calls == [("MX", "gmail.com")]  # 6 行 1 个域名 = 1 次查询
    assert report["unique_domains"] == 1 and report["distribution"]["mx_ok"] == 6


# ------------------------------------------------------------------ ④ 幂等重跑
def test_idempotent_rerun(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _insert(db, "a@gmail.com", source="youtube_about_declared")
    _insert(db, "b@gone.dev", source="raw_full_scan")
    _stub_dns(monkeypatch, {("MX", "gone.dev"): (_DIG_NXDOMAIN, "")})
    first = mx.run(db, limit=None, quota=mx.HARD_QUOTA, timeout=2.0, do_mx=True)
    rows_after_first = _evidence_rows(db)

    ceq.clear_mx_cache()  # 清进程缓存,逼第二遍只能靠库缓存
    calls = _stub_dns(monkeypatch, {("MX", "gone.dev"): (_DIG_NXDOMAIN, "")})
    second = mx.run(db, limit=None, quota=mx.HARD_QUOTA, timeout=2.0, do_mx=True)

    assert second["distribution"] == first["distribution"]
    assert second["by_source"] == first["by_source"]
    assert _evidence_rows(db) == rows_after_first  # 不堆重复行,verdict 不漂移
    assert calls == [] and second["dns_queries_spent"] == 0  # 第二遍零 DNS


def test_terminal_rows_excluded_and_contacts_untouched(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _insert(db, "live@gmail.com")
    _insert(db, "dead@gmail.com", status="invalid")
    _insert(db, "gone@gmail.com", status="revoked")
    before = [dict(r) for r in db.execute(
        "SELECT id, contact_value, verification_status FROM vkpi_kol_pool_contacts ORDER BY id").fetchall()]
    _stub_dns(monkeypatch, {})
    report = mx.run(db, limit=None, quota=mx.HARD_QUOTA, timeout=2.0, do_mx=True)
    after = [dict(r) for r in db.execute(
        "SELECT id, contact_value, verification_status FROM vkpi_kol_pool_contacts ORDER BY id").fetchall()]
    assert report["rows_checked"] == 1
    assert after == before  # 邮箱值与终态一个都没动


# ------------------------------------------------------------------ 报告与红线
def test_by_source_grouping_and_rates(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _insert(db, "a@ok.dev", source="youtube_about_declared")
    _insert(db, "b@ok.dev", source="raw_full_scan")
    _insert(db, "c@gone.dev", source="raw_full_scan")
    _stub_dns(monkeypatch, {("MX", "gone.dev"): (_DIG_NXDOMAIN, "")})
    report = mx.run(db, limit=None, quota=mx.HARD_QUOTA, timeout=2.0, do_mx=True)
    assert report["by_source"]["youtube_about_declared"]["mx_ok_rate"] == 1.0
    assert report["by_source"]["raw_full_scan"] == {
        "mx_ok": 1, "mx_fail": 1, "unknown": 0, "syntax_bad": 0, "rows": 2, "mx_ok_rate": 0.5,
    }
    assert report["mx_ok_rate"] == round(2 / 3, 3)


def test_report_carries_no_email_plaintext(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _insert(db, "secret.person@gone.dev", source="raw_full_scan")
    _stub_dns(monkeypatch, {("MX", "gone.dev"): (_DIG_NXDOMAIN, "")})
    report = mx.run(db, limit=None, quota=mx.HARD_QUOTA, timeout=2.0, do_mx=True)
    assert "secret.person" not in repr(report) and "@" not in repr(report["mx_fail_domains"])


def test_no_mx_mode_writes_unknown_with_zero_dns(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _insert(db, "a@gmail.com")
    calls = _stub_dns(monkeypatch, {})
    report = mx.run(db, limit=None, quota=mx.HARD_QUOTA, timeout=2.0, do_mx=False)
    assert calls == [] and report["mode"] == "syntax_only"
    assert report["distribution"]["unknown"] == 1
    assert "mx_disabled" in _evidence_rows(db)[0]["source_field"]


def test_dry_run_report_plans_without_writing(db: sqlite3.Connection) -> None:
    _insert(db, "a@gmail.com")
    _insert(db, "b@gmail.com", source="website_declared")
    _insert(db, "bad@example.com", source="website_declared")
    plans = mx.build_plans(mx.fetch_email_rows(db, None))
    report = mx.dry_run_report(plans, mx.unique_domains(plans), mx.seed_domains_from_db(db, plans))
    assert report["rows_total"] == 3 and report["syntax_bad_rows"] == 1
    assert report["unique_domains"] == 1 and report["worst_case_dns_queries"] == 2
    assert report["rows_by_source"] == {"raw_bio_scan": 1, "website_declared": 2}
    assert _evidence_rows(db) == []  # dry-run 零写库


def test_source_has_no_smtp_probe_path() -> None:
    """红线:域名级 MX/A 而已;源码里不许出现任何 SMTP 会话的零件(按 AST 判,不按字面)。"""
    src = (ROOT / "scripts" / "run_email_mx_verification.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"smtplib", "socket", "ssl", "asyncio"})
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert attrs.isdisjoint({"sendmail", "connect", "starttls", "docmd"})
