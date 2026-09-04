"""焦段家族解析契约:裸焦段认得出、卡口能收窄、非焦段数字不误伤、认不出要如实说。

回放依据(本地目录克隆 380 行 / 线上 304 条真实 query):
「我想要喜欢135的用户」这条真实 query 过去解析不出任何产品——不报错、不提示,
直接跑一趟没有产品锚的搜索。本文件把新口径钉死。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import product_focal_family, product_resolver


def _lens(
    sku: str,
    model_name: str,
    *,
    series: str = "",
    mount: str = "",
    category_main: str = "Lens",
    category_detail: str = "Prime Lens",
) -> dict[str, Any]:
    return {
        "sku": sku,
        "model_name": model_name,
        "marketing_name": model_name,
        "series": series,
        "mount": mount,
        "category_main": category_main,
        "category_detail": category_detail,
        "description": "",
        "price_usd": None,
    }


# 目录切片:真目录的形状(同焦段多卡口、legacy 无卡口行、以及最容易被错认成镜头的
# 灯 / 电池 / 接圈 / 监视器)。
_CATALOG = [
    _lens("AF-135MM-F18-LAB-FE", "Viltrox AF 135mm F1.8 LAB Lens", series="LAB", mount="FE-mount"),
    _lens("AF-135MM-F18-LAB-Z", "Viltrox AF 135mm F1.8 LAB Lens", series="LAB", mount="Z-mount"),
    _lens("EPIC-135MM-T2-4-1-33X-PL-ANAMORPHIC-CINE-L", "Viltrox EPIC 135mm T2.4 Anamorphic Cine", series="Cine", mount="L-mount"),
    _lens("VL-LEN057", "AF 135/1.8 FE"),
    _lens("AF-55MM-F18-EVO-FE", "Viltrox AF 55mm F1.8 EVO Lens", series="EVO", mount="FE-mount"),
    _lens("AF-55MM-F18-EVO-Z", "Viltrox AF 55mm F1.8 EVO Lens", series="EVO", mount="Z-mount"),
    _lens("AF-85MM-F14-PRO-FE", "Viltrox AF 85mm F1.4 Pro Lens", series="Pro", mount="FE-mount"),
    _lens("AF-85MM-F20-EVO-Z", "Viltrox AF 85mm F2.0 EVO Lens", series="EVO", mount="Z-mount"),
    _lens("AF-85MM-F18-II-X", "Viltrox AF 85mm F1.8 II APS-C Lens", mount="X-mount"),
    _lens("MF-23MM-T1-5-CINE-M4-3-MOUNT-M43", "Viltrox MF 23mm T1.5 Cine Lens", series="Cine", mount="M43"),
    _lens("AF-23MM-F14-X", "Viltrox AF 23mm F1.4 Lens", mount="X-mount"),
    # ↓ 非镜头。历史上「55mm 镜头被读成 300W 灯」就出在这类行上。
    _lens("NINJA-30-30B-300W-SINGLE-BI-COLOR-COB-LIGHT", "Viltrox Ninja 30/30B 300W COB Light", category_main="Product", category_detail="Lighting"),
    _lens("50-99-150WH-V-MOUNT-LITHIUM-BATTERY", "Viltrox 50/99/150Wh V-Mount Lithium Battery", category_main="Battery", category_detail="Battery"),
    _lens("DG-GFX-45MM-EXTENSION-TUBE", "Viltrox DG-GFX 45mm Extension Tube", category_main="Macro Extension Tube", category_detail="Macro Extension Tube"),
    _lens("DC-550-PRO-LL-PORTABLE-5-5-INCH-HD-CAMERA-MONITOR", "Viltrox DC-550 Pro ll 5.5 Inch HD Camera Monitor", category_main="Monitor", category_detail="Monitor"),
]


@pytest.fixture()
def catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        product_resolver,
        "list_product_catalog",
        lambda **_kwargs: {"products": [dict(row) for row in _CATALOG]},
    )
    monkeypatch.setattr(
        product_focal_family,
        "list_product_catalog",
        lambda **_kwargs: {"products": [dict(row) for row in _CATALOG]},
    )


# ── 1. 裸焦段认得出 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("query", "focal", "anchor"),
    [
        ("135", 135, "Viltrox 135mm"),
        ("55", 55, "Viltrox 55mm EVO"),
        ("85", 85, "Viltrox 85mm"),
        ("我想要喜欢135的用户", 135, "Viltrox 135mm"),
        ("找适合 85mm 人像镜头的婚礼摄影师", 85, "Viltrox 85mm"),
    ],
)
def test_bare_focal_resolves_to_the_focal_family(
    catalog: None,
    query: str,
    focal: int,
    anchor: str,
) -> None:
    resolved = product_resolver.resolve_product(query)

    assert resolved is not None, f"{query!r} 应当认出焦段"
    assert resolved["focal_mm"] == focal
    assert resolved["resolution_kind"] == "focal_family"
    # 同焦段多款时绝不挑一个具体型号,也绝不编一个价格。
    assert resolved["sku"] == ""
    assert resolved["price_usd"] is None
    assert resolved["marketing_name"] == anchor


def test_focal_family_lists_the_real_candidates_for_the_operator(catalog: None) -> None:
    resolved = product_resolver.resolve_product("135")

    assert resolved is not None
    assert resolved["focal_family_size"] == 4
    assert set(resolved["focal_family_skus"]) == {
        "AF-135MM-F18-LAB-FE",
        "AF-135MM-F18-LAB-Z",
        "EPIC-135MM-T2-4-1-33X-PL-ANAMORPHIC-CINE-L",
        "VL-LEN057",
    }
    assert resolved["focal_family_mounts"] == ["FE-mount", "L-mount", "Z-mount"]


def test_mixed_series_family_never_borrows_one_members_series(catalog: None) -> None:
    """23mm 家族里有一支 Cine,但另一支是平面定焦——不许把整族叫成 Cine。"""
    resolved = product_resolver.resolve_product("street photographer for 23mm prime lens")

    assert resolved is not None
    assert resolved["marketing_name"] == "Viltrox 23mm"
    assert "cine" not in resolved["marketing_name"].lower()


# ── 2. 卡口/系列线索收窄 ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("query", "expected_sku"),
    [
        ("135 e卡口", "AF-135MM-F18-LAB-FE"),
        ("135 索尼", "AF-135MM-F18-LAB-FE"),
        ("55 z卡口", "AF-55MM-F18-EVO-Z"),
        ("55mm nikon", "AF-55MM-F18-EVO-Z"),
        ("85 x卡口", "AF-85MM-F18-II-X"),
    ],
)
def test_mount_context_narrows_the_family_to_one_sku(
    catalog: None,
    query: str,
    expected_sku: str,
) -> None:
    resolved = product_resolver.resolve_product(query)

    assert resolved is not None
    assert resolved["sku"] == expected_sku
    assert resolved["resolution_kind"] == "focal_narrowed_by_mount"


def test_series_context_narrows_the_family_without_picking_a_mount(catalog: None) -> None:
    """点了系列没点卡口 → 收窄到该系列,但仍然不挑具体那一支。"""
    resolved = product_resolver.resolve_product("找适合 135mm LAB 镜头的人像摄影师")

    assert resolved is not None
    assert resolved["sku"] == ""
    assert resolved["series"] == "LAB"
    assert resolved["focal_family_size"] == 2
    assert resolved["focal_family_mounts"] == ["FE-mount", "Z-mount"]


# ── 3. 纯数字非焦段场景不误伤 ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "query",
    [
        "找20个美食达人",
        "找 85 位婚礼摄影师",
        "粉丝50万以上的摄影师",
        "50k followers travel creators",
        "at least 85 creators",
        "top 55 photographers",
        "Find 85 photographers",
        "Find 100 wedding photographers",
        "Find 75 documentary filmmakers",
        "Find 55 sports commentators",
        "找85摄影师",
        "90后摄影师",
        "2024年的旅拍达人",
        "找一些适合300美金evo系列的用户 300W EVO portable lighting",
        "找一个关550pro的、",
        "账号分析 · https://www.35mmc.com/?s=viltrox",
        "账号分析 · https://flokugrafie.de/objektive/viltrox-55mm-f-1-8-evo-apo-im-test/",
        "ig/p4-step23-life-1783537327076753000-kol",
        "85% 完播率的视频作者",
        "$135 预算的达人",
    ],
)
def test_non_focal_numbers_never_bind_a_lens_family(catalog: None, query: str) -> None:
    resolved = product_resolver.resolve_product(query)

    kind = str((resolved or {}).get("resolution_kind") or "")
    assert not kind.startswith("focal"), f"{query!r} 里的数字不是焦段"
    assert not (resolved or {}).get("focal_mm"), f"{query!r} 里的数字不是焦段"


def test_focal_index_excludes_lights_batteries_tubes_and_monitors(catalog: None) -> None:
    """焦段表只能由真镜头行构成——灯的 30、电池的 50/99、接圈的 45 一律不进。"""
    index = product_focal_family.focal_family_index(
        lambda **_kwargs: {"products": [dict(row) for row in _CATALOG]}
    )

    assert sorted(index) == [23, 55, 85, 135]
    for focal, rows in index.items():
        for row in rows:
            blob = f"{row['sku']} {row['model_name']}".lower()
            assert "light" not in blob and "battery" not in blob and "tube" not in blob
            assert "monitor" not in blob


def test_multi_focal_set_row_never_joins_a_single_focal_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """七头套装列了 135mm,但操作员说「135」时想的是一支 135,不是一整套。"""
    single = _lens("EPIC-135MM-T2-4-PL-L", "Viltrox EPIC 135mm T2.4 Anamorphic Cine", series="Cine", mount="L-mount")
    kit = _lens(
        "EPIC-25MM-35MM-50MM-65MM-75MM-100MM-135MM-1-33X-PL-ANAMORPHIC-CINE-SET-L",
        "Viltrox EPIC 25mm/35mm/50mm/65mm/75mm/100mm/135mm Anamorphic Cine Set",
        series="Cine",
        mount="L-mount",
    )
    catalog_rows = [dict(single), dict(kit)]
    monkeypatch.setattr(product_resolver, "list_product_catalog", lambda **_k: {"products": catalog_rows})
    monkeypatch.setattr(product_focal_family, "list_product_catalog", lambda **_k: {"products": catalog_rows})

    index = product_focal_family.focal_family_index()
    assert sorted(index) == [135]
    assert [row["sku"] for row in index[135]] == ["EPIC-135MM-T2-4-PL-L"]

    # 套装被剔掉后 135mm 只剩一支。写了单位的「135mm」= 操作员自己点明了焦段,
    # 落到唯一 SKU;裸数字「135」不行——那一支是目录形状凑出来的,不是他点的。
    resolved = product_resolver.resolve_product("135mm")
    assert resolved is not None
    assert resolved["sku"] == "EPIC-135MM-T2-4-PL-L"
    assert resolved["resolution_kind"] == "focal_single_in_catalog"

    bare = product_resolver.resolve_product("135")
    assert bare is not None
    assert bare["sku"] == ""
    assert bare["price_usd"] is None
    assert bare["resolution_kind"] == "focal_family"
    assert bare["marketing_name"] == "Viltrox 135mm EPIC"


# ── 3b. 反例批次:时间跨度 / 粉丝区间 / 年份 / 价格 / 百分比 / 排名 ───────────
# 事故形状(2026-08-25 线上):「找24小时内活跃的youtube博主」被解析成 24mm 镜头,
# 而目录里 24mm 只有一行 → 整条搜索被锚到一支操作员从没提过的镜头上,界面上看不出来。
# 认不出「135」只是搜不到;把「24 小时」认成镜头是把搜索引到完全错误的产品上。
# 所以这一批只判一件事:这些数字**一个都不许**变成焦段。

_NOT_A_FOCAL = [
    # 时间跨度
    "找24小时内活跃的youtube博主",
    "24小时内活跃的博主",
    "最近48小时发过视频的人",
    "过去72小时发布的视频",
    "24 hours active youtube creators",
    "active in the last 24 hours",
    "近30天活跃的达人",
    "30天内发过视频",
    "最近 14 天有更新",
    "观看时长 135 秒",
    # 粉丝 / 数量区间
    "粉丝10到50万",
    "粉丝 20 到 85 万的达人",
    "10万到50万粉丝",
    "20至85万粉丝的达人",
    "creators with 55 to 135 videos",
    "between 24 and 85 followers",
    "55台设备",
    "24支镜头",
    # 年份
    "2024年的旅拍达人",
    "从2019到2024",
    "24年入行的摄影师",
    # 价格
    "预算 85 到 135 美金",
    "价格85的达人",
    "价格 135 以内",
    "85块钱",
    # 百分比 / 比率
    "完播率 55 到 85",
    "互动率 85 的账号",
    "85% 完播率的视频作者",
    # 排名
    "排名 24 到 55 的账号",
    "第24名的博主",
    "评分 85 分",
]


@pytest.mark.parametrize("query", _NOT_A_FOCAL)
def test_time_spans_ranges_years_prices_and_ranks_are_never_focals(
    catalog: None,
    query: str,
) -> None:
    """判据层单独取证:这些 query 一个焦段候选都不许产出。"""
    assert product_focal_family.bare_focal_numbers(query) == [], f"{query!r} 里没有焦段"


@pytest.mark.parametrize("query", _NOT_A_FOCAL)
def test_non_focal_queries_never_reach_a_product_anchor(catalog: None, query: str) -> None:
    """端到端:不许解析出产品,也不许弹「没认出你要找的产品」误报拦住正常搜索。"""
    resolved = product_resolver.resolve_product(query)
    kind = str((resolved or {}).get("resolution_kind") or "")

    assert not kind.startswith("focal"), f"{query!r} 被错认成焦段产品"
    assert not (resolved or {}).get("focal_mm"), f"{query!r} 被错认成焦段产品"
    clarification = product_resolver.unresolved_product_request(query)
    assert (clarification or {}).get("reason") not in {
        "focal_not_in_catalog",
        "focal_mount_not_in_catalog",
        "multiple_focals_requested",
    }, f"{query!r} 不该触发焦段提示"


def test_bare_number_never_locks_a_sku_even_when_the_family_has_one_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """裸数字 + 「目录里恰好只有一行」= 目录形状替操作员挑了型号,不许锁 SKU。

    这是 24 小时误伤能一路锚到 AF-24MM-F18-Z（还带上定价）的最后一环。
    要认具体 SKU,得有卡口/系列线索,或者操作员自己写了单位。
    """
    only_row = _lens("AF-24MM-F18-Z", "Viltrox AF 24mm F1.8 Lens", mount="Z-mount")
    rows = [dict(only_row)]
    monkeypatch.setattr(product_resolver, "list_product_catalog", lambda **_k: {"products": rows})
    monkeypatch.setattr(product_focal_family, "list_product_catalog", lambda **_k: {"products": rows})

    bare = product_resolver.resolve_product("24")
    assert bare is not None
    assert bare["sku"] == ""
    assert bare["price_usd"] is None
    assert bare["marketing_name"] == "Viltrox 24mm"
    assert bare["resolution_kind"] == "focal_family"

    # 写了单位 → 操作员点名了焦段,可以落到那一行真值。
    typed = product_resolver.resolve_product("24mm")
    assert (typed or {})["sku"] == "AF-24MM-F18-Z"
    assert typed["resolution_kind"] == "focal_single_in_catalog"

    # 有卡口线索的裸数字照样可以收窄——本刀一分没松收窄能力。
    narrowed = product_resolver.resolve_product("24 尼康")
    assert (narrowed or {})["sku"] == "AF-24MM-F18-Z"
    assert narrowed["resolution_kind"] == "focal_narrowed_by_mount"


def test_real_focal_requests_still_resolve_after_the_counter_example_gate(
    catalog: None,
) -> None:
    """收紧不许误伤真诉求:含 to/top/at/vintage 的英文说法仍要认得出焦段。"""
    for query in ("135", "shot at 135", "photo 135 lens", "135 top creators", "vintage 55"):
        resolved = product_resolver.resolve_product(query)
        assert resolved is not None, f"{query!r} 仍应认出焦段"
        assert resolved["focal_mm"] in (55, 135)


@pytest.mark.parametrize(
    "query",
    [
        "24小时内更新的 evo 用户",
        "24小时内活跃的镜头测评博主",
        "近30天活跃的定焦镜头博主",
        "粉丝10到50万的镜头博主",
    ],
)
def test_the_second_focal_parser_shares_the_same_counting_judgement(
    catalog: None,
    query: str,
) -> None:
    """product_resolver 自己那套裸数字判据必须和本模块同源,不许各写各的。

    分家的代价:「24小时内更新的 evo 用户」在那一侧仍被读出焦段 24,再撞上目录里没有
    24mm EVO,就弹「请先选择正确产品」把一次正常搜索整个拦掉——操作员只看到一堵墙。
    """
    assert product_resolver._query_focals(query) == set(), f"{query!r} 里没有焦段"


def test_glued_focal_typing_survives_the_shared_judgement(catalog: None) -> None:
    """收紧不许砸掉真打法:"55evo" 这种数字粘字母仍要读出 55。"""
    assert 55 in product_resolver._query_focals("55evo")
    assert 85 in product_resolver._query_focals("85mm pro")


def test_bare_number_reader_is_the_only_gate_that_needs_context(catalog: None) -> None:
    """判据本身可单独取证:量词/比较词/URL 三类都不算焦段候选。"""
    assert product_focal_family.bare_focal_numbers("我想要喜欢135的用户") == [135]
    assert product_focal_family.bare_focal_numbers("找20个美食达人") == []
    assert product_focal_family.bare_focal_numbers("top 55 photographers") == []
    assert product_focal_family.bare_focal_numbers("Find 85 photographers") == []
    assert product_focal_family.bare_focal_numbers("Find 100 wedding photographers") == []
    assert product_focal_family.bare_focal_numbers("https://www.5050travelog.com/85") == []
    assert product_focal_family.bare_focal_numbers("550pro") == []


# ── 4. 解析不出时如实告知,并给出可选项 ─────────────────────────────────────

def test_unknown_focal_tells_the_operator_instead_of_running_a_doomed_search(
    catalog: None,
) -> None:
    query = "找适合 200mm 镜头的野生动物摄影师"

    assert product_resolver.resolve_product(query) is None
    clarification = product_resolver.unresolved_product_request(query)

    assert clarification is not None
    assert clarification["reason"] == "focal_not_in_catalog"
    assert "没认出你要找的产品" in clarification["message"]
    # 给的是目录里真实存在的最接近焦段,不是编的。
    assert "135mm" in clarification["message"]


def test_unavailable_mount_names_the_mounts_that_do_exist(catalog: None) -> None:
    query = "135 x卡口"

    assert product_resolver.resolve_product(query) is None
    clarification = product_resolver.unresolved_product_request(query)

    assert clarification is not None
    assert clarification["reason"] == "focal_mount_not_in_catalog"
    assert "没认出你要找的产品" in clarification["message"]
    assert "FE-mount" in clarification["message"]
    assert {item["sku"] for item in clarification["suggestions"]} == {
        "AF-135MM-F18-LAB-FE",
        "AF-135MM-F18-LAB-Z",
        "EPIC-135MM-T2-4-1-33X-PL-ANAMORPHIC-CINE-L",
        "VL-LEN057",
    }


def test_two_focals_at_once_asks_the_operator_to_pick_one(catalog: None) -> None:
    query = "找适合 55mm 和 135mm 的摄影师"

    assert product_resolver.resolve_product(query) is None
    clarification = product_resolver.unresolved_product_request(query)

    assert clarification is not None
    assert clarification["reason"] == "multiple_focals_requested"
    assert "55mm" in clarification["message"] and "135mm" in clarification["message"]


def test_generic_creator_request_still_runs_a_normal_search(catalog: None) -> None:
    """没提产品的正常搜索绝不能被这条新提示拦住。"""
    for query in ("找一些美食视频博主", "wedding photographers in Spain"):
        assert product_resolver.resolve_product(query) is None
        assert product_resolver.unresolved_product_request(query) is None


# ── 5. 兜底只加不减:原本能解析的一律不动 ───────────────────────────────────

def test_focal_fallback_only_fires_when_the_existing_resolver_found_nothing(
    catalog: None,
) -> None:
    for query in ("550pro", "找一个关550pro的、", "DC-550 Pro"):
        assert product_resolver.resolve_product(query) == product_resolver._resolve_product_impl(query)


def test_catalog_read_failure_degrades_to_no_product_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(product_resolver, "list_product_catalog", _boom)
    monkeypatch.setattr(product_focal_family, "list_product_catalog", _boom)

    assert product_resolver.resolve_product("135") is None
    assert product_resolver.unresolved_product_request("135") is None
