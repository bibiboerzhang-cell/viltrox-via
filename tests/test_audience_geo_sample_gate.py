"""受众地理去假(audience_stats 拆分后):硬信号样本门槛、语言不再推国家、收缩不无中生有、门面转发 + 行数硬线。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.kol import audience_stats  # noqa: E402
from app.domains.kol import audience_stats_age, audience_stats_geo, audience_stats_sampling  # noqa: E402


def _commenters(declared: int, named: int, language_only: int) -> list[dict]:
    rows: list[dict] = []
    for i in range(declared):
        rows.append({"author_key": f"d{i}", "country": "DE" if i % 3 else "US", "country_source": "declared", "language": "en", "gender": "male" if i % 2 else ""})
    for i in range(named):
        rows.append({"author_key": f"n{i}", "country": "BR", "country_source": "name", "language": "pt", "gender": "female"})
    for i in range(language_only):
        rows.append({"author_key": f"l{i}", "country": "", "country_source": "", "language": "en", "gender": ""})
    return rows


def test_geo_breakdown_requires_30_hard_signal_commenters() -> None:
    weak = audience_stats_geo.geo_breakdown(_commenters(declared=10, named=5, language_only=300))
    assert weak["method"] == "insufficient_sample"
    assert weak["determined_n"] == 15 and weak["sample_n"] == 315 and weak["min_required"] == 30
    assert weak["top_countries"] == [] and weak["confidence"] == 0.0
    assert weak["source_breakdown"] == {"declared": 10, "name": 5}

    strong = audience_stats_geo.geo_breakdown(_commenters(declared=24, named=6, language_only=300))
    assert strong["method"] == audience_stats.GEO_METHOD == "commenter_country_v1"
    assert strong["determined_n"] == 30
    codes = {row["code"]: row["pct"] for row in strong["top_countries"]}
    # pct 按 30 个硬信号归一(16 DE / 8 US / 6 BR),300 个仅有语言的评论者不进分母
    assert codes == {"DE": 53.3, "US": 26.7, "BR": 20.0}
    assert 0 < strong["confidence"] <= 0.9


def test_legacy_language_country_rows_do_not_count_as_geo() -> None:
    legacy = [{"author_key": f"x{i}", "country": "US", "country_source": "language", "language": "en"} for i in range(80)]
    geo = audience_stats_geo.geo_breakdown(legacy)
    assert geo["method"] == "insufficient_sample" and geo["determined_n"] == 0


def test_infer_commenter_no_longer_maps_language_to_country() -> None:
    rec = audience_stats.infer_commenter({"display_name": "random_user_99", "comment_text": "this is the best lens for the money"})
    assert rec["language"] == "en"
    assert rec["country"] == "" and rec["country_source"] == "" and rec["country_conf"] == 0.0
    declared = audience_stats.infer_commenter({"display_name": "x", "comment_text": "", "declared_country": "jp"})
    assert (declared["country"], declared["country_source"], declared["country_conf"]) == ("JP", "declared", 0.9)
    named = audience_stats.infer_commenter({"display_name": "Giuseppe Rossi", "comment_text": ""})
    assert (named["country"], named["country_source"], named["gender"]) == ("IT", "name", "male")


def _pool_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, audience_estimated_json TEXT, recommended_product_lines_json TEXT);
        INSERT INTO vkpi_kol_pool VALUES (1, NULL, '["lens"]');
        INSERT INTO vkpi_kol_pool VALUES (2, '{"method":"ensemble_v1","gender":{"male_pct":70,"female_pct":20,"unknown_pct":10},"top_countries":[{"code":"US","pct":80}]}', '["lens"]');
        """
    )
    return conn


