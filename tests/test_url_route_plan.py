"""贴任意链接的去向决策(``app.domains.kol.url_route_plan``)+ 入队口分流。

钉的是一件事:**用户贴进来的链接都要有可用产出**。历史上账号抓取通道读不了的
链接会一路走到最后才被拦下,在队列里堆成 202 条卡住的活;现在它们在入队之前
就分流 —— 公开站点走网页抓取腿,认得但打不开的平台当场诚实拒绝。

``HISTORIC_BLOCKED_URLS`` 是本地库那 202 条的**真形状**(2026-09-03 从
``apify_jobs`` 里 ``job_type='kol_profile_deep_crawl' AND status='blocked'
AND last_error='url_unknown_unsupported'`` 的 40 个去重 URL + 各自条数导出),
离线跑,不发一个网络请求。
"""
from __future__ import annotations

import json

import pytest

from app.domains.kol import url_deep_crawl_queue, url_route_plan
from app.domains.kol.url_deep_crawl import classify_url
from app.domains.kol.url_deep_crawl_helpers import SUPPORTED_PLATFORMS, _platform_from_host, _video_id

# (条数, URL) —— 合计 202,与本地库全时窗逐条对得上。
HISTORIC_BLOCKED_URLS: list[tuple[int, str]] = [
    (5, "forum.nikoniarze.pl"),
    (5, "http://www.digitalcamaralens.com/"),
    (5, "http://www.thephoblographer.com/"),
    (5, "https://alikgriffin.com/category/reviews/"),
    (5, "https://flokugrafie.de/objektive/viltrox-55mm-f-1-8-evo-apo-im-test/"),
    (5, "https://fstoppers.com/"),
    (5, "https://fstoppers.com/profile/361107/articles"),
    (5, "https://kojinakagawa.com/"),
    (5, "https://nikonrumors.com/"),
    (5, "https://opticallimits.com/"),
    (5, "https://photographylife.com/"),
    (5, "https://phototrend.fr/"),
    (5, "https://ricksreviews.org/"),
    (5, "https://sonyalpha.blog/#google_vignette"),
    (5, "https://topguide4you.com/"),
    (5, "https://www.35mmc.com"),
    (5, "https://www.35mmc.com/?s=viltrox"),
    (5, "https://www.5050travelog.com/new-camera-and-lens-reviews"),
    (5, "https://www.digitalcameraworld.com/"),
    (5, "https://www.diyphotography.net/"),
    (5, "https://www.ephotozine.com/"),
    (5, "https://www.facebook.com/2howfb?mibextid=ZbWKwL"),
    (5, "https://www.facebook.com/@paiiyar/?mibextid=ZbWKwL"),
    (5, "https://www.facebook.com/koeywithmiew?mibextid=ZbWKwL"),
    (5, "https://www.facebook.com/myeclecticstylephoto"),
    (5, "https://www.facebook.com/story.php?story_fbid=3754259644885020&id=100009030639269#"),
    (5, "https://www.facebook.com/xpotographer/?show_switched_toast=0&show_invite_to_follow=0"),
    (5, "https://www.fujirumors.com/"),
    (5, "https://www.imaging-resource.com/"),
    (5, "https://www.kieranhayesphotography.com/contact/"),
    (5, "https://www.macfilos.com/"),
    (5, "https://www.nikolaus-burgard.de/"),
    (5, "https://www.nikon-fotografie.de/"),
    (5, "https://www.nytimes.com/wirecutter/"),
    (5, "https://www.pcmag.com/"),
    (6, "https://www.photographyblog.com/"),
    (5, "https://www.snapsbyfox.com/blog"),
    (5, "https://www.thephoblographer.com/"),
    (6, "https://yphoto-journal.com/"),
    (5, "www.couponturtle.com"),
]

# 门面禁内部术语:回执文案是给操作员看的,机器码一个字都不许上门面。
FORBIDDEN_IN_USER_TEXT = ("LLM", "lexicon", "rule_v0", "词表", "embedding", "Qdrant", "Apify", "job", "payload")


