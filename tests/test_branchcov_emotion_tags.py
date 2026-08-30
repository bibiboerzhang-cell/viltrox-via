"""分支覆盖冲刺·kol/emotion_tags.py — 词表分类器/聚合/回打器/画像的分支与降级路径。

纯词表零成本模块:分类器与聚合是纯函数;回打器/画像用 fake conn 断言 SQL 副作用。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.kol import emotion_tags as et  # noqa: E402


class FakeCursor:
    def __init__(self, rows: list | None = None, row: Any = None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class FakeDb:
    def __init__(self, responders: list[tuple[str, Any]] | None = None):
        self.responders = responders or []
        self.executed: list[tuple[str, tuple]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple = ()):  # noqa: ANN001
        self.executed.append((sql, params))
        for fragment, resp in self.responders:
            if fragment in sql:
                return resp
        return FakeCursor()

    def commit(self):
        self.commits += 1


class HelperTests(unittest.TestCase):
    def test_as_dict_and_as_list_reject_wrong_types(self):
        self.assertEqual(et._as_dict([1]), {})
        self.assertEqual(et._as_dict({"a": 1}), {"a": 1})
        self.assertEqual(et._as_list("x"), [])
        self.assertEqual(et._as_list([1]), [1])

    def test_loads_dual_mode(self):
        self.assertEqual(et._loads({"a": 1}), {"a": 1})
        self.assertEqual(et._loads('["x"]'), ["x"])
        self.assertIsNone(et._loads("{broken"))
        self.assertIsNone(et._loads(123))
        self.assertIsNone(et._loads(None))

    def test_text_normalizes_whitespace_and_truncates(self):
        self.assertEqual(et._text("  a\x00b\n c  "), "a b c")
        self.assertEqual(et._text(None), "")
        self.assertEqual(et._text("x" * 500, limit=4), "xxxx")

    def test_int_or_none(self):
        self.assertEqual(et._int_or_none("7"), 7)
        self.assertIsNone(et._int_or_none(""))
        self.assertIsNone(et._int_or_none(None))
        self.assertIsNone(et._int_or_none("seven"))

    def test_term_pattern_english_word_boundary_vs_cjk_substring(self):
        pat = et._term_pattern("wow")
        self.assertIsNotNone(pat)
        self.assertIsNotNone(pat.search("that was wow indeed"))
        self.assertIsNone(pat.search("wowzers"))
        # 中文词无 ASCII 字母数字 → 走子串(pattern None)
        self.assertIsNone(et._term_pattern("震撼"))
        # 纯标点也走子串
        self.assertIsNone(et._term_pattern("?"))
        # 缓存命中路径:第二次取同词返回同对象
        self.assertIs(et._term_pattern("wow"), pat)

    def test_match_hits_caps_at_five(self):
        terms = tuple(f"kw{i}" for i in range(8))
        blob = " ".join(terms)
        hits = et._match_hits(blob, terms)
        self.assertEqual(len(hits), 5)

    def test_flatten_strings_recurses_and_caps(self):
        out: list[str] = []
        et._flatten_strings({"a": ["x", {"b": "y"}], "c": "z", "n": 5}, out)
        self.assertEqual(sorted(out), ["x", "y", "z"])
        capped: list[str] = []
        et._flatten_strings(["s"] * 100, capped, cap=3)
        self.assertEqual(len(capped), 3)


class ClassifyEmotionTextTests(unittest.TestCase):
    def test_no_signal_is_neutral_medium_unclassified(self):
        out = et.classify_emotion_text("nothing interesting here", "opening shot")
        self.assertEqual(out["arousal"], "medium")
        self.assertEqual(out["valence"], "neutral")
        self.assertEqual(out["quadrant"], "medium_arousal_neutral")
        self.assertEqual(out["gear_emotions"], [])
        self.assertIsNone(out["primary_gear_emotion"])
        self.assertFalse(out["awe"])
        self.assertEqual(out["hook_type"], "unclassified")
        self.assertFalse(out["has_cart"])
        self.assertEqual(out["confidence"], 0.2)
        self.assertEqual(out["matched_terms"], {})

    def test_high_arousal_wins_tie_and_positive_valence(self):
        out = et.classify_emotion_text("震撼 又 平淡 但 好看", "")
        self.assertEqual(out["arousal"], "high")  # 平手判 high
        self.assertEqual(out["valence"], "positive")
        self.assertIn("arousal_high", out["matched_terms"])
        self.assertIn("arousal_low", out["matched_terms"])

    def test_low_arousal_and_negative_valence(self):
        out = et.classify_emotion_text("整体乏味 且令人失望", "")
        self.assertEqual(out["arousal"], "low")
        self.assertEqual(out["valence"], "negative")

    def test_mixed_valence(self):
        out = et.classify_emotion_text("画面好看 但结尾翻车", "")
        self.assertEqual(out["valence"], "mixed")

    def test_negation_scrub_removes_negated_negative(self):
        out = et.classify_emotion_text("观众几乎没有反感", "")
        self.assertEqual(out["valence"], "neutral")
        # 未被否定包裹时同词应命中
        out2 = et.classify_emotion_text("观众相当反感", "")
        self.assertEqual(out2["valence"], "negative")

    def test_gear_emotions_multi_and_primary_by_hit_count(self):
        blob = "做工 精致 金属 手感 好, 还能 解锁 新玩法"
        out = et.classify_emotion_text(blob, "")
        self.assertEqual(set(out["gear_emotions"]), {"capability", "craft"})
        self.assertEqual(out["primary_gear_emotion"], "craft")  # craft 命中 4 > capability 2

    def test_awe_detection(self):
        out = et.classify_emotion_text("无人机视角 拍出 银河", "")
        self.assertTrue(out["awe"])
        self.assertIn("awe", out["matched_terms"])

    def test_hook_priority_question_beats_pattern_interrupt(self):
        out = et.classify_emotion_text("", "开场反转还抛出问题?")
        self.assertEqual(out["hook_type"], "question")
        self.assertNotIn("hook_pattern_interrupt", out["matched_terms"])

    def test_hook_each_type_detected(self):
        self.assertEqual(et.classify_emotion_text("", "开场大反转")["hook_type"], "pattern_interrupt")
        self.assertEqual(et.classify_emotion_text("", "before and after showcase")["hook_type"], "result_first")
        self.assertEqual(et.classify_emotion_text("", "this is the best lens")["hook_type"], "assertion")

    def test_cart_terms_read_from_both_blobs(self):
        out = et.classify_emotion_text("正片内容", "link in bio for discount")
        self.assertTrue(out["has_cart"])

    def test_confidence_caps_at_09(self):
        blob = "震撼 惊艳 炸裂 疯狂 上头 好看 心动 种草 认可 值得 做工 工艺 用料 金属 手感 解锁 能拍出 升级 越级 媲美"
        out = et.classify_emotion_text(blob, "before and after ?")
        self.assertEqual(out["confidence"], 0.9)


class AggregateTagsTests(unittest.TestCase):
    def test_empty_aggregation_is_honest(self):
        out = et._aggregate_tags([])
        self.assertEqual(out["sample_size"], 0)
        self.assertIsNone(out["awe_rate"])
        self.assertIsNone(out["has_cart_rate"])
        self.assertIsNone(out["avg_confidence"])

    def test_distribution_counts_and_rates(self):
        tags = [
            {
                "arousal": "high", "valence": "positive", "quadrant": "high_arousal_positive",
                "gear_emotions": ["craft", "upgrade"], "primary_gear_emotion": "craft",
                "hook_type": "question", "awe": True, "has_cart": True, "confidence": 0.5,
            },
            {
                "arousal": "low", "valence": "negative", "quadrant": "low_arousal_negative",
                "gear_emotions": ["craft"], "primary_gear_emotion": "craft",
                "hook_type": "nonsense", "awe": False, "has_cart": False, "confidence": "bad",
            },
            {
                "arousal": "weird", "valence": "", "quadrant": "high_arousal_positive",
                "gear_emotions": "notalist", "primary_gear_emotion": None,
                "hook_type": "assertion", "awe": False, "has_cart": False,
            },
        ]
        out = et._aggregate_tags(tags)
        self.assertEqual(out["sample_size"], 3)
        self.assertEqual(out["arousal"]["high"], 1)
        self.assertEqual(out["arousal"]["low"], 1)
        self.assertEqual(out["valence"]["positive"], 1)
        self.assertEqual(out["quadrant"]["high_arousal_positive"], 2)
        self.assertEqual(out["gear_emotions"]["craft"], 2)
        self.assertEqual(out["gear_emotions"]["upgrade"], 1)
        self.assertEqual(out["primary_gear_emotion"], {"craft": 2})
        self.assertEqual(out["hook_type"]["question"], 1)
        self.assertEqual(out["hook_type"]["assertion"], 1)
        # "nonsense" 不在白名单不计
        self.assertNotIn("nonsense", out["hook_type"])
        self.assertEqual(out["awe_count"], 1)
        self.assertEqual(out["awe_rate"], 0.333)
        self.assertEqual(out["avg_confidence"], 0.5)  # "bad" 被容错丢弃


class RowBlobTests(unittest.TestCase):
    def test_is_current_tags_requires_method_and_lexicon(self):
        good = {"method": et.METHOD, "lexicon_version": et.LEXICON_VERSION}
        self.assertTrue(et._is_current_tags(good))
        self.assertTrue(et._is_current_tags(__import__("json").dumps(good)))
        self.assertFalse(et._is_current_tags({"method": et.METHOD, "lexicon_version": "old"}))
        self.assertFalse(et._is_current_tags(None))
        self.assertFalse(et._is_current_tags("garbage"))

    def test_row_blobs_compose_and_lowercase(self):
        row = {
            "title": "Epic REVIEW",
            "content_summary": "Summary Here",
            "key_hook": "The HOOK",
            "layer2": '{"feeling": "Blown AWAY", "nested": ["so cool"]}',
            "first_scene": '{"what": "Sunrise shot", "why_it_matters": "sets tone"}',
        }
        full, hook = et._row_blobs(row)
        self.assertIn("epic review", full)
        self.assertIn("blown away", full)
        self.assertIn("so cool", full)
        self.assertIn("summary here", full)
        self.assertIn("the hook", hook)
        self.assertIn("sunrise shot sets tone", hook)

    def test_row_blobs_missing_pieces_stay_absent(self):
        full, hook = et._row_blobs({})
        self.assertEqual(full, "")
        self.assertEqual(hook, "")


class TagAnalyzedVideosTests(unittest.TestCase):
    def _row(self, row_id=1, **over):
        base = {
            "row_id": row_id,
            "kol_pool_id": 5,
            "source_evidence_id": 10 + row_id,
            "existing_tags": None,
            "content_summary": "震撼 做工 精彩",
            "first_scene": None,
            "key_hook": "before and after",
            "title": "Lens Review",
            "layer2": None,
        }
        base.update(over)
        return base

    def test_dry_run_counts_without_writing(self):
        rows = [
            self._row(1),
            self._row(2, existing_tags={"method": et.METHOD, "lexicon_version": et.LEXICON_VERSION}),
            self._row(3, content_summary="", key_hook="", title="", layer2=None),
        ]
        db = FakeDb([("FROM vkpi_kol_llm_deep_analysis_results", FakeCursor(rows=rows))])
        out = et.tag_analyzed_videos(dry_run=True, conn=db)
        self.assertEqual(out["status"], "dry_run")
        self.assertEqual(out["scanned"], 3)
        self.assertEqual(out["would_tag"], 1)
        self.assertEqual(out["tagged_written"], 0)
        self.assertEqual(out["skipped_existing"], 1)
        self.assertEqual(out["skipped_no_text"], 1)
        self.assertFalse(out["llm_calls"])
        self.assertEqual(len(out["sample"]), 1)
        update_sqls = [sql for sql, _ in db.executed if "UPDATE" in sql]
        self.assertEqual(update_sqls, [])
        self.assertEqual(db.commits, 1)

    def test_force_retags_existing(self):
        rows = [self._row(1, existing_tags={"method": et.METHOD, "lexicon_version": et.LEXICON_VERSION})]
        db = FakeDb([("FROM vkpi_kol_llm_deep_analysis_results", FakeCursor(rows=rows))])
        out = et.tag_analyzed_videos(dry_run=True, force=True, conn=db)
        self.assertEqual(out["would_tag"], 1)
        self.assertEqual(out["skipped_existing"], 0)

    def test_write_path_merges_single_key(self):
        rows = [self._row(1), self._row(2)]
        db = FakeDb([("FROM vkpi_kol_llm_deep_analysis_results", FakeCursor(rows=rows))])
        out = et.tag_analyzed_videos(dry_run=False, conn=db)
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["tagged_written"], 2)
        updates = [(sql, params) for sql, params in db.executed if "UPDATE vkpi_kol_llm_deep_analysis_results" in sql]
        self.assertEqual(len(updates), 2)
        sql, params = updates[0]
        self.assertIn("|| ?::jsonb", sql)
        self.assertIn(et.EMOTION_TAGS_KEY, params[0])
        self.assertEqual(params[1], 1)

    def test_row_without_id_is_dropped(self):
        rows = [self._row(1) | {"row_id": None}]
        db = FakeDb([("FROM vkpi_kol_llm_deep_analysis_results", FakeCursor(rows=rows))])
        out = et.tag_analyzed_videos(dry_run=True, conn=db)
        self.assertEqual(out["would_tag"], 0)

    def test_limit_appended_to_sql(self):
        db = FakeDb([("FROM vkpi_kol_llm_deep_analysis_results", FakeCursor(rows=[]))])
        et.tag_analyzed_videos(dry_run=True, limit=7, conn=db)
        self.assertIn("LIMIT 7", db.executed[0][0])


class VideoEmotionProfileTests(unittest.TestCase):
    _POOL = {"id": 3, "handle": "h", "display_name": "D", "platform": "youtube"}

    def test_unknown_kol_raises_lookup(self):
        db = FakeDb([("FROM vkpi_kol_pool", FakeCursor(row=None))])
        with self.assertRaises(LookupError):
            et.video_emotion_profile(3, conn=db)
        self.assertEqual(db.commits, 1)  # 纯读也 commit

    def test_no_analyzed_videos_is_empty_without_triggering_analysis(self):
        db = FakeDb([
            ("FROM vkpi_kol_pool", FakeCursor(row=dict(self._POOL))),
            ("WHERE r.kol_pool_id", FakeCursor(rows=[])),
        ])
        out = et.video_emotion_profile(3, conn=db)
        self.assertEqual(out["status"], "empty")
        self.assertEqual(out["coverage"], {"analyzed": 0, "tagged": 0})
        self.assertFalse(out["llm_calls"])
        self.assertIn("烧钱路径", out["reason"])

    def test_analyzed_but_untagged_points_to_backfill(self):
        rows = [
            {"row_id": 1, "source_evidence_id": 11, "tags": None, "title": "t", "view_count": 5, "content_url": "u"},
        ]
        db = FakeDb([
            ("FROM vkpi_kol_pool", FakeCursor(row=dict(self._POOL))),
            ("WHERE r.kol_pool_id", FakeCursor(rows=rows)),
        ])
        out = et.video_emotion_profile(3, conn=db)
        self.assertEqual(out["status"], "empty")
        self.assertEqual(out["coverage"], {"analyzed": 1, "tagged": 0})
        self.assertIn("backfill", out["reason"])

    def test_ready_path_dedupes_evidence_and_reports_dominant_quadrant(self):
        tags_a = {
            "quadrant": "high_arousal_positive", "arousal": "high", "valence": "positive",
            "gear_emotions": ["craft"], "primary_gear_emotion": "craft",
            "hook_type": "question", "awe": True, "has_cart": False, "confidence": 0.6,
        }
        tags_b = {
            "quadrant": "low_arousal_negative", "arousal": "low", "valence": "negative",
            "gear_emotions": [], "primary_gear_emotion": None,
            "hook_type": "assertion", "awe": False, "has_cart": True, "confidence": 0.4,
        }
        rows = [
            {"row_id": 9, "source_evidence_id": 11, "tags": tags_a, "title": "A", "view_count": 100, "content_url": "u1"},
            # 同 evidence 的旧深析行:必须被去重跳过
            {"row_id": 8, "source_evidence_id": 11, "tags": tags_b, "title": "A-old", "view_count": 100, "content_url": "u1"},
            {"row_id": 7, "source_evidence_id": 12, "tags": tags_a, "title": "B", "view_count": 50, "content_url": "u2"},
            {"row_id": 6, "source_evidence_id": 13, "tags": tags_b, "title": "C", "view_count": 10, "content_url": "u3"},
        ]
        db = FakeDb([
            ("FROM vkpi_kol_pool", FakeCursor(row=dict(self._POOL))),
            ("WHERE r.kol_pool_id", FakeCursor(rows=rows)),
        ])
        out = et.video_emotion_profile(3, conn=db)
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["coverage"], {"analyzed": 3, "tagged": 3})
        self.assertEqual(out["dominant_quadrant"], "high_arousal_positive")
        self.assertEqual(out["distribution"]["quadrant"]["high_arousal_positive"], 2)
        self.assertEqual(len(out["samples"]), 3)
        self.assertEqual(out["samples"][0]["title"], "A")
        self.assertIsInstance(out["playbook_refs"], list)
        self.assertEqual(out["kol"]["handle"], "h")


if __name__ == "__main__":
    unittest.main()
