"""F1 件③ golden:video_similarity 中心化余弦(v1,变相 Pearson)。

fake conn 喂固定行,断言:
- method 升 v1(dimensions_centered_cosine_v1),note 写明中心化理由;
- 「质量相似 ≠ 内容相似」被治:形状同向的候选(centered 同向)cosine 高,
  分数水平同样偏高但形状反向的候选 cosine 低 —— 未中心化时两者都≈1;
- 恰好落在语料均值上的候选 cosine 诚实 None,basis 退 jaccard_only;
- payload 键结构不变(seed/items/score_parts{cosine,tag_jaccard,basis}/weights/...)。
"""
import json

from app.domains.kol import video_similarity as vs


# ── 纯函数级:中心化余弦 ─────────────────────────────────────────────


def test_centered_cosine_separates_shape_from_level():
    # 语料三支:S=(90,50,90) A=S(同形状) B=(70,70,70)(高分但形状反向)
    s = {"content_quality_score": 90.0, "marketing_value_score": 50.0, "product_proof_score": 90.0}
    a = dict(s)
    b = {"content_quality_score": 70.0, "marketing_value_score": 70.0, "product_proof_score": 70.0}
    means = vs._dim_means([s, a, b])

    same_shape = vs._cosine(s, a, means)
    anti_shape = vs._cosine(s, b, means)
    assert same_shape is not None and anti_shape is not None
    # 中心化后:同形状 → 映射值≈1;反形状 → 映射值≈0
    assert same_shape > 0.99
    assert anti_shape < 0.01
    # 对照:未中心化(means 全 0)时两者都≈1,毫无区分度 —— 这就是 v0 的病
    raw_same = vs._cosine(s, a, {})
    raw_anti = vs._cosine(s, b, {})
    assert raw_same > 0.97 and raw_anti > 0.97


def test_centered_cosine_at_corpus_mean_is_none():
    s = {"content_quality_score": 90.0, "marketing_value_score": 50.0, "product_proof_score": 90.0}
    c = {"content_quality_score": 30.0, "marketing_value_score": 80.0, "product_proof_score": 30.0}
    mean_vec = {"content_quality_score": 60.0, "marketing_value_score": 65.0, "product_proof_score": 60.0}
    means = vs._dim_means([s, c, mean_vec])
    # mean_vec 恰好就是语料均值 → 中心化后零向量,形状无从比较 → None
    assert vs._cosine(s, mean_vec, means) is None


def test_min_shared_dims_still_enforced():
    a = {"content_quality_score": 90.0, "marketing_value_score": 50.0}
    b = {"content_quality_score": 70.0, "marketing_value_score": 70.0}
    assert vs._cosine(a, b, vs._dim_means([a, b])) is None  # 共有维 2 < 3


# ── golden fixture:fake conn ────────────────────────────────────────


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        for key, rows in self.routes.items():
            if key in sql:
                return _Result(rows)
        return _Result([])


def _dims(cq, mv, pp, title):
    return json.dumps({
        "scores": {
            "content_quality_score": {"score": cq},
            "marketing_value_score": {"score": mv},
            "product_proof_score": {"score": pp},
        },
        "layer1_summary": {"content_summary": title},
        "source": {"title": title, "platform": "youtube"},
    })


def _corpus_row(deep_id, eid, kol_id, dims, title):
    return {
        "deep_id": deep_id, "evidence_id": eid, "kol_pool_id": kol_id,
        "dims": dims, "title": title, "content_url": f"https://youtube.com/watch?v=x{eid}",
        "platform": "youtube", "view_count": 100 * eid, "posted_at": None,
        "handle": f"kol{kol_id}", "display_name": f"KOL {kol_id}",
    }


def _conn():
    # seed=(90,50,90);A 同形状同 title 词;B 高分反形状。
    return _Conn({
        "FROM vkpi_kol_video_evidence": [{
            "id": 1, "kol_pool_id": 100, "title": "seed street photography 35mm",
            "content_url": "u", "platform": "youtube", "view_count": 100, "is_active": 1,
        }],
        "FROM vkpi_kol_llm_deep_analysis_results": [
            _corpus_row(3, 1, 100, _dims(90, 50, 90, "street photography 35mm walk"), "seed street photography 35mm"),
            _corpus_row(2, 2, 200, _dims(90, 50, 90, "street photography 35mm night"), "cand A street 35mm"),
            _corpus_row(1, 3, 300, _dims(70, 70, 70, "street photography 35mm day"), "cand B street 35mm"),
        ],
    })


def test_similar_videos_centered_ranking_golden():
    payload = vs.similar_videos(1, conn=_conn())

    assert payload["status"] == "ready"
    assert payload["method"] == "dimensions_centered_cosine_v1"
    assert "中心化" in payload["note"]
    assert payload["weights"] == {"cosine": 0.6, "tag_jaccard": 0.4}

    items = {item["evidence_id"]: item for item in payload["items"]}
    assert set(items) == {2, 3}
    cos_a = items[2]["score_parts"]["cosine"]
    cos_b = items[3]["score_parts"]["cosine"]
    # 同形状的 A cosine≈1;高分反形状的 B cosine≈0(v0 时两者都≈1)
    assert cos_a > 0.99
    assert cos_b < 0.01
    assert items[2]["similarity"] > items[3]["similarity"]
    # 排序:A 在 B 前
    assert [it["evidence_id"] for it in payload["items"]] == [2, 3]

    # payload 键结构不变(下游抽屉在吃)
    for key in ("seed", "corpus_size", "excluded_same_kol", "exclude_same_kol", "items", "weights", "note"):
        assert key in payload
    item = payload["items"][0]
    for key in ("evidence_id", "title", "url", "platform", "view_count", "posted_at",
                "kol", "similarity", "score_parts", "shared_tags"):
        assert key in item
    for key in ("cosine", "tag_jaccard", "basis"):
        assert key in item["score_parts"]
    assert item["score_parts"]["basis"] == "cosine+jaccard"