def _route_counts(planner) -> dict[str, int]:
    counts: dict[str, int] = {}
    for weight, url in HISTORIC_BLOCKED_URLS:
        counts[planner(url)] = counts.get(planner(url), 0) + weight
    return counts


def test_historic_blocked_urls_total_matches_the_local_ledger():
    assert sum(weight for weight, _url in HISTORIC_BLOCKED_URLS) == 202


def test_historic_blocked_urls_split_into_website_and_refusal():
    counts = _route_counts(lambda url: url_route_plan.plan_url_route_from_url(url).route)
    assert counts == {url_route_plan.ROUTE_WEBSITE: 172, url_route_plan.ROUTE_UNSUPPORTED: 30}


def test_no_historic_blocked_url_reaches_the_account_crawler():
    """202 条一条都不该再进账号抓取通道 —— 这就是那 202 条卡住的活的来源。"""
    for _weight, url in HISTORIC_BLOCKED_URLS:
        assert url_route_plan.plan_url_route_from_url(url).handled_by_account_crawler is False


def test_every_refused_historic_url_is_facebook():
    refused = [
        url
        for _weight, url in HISTORIC_BLOCKED_URLS
        if url_route_plan.plan_url_route_from_url(url).route == url_route_plan.ROUTE_UNSUPPORTED
    ]
    assert len(refused) == 6  # 去重后 6 个 URL,按条数是 30 条
    assert all("facebook.com" in url for url in refused)


def test_url_only_and_classifier_aware_planners_agree_on_history():
    """入队口(只看链接)与识别后(有 classify_url 结果)两支必须给同一个去向。"""
    for _weight, url in HISTORIC_BLOCKED_URLS:
        from_url = url_route_plan.plan_url_route_from_url(url)
        from_classified = url_route_plan.plan_url_route(classify_url(url), raw_url=url)
        assert from_url.route == from_classified.route, url


def test_classify_url_return_shape_untouched():
    """本刀不动 ``classify_url``:这些链接在它眼里仍旧是 unknown / unsupported_platform。"""
    classified = classify_url("https://www.facebook.com/myeclecticstylephoto")
    assert (classified.url_type, classified.confidence) == ("unknown", "unsupported_platform")
    site = classify_url("https://www.35mmc.com")
    assert (site.url_type, site.confidence) == ("unknown", "unsupported_platform")


def test_facebook_really_has_no_account_crawler_support():
    """核实(而不是猜)Facebook 不支持:三处口径一致,所以拒绝是诚实的。"""
    assert "facebook" not in SUPPORTED_PLATFORMS
    assert _platform_from_host("facebook.com") == ""
    assert _video_id("facebook", "facebook.com", "/story.php", "story_fbid=1") == ""
    plan = url_route_plan.plan_url_route_from_url("https://www.facebook.com/myeclecticstylephoto")
    assert plan.route == url_route_plan.ROUTE_UNSUPPORTED
    assert plan.reason_code == "platform_not_supported"
    assert "Facebook" in plan.reason_human


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/@Creator",
        "https://www.youtube.com/watch?v=abcdefghijk",
        "https://www.instagram.com/reel/ABC123/",
        "https://www.tiktok.com/@creator/video/1234567890123456789",
        "https://youtu.be/abcdefghijk",
        "https://b23.tv/abc123",
        "https://v.douyin.com/abc123/",
    ],
)
def test_supported_platform_links_still_go_to_the_account_crawler(url):
    """今天能跑通的链接一条都不受影响 —— 分流只碰通道从来不认的主机名。"""
    assert url_route_plan.plan_url_route_from_url(url).route == url_route_plan.ROUTE_PROFILE


