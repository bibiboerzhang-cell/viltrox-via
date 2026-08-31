"""聚合页(link_hub)判定口径锁 —— 名单完整性 + host 级匹配 + 改判脚本纯函数。

为什么单独立一个文件:
  页面腿 run_website_contact_batch 按 `CASE WHEN contact_type='link_hub' THEN 0 ELSE 1 END`
  排序、每 KOL 只抓前 3 条。聚合页是邮箱产出率最高的一类页面,所以「名单漏一个域名」
  与「匹配写成 substring」这两类缺陷都不表现为报错,只表现为最好的线索被静默挤出抓取窗口。
  这里把两件事钉死:
    1. 已核实的聚合页域名必须在 _LINK_HUBS 里,已核实的「纯跳转短链」必须不在;
    2. 匹配必须是 host 级(== 或子域后缀),example.com/linktr.ee 不算聚合页。

覆盖:
  business_contact_extract._url_host / _host_matches / _LINK_HUBS / extract_contacts_multi_source
  contact_website_scrape._is_link_hub
  scripts/reclassify_link_hub_contacts 的 is_link_hub_url / plan_row(纯函数,不碰库)。
全部零出网、零 DB。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from app.domains.kol import business_contact_extract as bce
from app.domains.kol import contact_website_scrape as cws

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reclassify_link_hub_contacts.py"
_SPEC = importlib.util.spec_from_file_location("reclassify_link_hub_contacts", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
reclassify = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = reclassify
_SPEC.loader.exec_module(reclassify)


# 2026-08-31 本地库里被误记成 'website' 的聚合页 host(逐个核过产品形态是 link-in-bio)。
# 补进 _LINK_HUBS 前每个都验过:bio.site=Bio Sites by Squarespace、msha.ke/milkshake.app=
# Milkshake、superprofile.bio=SuperProfile 创作者店铺、linkfly.to=Linkfly、link.me=Linkme……
VERIFIED_HUBS = (
    "bio.site", "bio.link", "link.me", "liinks.co", "taplink.cc", "hoo.be", "dott.bio",
    "superprofile.bio", "allmylinks.com", "linkfly.to", "linkgenie.co", "lnk.bio",
    "msha.ke", "milkshake.app", "shorby.com", "manylink.co",
)
# 刻意排除:这三个是纯跳转短链/深链,不是「一页列全套链接」的聚合页。收进名单会让页面腿
# 把抓取名额花在一个空转发页上,比错记成 website 更亏。
# tr.ee 是 Linktree 旗下的通用短链:实测 tr.ee/0lE1CH 直跳 Shopee 商品页,不保证落在聚合页。
REJECTED_NOT_HUBS = ("tr.ee", "linktw.in", "flowcode.com")


# ---- 1. 名单完整性 ----

@pytest.mark.parametrize("host", VERIFIED_HUBS)
def test_verified_aggregator_hosts_are_in_link_hubs(host: str) -> None:
    assert host in bce._LINK_HUBS


@pytest.mark.parametrize("host", REJECTED_NOT_HUBS)
def test_pure_shortener_hosts_stay_out_of_link_hubs(host: str) -> None:
    """短链/深链域名不得被"顺手"补进名单 —— 它们跳到哪儿全凭 shortcode。"""
    assert host not in bce._LINK_HUBS


def test_link_hubs_has_no_duplicates_and_is_bare_hosts() -> None:
    assert len(bce._LINK_HUBS) == len(set(bce._LINK_HUBS))
    for hub in bce._LINK_HUBS:
        assert hub == hub.lower().strip()
        assert "/" not in hub and "://" not in hub and not hub.startswith("www.")


# ---- 2. host 级匹配(不是 substring)----

@pytest.mark.parametrize("url,expected", [
    ("https://bio.site/alexpantela", "bio.site"),
    ("http://www.liinks.co/srodalmenara", "liinks.co"),
    ("https://LNK.BIO/Snaptechstudioz", "lnk.bio"),
    ("https://taplink.cc:443/x", "taplink.cc"),
    ("https://linktr.ee?utm=1", "linktr.ee"),
    ("https://user@hoo.be/julienyork", "hoo.be"),
    ("https://linktr.ee#frag", "linktr.ee"),
    ("nohost", ""),
])
def test_url_host_strips_scheme_www_port_query_and_userinfo(url: str, expected: str) -> None:
    assert bce._url_host(url) == expected


@pytest.mark.parametrize("host,needle,expected", [
    ("linktr.ee", "linktr.ee", True),
    ("my.carrd.co", "carrd.co", True),
    ("linktr.ee.evil.com", "linktr.ee", False),
    ("jurjax.com", "x.com", False),      # substring 匹配会把它误判成 twitter
    ("notbio.site", "bio.site", False),
])
def test_host_matches_is_exact_or_subdomain(host: str, needle: str, expected: bool) -> None:
    assert bce._host_matches(host, needle) is expected


def test_path_lookalike_is_not_a_link_hub() -> None:
    """substring 匹配的原缺陷:example.com/linktr.ee 会被当成聚合页。"""
    assert reclassify.is_link_hub_url("https://example.com/linktr.ee") is False
    assert reclassify.is_link_hub_url("https://example.com/go?to=bio.site") is False
    assert reclassify.is_link_hub_url("https://bio.site/alexpantela") is True


def test_website_scrape_is_link_hub_shares_the_same_predicate() -> None:
    """页面腿与 L0 抽取腿必须对同一个 host 判出同一个结论。"""
    for host in ("bio.site", "www.bio.site", "my.carrd.co", "msha.ke"):
        assert cws._is_link_hub(host) is True
    for host in ("dustinabbott.net", "linktr.ee.evil.com", "tr.ee"):
        assert cws._is_link_hub(host) is False


# ---- 3. L0 抽取端到端分类 ----

def _types(rows: list[dict]) -> dict[str, str]:
    return {r["contact_value"]: r["contact_type"] for r in rows}


def test_extract_classifies_new_hubs_as_link_hub_not_website() -> None:
    raw = {
        "profile": {
            "biography": (
                "links https://bio.site/alexpantela and https://www.liinks.co/srodalmenara "
                "and https://msha.ke/afganfazri and https://dustinabbott.net"
            )
        }
    }
    rows = bce.extract_contacts_multi_source(raw, platform="instagram")
    kinds = _types(rows)
    assert kinds["https://bio.site/alexpantela"] == "link_hub"
    assert kinds["https://www.liinks.co/srodalmenara"] == "link_hub"
    assert kinds["https://msha.ke/afganfazri"] == "link_hub"
    assert kinds["https://dustinabbott.net"] == "website"


def test_extract_keeps_link_hub_confidence_convention() -> None:
    raw = {"profile": {"biography": "https://bio.site/x and https://dustinabbott.net"}}
    rows = bce.extract_contacts_multi_source(raw, platform="instagram")
    by_value = {r["contact_value"]: r for r in rows}
    assert by_value["https://bio.site/x"]["confidence"] == 0.5
    assert by_value["https://dustinabbott.net"]["confidence"] == 0.45


def test_extract_does_not_promote_path_lookalike_to_link_hub() -> None:
    raw = {"profile": {"biography": "https://example.com/linktr.ee/fake"}}
    rows = bce.extract_contacts_multi_source(raw, platform="instagram")
    assert _types(rows)["https://example.com/linktr.ee/fake"] == "website"


def test_extract_still_prefers_social_tag_over_hub_and_website() -> None:
    """补名单不能把社媒链抢走 —— 社媒判定仍排在聚合页之前。"""
    raw = {"profile": {"biography": "https://www.instagram.com/creator"}}
    rows = bce.extract_contacts_multi_source(raw, platform="youtube")
    assert _types(rows)["https://www.instagram.com/creator"] == "instagram_link"


# ---- 4. 改判脚本纯函数 ----

def _row(**kw: object) -> dict:
    base = {
        "id": 1, "kol_pool_id": 100, "contact_value": "https://bio.site/x",
        "contact_source": "raw_bio_link", "channel": "website",
        "normalized_value": "https://bio.site/x", "confidence": 0.45,
    }
    base.update(kw)
    return base


def test_plan_row_sets_link_hub_channel_normalized_and_confidence() -> None:
    plan = reclassify.plan_row(_row(), set(), set())
    assert plan["action"] == "fix"
    assert plan["channel"] == "link_hub"
    assert plan["normalized_value"] == "https://bio.site/x"
    assert plan["confidence"] == 0.5


def test_plan_row_fills_legacy_null_channel_and_normalized() -> None:
    """历史行 channel/normalized_value 是 NULL(uq 唯一索引对它们不生效),改判时要补齐。"""
    plan = reclassify.plan_row(_row(channel=None, normalized_value=None), set(), set())
    assert (plan["channel"], plan["normalized_value"]) == ("link_hub", "https://bio.site/x")


def test_plan_row_normalizes_www_and_strips_query() -> None:
    plan = reclassify.plan_row(
        _row(contact_value="https://www.liinks.co/srodalmenara?ref=ig"), set(), set()
    )
    assert plan["normalized_value"] == "https://liinks.co/srodalmenara"


def test_plan_row_skips_when_same_url_already_link_hub() -> None:
    plan = reclassify.plan_row(_row(), {(100, "https://bio.site/x")}, set())
    assert plan["action"] == "skip" and "type+value" in plan["reason"]


def test_plan_row_skips_when_normalized_would_collide() -> None:
    plan = reclassify.plan_row(_row(), set(), {(100, "https://bio.site/x")})
    assert plan["action"] == "skip" and "normalized" in plan["reason"]


def test_plan_row_does_not_lower_a_manually_raised_confidence() -> None:
    plan = reclassify.plan_row(_row(confidence=0.9), set(), set())
    assert plan["confidence"] == 0.9


def test_plan_row_skips_unnormalizable_value() -> None:
    plan = reclassify.plan_row(_row(contact_value="bio.site/x"), set(), set())
    assert plan["action"] == "skip"


def test_reclassify_confidence_convention_matches_the_table() -> None:
    assert reclassify.LINK_HUB_CONFIDENCE == 0.5
    assert reclassify.WEBSITE_CONFIDENCE == 0.45
