"""推断语言**真正接进搜索**的三处接线(2026-08-26 · 接线车道)。

对抗复核坐实过一件事:推断值落库了、闸也认了,但**线上真实链路根本走不到闸**——
在更早的取数 SQL 那一步,``language`` 为空的人就被整批剔掉了。本文件按那份复核逐条钉死:

* **H2 读路径**:``_entry_rows`` / ``_pool_rows_fallback`` 必须 SELECT 出迁移 305 的四列;
  列没迁移的旧库布局要自动退 NULL,而不是把整条搜索炸掉;
* **H3 取数腿**:``lexical_recall_candidates`` 与广度兜底腿的语言下推**同时认两列**,
  并且认三态(``include_unknown`` 此前结构上取不到任何人,``exclude`` 更是反着来);
* **H1 硬筛闸**:``_candidate_filter_verdict``(真正判 False 就丢人的那一道)
  按「自报优先 → 推断兜底 → 都没有则未知」取值;
* **不变式**:取数腿是闸的**超集** —— 闸会放行的人,取数腿一个都不许提前剔掉;
* **置信门槛**:门槛抬高时,SQL 腿与闸**同步**收紧,不许一边松一边紧;
* **红线**:推断值绝不冒充自报值;SQL 里零字面百分号、零 LIKE。

测试全程打真 sqlite,SQL 是真执行的 —— 不是字符串比对。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domains.kol import profile_recall_precision as precision  # noqa: E402
from app.domains.kol import profile_recall_storage as storage  # noqa: E402
from app.domains.kol.profile_recall_language_gate import (  # noqa: E402
    INFERRED_POOL_COLUMNS,
    MIN_INFERRED_CONFIDENCE,
    ORIGIN_INFERRED,
    ORIGIN_SELF_REPORTED,
    ORIGIN_UNKNOWN,
    language_sql_filter,
    meets_confidence_floor,
    resolve_language_match_key,
)
from app.domains.kol.profile_recall_projection import (  # noqa: E402
    _candidate_filter_verdict,
    _language_match_key,
    _normalize_recall_filters,
)

_QUERY = "photography"

#: (id, handle, 自报语言, 推断语言, 推断置信档)。每一行对着一个具体的坑。
_PEOPLE: tuple[tuple[int, str, str, str, str], ...] = (
    (1, "self_en", "en", "", ""),            # 自报英语:一直都进得来
    (2, "self_en_gb", "en-GB", "", ""),      # 自报带地区后缀:主码相同也算
    (3, "inferred_high", "", "en", "high"),  # 推断英语·高置信:本次要救回来的人
    (4, "inferred_low", "", "en", "low"),    # 推断英语·低置信:门槛话题的主角
    (5, "inferred_ja", "", "ja", "high"),    # 推断日语:确认不符,照旧拦
    (6, "no_signal", "", "", ""),            # 两样都没有:未知档
    (7, "self_ja", "ja", "en", "high"),      # 自报日语 + 推断英语:自报优先,拦
    (8, "padded_en", " en ", "", ""),        # 库里带空白:闸一直认,取数腿也要认
)


def _make_pool(conn: sqlite3.Connection, *, with_inferred: bool) -> None:
    inferred_ddl = (
        ", language_inferred TEXT, language_inferred_confidence TEXT,"
        " language_inferred_source TEXT, language_inferred_method TEXT"
        if with_inferred
        else ""
    )
    conn.executescript(
        f"""
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY, platform TEXT, handle TEXT, display_name TEXT,
            bio TEXT, primary_topic TEXT, content_style TEXT, secondary_topics_json TEXT,
            topic_details_json TEXT, tagged_brands_json TEXT, avg_likes INTEGER,
            source_type TEXT, source_ref TEXT, real_er REAL, real_er_sample_n INTEGER,
            real_er_computed_at TEXT, real_er_method TEXT, last_seen_at TEXT, updated_at TEXT,
            avatar_url TEXT, profile_url TEXT, avg_views INTEGER, avg_comments INTEGER,
            engagement_rate REAL, followers INTEGER, country TEXT, language TEXT,
            brand_collaborations_json TEXT, duplicate_of_id INTEGER{inferred_ddl}
        );
        CREATE TABLE vkpi_kol_profile_index_entries (
            kol_pool_id INTEGER, collection_name TEXT, method TEXT, status TEXT,
            profile_type TEXT, creator_type_score REAL, reviewer_type_score REAL,
            type_reason TEXT, type_method TEXT, sufficiency TEXT, profile_text TEXT
        );
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY, kol_pool_id INTEGER, title TEXT,
            video_title TEXT, content_url TEXT, is_active INTEGER DEFAULT 1
        );
        """
    )
    base = (
        "INSERT INTO vkpi_kol_pool (id, platform, handle, display_name, bio, primary_topic,"
        " content_style, followers, country, language"
    )
    for pool_id, handle, language, inferred, confidence in _PEOPLE:
        values: list[Any] = [
            pool_id, "youtube", handle, handle, f"{_QUERY} creator", _QUERY,
            "review", 80000, "US", language,
        ]
        sql = base
        if with_inferred:
            sql += ", language_inferred, language_inferred_confidence, language_inferred_method"
            values.extend([inferred, confidence, "kol_content_langdetect_vote_v1"])
        sql += ") VALUES (" + ",".join("?" * len(values)) + ")"
        conn.execute(sql, tuple(values))
        conn.execute(
            "INSERT INTO vkpi_kol_profile_index_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pool_id, "vkpi_kol_profile_index_v1", "vector_recall", "ready", "creator",
             0.8, 0.2, "portrait photography", "rule", "ok", f"{_QUERY} portrait creator"),
        )
    conn.commit()


def _pool(with_inferred: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _make_pool(conn, with_inferred=with_inferred)
    return conn


def _retrieval_filters(normalized: dict[str, Any]) -> dict[str, Any]:
    """照抄 ``recall_kol_profiles`` 构造 ``_language_values`` 的那段。"""
    out = dict(normalized)
    if normalized.get("languages"):
        out["_language_values"] = sorted({
            value
            for raw in normalized["languages"]
            for value in (str(raw).strip().lower(), _language_match_key(raw))
            if value
        })
    return out


def _recall_leg_handles(conn: sqlite3.Connection, raw_filters: dict[str, Any]) -> set[str]:
    """取数腿真捞回来了谁。"""
    normalized, _unsupported = _normalize_recall_filters(dict(raw_filters))
    result = precision.lexical_recall_candidates(
        _QUERY, operator_query=_QUERY, candidate_limit=100, conn=conn,
        hard_filters=_retrieval_filters(normalized),
    )
    ids = {int(item["kol_pool_id"]) for item in result.get("items") or []}
    return {handle for pool_id, handle, *_rest in _PEOPLE if pool_id in ids}


def _gate_handles(raw_filters: dict[str, Any]) -> set[str]:
    """硬筛闸真放行了谁(闸是纯函数,直接喂行)。"""
    normalized, _unsupported = _normalize_recall_filters(dict(raw_filters))
    passed: set[str] = set()
    for pool_id, handle, language, inferred, confidence in _PEOPLE:
        row = {
            "kol_pool_id": pool_id, "handle": handle, "platform": "youtube",
            "country": "US", "followers": 80000, "language": language,
            "language_inferred": inferred, "language_inferred_confidence": confidence,
        }
        if _candidate_filter_verdict(row, {}, normalized)[0]:
            passed.add(handle)
    return passed


# ── H2 读路径 ────────────────────────────────────────────────────────────────


def test_read_path_selects_the_migration_305_columns() -> None:
    """两条读路径都要把推断四列取出来 —— 取不出来,闸就永远看不到推断值。"""
    conn = _pool()
    for loader in (storage._entry_rows, storage._pool_rows_fallback):
        rows = loader([3], get_connection=lambda: conn)
        row = rows[3]
        for column in INFERRED_POOL_COLUMNS:
            assert column in row, f"{loader.__name__} 漏了 {column}"
        assert row["language_inferred"] == "en"
        assert row["language_inferred_confidence"] == "high"


def test_read_path_degrades_to_null_on_a_pre_migration_layout() -> None:
    """列没迁移的旧库:退 NULL,不炸 —— 那个人只是在语言这一路上算未知。"""
    conn = _pool(with_inferred=False)
    for loader in (storage._entry_rows, storage._pool_rows_fallback):
        row = loader([3], get_connection=lambda: conn)[3]
        assert row["language_inferred"] is None
        assert row["language_inferred_confidence"] is None


# ── H3 取数腿 ────────────────────────────────────────────────────────────────


def test_recall_leg_no_longer_culls_people_whose_only_signal_is_inferred() -> None:
    """本波的核心缺陷:取数腿只认 p.language,推断值再准也进不了搜索。"""
    conn = _pool()
    handles = _recall_leg_handles(conn, {"languages": ["en"]})
    assert "inferred_high" in handles
    assert "self_en" in handles and "padded_en" in handles
    # ``inferred_low`` 不在里面 —— 不是取数腿又把人剔了,而是缺省置信门槛
    # (``MIN_INFERRED_CONFIDENCE = "medium"``)在**两侧同时**把这一档挡在外面。
    # 他回到「未知」档(不是「不合格」),点「含未知」照样拿得回来,下一条钉住这一点。
    assert "inferred_low" not in handles
    assert "inferred_low" in _recall_leg_handles(
        conn, {"languages": {"values": ["en"], "mode": "include_unknown"}}
    )


def test_recall_leg_still_reaches_only_the_language_it_was_asked_for() -> None:
    """接线不等于放行:确认说别的语言的人,取数腿照旧不带回来。"""
    conn = _pool()
    handles = _recall_leg_handles(conn, {"languages": ["en"]})
    assert "inferred_ja" not in handles
    assert "no_signal" not in handles


def test_recall_leg_honours_include_unknown_mode() -> None:
    """「含未知」此前在取数腿上结构性失效(那一格永远 0 增益)。"""
    conn = _pool()
    strict = _recall_leg_handles(conn, {"languages": ["en"]})
    admitted = _recall_leg_handles(
        conn, {"languages": {"values": ["en"], "mode": "include_unknown"}}
    )
    assert "no_signal" not in strict
    assert "no_signal" in admitted
    assert strict < admitted


def test_recall_leg_stops_sabotaging_exclude_mode() -> None:
    """「排除英语」此前被下推成「只捞英语」,再被闸全排掉,结果恒为 0。"""
    conn = _pool()
    handles = _recall_leg_handles(
        conn, {"languages": {"values": ["en"], "mode": "exclude"}}
    )
    assert "inferred_ja" in handles and "self_ja" in handles


def test_recall_leg_survives_a_pre_migration_layout() -> None:
    """旧库没有推断列:只按自报列筛,行为与接线前逐字一致,不抛异常。"""
    conn = _pool(with_inferred=False)
    handles = _recall_leg_handles(conn, {"languages": ["en"]})
    assert {"self_en", "self_en_gb", "padded_en"} <= handles
    assert "inferred_high" not in handles


# ── H1 硬筛闸 ────────────────────────────────────────────────────────────────


def test_gate_reads_the_inferred_column() -> None:
    """真正判 False 就丢人的那一道闸,现在看得见推断值。"""
    assert "inferred_high" in _gate_handles({"languages": ["en"]})


def test_gate_keeps_self_reported_authority() -> None:
    """自报优先:自报日语的人,推断说英语也救不了他(也不许被抹平成英语)。"""
    assert "self_ja" not in _gate_handles({"languages": ["en"]})
    key, origin = resolve_language_match_key(
        {"language": "ja", "language_inferred": "en"}, match_key=_language_match_key,
    )
    assert (key, origin) == ("ja", ORIGIN_SELF_REPORTED)


def test_gate_keeps_unknown_rejected_under_require() -> None:
    """判不出来的人是「未知」,缺省 ``require`` 照旧拦 —— 一条闸都没放宽。"""
    assert "no_signal" not in _gate_handles({"languages": ["en"]})
    key, origin = resolve_language_match_key({"language": ""}, match_key=_language_match_key)
    assert (key, origin) == ("", ORIGIN_UNKNOWN)


def test_gate_marks_the_inferred_origin() -> None:
    """靠哪一档进来的,取值口径上说得出来。"""
    key, origin = resolve_language_match_key(
        {"language": "", "language_inferred": "en", "language_inferred_confidence": "high"},
        match_key=_language_match_key,
    )
    assert (key, origin) == ("en", ORIGIN_INFERRED)


# ── 不变式:取数腿是闸的超集 ─────────────────────────────────────────────────


def test_recall_leg_is_a_superset_of_the_gate() -> None:
    """本波最重要的一条:闸会放行的人,取数腿一个都不许提前剔掉。

    下推「自报 ∪ 推断」而不是「干脆不筛」,正是为了在**不多丢人**的前提下
    不把有限的行预算浪费在注定被闸驳回的人身上。
    """
    conn = _pool()
    for filters in (
        {"languages": ["en"]},
        {"languages": ["English"]},
        {"languages": {"values": ["en"], "mode": "include_unknown"}},
        {"languages": {"values": ["en"], "mode": "exclude"}},
        {"languages": ["ja"]},
    ):
        leg = _recall_leg_handles(conn, filters)
        gate = _gate_handles(filters)
        assert gate <= leg, f"{filters}: 闸放行但取数腿够不着 {sorted(gate - leg)}"


# ── 置信门槛 ─────────────────────────────────────────────────────────────────


def test_default_floor_requires_a_corroborated_inference() -> None:
    """默认门槛 = ``medium``:至少两条他自己写的文本互相印证,投票机制才算真起过作用。

    2026-08-26 复核把这条从 ``low``(= 不设门槛)抬上来 —— 原依据是拿「有平台自报
    语言」的人外推 low 档准确率,而 low 档恰恰是「只有一条短文本」那批人,两个群体
    不同分布。同分布重估见 ``profile_recall_language_gate.MIN_INFERRED_CONFIDENCE``
    的常量注释(判英语 32/35 = 91.4%,95% CI [77.6%, 97.0%])。
    """
    assert MIN_INFERRED_CONFIDENCE == "medium"
    for tier in ("high", "medium"):
        assert meets_confidence_floor(tier)
    # 低档、以及**读不出档位**的推断值都不参与硬筛 —— 证不出达标就不放行。
    for tier in ("low", "", None):
        assert not meets_confidence_floor(tier)


def test_raised_floor_tightens_the_gate_and_the_sql_together() -> None:
    """门槛抬高时两侧同步收紧,不许一边松一边紧;档位读不出来的一律不放行。"""
    assert meets_confidence_floor("high", "medium")
    assert not meets_confidence_floor("low", "medium")
    assert not meets_confidence_floor(None, "medium")
    row = {"language": "", "language_inferred": "en", "language_inferred_confidence": "low"}
    assert resolve_language_match_key(
        row, match_key=_language_match_key, min_confidence="low",
    )[1] == ORIGIN_INFERRED
    # 门槛在 medium(现缺省):这个人回到「未知」档(不是「不合格」),口径与新鲜闸一致。
    assert resolve_language_match_key(
        row, match_key=_language_match_key, min_confidence="medium",
    ) == ("", ORIGIN_UNKNOWN)
    conn = _pool()
    sql, params = language_sql_filter(["en"], has_inferred_column=True, min_confidence="medium")
    kept = {
        int(dict(row)["id"])
        for row in conn.execute(
            f"SELECT id FROM vkpi_kol_pool p WHERE {sql}", tuple(params)
        ).fetchall()
    }
    assert 3 in kept          # high 档:过门槛
    assert 4 not in kept      # low 档:被门槛挡在外面,与闸同步


# ── 红线 ─────────────────────────────────────────────────────────────────────


def test_inferred_value_never_impersonates_a_self_reported_one() -> None:
    """立身之本:推断值住在另一列,三态泾渭分明,判不出就是「未知」。"""
    conn = _pool()
    row = storage._entry_rows([3], get_connection=lambda: conn)[3]
    assert str(row["language"] or "").strip() == ""
    assert row["language_inferred"] == "en"
    assert resolve_language_match_key(row, match_key=_language_match_key)[1] == ORIGIN_INFERRED


def test_language_sql_carries_no_literal_percent_and_no_like() -> None:
    """compat 红线:占位符 ``?``、零字面百分号、零 LIKE(用 substr 做前缀匹配)。"""
    for kwargs in (
        {"has_inferred_column": True},
        {"has_inferred_column": True, "mode": "include_unknown"},
        {"has_inferred_column": True, "min_confidence": "high"},
        {"has_inferred_column": False},
    ):
        sql, _params = language_sql_filter(["en", "ja"], **kwargs)
        assert "%" not in sql
        assert "LIKE" not in sql.upper()
