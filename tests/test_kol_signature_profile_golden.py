"""F1 件① golden:signature_profile 英文词表词边界 + 多标签样本口径。

fake conn 喂固定行,断言:
- 英文词走词边界正则(review 不再误命中 previews、cam 不命中 camcorder);
- 中文词保持子串;
- 多标签口径:一片可同时属多个拍摄模式,各模式 video_count 之和可大于 sample_size;
- payload 结构键不变(status/sample_size/method/note/modes/unclassified_count/...)。
"""
import json

from app.domains.kol import signature_profile as sp


# ── 纯函数级:词边界行为 ─────────────────────────────────────────────


def test_match_terms_english_word_boundary():
    # 词边界:review 不命中 previews / previewer
    assert sp._match_terms("previews of the new lens previewer", ("review",)) is False
    assert sp._match_terms("my honest review of the lens", ("review",)) is True
    # 前后缀都拦:cam 不命中 camcorder / webcam1
    assert sp._match_terms("shot on a camcorder and webcam1", ("cam",)) is False
    assert sp._match_terms("shot on a cam rig", ("cam",)) is True
    # 非字母数字收尾的词(b-roll)照样有词尾边界:b-rolling 不算
    assert sp._match_terms("some b-rolling around town", ("b-roll",)) is False
    assert sp._match_terms("cinematic b-roll sequence", ("b-roll",)) is True
    # 中英混排:边界用 [a-z0-9],紧邻汉字不挡英文词
    assert sp._match_terms("这是review视频", ("review",)) is True


def test_match_terms_chinese_stays_substring():
    assert sp._match_terms("超深度评测视频", ("评测",)) is True
    assert sp._match_terms("这个开箱很详细", ("开箱",)) is True
    # 无字母数字的符号词(如 '?')保持子串
    assert sp._match_terms("really? yes", ("?",)) is True


# ── golden fixture:fake conn ────────────────────────────────────────


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Conn:
    """按 SQL 关键词路由固定行;记录全部调用供断言。"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        for key, rows in self.routes.items():
            if key in sql:
                return _Result(rows)
        return _Result([])


def _final_row(eid, summary, views=100):
    result = {
        "layer1_visual_content": {
            "content_summary": summary,
            "scene_timeline": [],
            "production_observations": {"professional_level": "high"},
        },
        "layer2_viewer_emotion": {"first_three_seconds_feeling": ""},
    }
    return {
        "evidence_id": eid,
        "title": f"video {eid}",
        "content_url": f"https://youtube.com/watch?v=v{eid}",
        "platform": "youtube",
        "view_count": views,
        "like_count": 10,
        "comment_count": 2,
        "posted_at": None,
        "result": json.dumps(result),
    }


def _conn():
    return _Conn({
        "FROM vkpi_kol_pool": [{
            "id": 7, "handle": "tester", "display_name": "Tester",
            "platform": "youtube", "avatar_url": "",
        }],
        # 片1:同时命中 tutorial(how to)+ cinematic_broll(b-roll)→ 多标签
        # 片2:词边界负例 —— previews/camcorder 不该命中任何模式 → unclassified
        "FROM vkpi_analysis_cache": [
            _final_row(1, "how to shoot cinematic b-roll with this lens", views=200),
            _final_row(2, "previews from my camcorder povero archive", views=50),
        ],
        "FROM vkpi_kol_video_evidence": [
            {
                "evidence_id": 1, "title": "video 1", "content_url": "u1",
                "platform": "youtube", "view_count": 200, "like_count": 10,
                "comment_count": 2, "posted_at": None, "is_active": 1,
            },
            {
                "evidence_id": 2, "title": "video 2", "content_url": "u2",
                "platform": "youtube", "view_count": 50, "like_count": 1,
                "comment_count": 1, "posted_at": None, "is_active": 1,
            },
        ],
        "FROM vkpi_comments": [],
    })


def test_signature_profile_multilabel_and_word_boundary_golden():
    payload = sp.signature_profile(7, conn=_conn())

    assert payload["status"] == "ready"
    styles = payload["shooting_styles"]
    assert styles["status"] == "ready"
    assert styles["sample_size"] == 2
    assert styles["method"] == "final_v1_layer1_lexicon_v1"

    mode_counts = {m["key"]: m["video_count"] for m in styles["modes"]}
    # 多标签:片1 同时进 tutorial 与 cinematic_broll,两模式各记 1
    assert mode_counts.get("tutorial") == 1
    assert mode_counts.get("cinematic_broll") == 1
    # video_count 之和(2)= 双标签的片1 × 2,> 命中样本数(1)
    assert sum(mode_counts.values()) == 2
    # 词边界:片2 的 previews/camcorder/povero 不误命中任何模式(pov 不命中 povero)
    assert "pov" not in mode_counts
    assert "talking_review" not in mode_counts
    assert styles["unclassified_count"] == 1
    # 多标签口径已写进 note
    assert "多标签" in styles["note"] or "可同时属" in styles["note"]

    # 结构键不变(下游抽屉面板在吃)
    for key in ("modes", "unclassified_count", "openings", "professional_level", "brand_exposure", "note"):
        assert key in styles
    for key in ("kol", "coverage", "shooting_styles", "top_videos", "feedback", "generated_at", "note"):
        assert key in payload
    mode = styles["modes"][0]
    for key in ("key", "label", "video_count", "avg_views", "views_sample", "top_example"):
        assert key in mode