def test_lookalike_host_is_not_mistaken_for_a_platform():
    """``jurjax.com`` 含 "x.com":子串匹配会把正经独立站误拒成 X。"""
    assert url_route_plan.plan_url_route_from_url("https://jurjax.com/").route == url_route_plan.ROUTE_WEBSITE
    assert url_route_plan.plan_url_route_from_url("https://x.com/someone").route == url_route_plan.ROUTE_UNSUPPORTED


@pytest.mark.parametrize(
    ("url", "reason_code"),
    [
        ("", "link_missing"),
        ("https://example.com/media-kit.pdf", "site_is_a_file"),
        ("http://192.168.0.1/admin", "site_not_public"),
        ("https://localhost/", "site_not_public"),
        ("https://box.local/page", "site_not_public"),
    ],
)
def test_links_without_a_readable_public_page_are_refused(url, reason_code):
    plan = url_route_plan.plan_url_route_from_url(url)
    assert (plan.route, plan.reason_code) == (url_route_plan.ROUTE_UNSUPPORTED, reason_code)


def test_classifier_aware_planner_labels_platform_links_without_an_account():
    plan = url_route_plan.plan_url_route(classify_url("https://www.youtube.com/feed/trending"))
    assert (plan.route, plan.reason_code) == (url_route_plan.ROUTE_UNSUPPORTED, "platform_link_without_account")
    cn = url_route_plan.plan_url_route(classify_url("https://www.bilibili.com/"))
    assert (cn.route, cn.reason_code) == (url_route_plan.ROUTE_UNSUPPORTED, "platform_single_content_only")


def test_user_facing_reasons_carry_no_internal_terms():
    urls = [url for _weight, url in HISTORIC_BLOCKED_URLS] + [
        "",
        "https://example.com/a.pdf",
        "https://localhost/",
        "https://www.youtube.com/@Creator",
        "https://x.com/someone",
    ]
    for url in urls:
        text = url_route_plan.plan_url_route_from_url(url).reason_human
        assert text and text[-1] in "。", url
        for term in FORBIDDEN_IN_USER_TEXT:
            assert term.lower() not in text.lower(), (url, term)


def test_site_contact_rows_lead_with_the_site_and_drop_page_body():
    rows = url_route_plan.site_contact_rows(
        "https://www.35mmc.com",
        [
            {"contact_type": "email", "contact_value": "hi@35mmc.com", "confidence": 0.85,
             "source_url": "https://www.35mmc.com/contact", "evidence_text": "mailto"},
            {"contact_type": "", "contact_value": "dropped"},
        ],
    )
    assert rows[0] == ("website", "https://www.35mmc.com", "https://www.35mmc.com", 0.5, "site")
    assert [row[0] for row in rows] == ["website", "email"]


# ── 入队口分流(不真抓,网页抓取腿被替身接管)──


class _FakeRows:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _FakeConn:
    """记录每一条 SQL:断言「没有一条活被入队」靠的是它,不是靠回执长得像。"""

    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self.commits = 0

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        if "SELECT id FROM vkpi_kol_pool_contacts" in sql:
            return _FakeRows(None)
        if "SELECT id FROM apify_jobs" in sql:
            return _FakeRows(None)
        if "INSERT INTO vkpi_kol_pool_contacts" in sql:
            return _FakeRows(None)
        if "INSERT INTO apify_jobs" in sql:
            return _FakeRows({"id": 9001})
        raise AssertionError(sql)

    def commit(self):
        self.commits += 1

    def enqueued_payloads(self) -> list[dict]:
        return [json.loads(params[1]) for sql, params in self.statements if "INSERT INTO apify_jobs" in sql]


