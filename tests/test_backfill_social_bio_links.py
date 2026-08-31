"""IG/TikTok/YouTube 外链回填脚本(scripts/backfill_social_bio_links.py)的纯函数锁。

覆盖任务五类验收:
  1. 三平台键路径:IG externalUrl / TT authorMeta.bioLink / YT 频道简介 URL;
  2. 分类:聚合页 -> link_hub,自有域 -> website;
  3. 纯社交跳转链接一律排除(写了只会让页面腿白跑);
  4. 幂等:已在表的外链重跑只进 skipped,且指纹忽略 scheme/www/query/尾斜杠;
  5. raw 结构异常(非 dict / items 非 list / 字段类型错 / 空)不炸,返回空。
全部走 normalize_link / classify_link / dedupe_key / extract_bio_links / plan_kol
纯函数,不碰库、零网络。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_social_bio_links.py"
_SPEC = importlib.util.spec_from_file_location("backfill_social_bio_links", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
mod = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mod
_SPEC.loader.exec_module(mod)


def _ig(*external_urls: object) -> dict:
    return {"profile": {"items": [{"externalUrl": u} for u in external_urls]}}


def _tt(*bio_links: object) -> dict:
    return {"profile": {"items": [{"authorMeta": {"bioLink": b}} for b in bio_links]}}


def _yt(description: str) -> dict:
    return {"profile": {"items": [{"snippet": {"description": description}}]}}


def _values(rows: list[dict]) -> list[str]:
    return [r["contact_value"] for r in rows]


# ---- 1. 三平台键路径 ----

def test_instagram_external_url_key_path() -> None:
    rows = mod.extract_bio_links(_ig("https://linktr.ee/jonnyedward"), "instagram")
    assert _values(rows) == ["https://linktr.ee/jonnyedward"]
    assert rows[0]["source_field"] == "profile.items.0.externalUrl"


def test_tiktok_bio_link_key_path_and_missing_scheme() -> None:
    # 实测 TikTok bioLink 常是无 scheme 的裸域名(www.realmdt.com),文本扫描够不到。
    rows = mod.extract_bio_links(_tt("Www.realmdt.com"), "tiktok")
    assert _values(rows) == ["https://Www.realmdt.com"]
    assert rows[0]["source_field"] == "profile.items.0.authorMeta.bioLink"


def test_tiktok_bio_link_accepts_legacy_dict_shape() -> None:
    rows = mod.extract_bio_links(_tt({"link": "https://beacons.ai/x"}), "tiktok")
    assert _values(rows) == ["https://beacons.ai/x"]


def test_tiktok_repeated_bio_link_across_video_items_dedupes_to_one() -> None:
    # items 是视频列表,同一 bioLink 会重复几十次;同 KOL 内必须只留一条。
    rows = mod.extract_bio_links(_tt("https://linktr.ee/a", "https://linktr.ee/a/"), "tiktok")
    assert _values(rows) == ["https://linktr.ee/a"]


def test_youtube_description_urls_extracted() -> None:
    raw = _yt("Gear list: https://kit.co/creator\nSite: https://creator.example.com/contact")
    assert _values(mod.extract_bio_links(raw, "youtube")) == [
        "https://kit.co/creator",
        "https://creator.example.com/contact",
    ]


def test_youtube_falls_back_to_branding_settings_description() -> None:
    raw = {"profile": {"items": [
        {"brandingSettings": {"channel": {"description": "see https://creator.example.com"}}}
    ]}}
    rows = mod.extract_bio_links(raw, "youtube")
    assert _values(rows) == ["https://creator.example.com"]
    assert rows[0]["source_field"] == "profile.items.0.brandingSettings.channel.description"


def test_platform_key_paths_do_not_cross_contaminate() -> None:
    # IG 的键路径不该在 tiktok 上生效,反之亦然(避免"看起来有活儿"的假产出)。
    assert mod.extract_bio_links(_ig("https://a.example.com"), "tiktok") == []
    assert mod.extract_bio_links(_tt("https://a.example.com"), "instagram") == []


# ---- 2. 聚合页 vs 自有域 分类 ----

def test_link_hub_hosts_classified_as_link_hub() -> None:
    for url in ("https://linktr.ee/x", "https://beacons.ai/x", "https://stan.store/x",
                "https://solo.to/x", "https://carrd.co/x"):
        assert mod.classify_link(url) == ("link_hub", ""), url


def test_own_domain_classified_as_website() -> None:
    assert mod.classify_link("https://andrebrown.com/") == ("website", "")
    assert mod.classify_link("http://www.impactphoto.biz/") == ("website", "")


def test_hub_match_is_host_scoped_not_substring() -> None:
    # 路径里出现 hub 名不算命中(否则 example.com/linktr.ee 会被误判成聚合页)。
    assert mod.classify_link("https://example.com/linktr.ee") == ("website", "")
    # 子域仍算命中。
    assert mod.classify_link("https://foo.linktr.ee/x") == ("link_hub", "")


def test_confidence_matches_existing_table_convention() -> None:
    assert mod.CONFIDENCE == {"link_hub": 0.5, "website": 0.45}


# ---- 3. 纯社交跳转排除 ----

def test_social_redirect_links_excluded() -> None:
    for url in ("https://www.instagram.com/gregselby_",
                "https://www.tiktok.com/@olayemight_001",
                "https://youtube.com/@tomknibbs",
                "https://youtu.be/_R-JlWYV9ww",
                "https://twitter.com/someone",
                "https://x.com/someone"):
        assert mod.classify_link(url) == ("", "social_redirect"), url


def test_social_host_suffix_match_never_hits_lookalike_domain() -> None:
    # jurjax.com 含 'x.com',绝不能被当成 twitter 跳转排除掉。
    assert mod.classify_link("https://jurjax.com/") == ("website", "")


def test_plan_kol_drops_social_and_keeps_real_link() -> None:
    plan = mod.plan_kol(_ig("https://www.instagram.com/someone"), "instagram", set())
    assert plan["to_insert"] == []
    assert [r["reason"] for r in plan["excluded"]] == ["social_redirect"]


def test_non_http_and_cdn_junk_rejected() -> None:
    assert mod.normalize_link("mailto:a@b.com") == ""
    assert mod.normalize_link("tel:+123456") == ""
    assert mod.normalize_link("   ") == ""
    assert mod.classify_link("https://scontent.cdninstagram.com/x.jpg") == ("", "cdn_junk")


# ---- 4. 幂等 ----

def test_existing_link_goes_to_skipped_not_insert() -> None:
    raw = _ig("https://linktr.ee/abelafilms")
    existing = {mod.dedupe_key("https://linktr.ee/abelafilms")}
    plan = mod.plan_kol(raw, "instagram", existing)
    assert plan["to_insert"] == []
    assert _values(plan["skipped"]) == ["https://linktr.ee/abelafilms"]


def test_dedupe_key_ignores_scheme_www_query_and_trailing_slash() -> None:
    canonical = mod.dedupe_key("https://andrebrown.com/bio")
    for variant in ("http://andrebrown.com/bio", "https://www.andrebrown.com/bio/",
                    "https://ANDREBROWN.com/bio?utm_source=ig"):
        assert mod.dedupe_key(variant) == canonical, variant


def test_rerun_after_apply_is_a_noop() -> None:
    # 第一轮的写入计划回灌成 existing,第二轮必须零新增(幂等重跑)。
    raw = _tt("www.realmdt.com", "https://linktr.ee/a")
    first = mod.plan_kol(raw, "tiktok", set())
    assert len(first["to_insert"]) == 2
    existing = {mod.dedupe_key(r["contact_value"]) for r in first["to_insert"]}
    second = mod.plan_kol(raw, "tiktok", existing)
    assert second["to_insert"] == []
    assert len(second["skipped"]) == 2


def test_plan_kol_puts_link_hub_first_and_caps_per_kol() -> None:
    raw = _yt("https://a.example.com https://b.example.com "
              "https://linktr.ee/hub https://c.example.com")
    plan = mod.plan_kol(raw, "youtube", set(), max_links=2)
    assert _values(plan["to_insert"]) == ["https://linktr.ee/hub", "https://a.example.com"]
    assert _values(plan["overflow"]) == ["https://b.example.com", "https://c.example.com"]


# ---- 5. raw 结构异常不炸 ----

def test_malformed_raw_returns_empty_without_raising() -> None:
    for raw in (None, {}, [], "not-json", 42, {"profile": None}, {"profile": "text"},
                {"profile": {"items": None}}, {"profile": {"items": "nope"}},
                {"profile": {"items": [None, 7, "x"]}},
                {"profile": {"items": [{"externalUrl": None}]}},
                {"profile": {"items": [{"externalUrl": {"nested": "dict"}}]}},
                {"profile": {"items": [{"authorMeta": "not-a-dict"}]}},
                {"profile": {"items": [{"authorMeta": {"bioLink": 123}}]}},
                {"profile": {"items": [{"snippet": {"description": None}}]}}):
        for platform in ("instagram", "tiktok", "youtube", "", "unknown"):
            assert mod.extract_bio_links(raw, platform) == [], (raw, platform)


def test_raw_without_profile_container_falls_back_to_root() -> None:
    # 与 extract_contacts_multi_source 同口径:没有 profile 容器就吃 raw 本身。
    raw = {"items": [{"externalUrl": "https://creator.example.com"}]}
    rows = mod.extract_bio_links(raw, "instagram")
    assert _values(rows) == ["https://creator.example.com"]
    assert rows[0]["source_field"] == "raw_platform_data.items.0.externalUrl"


def test_plan_kol_on_malformed_raw_is_all_empty() -> None:
    plan = mod.plan_kol({"profile": {"items": "nope"}}, "instagram", set())
    assert plan == {"to_insert": [], "overflow": [], "skipped": [], "excluded": []}
