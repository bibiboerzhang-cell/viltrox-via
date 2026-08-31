"""邮箱质检与跨源仲裁层测试(contact_email_quality)。

全部离线:DNS 层用 canned dig 输出打桩,零真实网络;库用内存 sqlite。
"""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from app.domains.kol import contact_email_quality as ceq

# ------------------------------------------------------------------ fixtures
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


def _insert_contact(
    db: sqlite3.Connection,
    value: str,
    *,
    kol: int = 1,
    source: str = "raw_bio_scan",
    ctype: str = "email",
    status: str = "observed",
    confidence: float | None = None,
    last_seen: str = "2026-08-01T00:00:00+00:00",
) -> int:
    cur = db.execute(
        """
        INSERT INTO vkpi_kol_pool_contacts
            (kol_pool_id, contact_type, contact_value, contact_source, confidence,
             verification_status, last_seen_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (kol, ctype, value, source, confidence, status, last_seen),
    )
    return int(cur.lastrowid)


_DIG_NOERROR_2 = ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n;; flags: qr rd ra; QUERY: 1, ANSWER: 2, AUTHORITY: 0"
_DIG_NOERROR_0 = ";; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 2\n;; flags: qr rd ra; QUERY: 1, ANSWER: 0, AUTHORITY: 1"
_DIG_NXDOMAIN = ";; ->>HEADER<<- opcode: QUERY, status: NXDOMAIN, id: 3\n;; flags: qr rd ra; QUERY: 1, ANSWER: 0, AUTHORITY: 1"
_DIG_SERVFAIL = ";; ->>HEADER<<- opcode: QUERY, status: SERVFAIL, id: 4"


def _stub_dns(monkeypatch: pytest.MonkeyPatch, mapping: dict[tuple[str, str], tuple[str, str]]):
    """打桩 _run_dig:mapping[(qtype, domain)] -> (stdout, err)。返回调用记录。"""
    calls: list[tuple[str, str]] = []

    def fake_run_dig(args: list[str], timeout: float) -> tuple[str, str]:
        qtype = "A" if "+short" in args else "MX"
        domain = args[-1]
        calls.append((qtype, domain))
        return mapping.get((qtype, domain), ("", "dig_error:NoFake"))

    monkeypatch.setattr(ceq, "_run_dig", fake_run_dig)
    monkeypatch.setattr(ceq, "_mx_via_dnspython", lambda domain, timeout: None)
    return calls


# ------------------------------------------------------------------ ① 语法层
@pytest.mark.parametrize(
    ("value", "ok", "reason"),
    [
        ("Jane.Doe+biz@Gmail.com", True, ""),
        ("john@studio.example.co", True, ""),
        ("", False, "empty_value"),
        ("user@example.com", False, "placeholder_domain"),
        ("someone@test.com", False, "placeholder_domain"),
        ("someone@mail.test.com", False, "placeholder_domain"),
        ("jo\nhn@gmail.com", False, "control_or_whitespace"),
        ("john\\ndoe@gmail.com", False, "control_or_whitespace"),
        ("jo hn@gmail.com", False, "control_or_whitespace"),
        ("john@@gmail.com", False, "at_sign_count"),
        ("johngmail.com", False, "at_sign_count"),
        (".john@gmail.com", False, "bad_local_part"),
        ("jo..hn@gmail.com", False, "bad_local_part"),
        ("john@-bad-.com", False, "bad_domain"),
        ("john@gmail", False, "bad_domain"),
        ("n@hamid.monadi", False, "n_mention_artifact"),
        ("n@away.ae", False, "n_mention_artifact"),  # 2 字母 ccTLD 也逃不过 \n@提及签名
        ("j@studio.dev", True, ""),  # 非 n 的单字母 local 合法,不误伤
        ("logo@site.png", False, "bad_tld_or_placeholder"),
    ],
)
def test_validate_email_syntax(value: str, ok: bool, reason: str) -> None:
    result = ceq.validate_email_syntax(value)
    assert result["ok"] is ok
    assert result["reason"] == reason


def test_syntax_normalizes_case_and_trailing_dot() -> None:
    result = ceq.validate_email_syntax("  Jane@Gmail.COM. ")
    assert result["ok"] is True
    assert result["email"] == "jane@gmail.com"


@pytest.mark.parametrize("value", ["noreply@gmail.com", "no-reply@brand.co", "no.reply@brand.co", "donotreply@x.dev"])
def test_role_emails_flagged_not_rejected(value: str) -> None:
    result = ceq.validate_email_syntax(value)
    assert result["ok"] is True
    assert result["role"] is True


def test_non_role_email_not_flagged() -> None:
    assert ceq.validate_email_syntax("norah@gmail.com")["role"] is False


def test_n_prefix_corrupt_detection() -> None:
    sibs = {"jane@gmail.com"}
    assert ceq.looks_n_prefix_corrupt("njane@gmail.com", sibs) is True
    assert ceq.looks_n_prefix_corrupt("jane@gmail.com", sibs) is False
    assert ceq.looks_n_prefix_corrupt("nick@gmail.com", sibs) is False  # 合法 n 开头人名不误伤


# ------------------------------------------------------------------ ② MX 层
def test_check_mx_ok_and_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_dns(monkeypatch, {("MX", "gmail.com"): (_DIG_NOERROR_2, "")})
    assert ceq.check_mx("gmail.com") == (ceq.MX_OK, "mx_records:2")
    assert ceq.check_mx("gmail.com") == (ceq.MX_OK, "mx_records:2")
    assert len(calls) == 1  # 第二次命中缓存,零新查询
    assert ceq.dns_budget_remaining() == ceq.MAX_DNS_BUDGET - 1


def test_check_mx_nxdomain_is_bad(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(monkeypatch, {("MX", "no-such-zone.dev"): (_DIG_NXDOMAIN, "")})
    assert ceq.check_mx("no-such-zone.dev") == (ceq.MX_BAD, "nxdomain")


def test_check_mx_implicit_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_dns(
        monkeypatch,
        {("MX", "apex.dev"): (_DIG_NOERROR_0, ""), ("A", "apex.dev"): ("93.184.216.34\n", "")},
    )
    assert ceq.check_mx("apex.dev") == (ceq.MX_OK, "implicit_mx_a")
    assert calls == [("MX", "apex.dev"), ("A", "apex.dev")]
    assert ceq.dns_budget_remaining() == ceq.MAX_DNS_BUDGET - 2  # fallback 消耗第二次预算


def test_check_mx_no_mx_no_a_is_bad(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(monkeypatch, {("MX", "hollow.dev"): (_DIG_NOERROR_0, ""), ("A", "hollow.dev"): ("", "")})
    assert ceq.check_mx("hollow.dev") == (ceq.MX_BAD, "no_mx_no_a")


def test_check_mx_timeout_and_servfail_are_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(
        monkeypatch,
        {("MX", "slow.dev"): ("", "dig_timeout"), ("MX", "flaky.dev"): (_DIG_SERVFAIL, "")},
    )
    assert ceq.check_mx("slow.dev") == (ceq.MX_UNKNOWN, "dig_timeout")
    assert ceq.check_mx("flaky.dev") == (ceq.MX_UNKNOWN, "dns_status:SERVFAIL")


def test_check_mx_budget_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(monkeypatch, {("MX", "a.dev"): (_DIG_NOERROR_2, ""), ("MX", "b.dev"): (_DIG_NOERROR_2, "")})
    ceq.set_dns_budget(1)
    assert ceq.check_mx("a.dev")[0] == ceq.MX_OK
    assert ceq.check_mx("b.dev") == (ceq.MX_UNKNOWN, "dns_budget_exhausted")


def test_budget_exhausts_mid_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(monkeypatch, {("MX", "apex.dev"): (_DIG_NOERROR_0, "")})
    ceq.set_dns_budget(1)
    assert ceq.check_mx("apex.dev") == (ceq.MX_UNKNOWN, "dns_budget_exhausted")


def test_dns_budget_hard_clamp() -> None:
    assert ceq.set_dns_budget(5000) == ceq.MAX_DNS_BUDGET == 200
    assert ceq.set_dns_budget(-3) == 0


def test_check_mx_rejects_bare_or_empty_domain() -> None:
    assert ceq.check_mx("")[0] == ceq.MX_BAD
    assert ceq.check_mx("localhost")[0] == ceq.MX_BAD


def test_dig_binary_missing_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ceq, "_mx_via_dnspython", lambda domain, timeout: None)

    def raise_missing(*args: Any, **kwargs: Any):
        raise FileNotFoundError("dig")

    monkeypatch.setattr(ceq.subprocess, "run", raise_missing)
    status, detail = ceq.check_mx("gmail.com")
    assert status == ceq.MX_UNKNOWN
    assert detail.startswith("dig_error:")


# ------------------------------------------------------------------ 缓存层
def test_cache_roundtrip_and_upsert(db: sqlite3.Connection) -> None:
    cid = _insert_contact(db, "jane@gmail.com", source="youtube_about_declared")
    ceq.cache_quality_result(
        db, contact_id=cid, kol_pool_id=1, email="jane@gmail.com",
        syntax_ok=True, syntax_reason="", mx_status=ceq.MX_UNKNOWN, role=False, detail="first",
    )
    ceq.cache_quality_result(
        db, contact_id=cid, kol_pool_id=1, email="jane@gmail.com",
        syntax_ok=True, syntax_reason="", mx_status=ceq.MX_OK, role=False, detail="second",
    )
    rows = db.execute(
        "SELECT * FROM vkpi_kol_contact_evidence WHERE contact_id=?", (cid,)
    ).fetchall()
    assert len(rows) == 1  # 同指纹 upsert,不堆重复行
    row = dict(rows[0])
    assert row["source_type"] == ceq.EVIDENCE_SOURCE_TYPE
    assert row["is_public_declared"] in (0, False)  # 绝不能进 verified 判定路径
    cache = ceq.load_quality_cache(db, [cid])
    assert cache[cid]["mx_status"] == ceq.MX_OK
    assert cache[cid]["syntax_ok"] is True
    assert cache[cid]["detail"] == "second"


def test_encode_parse_verdict_roundtrip() -> None:
    field = ceq.encode_verdict(
        syntax_ok=False, syntax_reason="placeholder_domain", mx_status=ceq.MX_UNKNOWN, role=True, detail="x"
    )
    parsed = ceq.parse_verdict(field)
    assert parsed["syntax_ok"] is False
    assert parsed["syntax_reason"] == "placeholder_domain"
    assert parsed["mx_status"] == ceq.MX_UNKNOWN
    assert parsed["role"] is True


def test_parse_verdict_garbage_defaults_unknown() -> None:
    parsed = ceq.parse_verdict("not-a-verdict")
    assert parsed["mx_status"] == ceq.MX_UNKNOWN
    assert parsed["syntax_ok"] is False


# ------------------------------------------------------------------ ③ 仲裁层
def test_best_email_source_order_dominates_unchecked_mx(db: sqlite3.Connection) -> None:
    """youtube 声明未校验(mx_unknown)仍胜过 raw_full_scan 已 mx_ok:未校验≠更差来源。"""
    low = _insert_contact(db, "scraped@low.dev", source="raw_full_scan")
    _insert_contact(db, "declared@high.dev", source="youtube_about_declared")
    ceq.cache_quality_result(
        db, contact_id=low, kol_pool_id=1, email="scraped@low.dev",
        syntax_ok=True, syntax_reason="", mx_status=ceq.MX_OK, role=False,
    )
    best = ceq.best_email_for_kol(1, conn=db)
    assert best["email"] == "declared@high.dev"
    assert best["mx_status"] == ceq.MX_UNKNOWN
    assert best["mx_checked"] is False
    assert best["usable"] is True


def test_best_email_mx_breaks_tie_within_same_source(db: sqlite3.Connection) -> None:
    a = _insert_contact(db, "a@one.dev", source="raw_bio_scan")
    b = _insert_contact(db, "b@two.dev", source="raw_bio_scan")
    ceq.cache_quality_result(db, contact_id=a, kol_pool_id=1, email="a@one.dev",
                             syntax_ok=True, syntax_reason="", mx_status=ceq.MX_UNKNOWN, role=False)
    ceq.cache_quality_result(db, contact_id=b, kol_pool_id=1, email="b@two.dev",
                             syntax_ok=True, syntax_reason="", mx_status=ceq.MX_OK, role=False)
    assert ceq.best_email_for_kol(1, conn=db)["email"] == "b@two.dev"


def test_best_email_mx_bad_sinks_below_lower_source(db: sqlite3.Connection) -> None:
    dead = _insert_contact(db, "dead@gone.dev", source="youtube_about_declared")
    _insert_contact(db, "alive@ok.dev", source="raw_full_scan")
    ceq.cache_quality_result(db, contact_id=dead, kol_pool_id=1, email="dead@gone.dev",
                             syntax_ok=True, syntax_reason="", mx_status=ceq.MX_BAD, role=False)
    best = ceq.best_email_for_kol(1, conn=db)
    assert best["email"] == "alive@ok.dev"
    assert best["usable"] is True


def test_best_email_role_demoted_below_personal(db: sqlite3.Connection) -> None:
    _insert_contact(db, "noreply@brand.dev", source="youtube_about_declared")
    _insert_contact(db, "jane@brand.dev", source="raw_full_scan")
    best = ceq.best_email_for_kol(1, conn=db)
    assert best["email"] == "jane@brand.dev"


def test_best_email_n_prefix_corrupt_sinks(db: sqlite3.Connection) -> None:
    _insert_contact(db, "njane@gmail.com", source="youtube_about_declared")
    _insert_contact(db, "jane@gmail.com", source="raw_full_scan")
    best = ceq.best_email_for_kol(1, conn=db)
    assert best["email"] == "jane@gmail.com"


def test_best_email_excludes_terminal_states(db: sqlite3.Connection) -> None:
    _insert_contact(db, "revoked@x.dev", source="youtube_about_declared", status="revoked")
    _insert_contact(db, "invalid@x.dev", source="youtube_about_declared", status="invalid")
    _insert_contact(db, "kept@x.dev", source="raw_full_scan")
    best = ceq.best_email_for_kol(1, conn=db)
    assert best["email"] == "kept@x.dev"
    assert best["candidates_considered"] == 1


def test_best_email_no_rows(db: sqlite3.Connection) -> None:
    best = ceq.best_email_for_kol(1, conn=db)
    assert best["email"] is None
    assert best["reason"] == "no_email_rows"


def test_best_email_all_bad_still_returns_with_usable_false(db: sqlite3.Connection) -> None:
    _insert_contact(db, "user@example.com", source="youtube_about_declared")
    best = ceq.best_email_for_kol(1, conn=db)
    assert best["email"] == "user@example.com"
    assert best["usable"] is False
    assert best["reason"] == "placeholder_domain"


def test_best_email_business_email_type_included(db: sqlite3.Connection) -> None:
    _insert_contact(db, "biz@brand.dev", source="raw_bio_scan", ctype="business_email")
    assert ceq.best_email_for_kol(1, conn=db)["email"] == "biz@brand.dev"


# ------------------------------------------------------------------ 批校验
def test_run_batch_distribution_and_cache(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(
        monkeypatch,
        {
            ("MX", "gmail.com"): (_DIG_NOERROR_2, ""),
            ("MX", "gone.dev"): (_DIG_NXDOMAIN, ""),
            ("MX", "slow.dev"): ("", "dig_timeout"),
        },
    )
    _insert_contact(db, "jane@gmail.com", source="youtube_about_declared")
    _insert_contact(db, "noreply@gmail.com", source="raw_bio_scan")  # 同域名,不再耗预算
    _insert_contact(db, "dead@gone.dev", source="raw_full_scan")
    _insert_contact(db, "late@slow.dev", source="raw_full_scan")
    _insert_contact(db, "user@example.com", source="raw_full_scan")
    _insert_contact(db, "skip@x.dev", source="raw_full_scan", status="invalid")  # 终态不进批

    report = ceq.run_batch(limit=50, conn=db)
    assert report["checked_rows"] == 5
    assert report["distribution"] == {
        ceq.MX_OK: 2, ceq.MX_UNKNOWN: 1, ceq.MX_BAD: 1, "syntax_bad": 1, "role_flagged": 1,
    }
    assert report["unique_domains_resolved"] == 3  # gmail.com 去重
    assert report["by_source"]["raw_full_scan"] == 3
    assert report["syntax_bad_reasons"] == {"placeholder_domain": 1}
    reasons = {s["reason"] for s in report["invalid_suggestions"]}
    assert reasons == {"placeholder_domain", "nxdomain"}  # mx_bad 行带具体 DNS 细节
    # 建议只带 contact_id/域名,不带邮箱明文
    assert all("@" not in str(s.get("domain", "")) for s in report["invalid_suggestions"])
    # verification_status 一律不动
    statuses = {r[0] for r in db.execute("SELECT DISTINCT verification_status FROM vkpi_kol_pool_contacts WHERE verification_status<>'invalid'")}
    assert statuses == {"observed"}
    # 第二轮:全部已缓存,零新行
    assert ceq.run_batch(limit=50, conn=db)["checked_rows"] == 0


def test_run_batch_no_mx_mode_zero_dns(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_dns(monkeypatch, {})
    _insert_contact(db, "jane@gmail.com")
    report = ceq.run_batch(limit=10, do_mx=False, conn=db)
    assert calls == []
    assert report["distribution"][ceq.MX_UNKNOWN] == 1
    assert ceq.load_quality_cache(db, [1])[1]["detail"] == "mx_disabled"


def test_run_batch_respects_limit(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(monkeypatch, {("MX", "gmail.com"): (_DIG_NOERROR_2, "")})
    for i in range(5):
        _insert_contact(db, f"user{i}@gmail.com")
    assert ceq.run_batch(limit=2, conn=db)["checked_rows"] == 2
    assert ceq.run_batch(limit=50, conn=db)["checked_rows"] == 3


def test_resolve_domains_worker_clamp(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    class FakePool:
        def __init__(self, max_workers: int) -> None:
            captured["max_workers"] = max_workers

        def __enter__(self) -> "FakePool":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def map(self, fn: Any, items: Any) -> list:
            return [fn(item) for item in items]

    monkeypatch.setattr(ceq, "ThreadPoolExecutor", FakePool)
    monkeypatch.setattr(ceq, "check_mx", lambda d, timeout: (ceq.MX_OK, "stub"))
    domains = [f"d{i}.dev" for i in range(9)]
    results = ceq._resolve_domains(domains, timeout=2.0)
    assert captured["max_workers"] == ceq.MAX_DNS_CONCURRENCY == 4  # 并发红线硬夹
    assert len(results) == 9


def test_select_unchecked_skips_cached_and_terminal(db: sqlite3.Connection) -> None:
    cached = _insert_contact(db, "done@x.dev")
    _insert_contact(db, "todo@y.dev")
    _insert_contact(db, "gone@z.dev", status="revoked")
    ceq.cache_quality_result(db, contact_id=cached, kol_pool_id=1, email="done@x.dev",
                             syntax_ok=True, syntax_reason="", mx_status=ceq.MX_OK, role=False)
    rows = ceq.select_unchecked_email_rows(db, 50)
    assert [r["contact_value"] for r in rows] == ["todo@y.dev"]


def test_cli_main_emits_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    seen: dict[str, Any] = {}

    def fake_run_batch(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"checked_rows": 0}

    monkeypatch.setattr(ceq, "run_batch", fake_run_batch)
    assert ceq.main(["--limit", "7", "--no-mx", "--dns-budget", "9999"]) == 0
    assert seen["limit"] == 7
    assert seen["do_mx"] is False
    assert seen["dns_budget"] == 9999  # 夹取在 run_batch->set_dns_budget 内执行
    assert '"checked_rows": 0' in capsys.readouterr().out