@pytest.fixture()
def queue_conn(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(url_deep_crawl_queue, "get_conn", lambda: conn)
    monkeypatch.setattr(url_deep_crawl_queue, "_robots_allows", lambda _url: True)
    return conn


def _patch_site_scrape(monkeypatch, found):
    from app.domains.kol import contact_website_scrape

    monkeypatch.setattr(
        contact_website_scrape,
        "scrape_contacts_from_url",
        lambda url, **_kwargs: list(found),
    )


def test_website_url_is_scraped_instead_of_queued(queue_conn, monkeypatch):
    _patch_site_scrape(
        monkeypatch,
        [{"contact_type": "email", "contact_value": "editor@35mmc.com", "confidence": 0.85,
          "source_url": "https://www.35mmc.com/contact", "evidence_text": "mailto"}],
    )

    result = url_deep_crawl_queue.enqueue_profile_deep_crawl_job("https://www.35mmc.com", kol_pool_id=1525)

    assert result["route"] == url_route_plan.ROUTE_WEBSITE
    assert result["status"] == "site_scanned"
    assert result["job_id"] is None
    assert result["contacts_saved"] == 2  # 站点根地址 + 邮箱
    assert queue_conn.enqueued_payloads() == []
    inserted = [params for sql, params in queue_conn.statements if "INSERT INTO vkpi_kol_pool_contacts" in sql]
    assert [row[1] for row in inserted] == ["website", "email"]
    assert all(row[3] == "website_declared" for row in inserted)


def test_facebook_url_is_refused_before_anything_is_queued(queue_conn):
    result = url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        "https://www.facebook.com/myeclecticstylephoto", kol_pool_id=1525
    )

    assert result["status"] == "not_supported"
    assert result["job_id"] is None
    assert result["reason"] == "platform_not_supported"
    assert queue_conn.statements == []  # 一条 SQL 都没跑:不入队,也不去抓
    for term in FORBIDDEN_IN_USER_TEXT:
        assert term.lower() not in result["message"].lower()


def test_site_that_opts_out_is_skipped_without_fetching(queue_conn, monkeypatch):
    monkeypatch.setattr(url_deep_crawl_queue, "_robots_allows", lambda _url: False)

    def _boom(*_args, **_kwargs):
        raise AssertionError("站点声明不许读取时不应发起抓取")

    from app.domains.kol import contact_website_scrape

    monkeypatch.setattr(contact_website_scrape, "scrape_contacts_from_url", _boom)

    result = url_deep_crawl_queue.enqueue_profile_deep_crawl_job("https://www.35mmc.com", kol_pool_id=1525)

    assert result["status"] == "site_scan_skipped"
    assert result["job_id"] is None


def test_second_pass_on_the_same_site_does_not_refetch(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(url_deep_crawl_queue, "get_conn", lambda: conn)
    monkeypatch.setattr(url_deep_crawl_queue, "_robots_allows", lambda _url: True)
    monkeypatch.setattr(
        url_deep_crawl_queue, "_site_already_scanned", lambda _conn, _kol_pool_id, _base: True
    )

    from app.domains.kol import contact_website_scrape

    def _boom(*_args, **_kwargs):
        raise AssertionError("同一个站点不该被反复抓")

    monkeypatch.setattr(contact_website_scrape, "scrape_contacts_from_url", _boom)

    result = url_deep_crawl_queue.enqueue_profile_deep_crawl_job("https://www.35mmc.com", kol_pool_id=1525)

    assert result["status"] == "site_already_scanned"
    assert conn.commits == 0


def test_site_that_cannot_be_opened_is_reported_not_swallowed(queue_conn, monkeypatch):
    from app.domains.kol import contact_website_scrape

    def _explode(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(contact_website_scrape, "scrape_contacts_from_url", _explode)

    result = url_deep_crawl_queue.enqueue_profile_deep_crawl_job("https://www.35mmc.com", kol_pool_id=1525)

    assert result["status"] == "site_scan_failed"
    assert result["job_id"] is None


def test_platform_url_still_enqueues_exactly_as_before(queue_conn):
    result = url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        "https://www.youtube.com/@ItiJarve", max_posts=3, staff={"id": 1}
    )

    assert result == {"status": "queued", "job_id": 9001}
    assert len(queue_conn.enqueued_payloads()) == 1
