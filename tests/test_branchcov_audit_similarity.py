"""分支覆盖冲刺·services/audit/similarity.py — 品牌/产品检测与风控评分分支。

覆盖:classify_product 置信度梯度、parse_gear_from_caption 三段解析、
detect_viltrox 状态机、compute_risk 各降分分支、campaign 评分 FB 纯图封顶。

缺 import math 的历史缺陷已修(2026-08-30),math 路径由
MathImportRegressionTests 正向锁定返回数值。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.audit import similarity as s  # noqa: E402


def _patch_dynamic_sources():
    """把学习词表与动态规则钉死为空,保证 classify_product 纯走内置规则。"""
    patches = []
    try:
        from app.services.audit import learning

        patches.append((learning, "get_all_learned_keywords", learning.get_all_learned_keywords))
        learning.get_all_learned_keywords = lambda: {}
    except Exception:
        pass
    try:
        from app.db.repositories import knowledge

        patches.append((knowledge, "list_product_knowledge_rules", knowledge.list_product_knowledge_rules))
        knowledge.list_product_knowledge_rules = lambda limit=300: []
    except Exception:
        pass
    return patches


def _unpatch(patches):
    for mod, name, original in patches:
        setattr(mod, name, original)


class ClassifyProductTests(unittest.TestCase):
    def setUp(self):
        self._patches = _patch_dynamic_sources()

    def tearDown(self):
        _unpatch(self._patches)

    def test_two_keyword_hits_is_high_confidence(self):
        out = s.classify_product("shot with the viltrox lab 35 aka 35mm lab lens")
        self.assertEqual(out["series"], "LAB")
        self.assertEqual(out["label"], "AF 35mm F1.2 LAB")
        self.assertEqual(out["confidence"], "high")
        self.assertGreaterEqual(len(out["evidence"]), 2)

    def test_single_hit_is_medium_confidence(self):
        out = s.classify_product("testing the pro50 today")
        self.assertEqual(out["confidence"], "medium")
        self.assertEqual(out["evidence"], ["pro50"])

    def test_brand_only_falls_back_to_low(self):
        out = s.classify_product("big fan of viltrox glass in general")
        self.assertEqual(out["series"], "VILTROX")
        self.assertEqual(out["label"], "Brand detected but no exact product")
        self.assertEqual(out["confidence"], "low")
        self.assertEqual(out["evidence"], [])

    def test_chinese_brand_alias_also_low(self):
        out = s.classify_product("唯卓仕 的新品")
        self.assertEqual(out["confidence"], "low")

    def test_no_signal_is_none(self):
        out = s.classify_product("cooking pasta tonight")
        self.assertEqual(
            out, {"series": "", "label": "", "confidence": "none", "evidence": []}
        )

    def test_learned_keywords_extend_builtin_rule(self):
        try:
            from app.services.audit import learning
        except Exception:
            self.skipTest("learning module unavailable in hermetic env")
        original = learning.get_all_learned_keywords
        learning.get_all_learned_keywords = lambda: {"AF 35mm F1.2 LAB": ["mysecretalias"]}
        try:
            out = s.classify_product("using mysecretalias for tonight")
        finally:
            learning.get_all_learned_keywords = original
        self.assertEqual(out["label"], "AF 35mm F1.2 LAB")
        self.assertEqual(out["evidence"], ["mysecretalias"])
        self.assertEqual(out["confidence"], "medium")


class ParseGearFromCaptionTests(unittest.TestCase):
    def test_empty_text_returns_empty_dict(self):
        self.assertEqual(s.parse_gear_from_caption(""), {})
        self.assertEqual(s.parse_gear_from_caption(None), {})

    def test_camera_label_with_brand_detection(self):
        out = s.parse_gear_from_caption("Camera: Sony FX3")
        self.assertEqual(out["camera_body"], "Sony FX3")
        self.assertEqual(out["camera_brand"], "Sony")
        self.assertEqual(out["gear_combo"], "Sony FX3")

    def test_camera_mention_cleanup_leaves_brandless_body(self):
        out = s.parse_gear_from_caption("Camera: @sonyalpha 6700")
        self.assertEqual(out["camera_body"], "6700")
        self.assertIsNone(out["camera_brand"])

    def test_overlong_camera_string_is_dropped(self):
        out = s.parse_gear_from_caption("Camera: Sony " + "x" * 60)
        self.assertIsNone(out["camera_body"])
        self.assertIsNone(out["camera_brand"])

    def test_viltrox_lens_via_mention_and_prefix(self):
        out = s.parse_gear_from_caption("Lens: @viltrox.usa 27mm f/1.2")
        self.assertEqual(out["viltrox_lens"], "Viltrox 27mm f/1.2")
        self.assertIsNone(out["other_lens"])
        self.assertEqual(out["gear_combo"], "Viltrox 27mm f/1.2")

    def test_non_viltrox_lens_lands_in_other_lens(self):
        out = s.parse_gear_from_caption("Lens: Sigma 18-300 zoom")
        self.assertIsNone(out["viltrox_lens"])
        self.assertEqual(out["other_lens"], "Sigma 18-300 zoom")
        self.assertEqual(out["gear_combo"], "Sigma 18-300 zoom")

    def test_already_prefixed_viltrox_not_doubled(self):
        out = s.parse_gear_from_caption("Lens: Viltrox 75mm")
        self.assertEqual(out["viltrox_lens"], "Viltrox 75mm")

    def test_inline_combo_pattern(self):
        out = s.parse_gear_from_caption("sony a6700 + viltrox 27mm f1.2 magic")
        self.assertEqual(out["camera_brand"], "sony")
        self.assertIn("a6700", out["camera_body"])
        self.assertIn("27mm", out["viltrox_lens"])
        self.assertIn(" + ", out["gear_combo"])

    def test_combo_pattern_skipped_when_labels_already_matched(self):
        out = s.parse_gear_from_caption("Camera: Sony FX3, Lens: Viltrox 75mm")
        self.assertEqual(out["camera_body"], "Sony FX3")
        self.assertEqual(out["viltrox_lens"], "Viltrox 75mm")
        self.assertEqual(out["gear_combo"], "Sony FX3 + Viltrox 75mm")


class DetectGearMentionsTests(unittest.TestCase):
    def test_mentions_are_deduped_and_sorted(self):
        out = s.detect_gear_mentions("Sony sony FX3 with 85mm and 13mm glass")
        self.assertEqual(out["camera_mentions"], ["fx3", "sony"])
        self.assertEqual(out["lens_mentions"], ["13mm", "85mm"])

    def test_no_mentions(self):
        out = s.detect_gear_mentions("just vibes")
        self.assertEqual(out, {"camera_mentions": [], "lens_mentions": []})


class DetectViltroxTests(unittest.TestCase):
    def test_brand_plus_product_confirms(self):
        out = s.detect_viltrox("viltrox lab 35 review", {})
        self.assertEqual(out["status"], "confirmed")
        self.assertTrue(out["confirmed"])
        self.assertTrue(out["auto_flags"]["logo"])
        self.assertTrue(out["auto_flags"]["product"])
        self.assertTrue(out["auto_flags"]["review"])

    def test_brand_plus_manual_hint_confirms(self):
        out = s.detect_viltrox("viltrox cinematic short", {"logo": True})
        self.assertEqual(out["status"], "confirmed")
        self.assertIn("Manual mark: Logo", out["evidence"])

    def test_product_only_is_suspected(self):
        out = s.detect_viltrox("the lab 35 has crazy bokeh", {})
        self.assertEqual(out["status"], "suspected")
        self.assertFalse(out["confirmed"])

    def test_hint_only_is_suspected(self):
        out = s.detect_viltrox("nice colors here", {"voice": True})
        self.assertEqual(out["status"], "suspected")
        self.assertIn("Manual mark: Voice mention", out["evidence"])

    def test_nothing_is_not_detected_with_honest_evidence(self):
        out = s.detect_viltrox("cat video", {})
        self.assertEqual(out["status"], "not_detected")
        self.assertEqual(out["evidence"], ["No strong Viltrox evidence found"])
        self.assertEqual(
            out["auto_flags"], {"logo": False, "product": False, "voice": False, "review": False}
        )

    def test_double_brand_with_content_tag_confirms(self):
        out = s.detect_viltrox("viltrox 唯卓仕 street portrait session", {})
        self.assertEqual(out["status"], "confirmed")
        self.assertIn("Content context: Photography", out["evidence"])


class SpamAnalysisTests(unittest.TestCase):
    def test_empty_comments_short_circuit(self):
        self.assertEqual(
            s.analyze_comments_for_spam([]),
            {"spam_ratio": 0.0, "spam_hits": [], "spam_count": 0},
        )

    def test_ratio_and_matched_keywords(self):
        out = s.analyze_comments_for_spam(["DM me for crypto", "lovely shot", "check my page"])
        self.assertEqual(out["spam_count"], 2)
        self.assertEqual(out["spam_ratio"], 0.667)
        self.assertEqual(out["spam_hits"][0]["matched"], ["dm me", "crypto"])

    def test_hits_truncated_to_five(self):
        out = s.analyze_comments_for_spam(["telegram plz"] * 8)
        self.assertEqual(out["spam_count"], 8)
        self.assertEqual(len(out["spam_hits"]), 5)


class ComputeRiskTests(unittest.TestCase):
    def _metrics(self, **over):
        base = {"views": 10000, "likes": 500, "comments": 100, "shares": 50, "favorites": 10}
        base.update(over)
        return base

    def _avail(self, **over):
        base = {"views": True, "likes": True, "comments": True, "shares": True, "favorites": True}
        base.update(over)
        return base

    def _no_spam(self):
        return {"spam_ratio": 0.0, "spam_count": 0}

    def test_healthy_metrics_report_no_anomaly(self):
        out = s.compute_risk(self._metrics(), self._avail(), self._no_spam())
        self.assertEqual(out, {"risk_score": 0, "penalty": 0, "reasons": ["No obvious anomaly"]})

    def test_each_low_rate_branch_accumulates(self):
        out = s.compute_risk(
            self._metrics(likes=10, comments=5, shares=1),
            self._avail(),
            self._no_spam(),
        )
        self.assertEqual(out["risk_score"], 60)
        self.assertEqual(out["penalty"], 48)
        self.assertEqual(
            out["reasons"], ["Low like rate", "Low comment rate", "Low share rate"]
        )

    def test_high_save_rate_branch(self):
        out = s.compute_risk(self._metrics(favorites=5000), self._avail(), self._no_spam())
        self.assertEqual(out["risk_score"], 10)
        self.assertEqual(out["reasons"], ["Unusually high save rate"])

    def test_spam_needs_both_ratio_and_count(self):
        spam_light = {"spam_ratio": 0.5, "spam_count": 2}
        out = s.compute_risk(self._metrics(), self._avail(), spam_light)
        self.assertEqual(out["risk_score"], 0)
        spam_heavy = {"spam_ratio": 0.3, "spam_count": 3}
        out = s.compute_risk(self._metrics(), self._avail(), spam_heavy)
        self.assertEqual(out["risk_score"], 20)
        self.assertIn("Comment section may contain spam / fake engagement", out["reasons"])

    def test_views_unavailable_skips_rate_checks(self):
        out = s.compute_risk(
            self._metrics(likes=0, comments=0, shares=0),
            self._avail(views=False),
            self._no_spam(),
        )
        self.assertEqual(out["risk_score"], 0)

    def test_small_views_below_5000_never_penalized(self):
        out = s.compute_risk(
            self._metrics(views=1000, likes=0, comments=0, shares=0),
            self._avail(),
            self._no_spam(),
        )
        self.assertEqual(out["risk_score"], 0)


class CampaignScoreTests(unittest.TestCase):
    def test_not_detected_scores_zero(self):
        out = s.compute_campaign_score({}, {}, False, [])
        self.assertEqual(
            out,
            {"content_score": 0, "campaign_interaction_score": 0, "raw_score": 0, "final_score": 0},
        )

    def test_facebook_image_only_capped_at_20(self):
        out = s.compute_campaign_score(
            {"likes": 500, "comments": 100},
            {"likes": True, "comments": True},
            True,
            [],
            platform="Facebook",
            video_analysis={},
        )
        self.assertEqual(out["final_score"], 20)
        self.assertEqual(out["content_score"], 10)
        self.assertEqual(out["campaign_interaction_score"], 10)
        self.assertEqual(out["capped_reason"], "Facebook image-only post — max 20pts")

    def test_facebook_image_only_small_interaction(self):
        out = s.compute_campaign_score(
            {"likes": 20, "comments": 2},
            {"likes": True, "comments": True},
            True,
            [],
            platform="Facebook",
            video_analysis=None,
        )
        # min(10, 20//10 + 2//2) = 3 -> raw = 13
        self.assertEqual(out["campaign_interaction_score"], 3)
        self.assertEqual(out["final_score"], 13)

    def test_facebook_with_video_content_type_not_capped(self):
        # 有 Video 标签 -> 不吃纯图封顶,走主路径。
        out = s.compute_campaign_score(
            {"views": 100, "likes": 1, "comments": 1, "shares": 0, "favorites": 0},
            {"views": True},
            True,
            ["Video"],
            platform="Facebook",
            video_analysis={},
        )
        self.assertNotIn("capped_reason", out)
        # content_score = min(100, 20 + 12 + 30) = 62;
        # interaction_weight = 100/1000 = 0.1 -> floor(0.1/5) = 0;
        # raw = min(400, 20 + 30 + 62 + 0) = 112。
        self.assertEqual(out["content_score"], 62)
        self.assertEqual(out["campaign_interaction_score"], 0)
        self.assertEqual(out["final_score"], 112)


class MathImportRegressionTests(unittest.TestCase):
    """回归锁:模块曾缺 import math,math 路径一调就 NameError。"""

    def test_creator_score_math_path_returns_number(self):
        score = s.compute_creator_score(
            {"views": 1000, "likes": 100, "comments": 10, "shares": 0, "favorites": 0},
            {"views": True, "likes": True, "comments": True, "shares": True, "favorites": True},
            risk_score=0,
        )
        self.assertIsInstance(score, int)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)
        # log10(1001)*8 + log10(101)*18 + log10(11)*22 ≈ 82.99 -> round = 83
        self.assertEqual(score, 83)

    def test_campaign_score_math_path_returns_numbers(self):
        out = s.compute_campaign_score(
            {"views": 5000, "likes": 200, "comments": 30, "shares": 5, "favorites": 10},
            {"views": True, "likes": True, "comments": True, "shares": True, "favorites": True},
            True,
            ["Video", "Review"],
            platform="YouTube",
            video_analysis={"uploaded": True},
        )
        for key in ("content_score", "campaign_interaction_score", "raw_score", "final_score"):
            self.assertIsInstance(out[key], int)
        # interaction_weight = 5 + 200 + 180 + 50 + 80 = 515 -> floor(515/5) = 103
        self.assertEqual(out["campaign_interaction_score"], 103)


if __name__ == "__main__":
    unittest.main()
