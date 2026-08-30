"""分支覆盖冲刺·services/via/product_brain.py — 确定性产品导购的意图/系列/卡口/预算分支。

外部信号(B&H 行情、竞品守卫)一律 monkeypatch 掉,聚焦本模块的规则分支;
断言均为具体的推荐产品/回复结构/会话补丁。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.via import product_brain as pb  # noqa: E402
from app.services.via.product_brain_catalog import CATALOG, STORE_URL  # noqa: E402


class PatchMixin:
    def _patch(self, obj, name, value):
        original = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(lambda: setattr(obj, name, original))

    def _quiet_externals(self):
        self._patch(pb, "_bh_market_rows", lambda products: {})
        self._patch(pb, "handle_external_competitor_query", lambda text: None)


class BudgetExtractionTests(unittest.TestCase):
    def test_pattern_forms(self):
        self.assertEqual(pb._extract_budget("keep it under $300 please"), 300)
        self.assertEqual(pb._extract_budget("大概 250 usd 吧"), 250)
        self.assertEqual(pb._extract_budget("预算300"), 300)

    def test_out_of_range_rejected(self):
        self.assertIsNone(pb._extract_budget("under 9999 maybe"))

    def test_session_hint_fallback(self):
        self.assertEqual(pb._extract_budget("anything", session_state={"last_budget_hint": 250}), 250)
        self.assertIsNone(pb._extract_budget("anything", session_state={"last_budget_hint": "junk"}))

    def test_profile_budget_query_defaults_300(self):
        self.assertEqual(pb._extract_budget("anything", profile_context="student on a budget"), 300)
        self.assertIsNone(pb._extract_budget("anything"))


class FamilyKeyTests(unittest.TestCase):
    def test_each_family_token(self):
        self.assertEqual(pb._family_key("有饼干头吗"), "pancake")
        self.assertEqual(pb._family_key("轻便一点的"), "air")
        self.assertEqual(pb._family_key("evo apo 系列"), "evo")
        self.assertEqual(pb._family_key("旗舰画质"), "lab")
        self.assertEqual(pb._family_key("pro 系列呢"), "pro")
        self.assertEqual(pb._family_key("anamorphic 变形"), "epic")
        self.assertEqual(pb._family_key("30-300 cine zoom"), "luna")
        self.assertEqual(pb._family_key("闪光灯有吗"), "lighting")
        self.assertIsNone(pb._family_key("tell me a joke"))


class SeriesRuleTests(unittest.TestCase):
    def test_no_family_no_matches(self):
        self.assertEqual(pb._series_rule_matches(None, "lab 35"), [])
        self.assertEqual(pb._series_rule_matches("not_a_family", "x"), [])

    def test_text_hit_then_highlight_fill(self):
        matches = pb._series_rule_matches("lab", "thinking about the lab 135", limit=2)
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0]["label"], "AF 135mm F1.8 LAB")
        self.assertEqual(matches[0]["series"], "LAB")
        self.assertTrue(matches[0]["official_url"].startswith("https://"))

    def test_selection_line_languages_and_empty(self):
        zh = pb._series_selection_line("epic", "epic 系列", "zh")
        self.assertIn("系列官方入口", zh)
        en = pb._series_selection_line("epic", "epic series", "en")
        self.assertIn("Official series entry", en)
        self.assertEqual(pb._series_selection_line(None, "x", "en"), "")


class MountDetectionTests(unittest.TestCase):
    def test_short_token_and_library_tokens(self):
        self.assertEqual(pb._detect_mount("e"), "sony_e")
        self.assertEqual(pb._detect_mount("shooting on a nikon body"), "nikon_z")
        self.assertEqual(pb._detect_mount("富士机身"), "fuji_x")

    def test_session_then_profile_fallback(self):
        self.assertEqual(pb._detect_mount("anything", session_state={"last_mount_hint": "canon_rf"}), "canon_rf")
        self.assertEqual(pb._detect_mount("anything", profile_context="user shoots sony a7"), "sony_e")
        self.assertIsNone(pb._detect_mount("anything"))


class ProductTopicTests(unittest.TestCase):
    def test_short_mount_needs_session_labels(self):
        self.assertTrue(pb._product_topic("e", session_state={"last_product_labels": ["AF 50mm F2.0 Air FF"]}))
        self.assertFalse(pb._product_topic("hello there"))

    def test_budget_plus_profile_gear_signal(self):
        self.assertTrue(pb._product_topic("cheap options?", profile_context="sony shooter"))

    def test_keyword_and_focal_regex(self):
        self.assertTrue(pb._product_topic("镜头推荐"))
        self.assertTrue(pb._product_topic("is 27 wide enough"))
        self.assertFalse(pb._product_topic("weather tomorrow"))


class FilterTests(unittest.TestCase):
    def test_mount_filter_with_honest_fallback(self):
        products = [CATALOG[0], CATALOG[5]]  # chip 只有 Sony E;35 air 有富士
        fuji = pb._filter_by_mount(products, "fuji_x")
        self.assertEqual([p.label for p in fuji], [CATALOG[5].label])
        # 全部不匹配 → 退回原列表(不空手)
        only_sony = pb._filter_by_mount([CATALOG[0]], "fuji_x")
        self.assertEqual([p.label for p in only_sony], [CATALOG[0].label])
        self.assertEqual(pb._filter_by_mount(products, None), products)
        self.assertEqual(pb._filter_by_mount(products, "ghost_mount"), products)

    def test_budget_filter_with_headroom_and_fallback(self):
        products = [CATALOG[0], CATALOG[7]]  # $99 与 $399
        cheap = pb._filter_by_budget(products, 100)
        self.assertEqual([p.label for p in cheap], [CATALOG[0].label])
        # 25 美金余量:cap 375 能容下 399
        both = pb._filter_by_budget(products, 375)
        self.assertEqual(len(both), 2)
        # cap 全灭 → 退回原列表
        self.assertEqual(pb._filter_by_budget([CATALOG[7]], 100), [CATALOG[7]])
        self.assertEqual(pb._filter_by_budget(products, None), products)


class RecommendedProductsTests(unittest.TestCase):
    def test_explicit_alias_match_wins(self):
        out = pb._recommended_products("the 56mm one")
        self.assertEqual(out[0].label, "AF 56mm F1.7 Air APS-C")

    def test_family_branches(self):
        self.assertEqual(pb._recommended_products("饼干头")[0].label, CATALOG[0].label)
        self.assertEqual(pb._recommended_products("anamorphic 电影镜头"), [CATALOG[17]])
        # 用 luna 家族词但避开具体产品别名,踩 family 分支而非显式命中分支
        self.assertEqual([p.label for p in pb._recommended_products("体育转播 用什么")],
                         [CATALOG[18].label, CATALOG[19].label])
        self.assertEqual(pb._recommended_products("闪光灯")[0].label, CATALOG[20].label)

    def test_apsc_and_focal_paths(self):
        apsc = pb._recommended_products("crop body lens ideas")
        self.assertEqual(apsc[0].label, CATALOG[6].label)
        eighty_five = pb._recommended_products("something 85 for portraits")
        self.assertEqual(eighty_five[0].label, CATALOG[9].label)

    def test_budget_cap_path(self):
        out = pb._recommended_products("under $200 lens")
        self.assertTrue(all(p.est_price_usd <= 225 for p in out))
        self.assertEqual(out[0].label, CATALOG[4].label)

    def test_default_lane(self):
        out = pb._recommended_products("镜头推荐一下")
        self.assertEqual([p.label for p in out],
                         [CATALOG[4].label, CATALOG[7].label, CATALOG[12].label])


class BuildProductContextTests(PatchMixin, unittest.TestCase):
    def setUp(self):
        self._quiet_externals()

    def test_non_topic_returns_empty(self):
        self.assertEqual(pb.build_product_context("how is the weather"), [])

    def test_context_lines_capped_and_shaped(self):
        lines = pb.build_product_context("under $200 sony lens", limit=3)
        self.assertLessEqual(len(lines), 3)
        self.assertIn("est_price_usd", lines[0])
        self.assertIn("requested_mount: Sony E", lines[0])
        self.assertIn("budget_cap: 200", lines[0])

    def test_epic_family_appends_series_lines(self):
        lines = pb.build_product_context("epic anamorphic 电影镜头", limit=5)
        self.assertTrue(any("series_url" in line for line in lines))


class BehaviorAndSubintentTests(unittest.TestCase):
    def test_behavior_mode_buckets(self):
        self.assertEqual(pb._product_behavior_mode("budget"), "photography")
        self.assertEqual(pb._product_behavior_mode("links"), "gear")
        self.assertEqual(pb._product_behavior_mode("catalog"), "pet")

    def test_subintent_priority(self):
        self.assertEqual(
            pb._classify_product_subintent(lowered="e", user_text="e", family=None, mount_only=True, has_products=True),
            "mount",
        )
        self.assertEqual(
            pb._classify_product_subintent(lowered="a vs b", user_text="a vs b", family=None, mount_only=False, has_products=True),
            "comparison",
        )
        self.assertEqual(
            pb._classify_product_subintent(lowered="specs?", user_text="specs?", family=None, mount_only=False, has_products=True),
            "specs",
        )
        self.assertEqual(
            pb._classify_product_subintent(lowered="give link", user_text="give link", family=None, mount_only=False, has_products=True),
            "links",
        )
        self.assertEqual(
            pb._classify_product_subintent(lowered="under $300", user_text="under $300", family=None, mount_only=False, has_products=True),
            "budget",
        )
        self.assertEqual(
            pb._classify_product_subintent(lowered="recommend", user_text="recommend", family=None, mount_only=False, has_products=True),
            "recommendation",
        )
        self.assertEqual(
            pb._classify_product_subintent(lowered="x", user_text="x", family=None, mount_only=False, has_products=False),
            "catalog",
        )


class GetViaProductReplyTests(PatchMixin, unittest.TestCase):
    def setUp(self):
        self._quiet_externals()

    def test_competitor_guard_short_circuits(self):
        self._patch(pb, "handle_external_competitor_query", lambda text: {"title": "compet", "text": "t"})
        out = pb.get_via_product_reply("sony 50mm vs viltrox")
        self.assertEqual(out["title"], "compet")

    def test_non_product_topic_returns_none(self):
        self.assertIsNone(pb.get_via_product_reply("tell me a story"))

    def test_family_guide_reply_zh(self):
        out = pb.get_via_product_reply("介绍一下 air 系列")
        self.assertEqual(out["product_subintent"], "family_guide")
        self.assertEqual(out["behavior_mode"], "photography")
        self.assertEqual(out["session_state_patch"]["last_family_key"], "air")

    def test_epic_series_links_reply_locks_ai(self):
        out = pb.get_via_product_reply("epic anamorphic 官网链接")
        self.assertEqual(out["product_subintent"], "links")
        self.assertTrue(out["lock_ai_override"])
        self.assertIn("EPIC", out["text"] + str(out))

    def test_mount_reply_for_short_mount_message(self):
        session = {"last_product_labels": ["AF 50mm F2.0 Air FF"], "last_user_language": "en"}
        out = pb.get_via_product_reply("e", session_state=session)
        self.assertEqual(out["product_subintent"], "mount")
        self.assertEqual(out["behavior_mode"], "gear")
        self.assertIn("Sony E", out["text"])
        self.assertEqual(out["session_state_patch"]["last_mount_hint"], "sony_e")

    def test_specs_links_comparison_and_recommendation(self):
        specs = pb.get_via_product_reply("50mm 参数如何")
        self.assertEqual(specs["product_subintent"], "specs")
        self.assertTrue(specs["lock_ai_override"])

        links = pb.get_via_product_reply("give me the 50mm link")
        self.assertEqual(links["product_subintent"], "links")
        self.assertIn(STORE_URL, links["text"])

        comp = pb.get_via_product_reply("50mm vs 85mm 哪个好")
        self.assertEqual(comp["product_subintent"], "comparison")
        self.assertFalse(comp["lock_ai_override"])

        rec = pb.get_via_product_reply("推荐一支镜头")
        self.assertEqual(rec["product_subintent"], "recommendation")
        self.assertEqual(rec["title"], "唯卓仕推荐")

    def test_budget_subintent_with_cap_in_text(self):
        out = pb.get_via_product_reply("预算300 求推荐镜头")
        self.assertEqual(out["product_subintent"], "budget")
        self.assertIn("$300", out["text"])
        self.assertEqual(out["session_state_patch"]["last_budget_hint"], 300)

    def test_state_patch_carries_products(self):
        out = pb.get_via_product_reply("推荐一支镜头")
        patch = out["session_state_patch"]
        self.assertEqual(len(patch["last_product_labels"]), 3)
        self.assertEqual(patch["last_product_summary"], patch["last_product_labels"][0])


if __name__ == "__main__":
    unittest.main()
