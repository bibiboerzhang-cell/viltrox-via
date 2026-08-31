"""邮箱模式推断层测试(contact_email_inference)。

全部离线:MX 层用打桩替换,零真实 DNS;库用内存 sqlite。
覆盖任务五点:候选生成确定性、平台域排除、已有实抓不推断、置信度与来源标记、
仲裁排序(实抓 > 推断)。
"""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from app.domains.kol import contact_email_inference as cei
from app.domains.kol import contact_email_quality as ceq

_SCHEMA = """
CREATE TABLE vkpi_kol_pool (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'youtube',
    handle TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT ''
);
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
    return conn


@pytest.fixture(autouse=True)
def _reset_state():
    ceq.clear_mx_cache()
    ceq.set_dns_budget(ceq.MAX_DNS_BUDGET)
    yield
    ceq.clear_mx_cache()
    ceq.set_dns_budget(ceq.MAX_DNS_BUDGET)


def _add_kol(db: sqlite3.Connection, kid: int, *, handle: str = "", name: str = "", email: str = "") -> None:
    db.execute(
        "INSERT INTO vkpi_kol_pool (id, platform, handle, display_name, email) VALUES (?,?,?,?,?)",
        (kid, "youtube", handle, name, email),
    )


def _add_contact(
    db: sqlite3.Connection, kid: int, ctype: str, value: str, *, source: str = "raw_bio_scan"
) -> int:
    cur = db.execute(
        """
        INSERT INTO vkpi_kol_pool_contacts
            (kol_pool_id, contact_type, contact_value, contact_source, confidence, last_seen_at)
        VALUES (?,?,?,?,?,?)
        """,
        (kid, ctype, value, source, 0.7, "2026-08-01T00:00:00+00:00"),
    )
    return int(cur.lastrowid)


# ------------------------------------------------------------ ① 候选生成确定性
def test_generate_candidates_is_deterministic_and_capped():
    args = {"domain": "juliatrotti.com", "handle": "@juliatrotti", "display_name": "Julia Trotti"}
    first = cei.generate_candidates(**args)
    second = cei.generate_candidates(**args)
    assert [c["email"] for c in first] == [c["email"] for c in second]
    assert len(first) <= cei.MAX_CANDIDATES_PER_DOMAIN
    # 角色 local 在前,按 ROLE_LOCALS 固定序;人名派生在后。
    assert [c["email"] for c in first][:3] == [
        "hello@juliatrotti.com", "contact@juliatrotti.com", "info@juliatrotti.com",
    ]
    assert [c["email"] for c in first][-3:] == [
        "julia@juliatrotti.com", "julia.trotti@juliatrotti.com", "j.trotti@juliatrotti.com",
    ]
    assert [c["pattern"] for c in first][-3:] == ["name_first", "name_first_last", "name_initial_last"]


def test_generate_candidates_no_duplicates_and_never_exceeds_twelve():
    cands = cei.generate_candidates(
        domain="example-studio.de", handle="hello", display_name="Hello Hello"
    )
    emails = [c["email"] for c in cands]
    assert len(emails) == len(set(emails))
    assert len(emails) <= 12


def test_derive_name_locals_falls_back_to_handle_and_handles_cjk():
    assert cei.derive_name_locals("@juliatrotti", "") == ["juliatrotti"]
    # display_name 全 CJK → 无 ASCII 词元 → 退回 handle
    assert cei.derive_name_locals("markwu", "吴老师") == ["markwu"]
    # 两者都派生不出 → 空,不硬造
    assert cei.derive_name_locals("", "吴老师") == []


def test_derive_name_locals_strips_brand_noise_tokens():
    assert cei.derive_name_locals("", "Kevin Mullins Photography") == [
        "kevin", "kevin.mullins", "k.mullins",
    ]


# ------------------------------------------------------------------ ② 平台域排除
@pytest.mark.parametrize(
    "domain",
    [
        "linktr.ee",          # ceq.PLATFORM_DOMAINS
        "patreon.com",        # ceq.PLATFORM_DOMAINS
        "example.com",        # ceq.PLACEHOLDER_DOMAINS
        "bit.ly",             # 本模块增量:短链
        "amzn.to",            # 本模块增量:短链
        "paypal.me",          # 本模块增量:支付
        "flickr.com",         # 本模块增量:社媒/UGC
        "gmail.com",          # 本模块增量:免费邮箱提供商
        "www.linktr.ee",      # 子域一并命中
        "myportfolio.com",    # 共享作品集托管(实测漏网)
        "amzlink.to",         # 联盟短链(实测漏网)
        "pinterest.co.uk",    # 词干闸:ccTLD 变体
        "facebook.de",        # 词干闸:ccTLD 变体
        "fb.com",             # 词干闸:品牌缩写域
        "spotify.com",        # 词干闸
        "github.com",         # 词干闸
    ],
)
def test_platform_and_non_personal_domains_are_excluded(domain: str):
    assert cei.is_personal_domain(domain) is False
    assert cei.generate_candidates(domain=domain, display_name="Julia Trotti") == []


def test_personal_domain_passes():
    assert cei.is_personal_domain("juliatrotti.com") is True
    assert cei.is_personal_domain("kevinmullinsphotography.co.uk") is True


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.juliatrotti.com/contact", "juliatrotti.com"),
        ("http://store.videozappo.com", "videozappo.com"),
        ("juliatrotti.com", "juliatrotti.com"),
        ("https://shop.kevinmullinsphotography.co.uk/x", "kevinmullinsphotography.co.uk"),
        ("https://192.168.0.1/x", ""),
        ("https://localhost/x", ""),
        ("", ""),
    ],
)
def test_registrable_domain(url: str, expected: str):
    assert cei.registrable_domain(url) == expected


# ------------------------------------------------------- ③ 已有实抓邮箱 → 不推断
def test_domain_with_real_email_is_never_inferred(db: sqlite3.Connection):
    _add_kol(db, 1, handle="julia", name="Julia Trotti")
    _add_contact(db, 1, "email", "julia@juliatrotti.com", source="website_declared")
    _add_kol(db, 2, handle="jt2", name="Julia Trotti Two")
    _add_contact(db, 2, "website", "https://juliatrotti.com")
    assert "juliatrotti.com" in cei.domains_with_real_email(db)
    # KOL 2 无自己的邮箱,但域名上已有实抓 → 不进目标
    assert cei.select_inference_targets(db, 50) == []


def test_kol_with_own_email_is_excluded_from_targets(db: sqlite3.Connection):
    _add_kol(db, 1, handle="mark", name="Mark Wu")
    _add_contact(db, 1, "email", "mark@markwu.com")
    _add_contact(db, 1, "website", "https://markwu.com")
    assert cei.select_inference_targets(db, 50) == []


def test_pool_email_column_also_blocks_target(db: sqlite3.Connection):
    _add_kol(db, 1, handle="mark", name="Mark Wu", email="mark@markwu.com")
    _add_contact(db, 1, "website", "https://markwu.com")
    assert cei.select_inference_targets(db, 50) == []


def test_pattern_inferred_rows_do_not_count_as_real_emails(db: sqlite3.Connection):
    _add_kol(db, 1, handle="mark", name="Mark Wu")
    _add_contact(db, 1, "email", "hello@markwu.com", source=cei.PATTERN_INFERRED_SOURCE)
    assert cei.domains_with_real_email(db) == set()


def test_select_targets_dedupes_by_domain_and_respects_limit(db: sqlite3.Connection):
    for kid, dom in ((1, "aaa.com"), (2, "bbb.com"), (3, "ccc.com")):
        _add_kol(db, kid, handle=f"h{kid}", name=f"Name {kid}")
        _add_contact(db, kid, "website", f"https://www.{dom}/about")
        _add_contact(db, kid, "website", f"https://{dom}/contact")  # 同域第二条
    targets = cei.select_inference_targets(db, 2)
    assert [t["domain"] for t in targets] == ["aaa.com", "bbb.com"]


# --------------------------------------------------- ④ 置信度与来源标记 + MX 必要
def test_candidate_carries_inferred_markers():
    cand = cei.generate_candidates(domain="markwu.com", display_name="Mark Wu")[0]
    assert cand["contact_source"] == "pattern_inferred"
    assert cand["confidence"] == pytest.approx(0.35)
    assert cand["is_inferred"] is True
    assert cand["usable"] is False


def test_mx_is_a_necessary_condition(monkeypatch: pytest.MonkeyPatch):
    statuses = {
        "ok.com": (ceq.MX_OK, "mx_records:2"),
        "unknown.com": (ceq.MX_UNKNOWN, "dig_timeout"),
        "bad.com": (ceq.MX_BAD, "nxdomain"),
    }
    monkeypatch.setattr(ceq, "check_mx", lambda d, **kw: statuses[d])
    targets = [
        {"domain": d, "kol_pool_id": i, "handle": "x", "display_name": "Mark Wu"}
        for i, d in enumerate(statuses, start=1)
    ]
    summary = cei.infer_for_targets(targets, dns_budget=10)
    by_domain = {r["domain"]: r for r in summary["results"]}
    assert by_domain["ok.com"]["candidates"], "mx_ok 必须出候选"
    assert by_domain["unknown.com"]["candidates"] == [], "mx_unknown 不出候选"
    assert by_domain["bad.com"]["candidates"] == [], "mx_bad 不出候选"
    assert summary["domains_with_candidates"] == 1
    assert summary["mx_distribution"][ceq.MX_OK] == 1


def test_dns_budget_is_clamped_by_the_shared_ceq_ceiling():
    effective = cei.resolve_effective_dns_budget(99999)
    assert effective == ceq.MAX_DNS_BUDGET
    assert effective <= cei.MAX_INFERENCE_DNS_BUDGET
    assert cei.resolve_effective_dns_budget(7) == 7


def test_persist_plan_marks_itself_blocked_and_writes_nothing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ceq, "check_mx", lambda d, **kw: (ceq.MX_OK, "mx_records:1"))
    summary = cei.infer_for_targets(
        [{"domain": "markwu.com", "kol_pool_id": 1, "handle": "mark", "display_name": "Mark Wu"}],
        dns_budget=5,
    )
    plan = cei.build_persist_plan(summary)
    assert plan and all(p["contact_source"] == "pattern_inferred" for p in plan)
    assert all(p["confidence"] == pytest.approx(0.35) for p in plan)
    assert all(p["is_public_declared"] is False for p in plan)
    assert all(p["blocked_reason"] == "no_schema_landing_zone_for_inferred_email" for p in plan)


# ------------------------------------------------ ⑤ 仲裁排序:实抓 > 推断
def _inferred(email: str) -> list[dict[str, Any]]:
    return [{"email": email, "pattern": "role_hello", "local": "hello", "domain": "markwu.com"}]


def test_real_email_always_beats_inferred(db: sqlite3.Connection):
    _add_kol(db, 1, handle="mark", name="Mark Wu")
    # 最弱的实抓来源(raw_full_scan)也必须赢过推断
    cid = _add_contact(db, 1, "email", "m@markwu.com", source="raw_full_scan")
    ceq.cache_quality_result(
        db, contact_id=cid, kol_pool_id=1, email="m@markwu.com",
        syntax_ok=True, syntax_reason="", mx_status=ceq.MX_OK, role=False,
    )
    db.commit()
    best = cei.best_email_with_inference(1, conn=db, inferred=_inferred("hello@markwu.com"))
    assert best["email"] == "m@markwu.com"
    assert best["is_inferred"] is False


def test_inferred_used_only_when_no_usable_real_email(db: sqlite3.Connection):
    _add_kol(db, 1, handle="mark", name="Mark Wu")
    best = cei.best_email_with_inference(1, conn=db, inferred=_inferred("hello@markwu.com"))
    assert best["email"] == "hello@markwu.com"
    assert best["is_inferred"] is True
    assert best["contact_source"] == "pattern_inferred"
    assert best["confidence"] == pytest.approx(0.35)
    assert best["usable"] is False, "推断邮箱绝不可用于自动发信"
    assert best["reason"] == "pattern_inferred_lead_not_fact"


def test_unusable_real_email_falls_through_to_inferred(db: sqlite3.Connection):
    _add_kol(db, 1, handle="mark", name="Mark Wu")
    # 语法坏行 → best_email_for_kol 返回 usable=False → 落到推断
    _add_contact(db, 1, "email", "n@markwu.com", source="raw_bio_scan")
    db.commit()
    best = cei.best_email_with_inference(1, conn=db, inferred=_inferred("hello@markwu.com"))
    assert best["is_inferred"] is True
    assert best["real_email_fallback"] == "n@markwu.com"


def test_no_inferred_candidates_returns_real_verdict_unchanged(db: sqlite3.Connection):
    _add_kol(db, 1, handle="mark", name="Mark Wu")
    best = cei.best_email_with_inference(1, conn=db, inferred=[])
    assert best["is_inferred"] is False
    assert best["reason"] == "no_email_rows"
