"""contact_website_scrape 页面抓取腿 + 批跑器分类逻辑测试。

fixture HTML 四态:mailto / 纯文本邮箱 / Linktree 内嵌 JSON 转义邮箱 / 无邮箱;
外加超时 mock(错误台账)与聚合页单页抓取、节流、批跑分类。全部零出网。
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

from app.domains.kol import contact_website_scrape as cws

ROOT = Path(__file__).resolve().parents[1]

FIXTURE_MAILTO = """
<html><body>
  <h1>Contact Me</h1>
  <a href="mailto:booking@creatorstudio.com?subject=Collab">Email me</a>
  <a href="https://instagram.com/creator">IG</a>
</body></html>
"""

FIXTURE_TEXT_EMAIL = """
<html><body>
  <p>For business inquiries: hello.biz@lensreview.net</p>
  <p>fake mention n@hamid.monadi should be filtered</p>
</body></html>
"""

# Linktree 类聚合页:邮箱埋在内嵌 JSON,@ 转义 @,\/ 转义斜杠,外加 &#64; 实体。
FIXTURE_LINKHUB_JSON = """
<html><body><script id="__NEXT_DATA__" type="application/json">
{"props":{"links":[{"url":"https:\\/\\/instagram.com\\/gearguy"},
{"url":"mailto:collab\\u0040gearguy.studio"}],
"bio":"press: media&#64;gearguy.studio"}}
</script></body></html>
"""

FIXTURE_NO_EMAIL = """
<html><body><p>Welcome to my portfolio. No contact info here.</p></body></html>
"""


def test_extract_mailto_high_confidence():
    got = cws._extract_from_html(FIXTURE_MAILTO, "https://creatorstudio.com")
    emails = [c for c in got if c["contact_type"] == "email"]
    assert [e["contact_value"] for e in emails] == ["booking@creatorstudio.com"]
    assert emails[0]["confidence"] == 0.85
    assert any(c["contact_type"] == "instagram_link" for c in got)


def test_extract_text_email_filters_fake_mentions():
    got = cws._extract_from_html(FIXTURE_TEXT_EMAIL, "https://lensreview.net")
    emails = [c["contact_value"] for c in got if c["contact_type"] == "email"]
    assert emails == ["hello.biz@lensreview.net"]  # .monadi 假 TLD 被滤掉
    assert got[0]["confidence"] == 0.7


def test_extract_linkhub_embedded_json_escapes():
    got = cws._extract_from_html(FIXTURE_LINKHUB_JSON, "https://linktr.ee/gearguy")
    emails = sorted(c["contact_value"] for c in got if c["contact_type"] == "email")
    assert emails == ["collab@gearguy.studio", "media@gearguy.studio"]
    mailto = [c for c in got if c["evidence_text"] == "mailto"]
    assert mailto and mailto[0]["confidence"] == 0.85  # \\u0040 解转义后 mailto 仍算高置信


def test_extract_no_email_returns_empty():
    assert cws._extract_from_html(FIXTURE_NO_EMAIL, "https://example.com") == []


def test_scrape_link_hub_fetches_single_page(monkeypatch):
    fetched: list[str] = []

    def fake_fetch(url: str, *, timeout: int = 6) -> str:
        fetched.append(url)
        return FIXTURE_NO_EMAIL

    monkeypatch.setattr(cws, "_fetch", fake_fetch)
    cws.scrape_contacts_from_url("https://linktr.ee/gearguy")
    assert fetched == ["https://linktr.ee/gearguy"]  # 聚合页不追 /contact 子页

    fetched.clear()
    cws.scrape_contacts_from_url("https://dustinabbott.net/", max_pages=4)
    assert len(fetched) == 4  # 普通独立站无邮箱时追常见子页
    assert fetched[1] == "https://dustinabbott.net/contact"


def test_scrape_stops_early_on_mailto(monkeypatch):
    fetched: list[str] = []

    def fake_fetch(url: str, *, timeout: int = 6) -> str:
        fetched.append(url)
        return FIXTURE_MAILTO

    monkeypatch.setattr(cws, "_fetch", fake_fetch)
    got = cws.scrape_contacts_from_url("https://creatorstudio.com")
    assert len(fetched) == 1  # 首页拿到高置信 mailto 即停
    assert got[0]["contact_value"] == "booking@creatorstudio.com"


def test_fetch_timeout_recorded_not_swallowed(monkeypatch):
    def boom(*args, **kwargs):
        raise TimeoutError("timed out")

    # S-04(2026-09-02):_fetch 出站改走 app.platform.safe_fetch(DNS 先校验),mock 缝随之搬到 open_url。
    monkeypatch.setattr(cws.safe_fetch, "open_url", boom)
    cws.pop_fetch_errors()  # 清残留
    assert cws._fetch("https://slow.example.com/contact") == ""
    errs = cws.pop_fetch_errors()
    assert len(errs) == 1
    assert "TimeoutError" in errs[0] and "slow.example.com" in errs[0]
    assert cws.pop_fetch_errors() == []  # pop 语义:取走即清空


def test_fetch_throttle_enforces_min_interval(monkeypatch):
    monkeypatch.setattr(cws.safe_fetch, "open_url", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    cws.set_fetch_throttle(0.12)
    try:
        cws._fetch("https://a.example.com")
        t0 = time.monotonic()
        cws._fetch("https://b.example.com")
        assert time.monotonic() - t0 >= 0.1
    finally:
        cws.set_fetch_throttle(0.0)
        cws.pop_fetch_errors()


def test_enrich_allow_url_predicate_skips_blocked(monkeypatch):
    calls: list[str] = []

    class FakeDB:
        def execute(self, sql: str, params=()):  # noqa: ANN001
            class R:
                @staticmethod
                def fetchall():
                    return [{"contact_value": "https://blocked.example.com"},
                            {"contact_value": "https://ok.example.com"}]
            return R()

    monkeypatch.setattr(cws, "scrape_contacts_from_url", lambda u, **k: calls.append(u) or [])
    res = cws.enrich_website_contacts_l1(7, conn=FakeDB(), allow_url=lambda u: "blocked" not in u)
    assert calls == ["https://ok.example.com"]
    assert res["status"] == "no_contacts_from_web"


def _load_batch_module():
    spec = importlib.util.spec_from_file_location(
        "run_website_contact_batch", ROOT / "scripts" / "run_website_contact_batch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))  # stdout_utils
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    return mod


def test_batch_classify_outcomes():
    mod = _load_batch_module()
    assert mod._classify({"status": "ok", "email": "a@b.com"}, []) == "email_found"
    assert mod._classify({"status": "ok", "email": ""}, []) == "contacts_no_email"
    assert mod._classify({"status": "no_links"}, []) == "no_links"
    assert mod._classify({"status": "no_contacts_from_web"}, []) == "no_email"
    assert mod._classify({"status": "no_contacts_from_web"},
                         ["TimeoutError: timed out @https://x.com"]) == "timeout"
    assert mod.HARD_CAP == 100 and mod.MIN_INTERVAL_S == 2.0


def test_batch_hard_cap_is_100():
    mod = _load_batch_module()
    assert max(1, min(500, mod.HARD_CAP)) == 100  # --limit 500 会被截到 100


@pytest.mark.parametrize("url,is_hub", [
    ("linktr.ee", True), ("www.linktr.ee", True), ("beacons.ai", True),
    ("my.carrd.co", True), ("dustinabbott.net", False), ("linktr.ee.evil.com", False),
    # 2026-08-31 补名单后:这些 host 之前落进 'website',页面腿因此把邮箱产出率
    # 最高的目标排到随机个人站后面(每 KOL 只抓前 3 条)。
    ("bio.site", True), ("www.bio.site", True), ("liinks.co", True), ("taplink.cc", True),
    ("hoo.be", True), ("msha.ke", True), ("lnk.bio", True), ("superprofile.bio", True),
    # 纯跳转短链不算聚合页,收了只会让页面腿抓一个空转发页。
    ("tr.ee", False), ("linktw.in", False), ("flowcode.com", False),
    # host 剥法要吃掉端口/query,别把它们当成 host 的一部分。
    ("bio.site:443", True), ("bio.site?utm=1", True),
])
def test_is_link_hub(url, is_hub):
    assert cws._is_link_hub(url) is is_hub


def test_quality_filter_blocks_platform_and_placeholder_before_write():
    """页面腿写库前必过质检——2026-08-31 首批 50 个实测:37 条 website_declared 邮箱里
    10 条是平台客服/占位/转义残留污染(support@boosty.to、u003eguidelines@patreon.com、
    example@domain.com…)。质检层与页面腿同日造出却没接线,污染直入外联可用池。"""
    from app.domains.kol.contact_website_scrape import _filter_quality

    found = [
        {"contact_type": "email", "contact_value": "josh@joshsattin.com"},
        {"contact_type": "email", "contact_value": "support@boosty.to"},
        {"contact_type": "email", "contact_value": "u003eguidelines@patreon.com"},
        {"contact_type": "email", "contact_value": "example@domain.com"},
        {"contact_type": "website", "contact_value": "https://joshsattin.com"},
    ]
    kept, rejected = _filter_quality(found)
    assert [k["contact_value"] for k in kept] == ["josh@joshsattin.com", "https://joshsattin.com"]
    assert {r["reason"] for r in rejected} == {"platform_domain", "placeholder_domain"}
    assert len(rejected) == 3


def test_short_unicode_escapes_decoded_not_swallowed_into_local_part():
    """内嵌 JSON 的 \\u003e(>)残留曾被正则吞成 local 前缀(u003eguidelines@…)。"""
    from app.domains.kol.contact_website_scrape import _normalize_page_text

    assert "@" in _normalize_page_text("x u003eguidelines@patreon.com")
    assert "u003e" not in _normalize_page_text("x u003eguidelines@patreon.com")
