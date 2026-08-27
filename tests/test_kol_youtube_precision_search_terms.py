"""精准检索词(品牌+品类窄词并联)——2026-08-27「搜出来一堆再筛选 → 精准搜索」车道。

守的是**本机真链路实测**结论,不是口味(实测表在 account_search_terms 模块头):
- 无锚泛词("photography gear review")21 人过相关闸、**0 人**过 8 道严格闸 → 每条词必须带锚;
- 有锚但无意图词("Viltrox prime lens")出货率 2.4% → 每条词必须带 review/test;
- 锚到型号级("Viltrox AF 135mm f1.8 LAB")语料已抓干、0 产出 → 光圈/系列词必须被剥掉;
- 同 query 重跑返回逐条相同、0 产出 → 抓干的词必须被跳过,不许重发第一页。

红线自证:本文件零触任何质量判据(新鲜度/粉丝下限/器材证据/检测器阈值),只测检索词与记账。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.intelligence import account_search_discovery as discovery
from app.services.intelligence import account_search_terms as terms

PERSONA = "young content creators Sony E-mount 135mm portrait prime lens review photography video"
MODEL_QUERY = "Viltrox AF 135mm f1.8 LAB Sony E mount portrait creators"
_INTENT_WORDS = {"review", "test"}


def test_every_precision_term_carries_an_anchor_and_an_intent_word() -> None:
    ladder = terms.youtube_precision_terms(PERSONA)
    assert len(ladder) == 6
    for row in ladder:
        assert row["anchor"], row
        assert row["anchor_source"] in {
            "own_brand_category", "mount_category", "use_case_category",
            "focal_family_category", "peer_brand_category",
        }
        assert row["term"].split()[-1] in _INTENT_WORDS, row
        # 锚必须真的出现在词里 —— 记账里写了锚、词里没有,就是假记账。
        assert row["anchor"].split()[0].lower() in row["term"].lower()


def test_prospect_anchors_lead_and_own_brand_trails() -> None:
    """用户裁决「要找的是 135 的**潜在**用户,不是已经有 135 的」的落点。

    自有品牌词捞回的人手上已经有我们的镜头 —— 出货率再高也不是要找的人,排到最后。
    代价比预期小:实测最高产的 ``Sony lens review``(26.0%,harness 真链路第一页)
    本来就在潜在用户档里,旧排 tier 6 被 cap 砍掉,从来没发出去过、也就没人测过它。
    """
    ladder = terms.youtube_precision_terms(PERSONA)
    assert ladder[0]["anchor_source"] == "peer_brand_category"
    assert ladder[0]["term"] == "Sony lens review"
    # 自有品牌两条必须垫底 —— 它们一旦回到头位就会把整轮配额吃光(装满即停)。
    assert [row["anchor_source"] for row in ladder[-2:]] == ["own_brand_category"] * 2
    prospect = [r["anchor_source"] for r in ladder if r["anchor_source"] != "own_brand_category"]
    assert prospect == ["peer_brand_category", "focal_family_category",
                        "use_case_category", "mount_category"]


def test_model_level_tokens_are_stripped_and_recorded() -> None:
    signals = terms.query_anchor_signals(MODEL_QUERY)
    assert signals["dropped_model_tokens"] == ["f1.8", "lab"]
    # 焦段家族是允许的锚,不算型号 token。
    assert signals["focal_family"] == "135mm"
    for row in terms.youtube_precision_terms(MODEL_QUERY):
        lowered = row["term"].lower()
        assert "f1.8" not in lowered and "lab" not in lowered
        # 品牌与焦段绝不同时出现在一条词里 —— 那就是型号级检索词。
        assert not ("viltrox" in lowered and "135mm" in lowered)


def test_focal_length_implies_lens_category() -> None:
    """整句只写 135mm 没写 lens:不推品类的话会掉回旧 5 词块,第一条就是型号级检索词。"""
    assert terms.query_anchor_signals(MODEL_QUERY)["category"] == "lens"
    assert terms._youtube_search_query_variants(MODEL_QUERY, max_variants=6)[0] == "Sony lens review"


def test_non_gear_query_falls_back_to_legacy_chunks_unchanged() -> None:
    """认不出器材品类 = 这不是器材检索 → 整条精准路径让位,零回归。"""
    plain = "vegan cooking channels in germany"
    assert terms.youtube_precision_terms(plain) == []
    assert terms._youtube_search_query_variants(plain) == [plain]
    assert terms._youtube_search_query_variants("street photographer") == ["street photographer"]
    assert terms._youtube_search_query_variants("") == []


def test_short_operator_query_keeps_its_own_words_first() -> None:
    """operator 在 lens_monitor / kol_ops 手打的短词是显式意图,不许被改写掉。"""
    variants = terms._youtube_search_query_variants("Viltrox 135mm lens review", max_variants=5)
    assert variants[0] == "Viltrox 135mm lens review"
    assert "Viltrox lens review" in variants


def test_terms_are_deterministic_because_page_cursors_are_keyed_by_term() -> None:
    assert terms.youtube_precision_terms(PERSONA) == terms.youtube_precision_terms(PERSONA)


def test_no_precision_term_is_a_bare_generic() -> None:
    """实测唯一 0 产出的形状:泛词 + 意图词、零产品锚。它绝不许被造出来。"""
    generated = {row["term"].lower() for row in terms.youtube_precision_terms(PERSONA)}
    assert "photography gear review" not in generated
    assert "camera gear review" not in generated


class _FakeCrawler:
    """只回 search/channels 两种响应的假 crawler;记录每次实发的 q 与 pageToken。"""

    api_key = "test-key"

    def __init__(self, pages: dict[str, str | None]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((endpoint, dict(params)))
        if endpoint == "channels":
            return {"provider_status": "ok", "items": [
                {"id": cid, "snippet": {"title": "creator", "customUrl": f"@{cid[-4:]}"},
                 "statistics": {"subscriberCount": "5000"}}
                for cid in str(params.get("id") or "").split(",") if cid
            ]}
        query = str(params.get("q") or "")
        return {
            "provider_status": "ok",
            "nextPageToken": self.pages.get(query) or "",
            "items": [{
                "id": {"videoId": f"vid-{query[:6]}-{len(self.calls)}"},
                "snippet": {"channelId": f"UC-{query[:8]}-{len(self.calls)}", "channelTitle": "creator"},
            }],
        }

    @staticmethod
    def _should_use_apify_fallback(payload: dict[str, Any]) -> bool:
        return False


@pytest.fixture()
def fake_crawler(monkeypatch: pytest.MonkeyPatch):
    holder: dict[str, _FakeCrawler] = {}

    def install(pages: dict[str, str | None]) -> _FakeCrawler:
        crawler = _FakeCrawler(pages)
        holder["crawler"] = crawler
        import app.platform.industry_crawlers.youtube_crawler as yt

        monkeypatch.setattr(yt, "YouTubeCrawler", lambda *a, **k: crawler)
        return crawler

    return install


def _run(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(discovery._youtube_data_api_strict_video_search(PERSONA, **kwargs))


def test_term_ledger_records_anchor_quota_and_exhaustion(fake_crawler) -> None:
    crawler = fake_crawler({"Sony lens review": "TOKEN-2"})
    result = _run(safe_limit=3)
    meta = result["metadata"]
    ledger = meta["term_ledger"]
    assert [row["term"] for row in ledger][:2] == ["Sony lens review", "135mm lens review"]
    assert ledger[0]["anchor_source"] == "peer_brand_category"
    assert ledger[0]["exhausted"] is False
    assert ledger[1]["exhausted"] is True  # 假 crawler 只给第一条词发了 nextPageToken
    # 实发几次 search.list 就记几个 100(旧式 max(1, ...) 会给零调用的轮次凭空记 100)。
    searches = sum(1 for endpoint, _ in crawler.calls if endpoint == "search")
    assert meta["quota_units"] == 100 * searches + 1
    assert sum(row["quota_units"] for row in ledger) == 100 * searches
    assert meta["query_anchor_signals"]["category"] == "lens"


def test_exhausted_term_is_skipped_next_round_instead_of_refetching_page_one(fake_crawler) -> None:
    """同 query 重跑无效的落点:抓干的词进游标当哨兵,下一轮 0 配额跳过。

    ``safe_limit=50`` 是刻意的:让第一轮把 6 条词**全部**发一遍,第二轮就没有「没发过的
    词」可优先了 —— 否则轮转会先发未发过的词,那样即便哨兵完全失灵,抓干的词也照样不会
    出现在第二轮里,测试就证明不了哨兵在起作用(只证明了轮转在起作用)。
    """
    first = fake_crawler({"Sony lens review": "TOKEN-2"})
    cursor = _run(safe_limit=50)["metadata"]["next_page_cursor"]
    assert cursor["Sony lens review"] == "TOKEN-2"
    exhausted = {t for t, tok in cursor.items() if tok == terms.TERM_EXHAUSTED_TOKEN}
    assert "135mm lens review" in exhausted
    assert len(cursor) == 6  # 6 条词这一轮全发过了,第二轮无「未发过」可优先

    second = fake_crawler({"Sony lens review": ""})
    meta = _run(safe_limit=50, page_cursor=cursor)["metadata"]
    issued = [params.get("q") for endpoint, params in second.calls if endpoint == "search"]
    assert not (exhausted & set(issued))  # 哨兵生效:抓干的词一条都没再发
    assert "Sony lens review" in issued   # 没抓干的词照常续页,不是整体停摆
    assert first is not second
    skipped = [row for row in meta["term_ledger"] if row.get("skipped") == "exhausted_previous_round"]
    assert skipped and all(row["quota_units"] == 0 for row in skipped)


def test_has_more_is_false_once_every_term_is_exhausted(fake_crawler) -> None:
    """全部词都抓干 → 诚实说没有下一页;哨兵不许被当成「还有游标」。"""
    fake_crawler({})
    meta = _run(safe_limit=50)["metadata"]
    assert meta["has_more"] is False
    assert set(meta["next_page_cursor"].values()) == {terms.TERM_EXHAUSTED_TOKEN}
