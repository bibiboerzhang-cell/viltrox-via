"""市场之声报表增强字段 V0i 三组增量(topics / language_dist / competitor_voice)契约测试。

零 DB 依赖(mock conn,仿 test_voice_report_ext_contract._FakeConn)。每组覆盖:
窗口口径(参数下推 + 环比上窗)/ 诚实空态 / 私字段不外泄 / LIMIT 双层封顶 / 参数化。
SQL 常量静态红线(零 percent、零 LIKE、私列不入 SELECT)已并入
test_voice_report_ext_contract.ALL_SQL_CONSTANTS 一并审查。
红线:纯读契约,不触真库,不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.market import voice_report_ext  # noqa: E402
from app.domains.market.market_voice import _window  # noqa: E402


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """routes: {SQL 常量: rows 或 callable(params)->rows};没配的 SQL 返回空。"""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        for known_sql, rows in self.routes.items():
            if sql == known_sql:
                return _FakeResult(rows(tuple(params)) if callable(rows) else rows)
        return _FakeResult([])


SINCE_0606, UNTIL_0606, _LABEL_0606 = _window("2026-06")
# 上一等长窗:2026-06 有 30 天 → 上窗 [2026-05-02, 2026-06-01)(严格等长,不取整月)
_SINCE_DT = datetime.fromisoformat(SINCE_0606)
_UNTIL_DT = datetime.fromisoformat(UNTIL_0606)
PREV_SINCE = (_SINCE_DT - (_UNTIL_DT - _SINCE_DT)).isoformat()


# ── 1. topics:词族命中 + 环比口径 ───────────────────────────────────────


def test_topics_counts_and_delta_pct_null_when_prev_zero():
    def topic_rows(params):
        if params[0] == SINCE_0606:  # 本窗:3 条对焦话题 + 1 条愿望
            return [
                {"comment_text": "The autofocus keeps hunting on my a7iv"},
                {"comment_text": "focus is slow in low light"},
                {"comment_text": "AF speed is amazing"},
                {"comment_text": "wish you would make a 85mm for nikon"},
            ]
        # 上窗:2 条对焦话题,0 条愿望
        return [
            {"comment_text": "autofocus hunts a lot"},
            {"comment_text": "focus problem on fx30"},
        ]

    conn = _FakeConn(routes={voice_report_ext.TOPIC_TEXT_SQL: topic_rows})
    topics = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)["topics"]

    assert topics["status"] == "ready"
    assert topics["scanned"] == 4
    assert topics["window_prev"] == {"since": PREV_SINCE, "until": SINCE_0606}

    by_key = {i["key"]: i for i in topics["items"]}
    for item in topics["items"]:
        assert set(item.keys()) == {"key", "label", "count", "delta_pct"}
    # 对焦:本窗 3 vs 上窗 2 → +50.0%
    assert by_key["autofocus"]["count"] == 3
    assert by_key["autofocus"]["delta_pct"] == 50.0
    assert by_key["autofocus"]["label"] == "对焦"
    # 愿望:上窗 0 → delta_pct=null(诚实省略环比,绝不编百分比)
    assert by_key["wishlist"]["count"] == 1
    assert by_key["wishlist"]["delta_pct"] is None
    # 榜单按 count 降序;零命中词族不进榜(命中榜,不摊 0 行)
    counts = [i["count"] for i in topics["items"]]
    assert counts == sorted(counts, reverse=True)
    assert all(c > 0 for c in counts)


def test_topics_window_params_pushed_down_both_windows():
    conn = _FakeConn()
    voice_report_ext.voice_report_ext(month="2026-06", conn=conn)
    topic_calls = [c[1] for c in conn.calls if c[0] == voice_report_ext.TOPIC_TEXT_SQL]
    assert topic_calls == [
        (SINCE_0606, UNTIL_0606, voice_report_ext.TOPIC_SCAN_LIMIT),   # 本窗
        (PREV_SINCE, SINCE_0606, voice_report_ext.TOPIC_SCAN_LIMIT),   # 上一等长窗
    ]


def test_topics_empty_window_is_honest():
    topics = voice_report_ext.voice_report_ext(month="2026-06", conn=_FakeConn())["topics"]
    assert topics["status"] == "empty"
    assert topics["items"] == []
    assert "reason" in topics
    assert "lexicon_v0" in topics["basis"]  # basis 注明词表版本


def test_topics_scan_limit_python_side_capped_and_top12():
    over = [{"comment_text": "price is too expensive"}] * (voice_report_ext.TOPIC_SCAN_LIMIT + 10)

    def topic_rows(params):
        return over if params[0] == SINCE_0606 else []

    conn = _FakeConn(routes={voice_report_ext.TOPIC_TEXT_SQL: topic_rows})
    topics = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)["topics"]
    # Python 层二次封顶(哪怕 SQL 层被 mock 放水)
    assert topics["scanned"] == voice_report_ext.TOPIC_SCAN_LIMIT
    by_key = {i["key"]: i for i in topics["items"]}
    assert by_key["price"]["count"] == voice_report_ext.TOPIC_SCAN_LIMIT
    assert voice_report_ext.TOPIC_MAX_ITEMS == 12
    assert len(topics["items"]) <= voice_report_ext.TOPIC_MAX_ITEMS


# ── 2. language_dist:und 归桶 + 归一化 ─────────────────────────────────


def test_language_dist_und_bucket_merges_null_and_blank():
    conn = _FakeConn(routes={
        voice_report_ext.LANGUAGE_DIST_SQL: [
            {"language_detected": "en", "n": 5},
            {"language_detected": "", "n": 3},       # 空串 → und
            {"language_detected": None, "n": 2},     # NULL → und(与空串合桶)
            {"language_detected": " PT ", "n": 1},   # 大小写/空白归一
        ],
    })
    dist = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)["language_dist"]
    assert dist["status"] == "ready"
    assert dist["total"] == 11
    for item in dist["items"]:
        assert set(item.keys()) == {"language", "count"}
    assert dist["items"][0] == {"language": "en", "count": 5}
    assert dist["items"][1] == {"language": voice_report_ext.UND_LANGUAGE, "count": 5}  # 3+2 合桶
    assert dist["items"][2] == {"language": "pt", "count": 1}


def test_language_dist_empty_window_is_honest():
    dist = voice_report_ext.voice_report_ext(month="2026-06", conn=_FakeConn())["language_dist"]
    assert dist["status"] == "empty"
    assert dist["items"] == []
    assert dist["total"] == 0
    assert "reason" in dist
    # 诚实口径:这是语言分布不是地理(与 demo 口径一致,不冒充 geo)
    assert "语言" in dist["basis"]


def test_language_dist_limits_and_window_params():
    over = [{"language_detected": f"l{i:02d}", "n": 1} for i in range(50)]
    conn = _FakeConn(routes={voice_report_ext.LANGUAGE_DIST_SQL: over})
    body = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)
    assert len(body["language_dist"]["items"]) == voice_report_ext.LANGUAGE_MAX_ITEMS
    lang_calls = [c[1] for c in conn.calls if c[0] == voice_report_ext.LANGUAGE_DIST_SQL]
    assert lang_calls == [(SINCE_0606, UNTIL_0606, voice_report_ext.LANGUAGE_ROWS_LIMIT)]


# ── 3. competitor_voice:百家饭口径(视频×品牌去重)─────────────────────


def test_competitor_voice_video_brand_dedupe_shares_and_is_self():
    conn = _FakeConn(routes={
        voice_report_ext.COMPETITOR_VOICE_SQL: [
            {   # 同视频 Sigma 提两个型号只记 1 次(视频×品牌防刷屏)
                "viltrox_detected_text": "true",
                "viltrox_products": '["Viltrox AF 85mm F1.8"]',
                "competitor_mentions": '[{"brand": "Sigma"}, {"brand": "Sigma 35mm F1.4 Art"}, {"brand": "Tamron"}]',
            },
            {
                "viltrox_detected_text": "false",
                "viltrox_products": None,
                "competitor_mentions": '["Sigma"]',
            },
            {   # 模型把 Viltrox 误放 mentions → 仍归自家
                "viltrox_detected_text": None,
                "viltrox_products": "[]",
                "competitor_mentions": '["Viltrox 27mm f1.2"]',
            },
        ],
    })
    cv = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)["competitor_voice"]
    assert cv["status"] == "ready"
    assert cv["sample_videos"] == 3
    assert cv["viltrox_videos"] == 2          # row1 detected + row3 误放 mentions 归自家
    assert cv["competitor_exposures"] == 3    # sigma 2 + tamron 1(row1 内去重)

    for item in cv["items"]:
        assert set(item.keys()) == {"brand", "count", "share", "is_self"}
    assert cv["items"][0] == {"brand": "Viltrox", "count": 2, "share": 0.4, "is_self": True}
    by_brand = {i["brand"]: i for i in cv["items"]}
    assert by_brand["Sigma"] == {"brand": "Sigma", "count": 2, "share": 0.4, "is_self": False}
    assert by_brand["Tamron"] == {"brand": "Tamron", "count": 1, "share": 0.2, "is_self": False}
    assert sum(1 for i in cv["items"] if i["is_self"]) == 1


def test_competitor_voice_empty_is_honest_with_real_table_basis():
    cv = voice_report_ext.voice_report_ext(month="2026-06", conn=_FakeConn())["competitor_voice"]
    assert cv["status"] == "empty"
    assert cv["items"] == []
    assert cv["sample_videos"] == 0 and cv["viltrox_videos"] == 0
    assert "reason" in cv
    # basis 注明真实表名 + 百家饭同口径
    assert "vkpi_analysis_cache" in cv["basis"]
    assert "vkpi_kol_video_evidence" in cv["basis"]
    assert "competitor_exposure" in cv["basis"]


def test_competitor_voice_window_params_and_python_cap():
    over = [
        {"viltrox_detected_text": "true", "viltrox_products": None, "competitor_mentions": None}
        for _ in range(voice_report_ext.COMPETITOR_SCAN_LIMIT + 5)
    ]
    conn = _FakeConn(routes={voice_report_ext.COMPETITOR_VOICE_SQL: over})
    cv = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)["competitor_voice"]
    # Python 层二次封顶(哪怕 SQL 层被 mock 放水)
    assert cv["sample_videos"] == voice_report_ext.COMPETITOR_SCAN_LIMIT
    assert cv["viltrox_videos"] == voice_report_ext.COMPETITOR_SCAN_LIMIT
    calls = [c[1] for c in conn.calls if c[0] == voice_report_ext.COMPETITOR_VOICE_SQL]
    assert calls == [(SINCE_0606, UNTIL_0606, voice_report_ext.COMPETITOR_SCAN_LIMIT)]


# ── 4. 三组私字段不外泄 + 纯读 ──────────────────────────────────────────


def test_new_blocks_never_leak_private_fields_and_stay_readonly():
    conn = _FakeConn(routes={
        voice_report_ext.TOPIC_TEXT_SQL: [
            {"comment_text": "autofocus issue", "author_handle": "leak_marker_topic", "author_id": "u-701"},
        ],
        voice_report_ext.LANGUAGE_DIST_SQL: [
            {"language_detected": "en", "n": 2, "author_handle": "leak_marker_lang"},
        ],
        voice_report_ext.COMPETITOR_VOICE_SQL: [
            {
                "viltrox_detected_text": "true", "viltrox_products": None,
                "competitor_mentions": '["Sigma"]',
                "author_handle": "leak_marker_cv", "raw_data_json": "{}", "channel_name": "leak_channel",
            },
        ],
    })
    body = voice_report_ext.voice_report_ext(month="2026-06", conn=conn)
    flat = repr({k: body[k] for k in ("topics", "language_dist", "competitor_voice")})
    for leaked in (
        "leak_marker_topic", "leak_marker_lang", "leak_marker_cv", "leak_channel",
        "u-701", "author_handle", "author_id", "raw_data_json", "channel_name",
    ):
        assert leaked not in flat
    # 三组整链纯读:全 SELECT,零 INSERT/UPDATE/DELETE
    for sql, _params in conn.calls:
        head = sql.strip().upper()
        assert head.startswith("SELECT")
        for verb in ("INSERT ", "UPDATE ", "DELETE "):
            assert verb not in head
