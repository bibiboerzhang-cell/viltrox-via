"""F1 件④ golden:quality_score 综合分透出 LLM 模型/prompt 版本痕迹(provenance)。

fake conn 喂固定行,断言:
- composite.provenance.llm_models / prompt_versions 按 llm_dimensions_11 自带痕迹
  (source.final_model / schema_version)计数;
- 无痕迹的行如实计 unknown,note 写明漂移哨兵待人工抽检;
- 原有键(score/method/weights/basis)与整体 payload 结构不变。
"""
import json

from app.domains.kol import quality_compliance as qc


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


def _deep_row(deep_id, eid, dims, created_at="2026-07-01T00:00:00"):
    return {
        "deep_id": deep_id, "evidence_id": eid, "dims": json.dumps(dims),
        "confidence": 0.8, "created_at": created_at,
        "title": f"video {eid}", "content_url": f"https://youtube.com/watch?v=q{eid}",
        "platform": "youtube", "view_count": 100 * eid,
    }


# 历史 provenance fixture:gemini-3.1-pro-preview 是旧裁判(2026-08 模型升级刀前)落库的真实行,
# 本测试只验 provenance 透传,故意保留旧 id 记录老数据形态,不随裁判换模型改写。
_DIMS_WITH_TRACE = {
    "schema_version": "kol_llm_deep_analysis_from_final_v1_v1",
    "source": {"final_model": "gemini-3.1-pro-preview"},
    "scores": {
        "content_quality_score": {"score": 80},
        "marketing_value_score": {"score": 70},
        "product_proof_score": {"score": 60},
        "viewer_heart_score": {"score": 75},
        "channel_value_score": {"score": 65},
    },
}

_DIMS_NO_TRACE = {
    # 无 schema_version / 无 source.final_model → unknown
    "scores": {
        "content_quality_score": {"score": 90},
        "marketing_value_score": {"score": 80},
        "product_proof_score": {"score": 70},
    },
}


def _conn():
    return _Conn({
        "FROM vkpi_kol_pool": [{
            "id": 9, "handle": "q_tester", "display_name": "Q Tester",
            "platform": "youtube", "raw_platform_data": None,
        }],
        "FROM vkpi_kol_llm_deep_analysis_results": [
            _deep_row(1, 21, _DIMS_WITH_TRACE),
            _deep_row(2, 22, _DIMS_NO_TRACE, created_at="2026-06-30T00:00:00"),
        ],
        "FROM vkpi_kol_video_evidence": [
            {"evidence_id": 21, "title": "video 21", "content_url": "u21",
             "platform": "youtube", "view_count": 2100, "is_active": 1},
            {"evidence_id": 22, "title": "video 22", "content_url": "u22",
             "platform": "youtube", "view_count": 2200, "is_active": 1},
        ],
    })


def test_quality_score_exposes_llm_provenance_golden():
    payload = qc.quality_score(9, conn=_conn())

    assert payload["status"] == "ready"
    assert payload["sample_size"] == 2

    composite = payload["composite"]
    # 原有键不变(下游抽屉在吃)
    for key in ("score", "method", "weights", "basis"):
        assert key in composite
    assert composite["method"] == "final_v1_scores_weighted_mean_v1"
    assert composite["score"] is not None

    prov = composite["provenance"]
    assert prov["llm_models"] == {"gemini-3.1-pro-preview": 1, "unknown": 1}
    assert prov["prompt_versions"] == {"kol_llm_deep_analysis_from_final_v1_v1": 1, "unknown": 1}
    assert prov["unknown_model_count"] == 1
    assert prov["unknown_prompt_version_count"] == 1
    assert "抽检" in prov["note"] and "漂移" in prov["note"]

    # 整体结构键不变
    for key in ("kol", "coverage", "sample_size", "composite", "dimensions",
                "confidence", "best_videos", "weakest_video", "generated_at", "note"):
        assert key in payload


def test_quality_score_all_traced_has_zero_unknown():
    conn = _Conn({
        "FROM vkpi_kol_pool": [{
            "id": 9, "handle": "q_tester", "display_name": "Q Tester",
            "platform": "youtube", "raw_platform_data": None,
        }],
        "FROM vkpi_kol_llm_deep_analysis_results": [_deep_row(1, 21, _DIMS_WITH_TRACE)],
        "FROM vkpi_kol_video_evidence": [],
    })
    payload = qc.quality_score(9, conn=conn)
    prov = payload["composite"]["provenance"]
    assert prov["unknown_model_count"] == 0
    assert prov["unknown_prompt_version_count"] == 0
    assert list(prov["llm_models"]) == ["gemini-3.1-pro-preview"]