def test_aggregate_gates_top_countries_and_shrinkage_cannot_invent_countries() -> None:
    conn = _pool_conn()
    payload = audience_stats.aggregate_audience(1, _commenters(declared=5, named=0, language_only=120), conn=conn, platform="youtube")
    assert payload["geo"]["method"] == "insufficient_sample"
    assert payload["top_countries"] == []  # 同垂类 prior 有 US 80%,但地理样本不足时不得无中生有
    assert payload["shrinkage"]["applied"] is True
    assert payload["shrinkage"]["countries"] == "skipped_insufficient_geo_sample"
    assert payload["coverage"]["declared_pct"] == 4.0 and payload["coverage"]["lang_pct"] == 100.0
    assert payload["languages"][0]["lang"] == "en"

    payload = audience_stats.aggregate_audience(1, _commenters(declared=40, named=0, language_only=0), conn=conn, platform="youtube")
    assert payload["geo"]["method"] == "commenter_country_v1"
    assert {row["code"] for row in payload["top_countries"]} == {"DE", "US"}
    assert "countries" not in payload["shrinkage"]
    # 同垂类旧口径 JSON(无 geo.method)的 US 80% 不得当 prior 传染:US 仍按本样本 16/40 + 收缩权重
    us = next(row["pct"] for row in payload["top_countries"] if row["code"] == "US")
    assert us < 50.0
    conn.execute("UPDATE vkpi_kol_pool SET audience_estimated_json=? WHERE id=2", (
        '{"method":"ensemble_v1","geo":{"method":"commenter_country_v1"},"gender":{"male_pct":70,"female_pct":20,"unknown_pct":10},"top_countries":[{"code":"JP","pct":100}]}',
    ))
    payload = audience_stats.aggregate_audience(1, _commenters(declared=40, named=0, language_only=0), conn=conn, platform="youtube")
    assert "JP" in {row["code"] for row in payload["top_countries"]}  # 新口径 prior 才参与收缩

    empty = audience_stats.aggregate_audience(1, [], conn=conn, platform="tiktok")
    assert empty["geo"]["method"] == "insufficient_sample" and empty["top_countries"] == []


def test_infer_with_cache_sanitizes_legacy_language_country(monkeypatch) -> None:
    upserts: list[list[dict]] = []
    monkeypatch.setattr(audience_stats, "_load_cached_profiles", lambda conn, platform, keys: {
        "u1": {"platform": "youtube", "author_key": "u1", "country": "US", "country_source": "language", "country_conf": 0.3,
               "gender": "male", "gender_conf": 0.55, "language": "en", "age_bucket": "30-39", "age_conf": 0.5},
        "u2": {"platform": "youtube", "author_key": "u2", "country": "DE", "country_source": "declared", "country_conf": 0.9,
               "gender": "", "gender_conf": 0.0, "language": "de"},
    })
    monkeypatch.setattr(audience_stats, "_upsert_commenter_profiles", lambda conn, rows: upserts.append(rows) or len(rows))
    inferred, stats = audience_stats._infer_with_cache(None, "youtube", [
        {"author_key": "u1", "display_name": "u1", "comment_text": "x"},
        {"author_key": "u2", "display_name": "u2", "comment_text": "y"},
    ])
    by_key = {row["author_key"]: row for row in inferred}
    assert by_key["u1"]["country"] == "" and by_key["u1"]["country_source"] == ""
    assert by_key["u1"]["gender"] == "male" and by_key["u1"]["age_bucket"] == "30-39"  # 其它缓存字段原样保留
    assert by_key["u2"]["country"] == "DE"
    assert stats["cache_hits"] == 2 and stats["inferred_fresh"] == 0
    assert [row["author_key"] for row in upserts[0]] == ["u1"]  # 只回写被清洗的那行


def test_facade_forwards_symbols_and_monkeypatch_paths() -> None:
    assert audience_stats.sample_youtube_commenters is audience_stats_sampling.sample_youtube_commenters
    assert audience_stats._age_llm_batches is audience_stats_age._age_llm_batches
    assert audience_stats.aggregate_audience is audience_stats_geo.aggregate_audience
    assert audience_stats._extract_json_array('x [{"i":1}] y') == [{"i": 1}]
    for name in ("_yt_get", "_yt_api_key", "_resolve_channel_id", "load_avatar_gemini", "download_avatar",
                 "_age_avatar_batch", "_age_m3_batch", "refresh_audience_stats", "MIN_LOCAL_COMMENTS", "GEO_MIN_SAMPLE"):
        assert hasattr(audience_stats, name), name
    # 子模块经 _live() 回到门面取协作函数:门面被 monkeypatch 时子模块内部调用也跟着变
    assert audience_stats_sampling._live("_yt_get") is audience_stats._yt_get
    assert audience_stats_age._live("_age_avatar_batch") is audience_stats._age_avatar_batch


def test_split_modules_respect_line_hard_limit() -> None:
    for name in ("audience_stats", "audience_stats_age", "audience_stats_geo", "audience_stats_sampling"):
        lines = (BACKEND / "app/domains/kol" / f"{name}.py").read_text(encoding="utf-8").count("\n")
        assert lines <= 1000, (name, lines)
