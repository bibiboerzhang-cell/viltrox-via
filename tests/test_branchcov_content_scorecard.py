"""分支覆盖冲刺·content/content_scorecard.py — 规则册消费判档引擎的档位/闸门/平台轴分支。

阈值一律经模块自身的 _rule_value 现场读规则册(不硬编数字),
构造相对于阈值的输入命中 A/B/C/eliminate/unrated 各档与统计功效闸。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.content import content_scorecard as cs  # noqa: E402

MIN_VIEWS = int(cs._rule_value(cs.RULE_STAT_EXPOSURE))
ENG_ANCHOR = float(cs._rule_value(cs.RULE_ENG_ANCHOR))
TOP_PCT = float(cs._rule_value(cs.RULE_PCT_TOP))
MIN_COHORT = int(cs._rule_value(cs.RULE_STAT_SAMPLE))


class FakeCursor:
    def __init__(self, rows: list | None = None, row: Any = None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, responders: list[tuple[str, Any]] | None = None):
        self.responders = responders or []
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()):  # noqa: ANN001
        self.executed.append((sql, params))
        for fragment, resp in self.responders:
            if fragment in sql:
                return resp
        return FakeCursor()


class SmallHelperTests(unittest.TestCase):
    def test_norm_platform(self):
        self.assertEqual(cs._norm_platform(" TikTok "), "tiktok")
        self.assertEqual(cs._norm_platform("bilibili"), "bilibili")
        self.assertEqual(cs._norm_platform(""), "other")
        self.assertEqual(cs._norm_platform(None), "other")

    def test_completion_tiers_consume_rulebook(self):
        self.assertIsNone(cs._completion_tier(None))
        self.assertEqual(cs._completion_tier(0.75), "viral")
        self.assertEqual(cs._completion_tier(0.6), "strong")
        self.assertEqual(cs._completion_tier(0.4), "average")
        self.assertEqual(cs._completion_tier(0.2), "weak")

    def test_hook_tiers_with_between_anchor_gap(self):
        self.assertIsNone(cs._hook_tier(None))
        self.assertEqual(cs._hook_tier(0.5), "A")
        self.assertEqual(cs._hook_tier(0.2), "fail")
        self.assertEqual(cs._hook_tier(0.35), "between_anchors")

    def test_engagement_rate_refuses_zero_views(self):
        self.assertIsNone(cs._engagement_rate(None, 1, 1, 1))
        self.assertIsNone(cs._engagement_rate(0, 1, 1, 1))
        self.assertEqual(cs._engagement_rate(1000, 30, 10, 0), 0.04)
        self.assertEqual(cs._engagement_rate(1000, None, None, None), 0.0)

    def test_percentile_needs_two_samples(self):
        self.assertIsNone(cs._percentile([100], 100))
        self.assertEqual(cs._percentile([10, 20, 30, 40, 100], 100), 1.0)
        self.assertEqual(cs._percentile([100, 200], 100), 0.0)

    def test_unknown_rule_raises(self):
        with self.assertRaises(LookupError):
            cs._rule("ghost_rule_xyz")
        with self.assertRaises(LookupError):
            cs._ref("ghost_rule_xyz", "applied", "n")

    def test_match_any_case_insensitive(self):
        self.assertEqual(cs._match_any("Great TUTORIAL here", cs.SAVEABLE_TERMS), ["tutorial"])
        self.assertEqual(cs._match_any("nothing", cs.SAVEABLE_TERMS), [])


class GradeGateTests(unittest.TestCase):
    def _cohort_top(self, views: int) -> list[int]:
        return [views // 10] * (MIN_COHORT - 1) + [views]

    def test_missing_views_is_unrated(self):
        out = cs._grade("tiktok", None, 1, 1, 1, [], "")
        self.assertEqual(out["tier"], "unrated")
        self.assertIn("播放数缺失", out["tier_basis"])
        gate_refs = [r for r in out["rule_refs"] if r["role"] == "gate"]
        self.assertEqual(gate_refs[-1]["rule_id"], cs.RULE_STAT_EXPOSURE)

    def test_below_min_exposure_is_unrated(self):
        views = MIN_VIEWS - 1
        out = cs._grade("tiktok", views, 100, 100, 100, self._cohort_top(views), "")
        self.assertEqual(out["tier"], "unrated")
        self.assertIn(str(MIN_VIEWS), out["tier_basis"])

    def test_double_pass_is_tier_a(self):
        views = MIN_VIEWS * 10
        likes = int(views * ENG_ANCHOR)  # 恰到锚
        out = cs._grade("tiktok", views, likes, 0, 0, self._cohort_top(views), "")
        self.assertEqual(out["tier"], "A")
        self.assertEqual(out["signals"]["account_percentile"], 1.0)

    def test_double_fail_bottom_is_eliminate(self):
        views = MIN_VIEWS
        cohort = [views * 100] * (MIN_COHORT - 1) + [views]
        out = cs._grade("tiktok", views, 0, 0, 0, cohort, "")
        self.assertEqual(out["tier"], "eliminate")

    def test_single_pass_is_tier_b(self):
        views = MIN_VIEWS
        likes = int(views * ENG_ANCHOR)
        cohort = [views * 100] * (MIN_COHORT - 1) + [views]  # 分位垫底但互动过锚
        out = cs._grade("tiktok", views, likes, 0, 0, cohort, "")
        self.assertEqual(out["tier"], "B")

    def test_middle_pack_is_tier_c(self):
        views = MIN_VIEWS
        # 分位 0.5(不进顶部不落底部),互动 0 → C
        cohort = [views // 2, views // 2, views * 10, views * 10, views]
        self.assertGreaterEqual(len(cohort), MIN_COHORT)
        out = cs._grade("tiktok", views, 0, 0, 0, cohort, "")
        self.assertEqual(out["tier"], "C")

    def test_small_cohort_drops_percentile_no_eliminate(self):
        views = MIN_VIEWS
        out = cs._grade("tiktok", views, 0, 0, 0, [views], "")
        self.assertEqual(out["tier"], "C")  # 单代理不判淘汰
        self.assertIsNone(out["signals"]["account_percentile"])
        gate_refs = [r for r in out["rule_refs"] if r["role"] == "gate"]
        self.assertEqual(gate_refs[-1]["rule_id"], cs.RULE_STAT_SAMPLE)

    def test_shares_missing_noted_in_engagement_basis(self):
        views = MIN_VIEWS
        out = cs._grade("tiktok", views, 1, 1, None, self._cohort_top(views), "")
        self.assertIn("转发缺失", out["signals"]["engagement_basis"])


class GradePlatformAxesTests(unittest.TestCase):
    def _grade(self, platform, blob=""):
        views = MIN_VIEWS
        cohort = [views] * MIN_COHORT
        return cs._grade(platform, views, 0, 0, 0, cohort, blob)

    def test_tiktok_axes_honestly_unknown(self):
        out = self._grade("tiktok")
        self.assertEqual(out["axes"]["completion"]["status"], "unknown")
        self.assertEqual(out["axes"]["hook_2s"]["status"], "unknown")
        unavailable = [r for r in out["rule_refs"] if r["role"] == "axis_unavailable"]
        self.assertEqual(len(unavailable), len(cs.RULES_TT_COMPLETION) + len(cs.RULES_TT_HOOK))

    def test_instagram_axes_and_comment_share(self):
        views = MIN_VIEWS
        cohort = [views] * MIN_COHORT
        out = cs._grade("instagram", views, 30, 10, 0, cohort, "great tutorial for settings")
        self.assertEqual(out["axes"]["sends"]["status"], "unavailable")
        self.assertEqual(out["signals"]["comment_share_of_interactions"], 0.25)
        self.assertIn("tutorial", out["signals"]["saveable_format_terms"])
        applied = [r["rule_id"] for r in out["rule_refs"] if r["role"] == "applied"]
        self.assertIn(cs.RULE_IG_SAVEABLE, applied)

    def test_instagram_zero_interactions_comment_share_none(self):
        out = self._grade("instagram")
        self.assertIsNone(out["signals"]["comment_share_of_interactions"])
        self.assertEqual(out["signals"]["saveable_format_terms"], [])

    def test_youtube_double_gate_proxy(self):
        out = self._grade("youtube")
        self.assertEqual(out["axes"]["double_gate"]["status"], "proxy")

    def test_other_platform_generic_axis(self):
        out = self._grade("other")
        self.assertEqual(out["axes"]["generic"]["status"], "proxy")


class EmotionBlockTests(unittest.TestCase):
    def test_no_text_refuses_to_tag(self):
        out = cs._emotion_block("")
        self.assertEqual(out["status"], "no_text")

    def test_no_hits_is_unclassified(self):
        out = cs._emotion_block("plain description of a walk")
        self.assertEqual(out["status"], "unclassified")
        self.assertEqual(out["rule_refs"][0]["role"], "axis_unavailable")

    def test_primary_by_most_hits(self):
        text = "震撼 stunning cinematic 大片感 upgrade"
        out = cs._emotion_block(text)
        self.assertEqual(out["status"], "tagged")
        self.assertEqual(out["primary_emotion"], "awe")
        self.assertIn("transformation", out["matched"])
        self.assertLessEqual(len(out["matched"]["awe"]), 4)


class DeepBlobTests(unittest.TestCase):
    def test_blob_composed_from_layer1_and_risk(self):
        dim = {
            "layer1_summary": {"content_summary": "sum", "production_observations": "obs",
                               "brand_exposure": "brand"},
            "risk": {"key_hook": "hook", "final_verdict": "ok"},
        }
        self.assertEqual(cs._deep_text_blob(dim), "sum obs brand hook ok")
        self.assertEqual(cs._deep_text_blob({}), "")


class ScoreVideoTests(unittest.TestCase):
    def test_missing_evidence_raises(self):
        conn = FakeConn([("FROM vkpi_kol_video_evidence", FakeCursor(row=None))])
        with self.assertRaises(LookupError):
            cs.score_video(9, conn=conn)

    def test_ready_path_with_deep_analysis(self):
        views = MIN_VIEWS * 10
        ev = {
            "id": 9, "kol_pool_id": 3, "platform": "TikTok", "title": "T",
            "content_url": "u", "view_count": views,
            "like_count": int(views * ENG_ANCHOR), "comment_count": 0, "share_count": 0,
            "published_at_norm": "2026-05-01",
        }
        cohort_rows = [{"view_count": views // 10}] * (MIN_COHORT - 1) + [{"view_count": views}]
        deep = {
            "llm_dimensions_11": {
                "layer1_summary": {"content_summary": "震撼 stunning footage"},
                "risk": {"key_hook": "hook line", "final_verdict": "good"},
            }
        }
        conn = FakeConn([
            ("WHERE id = ?", FakeCursor(row=ev)),
            ("FROM vkpi_kol_llm_deep_analysis_results", FakeCursor(row=deep)),
            ("is_active = TRUE", FakeCursor(rows=cohort_rows)),
        ])
        out = cs.score_video(9, conn=conn)
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["platform"], "tiktok")
        self.assertEqual(out["tier"], "A")
        self.assertEqual(out["emotion"]["status"], "tagged")
        self.assertTrue(out["deep_analysis"]["present"])
        self.assertEqual(out["deep_analysis"]["key_hook"], "hook line")

    def test_no_deep_row_falls_back_to_title_blob(self):
        ev = {
            "id": 9, "kol_pool_id": None, "platform": "instagram", "title": "quick tutorial",
            "content_url": "", "view_count": None, "like_count": None,
            "comment_count": None, "share_count": None, "published_at_norm": None,
        }
        conn = FakeConn([
            ("WHERE id = ?", FakeCursor(row=ev)),
            ("FROM vkpi_kol_llm_deep_analysis_results", FakeCursor(row=None)),
        ])
        out = cs.score_video(9, conn=conn)
        self.assertEqual(out["tier"], "unrated")
        self.assertEqual(out["emotion"]["status"], "no_text")
        self.assertFalse(out["deep_analysis"]["present"])
        self.assertIn("tutorial", out["signals"]["saveable_format_terms"])


class ScoreChannelPostsTests(unittest.TestCase):
    _CH = {"id": 5, "channel_uid": "cu", "platform": "youtube", "account_handle": "h",
           "account_display_name": "D", "account_url": "u"}

    def _post(self, uid, views, likes=0, snapshot="2026-05-01", **over):
        base = {
            "post_uid": uid, "platform": "youtube", "post_url": f"u/{uid}", "title": uid,
            "posted_at": "2026-04-01", "views": views, "likes": likes, "comments": 0,
            "shares": 0, "snapshot_date": snapshot, "captured_at": snapshot + "T01:00:00Z",
        }
        base.update(over)
        return base

    def test_missing_channel_raises(self):
        conn = FakeConn([("FROM vkpi_employee_channels", FakeCursor(row=None))])
        with self.assertRaises(LookupError):
            cs.score_channel_posts(5, conn=conn)

    def test_no_posts_is_empty(self):
        conn = FakeConn([
            ("FROM vkpi_employee_channels", FakeCursor(row=dict(self._CH))),
            ("FROM vkpi_channel_post_metrics", FakeCursor(rows=[])),
        ])
        out = cs.score_channel_posts(5, conn=conn)
        self.assertEqual(out["status"], "empty")
        self.assertEqual(out["channel"]["platform"], "youtube")

    def test_distribution_dedupes_snapshots_and_counts_tiers(self):
        views_a = MIN_VIEWS * 10
        posts = [
            # p1 最新快照在前;旧快照必须被 post_uid 去重跳过
            self._post("p1", views_a, likes=int(views_a * ENG_ANCHOR), snapshot="2026-05-02"),
            self._post("p1", 1, snapshot="2026-04-01"),
            self._post("p2", MIN_VIEWS // 2),                     # 低于曝光闸 → unrated
            self._post("p3", MIN_VIEWS),                          # 无互动、中游分位 → C
            self._post("p4", MIN_VIEWS),
            self._post("p5", MIN_VIEWS),
            self._post("", MIN_VIEWS),                            # 空 uid 丢弃
        ]
        conn = FakeConn([
            ("FROM vkpi_employee_channels", FakeCursor(row=dict(self._CH))),
            ("FROM vkpi_channel_post_metrics", FakeCursor(rows=posts)),
        ])
        out = cs.score_channel_posts(5, conn=conn)
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["posts_total"], 5)
        dist = {d["tier"]: d["count"] for d in out["distribution"]}
        self.assertEqual(dist["A"], 1)
        self.assertEqual(dist["unrated"], 1)
        self.assertEqual(dist["A"] + dist["B"] + dist["C"] + dist["eliminate"] + dist["unrated"], 5)
        self.assertEqual(out["posts_judged"], 4)
        self.assertEqual(out["examples"]["A"][0]["post_uid"], "p1")
        self.assertEqual(out["examples"]["A"][0]["views"], views_a)
        self.assertEqual(out["latest_snapshot_date"], "2026-05-02")
        # applied 角色的 ref 应压过 gate/unavailable 的同 id 记录
        roles = {r["rule_id"]: r["role"] for r in out["rule_refs"]}
        self.assertEqual(roles.get(cs.RULE_ENG_ANCHOR), "applied")


if __name__ == "__main__":
    unittest.main()
