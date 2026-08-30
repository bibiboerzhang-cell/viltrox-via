"""分支覆盖冲刺·kol/brand_safety.py — 四路信号块的分级阈值/低样本诚实闸/降级路径。

纯读模块:信号块用构造数据直测;守卫 import 的外件(ftc_scan/竞品词表)按需
monkeypatch 制造 error/empty/ready 三态;主入口用 fake conn。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.kol import brand_safety as bs  # noqa: E402


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

    def commit(self):
        pass


class MaxLevelTests(unittest.TestCase):
    def test_ordering_and_unknown_levels(self):
        self.assertEqual(bs._max_level([]), "none")
        self.assertEqual(bs._max_level(["none", "info"]), "info")
        self.assertEqual(bs._max_level(["info", "warn", "none"]), "warn")
        self.assertEqual(bs._max_level(["bogus"]), "none")


class MatchedTermsTests(unittest.TestCase):
    def test_english_word_boundary(self):
        # previews 不命中 review 类词口径:shotgun 不吃 gun
        hits = bs._matched_terms("my shotgun mic review", ("gun",))
        self.assertEqual(hits, [])
        hits = bs._matched_terms("bring a gun to set", ("gun",))
        self.assertEqual(hits, ["gun"])

    def test_cjk_substring(self):
        hits = bs._matched_terms("这事有点争议啊", ("争议",))
        self.assertEqual(hits, ["争议"])


class CommentNegativityTests(unittest.TestCase):
    def _comments(self, texts):
        return [{"comment_text": t} for t in texts]

    def test_no_comments_is_empty(self):
        out = bs._comment_negativity_block([])
        self.assertEqual(out["status"], "empty")
        self.assertEqual(out["level"], "none")

    def test_blank_texts_filtered_to_empty(self):
        out = bs._comment_negativity_block(self._comments(["", "   "]))
        self.assertEqual(out["status"], "empty")

    def test_low_sample_never_escalates(self):
        texts = ["terrible product"] * 5  # 100% 负面但样本 5 < 10
        out = bs._comment_negativity_block(self._comments(texts))
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["level"], "none")
        self.assertIn("样本仅 5", out["note"])
        self.assertEqual(out["negative_count"], 5)

    def test_warn_needs_density_and_count(self):
        texts = ["terrible shot"] * 5 + ["nice"] * 5
        out = bs._comment_negativity_block(self._comments(texts))
        self.assertEqual(out["level"], "warn")
        self.assertEqual(out["negative_density"], 0.5)

    def test_info_band(self):
        texts = ["terrible shot", "awful colors"] + ["nice"] * 14
        out = bs._comment_negativity_block(self._comments(texts))
        self.assertEqual(out["level"], "info")

    def test_clean_comments_stay_none(self):
        texts = ["lovely work"] * 12
        out = bs._comment_negativity_block(self._comments(texts))
        self.assertEqual(out["level"], "none")
        self.assertEqual(out["negative_count"], 0)

    def test_clusters_sorted_with_capped_samples(self):
        texts = ["so blurry footage"] * 3 + ["overpriced imo"] * 2 + ["ok"] * 7
        out = bs._comment_negativity_block(self._comments(texts))
        self.assertEqual(out["clusters"][0]["term"], "blurry")
        self.assertEqual(out["clusters"][0]["count"], 3)
        self.assertEqual(len(out["clusters"][0]["samples"]), 2)


class ControversyBlockTests(unittest.TestCase):
    def _evid(self, eid, title):
        return {"evidence_id": eid, "title": title}

    def test_nothing_to_scan_is_empty(self):
        out = bs._controversy_block([], [])
        self.assertEqual(out["status"], "empty")
        self.assertEqual(out["categories"], [])

    def test_title_hit_is_info_below_three_videos(self):
        out = bs._controversy_block([self._evid(1, "my political rant")], [])
        cats = {c["key"]: c for c in out["categories"]}
        self.assertEqual(cats["politics"]["level"], "info")
        self.assertEqual(cats["politics"]["video_count"], 1)
        self.assertEqual(cats["politics"]["examples"][0]["sources"], ["title"])
        self.assertEqual(out["level"], "info")

    def test_three_videos_same_category_warns(self):
        rows = [self._evid(i, f"protest footage {i}") for i in range(3)]
        out = bs._controversy_block(rows, [])
        cats = {c["key"]: c for c in out["categories"]}
        self.assertEqual(cats["politics"]["level"], "warn")
        self.assertEqual(out["level"], "warn")

    def test_title_and_deep_hit_same_evidence_counted_once(self):
        rows = [self._evid(7, "election special")]
        deep = [{"evidence_id": 7, "title": "election special", "blob": "election talk here"}]
        out = bs._controversy_block(rows, deep)
        cats = {c["key"]: c for c in out["categories"]}
        self.assertEqual(cats["politics"]["video_count"], 1)
        self.assertEqual(
            cats["politics"]["examples"][0]["sources"], ["deep_analysis", "title"]
        )

    def test_deep_only_hit(self):
        deep = [{"evidence_id": None, "title": "t", "blob": "graphic gore scene"}]
        out = bs._controversy_block([], deep)
        cats = {c["key"]: c for c in out["categories"]}
        self.assertEqual(cats["violence"]["video_count"], 1)
        self.assertEqual(cats["violence"]["examples"][0]["sources"], ["deep_analysis"])
        self.assertEqual(out["scanned"], {"titles": 0, "deep_texts": 1})


class CompetitorBindingTests(unittest.TestCase):
    def setUp(self):
        from app.domains.kol import competitor_text as ct

        self._ct = ct
        self._orig = ct.load_competitor_brands
        ct.load_competitor_brands = lambda: {
            "sony": {"keywords": ["sony"]},
            "sigma": {"keywords": ["sigma"]},
        }
        self.addCleanup(lambda: setattr(self._ct, "load_competitor_brands", self._orig))

    def _evid(self, eid, title):
        return {"evidence_id": eid, "title": title}

    def test_no_material_is_empty(self):
        out = bs._competitor_binding_block([], [])
        self.assertEqual(out["status"], "empty")

    def test_no_brand_mentions_is_empty_with_scanned_count(self):
        out = bs._competitor_binding_block([self._evid(1, "nice sunset")], [])
        self.assertEqual(out["status"], "empty")
        self.assertIn("扫描 1 条", out["reason"])

    def test_low_sample_units_never_escalate(self):
        rows = [self._evid(1, "sony lens day"), self._evid(2, "sigma test")]
        out = bs._competitor_binding_block(rows, [])
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["level"], "none")
        self.assertEqual(out["competitor_share"], 1.0)
        self.assertIn("诚实低样本", out["message"])

    def test_warn_share_with_single_brand_concentration(self):
        rows = [self._evid(i, "sony video") for i in range(5)] + [self._evid(9, "viltrox video")]
        out = bs._competitor_binding_block(rows, [])
        self.assertEqual(out["level"], "warn")
        self.assertIn("深度绑定预警", out["message"])
        self.assertIn("单一品牌 sony", out["message"])
        self.assertEqual(out["viltrox_videos"], 1)
        self.assertEqual(out["competitor_units"], 5)

    def test_info_band_share(self):
        rows = (
            [self._evid(i, "sony video") for i in range(3)]
            + [self._evid(10 + i, "viltrox video") for i in range(2)]
        )
        out = bs._competitor_binding_block(rows, [])
        self.assertEqual(out["level"], "info")
        self.assertIn("排他条款", out["message"])

    def test_balanced_share_below_thresholds(self):
        rows = (
            [self._evid(i, "sony video") for i in range(3)]
            + [self._evid(10 + i, "viltrox video") for i in range(4)]
        )
        out = bs._competitor_binding_block(rows, [])
        self.assertEqual(out["level"], "none")
        self.assertIn("未达提示阈值", out["message"])

    def test_deep_blob_merged_into_title_blob(self):
        rows = [self._evid(1, "untitled")]
        deep = [{"evidence_id": 1, "title": "untitled", "blob": "heavy sony talk"}]
        out = bs._competitor_binding_block(rows, deep)
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["competitor_units"], 1)


class DisclosureBlockTests(unittest.TestCase):
    def _patch_ftc(self, impl):
        from app.domains.kol import quality_compliance as qc

        original = qc.ftc_scan
        qc.ftc_scan = impl
        self.addCleanup(lambda: setattr(qc, "ftc_scan", original))

    def test_scan_error_degrades_honestly(self):
        def boom(kol_pool_id, conn=None):
            raise RuntimeError("ftc exploded")

        self._patch_ftc(boom)
        out = bs._disclosure_block(FakeConn(), 3)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["level"], "none")
        self.assertIn("ftc exploded", out["reason"])

    def test_not_ready_scan_is_empty(self):
        self._patch_ftc(lambda kid, conn=None: {"status": "empty", "reason": "no videos"})
        out = bs._disclosure_block(FakeConn(), 3)
        self.assertEqual(out["status"], "empty")
        self.assertEqual(out["reason"], "no videos")

    def test_ready_scan_passthrough_with_level_whitelist(self):
        self._patch_ftc(lambda kid, conn=None: {
            "status": "ready",
            "summary": {"risk_level": "warn", "disclosed": 2, "undisclosed_suspect": 1,
                        "clean": 3, "warn_count": 1},
            "risks": [{"r": i} for i in range(9)],
            "coverage": {"videos": 6},
        })
        out = bs._disclosure_block(FakeConn(), 3)
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["level"], "warn")
        self.assertEqual(len(out["top_risks"]), 5)
        self.assertEqual(out["coverage"], {"videos": 6})

    def test_ready_scan_with_garbage_level_coerced_to_none(self):
        self._patch_ftc(lambda kid, conn=None: {
            "status": "ready", "summary": {"risk_level": "catastrophic"}, "risks": [],
        })
        out = bs._disclosure_block(FakeConn(), 3)
        self.assertEqual(out["level"], "none")


class FrameworkTests(unittest.TestCase):
    def test_external_entry_is_pending_not_clean(self):
        entries = bs._framework(
            {"status": "empty", "categories": []},
            {"status": "empty"},
            {"status": "empty"},
            {"status": "empty"},
        )
        by_key = {e["key"]: e for e in entries}
        self.assertEqual(len(entries), 12)
        mis = by_key["misinformation"]
        self.assertEqual(mis["coverage"], "external_pending")
        self.assertEqual(mis["risk_level"], "none")
        self.assertIn("诚实待接", mis["note"])
        # 非 ready 信号一律 risk none / signal 0
        self.assertEqual(by_key["audience_negativity"]["risk_level"], "none")
        self.assertEqual(by_key["politics"]["risk_level"], "none")

    def test_ready_blocks_propagate_levels(self):
        controversy = {
            "status": "ready",
            "categories": [{"key": "politics", "level": "warn", "video_count": 3}],
        }
        entries = bs._framework(
            controversy,
            {"status": "ready", "level": "info"},
            {"status": "ready", "level": "none"},
            {"status": "ready", "level": "warn"},
        )
        by_key = {e["key"]: e for e in entries}
        self.assertEqual(by_key["politics"]["risk_level"], "warn")
        self.assertEqual(by_key["politics"]["signal_count"], 3)
        self.assertEqual(by_key["disclosure_compliance"]["risk_level"], "info")
        self.assertEqual(by_key["disclosure_compliance"]["signal_count"], 1)
        self.assertEqual(by_key["audience_negativity"]["signal_count"], 0)
        self.assertEqual(by_key["competitor_binding"]["risk_level"], "warn")


class BrandSafetyScanTests(unittest.TestCase):
    def setUp(self):
        # 深析/评论装载与披露块直接钉死,聚焦主入口分支
        self._origs = (bs._load_deep_blobs, bs._load_comments, bs._disclosure_block)
        bs._load_deep_blobs = lambda conn, kid: []  # type: ignore[assignment]
        bs._load_comments = lambda conn, kid: []  # type: ignore[assignment]
        bs._disclosure_block = lambda conn, kid: {"status": "empty", "level": "none"}  # type: ignore[assignment]
        self.addCleanup(self._restore)

    def _restore(self):
        bs._load_deep_blobs, bs._load_comments, bs._disclosure_block = self._origs  # type: ignore[assignment]

    _POOL = {"id": 3, "handle": "h", "display_name": "D", "platform": "youtube"}

    def test_unknown_kol_raises(self):
        conn = FakeConn([("FROM vkpi_kol_pool", FakeCursor(row=None))])
        with self.assertRaises(LookupError):
            bs.brand_safety_scan(3, conn=conn)

    def test_no_material_reports_empty_status(self):
        conn = FakeConn([
            ("FROM vkpi_kol_pool", FakeCursor(row=dict(self._POOL))),
            ("FROM vkpi_kol_video_evidence", FakeCursor(rows=[])),
        ])
        out = bs.brand_safety_scan(3, conn=conn)
        self.assertEqual(out["status"], "empty")
        self.assertEqual(out["risk_level"], "none")
        self.assertEqual(len(out["framework"]), 12)
        self.assertEqual(out["coverage"], {"evidence_count": 0, "deep_analyzed_count": 0, "comments_scanned": 0})

    def test_ready_path_filters_inactive_evidence_and_rolls_up_level(self):
        rows = [
            {"evidence_id": 1, "title": "protest footage a", "content_url": "u", "platform": "yt",
             "view_count": 10, "is_active": 1},
            {"evidence_id": 2, "title": "protest footage b", "content_url": "u", "platform": "yt",
             "view_count": 9, "is_active": 1},
            {"evidence_id": 3, "title": "protest footage c", "content_url": "u", "platform": "yt",
             "view_count": 8, "is_active": 1},
            # BOOLEAN 读回 int 0:必须被 _truthy 过滤
            {"evidence_id": 4, "title": "protest footage d", "content_url": "u", "platform": "yt",
             "view_count": 7, "is_active": 0},
        ]
        conn = FakeConn([
            ("FROM vkpi_kol_pool", FakeCursor(row=dict(self._POOL))),
            ("FROM vkpi_kol_video_evidence", FakeCursor(rows=rows)),
        ])
        out = bs.brand_safety_scan(3, conn=conn)
        self.assertEqual(out["status"], "ready")
        self.assertEqual(out["coverage"]["evidence_count"], 3)
        self.assertEqual(out["risk_level"], "warn")  # 3 条政治标题 → warn
        by_key = {e["key"]: e for e in out["framework"]}
        self.assertEqual(by_key["politics"]["risk_level"], "warn")
        self.assertEqual(out["kol"]["handle"], "h")


if __name__ == "__main__":
    unittest.main()
