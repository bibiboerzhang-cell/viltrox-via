"""语言硬筛的来源分层(2026-08-26 · 筛选接入车道 + 归属真相车道)。

钉死四件事:

1. **判定顺序**:自报优先 -> 推断兜底 -> 两样都没有就是「未知」;
2. **来源可追**:推断值绝不冒充自报值 —— 证据里 ``origin`` / ``inferred`` / ``source``
   三处都说得出这个值是谁说的,自报值与推断值各自旁挂,分歧不被抹平;
3. **归属真相(H6)**:「``language`` 字段非空」**不等于**「他自己填的」。
   归属必须真的去读来源(``language_source`` / facet 证据块),分清
   平台自报 / 我们推断 / 别处投影来的 / 未知;读不出来源时**不许默认成自报**。
   并且这一层修的是**那句话的真假,不是任何人的去留** —— 取值口径逐字不变。
4. **一条闸都没放宽**:粉丝下限 / 新鲜度天数 / 器材证据 / 未知语言的处置
   (缺省 ``require`` = 拦)全部与接入前逐字节一致。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domains.kol import profile_recall_qualification as qualification  # noqa: E402
from app.domains.kol.profile_recall_language_gate import (  # noqa: E402
    INFERRED_SOURCE,
    MIN_INFERRED_CONFIDENCE,
    ORIGIN_INFERRED,
    ORIGIN_PROJECTED,
    ORIGIN_SELF_REPORTED,
    ORIGIN_UNKNOWN,
    SELF_REPORTED_SOURCE,
    classify_language_origin,
    language_gate_evidence,
    language_source_token,
    resolve_candidate_language,
)
from app.domains.kol.profile_recall_search_spec import normalize_operator_languages  # noqa: E402


NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _resolve(row=None, item=None):
    return resolve_candidate_language(row or {}, item or {}, normalize=normalize_operator_languages)


# ── 1. 判定顺序 ───────────────────────────────────────────────────────────────
def test_self_reported_wins_over_inferred():
    resolution = _resolve({"language": "English", "language_inferred": "ja"})
    assert resolution["origin"] == ORIGIN_SELF_REPORTED
    assert resolution["values"] == ["en"]
    # 分歧不抹平:两边各自留在证据里,操作员看得见。
    assert resolution["self_reported_values"] == ["en"]
    assert resolution["inferred_values"] == ["ja"]


def test_inferred_fills_in_when_self_reported_is_empty():
    resolution = _resolve({"language": "", "language_inferred": "en",
                           "language_inferred_confidence": "high"})
    assert resolution["origin"] == ORIGIN_INFERRED
    assert resolution["values"] == ["en"]
    assert resolution["self_reported_values"] == []


def test_no_signal_at_all_stays_unknown():
    resolution = _resolve({"language": ""})
    assert resolution["origin"] == ORIGIN_UNKNOWN
    assert resolution["values"] == []


def test_alternative_key_spelling_is_accepted():
    """两条车道的拼法不同也要对得上,不许在中间掉值。"""
    assert _resolve({"inferred_language": "en",
                     "inferred_language_confidence": "high"})["origin"] == ORIGIN_INFERRED


def test_item_and_facets_fallbacks_keep_working():
    """三个容器都取得到值 —— 但归属各按各的证据算,不是一律「自报」。"""
    # item 装的仍是 vkpi_kol_pool.language 那一列(投影层原样搬过来),列契约作数。
    assert _resolve({}, {"language": "en"})["origin"] == ORIGIN_SELF_REPORTED
    # candidate_facets 是给前端重算分布的**展示投影**,不带来源、没有列契约可援引:
    # 值照旧参与硬筛,但绝不许被说成「他自己填的」。
    facets = _resolve({}, {"candidate_facets": {"language": "en"}})
    assert facets["values"] == ["en"]
    assert facets["origin"] == ORIGIN_PROJECTED
    assert facets["self_reported_values"] == []
    assert facets["projected_values"] == ["en"]
    assert _resolve({}, {"candidate_facets": {"language_inferred": "en",
                                              "language_inferred_confidence": "high"}},
                    )["origin"] == ORIGIN_INFERRED


# ── 2. 来源可追 ───────────────────────────────────────────────────────────────
def test_evidence_names_the_column_the_value_came_from():
    self_evidence = language_gate_evidence(
        _resolve({"language": "en"}),
        targets=["en"], filter_requested=True, invalid_targets=[], passed=True,
    )
    assert self_evidence["source"] == SELF_REPORTED_SOURCE
    assert self_evidence["origin"] == ORIGIN_SELF_REPORTED
    assert self_evidence["inferred"] is False
    assert "inference_basis" not in self_evidence

    inferred_evidence = language_gate_evidence(
        _resolve({"language_inferred": "en", "language_inferred_basis": "video_titles",
                  "language_inferred_method": "langdetect", "language_inferred_confidence": "high"}),
        targets=["en"], filter_requested=True, invalid_targets=[], passed=True,
    )
    assert inferred_evidence["source"] == INFERRED_SOURCE != SELF_REPORTED_SOURCE
    assert inferred_evidence["origin"] == ORIGIN_INFERRED
    assert inferred_evidence["inferred"] is True
    assert inferred_evidence["inference_basis"] == "video_titles"
    assert inferred_evidence["evidence_fields"] == ["video_titles"]
    assert inferred_evidence["inference_confidence"] == "high"


def test_evidence_keeps_every_pre_existing_key():
    evidence = language_gate_evidence(
        _resolve({"language": "en"}),
        targets=["en"], filter_requested=True, invalid_targets=["zz"], passed=False,
    )
    for key in ("values", "targets", "filter_requested", "invalid_targets", "passed", "source"):
        assert key in evidence, key


def test_inference_basis_never_carries_the_raw_text():
    """依据只出字段名。白名单外的一律丢掉 —— 简介原文绝不能顺着证据漏出去。"""
    evidence = language_gate_evidence(
        _resolve({"language_inferred": "en", "language_inferred_confidence": "high",
                  "language_inferred_basis": "Hi! My name is Ryth!"}),
        targets=["en"], filter_requested=True, invalid_targets=[], passed=True,
    )
    assert evidence["inference_basis"] is None
    assert evidence["evidence_fields"] == []


def test_bad_confidence_is_dropped_not_faked():
    resolution = _resolve({"language_inferred": "en", "language_inferred_confidence": "n/a"})
    assert resolution["inference_confidence"] is None


# ── 跨车道接缝:推断车道写什么列,本闸就得读得到什么列 ───────────────────────
def test_reads_the_real_migration_305_columns():
    """迁移 305 的真列名 —— 拼错一个字这一路就静默取不到值,必须钉死。"""
    resolution = _resolve({
        "language": None,
        "language_inferred": "en",
        "language_inferred_confidence": "high",     # 305 存的是档位文字,不是小数
        "language_inferred_source": "bio+video_titles",
        "language_inferred_method": "kol_content_langdetect_vote_v1",
    })
    assert resolution["origin"] == ORIGIN_INFERRED
    assert resolution["values"] == ["en"]
    assert resolution["inference_confidence"] == "high"
    assert resolution["inference_basis"] == "bio+video_titles"
    assert resolution["inference_method"] == "kol_content_langdetect_vote_v1"


def test_engine_verdict_shape_flows_straight_through_the_gate():
    """真推断器的返回体接到列上,本闸原样认得 —— 两条车道在这里对得上。"""
    from app.domains.kol.language_inference import infer_language_from_content

    verdict = infer_language_from_content(
        bio="Welcome to my channel! Here you can find videos on training and advice for photographers.",
        titles=["Behind the Scenes: How to Photograph a REAL Commercial Client"],
    )
    assert verdict["language"] == "en"
    resolution = _resolve({
        "language_inferred": verdict["language"],
        "language_inferred_confidence": verdict["confidence"],
        "language_inferred_source": verdict["source"],
        "language_inferred_method": verdict["method"],
    })
    assert resolution["origin"] == ORIGIN_INFERRED
    assert resolution["values"] == ["en"]
    assert resolution["inference_confidence"] == verdict["confidence"]
    assert resolution["inference_basis"] == verdict["source"]


def test_engine_unknown_verdict_leaves_the_person_in_the_unknown_bucket():
    """推断器说不知道,这个人就是「未知」—— 不许被硬塞一个值进来。"""
    from app.domains.kol.language_inference import infer_language_from_content

    verdict = infer_language_from_content(bio="🔥🔥🔥", titles=[])
    assert verdict["language"] is None
    resolution = _resolve({"language_inferred": verdict["language"]})
    assert resolution["origin"] == ORIGIN_UNKNOWN
    assert resolution["values"] == []


# ── 3. 一条闸都没放宽 ─────────────────────────────────────────────────────────
def _candidate(kol_id: int, **row_extra):
    row = {
        "kol_pool_id": kol_id,
        "platform": "youtube",
        "handle": f"h{kol_id}",
        "followers": 120_000,
        "country": "US",
        "bio": "camera lens review channel",
        **row_extra,
    }
    item = {"kol_pool_id": kol_id, "bucket": "creator", "platform": "youtube",
            "match_evidence": [{"field": "bio", "term": "lens", "source": "server_profile_evidence"}]}
    evidence = {
        "latest_video": {
            "posted_at": (NOW - timedelta(days=3)).isoformat(),
            "content_url": f"https://youtube.com/watch?v=v{kol_id}",
            "evidence_type": "video",
        },
        "used_lenses": ["lens"],
    }
    return row, item, evidence


def _qualify(rows_spec, *, languages=None):
    rows_by_id, evidence_by_id, items = {}, {}, []
    for kol_id, extra in rows_spec:
        row, item, evidence = _candidate(kol_id, **extra)
        rows_by_id[kol_id] = row
        evidence_by_id[kol_id] = evidence
        items.append(item)
    selected, _deferred, contract = qualification.qualify_local_candidates(
        buckets={"creator": items, "reviewer": []},
        rows_by_id=rows_by_id,
        evidence_by_id=evidence_by_id,
        policy=qualification.smart_local_policy(market="US", platforms=["youtube"], languages=languages),
        creator_quota=30,
        reviewer_quota=0,
        as_of=NOW,
    )
    return selected, contract


def test_thresholds_are_untouched_by_this_lane():
    assert qualification.SMART_LOCAL_MIN_FOLLOWERS == 3_000
    assert qualification.SMART_LOCAL_FRESH_DAYS == 30
    assert qualification.SMART_LOCAL_MAX_VIDEO_AGE_DAYS == 45
    # 语言未知 + 有语言筛选 = 仍然拦(缺省 require,与接入前一致)。
    policy = qualification.smart_local_policy(languages=["en"])
    assert policy["allow_unknown_language"] is False
    assert qualification.smart_local_policy()["allow_unknown_language"] is True


def test_inferred_language_passes_the_filter_and_is_labelled_inferred():
    selected, _ = _qualify([(901, {"language": "", "language_inferred": "en",
                                   "language_inferred_confidence": "high",
                                   "language_inferred_basis": "bio"})], languages=["en"])
    assert [item["kol_pool_id"] for item in selected] == [901]
    evidence = selected[0]["qualification_evidence"]["language"]
    assert evidence["values"] == ["en"]
    assert evidence["origin"] == ORIGIN_INFERRED
    assert evidence["inferred"] is True
    assert evidence["source"] == INFERRED_SOURCE


def test_self_reported_language_is_never_relabelled_as_inferred():
    selected, _ = _qualify([(902, {"language": "en"})], languages=["en"])
    evidence = selected[0]["qualification_evidence"]["language"]
    assert evidence["origin"] == ORIGIN_SELF_REPORTED
    assert evidence["inferred"] is False
    assert evidence["source"] == SELF_REPORTED_SOURCE


def test_inferred_mismatch_is_still_a_rejection():
    """推断出来的是日语,勾了英语 —— 照样不合格。接入推断不等于放行。"""
    selected, contract = _qualify([(903, {"language": "", "language_inferred": "ja",
                                          "language_inferred_confidence": "high"})], languages=["en"])
    assert selected == []
    assert contract["funnel"]["language_pass"] == 0


def test_still_unknown_after_inference_stays_rejected_under_require():
    selected, contract = _qualify([(904, {"language": ""})], languages=["en"])
    assert selected == []
    assert contract["rejected_by_reason"].get("language_unknown") == 1


def test_no_language_filter_still_admits_the_unknown_bucket():
    selected, _ = _qualify([(905, {"language": ""})])
    assert [item["kol_pool_id"] for item in selected] == [905]
    assert selected[0]["qualification_evidence"]["language"]["origin"] == ORIGIN_UNKNOWN


# ── H6 归属真相:「language 非空」不等于「他自己填的」 ───────────────────────
#
# 复核原文:``resolve_candidate_language`` 把「``language`` 字段非空」直接等同于
# 「他自己填的」,从不读 ``language_source`` / facet evidence,于是别处投影来的值
# 会被贴上「自报」标签。下面这一组把**生产里真实存在的那条路径**钉住。


def test_online_lane_langdetect_result_is_never_stamped_self_reported():
    """最要命的一条:在线腿当场用 langdetect 判出来的语言,曾被说成「他自己填的」。

    ``profile_online_qualification._candidate_row`` 把
    ``profile_online_facets.adapt_language`` 的出参直接落进 ``row["language"]``,
    而那个值在没有平台声明时是**我们自己**从公开简介里检测出来的
    (``source = provider_public_content_language_v1``)。旧口径只看「非空」,
    于是给它盖了「自报」的章,``source`` 还指向一个与它无关的数据库列。
    """
    from app.domains.kol import profile_online_facets

    evidence = profile_online_facets.adapt_language({
        "bio": "Hey everyone, welcome back to the channel where I test camera lenses "
               "and share honest reviews about the gear I actually use every week.",
    })
    assert evidence["source"] == "provider_public_content_language_v1"
    assert evidence["value"] == "en"

    # _candidate_row 的真实形状:值 + 来源串 + facet 证据块,三处都落。
    row = {
        "language": evidence["value"],
        "language_source": evidence["source"],
        "facet_evidence": {"language": evidence},
    }
    resolution = _resolve(row)
    assert resolution["values"] == ["en"]          # 去留不变:照旧参与硬筛
    assert resolution["origin"] == ORIGIN_INFERRED  # 但它是我们推断的
    assert resolution["self_reported_values"] == []  # 他一个字都没填过
    assert resolution["inferred_values"] == ["en"]

    gate = language_gate_evidence(
        resolution, targets=["en"], filter_requested=True, invalid_targets=[], passed=True,
        self_source="online_provider.language",
    )
    assert gate["origin"] == ORIGIN_INFERRED
    assert gate["inferred"] is True
    assert gate["self_reported"] is False
    # 来源必须指向真正干活的那条腿,不许冒充迁移 305 那一列。
    assert gate["source"] == "provider_public_content_language_v1"
    assert gate["source"] != INFERRED_SOURCE != SELF_REPORTED_SOURCE


def test_video_metadata_language_is_projected_not_self_reported():
    """``platform_content_metadata`` = 平台对**这条片子**的音轨标注,
    不是他对**自己**的声明。这正是复核说的「别处投影来的值」。"""
    resolution = _resolve({"language": "ja", "language_source": "platform_content_metadata"})
    assert resolution["values"] == ["ja"]              # 值照旧参与硬筛
    assert resolution["origin"] == ORIGIN_PROJECTED
    assert resolution["self_reported_values"] == []
    assert resolution["projected_values"] == ["ja"]
    assert resolution["origin_source"] == "platform_content_metadata"


def test_platform_profile_source_is_the_one_that_earns_self_reported():
    """真的是平台资料声明时,照旧标「自报」—— 修的是假话,不是把真话也删掉。"""
    resolution = _resolve({"language": "en", "language_source": "platform_profile"})
    assert resolution["origin"] == ORIGIN_SELF_REPORTED
    assert resolution["self_reported_values"] == ["en"]
    assert resolution["projected_values"] == []


def test_facet_evidence_block_is_read_when_the_scalar_key_is_missing():
    """来源也可能只落在 facet 证据块里(``facet_evidence.language.source``)。"""
    assert language_source_token([{"facet_evidence": {"language": {"source": "PLATFORM_PROFILE"}}}]) == "platform_profile"
    assert language_source_token([{"language_evidence": {"source": "content_inference_v1"}}]) == "content_inference_v1"
    assert language_source_token([{"language": "en"}]) == ""
    resolution = _resolve({"language": "de", "facet_evidence": {"language": {
        "source": "content_inference_v1", "confidence": 0.93, "evidence_fields": ["bio"]}}})
    assert resolution["origin"] == ORIGIN_INFERRED
    assert resolution["inference_confidence"] == 0.93


def test_an_unreadable_source_never_defaults_to_self_reported():
    """读不出来源就**不许**默认成「自报」—— 那正是在编造。

    ``row`` / ``item`` 走列契约(迁移 039/305 把 ``vkpi_kol_pool.language``
    定义成平台/创作者的自我声明),那是有据可查的一句话;
    展示投影与任何认不出来的来源串,一律落进「证不出」的 ``projected``。
    """
    assert classify_language_origin("", container="row") == ORIGIN_SELF_REPORTED
    assert classify_language_origin("", container="item") == ORIGIN_SELF_REPORTED
    assert classify_language_origin("", container="facets") == ORIGIN_PROJECTED
    assert classify_language_origin("", container="") == ORIGIN_PROJECTED
    for token in ("online_provider_unverified", "some_upstream_blob", "unknown", "n/a"):
        assert classify_language_origin(token, container="row") == ORIGIN_PROJECTED, token


def test_every_inference_marker_is_classified_as_inferred():
    """来源串一旦带上推断印记,不管它出自哪条腿,都不许算「自报」。"""
    for token in (
        "provider_public_content_language_v1", "content_inference_v1",
        "inferred_from_public_text", "kol_content_langdetect_vote_v1",
        "derived_from_titles", "language_detector_v2", "estimated_language",
    ):
        assert classify_language_origin(token, container="row") == ORIGIN_INFERRED, token


def test_attribution_changes_the_claim_but_never_a_single_person_s_fate():
    """本车道的立身之本:修的是「谁说的」,不是「谁进得来」。

    同一个值,配上四种不同的来源,``values``(唯一参与硬筛比对的那组)必须逐字相同 ——
    归属分层不许顺手多丢一个人,也不许顺手多放一个人。
    """
    baseline = _resolve({"language": "en"})["values"]
    assert baseline == ["en"]
    for source in ("platform_profile", "platform_content_metadata",
                   "provider_public_content_language_v1", "", "who_knows"):
        row = {"language": "en"}
        if source:
            row["language_source"] = source
        assert _resolve(row)["values"] == baseline, source


def test_evidence_carries_a_second_line_of_defence_for_the_chrome():
    """门面万一没认出 ``origin`` 这个字符串,也不许退化成「自报」。

    ``self_reported`` 是明牌布尔:**证不出就是 False**。
    """
    for row, expected in (
        ({"language": "en", "language_source": "platform_profile"}, True),
        ({"language": "en", "language_source": "platform_content_metadata"}, False),
        ({"language": "en", "language_source": "provider_public_content_language_v1"}, False),
        ({"language": "", "language_inferred": "en", "language_inferred_confidence": "high"}, False),
        ({"language": ""}, False),
    ):
        evidence = language_gate_evidence(
            _resolve(row), targets=["en"], filter_requested=True,
            invalid_targets=[], passed=True,
        )
        assert evidence["self_reported"] is expected, row
        # 「他自己填的是……」那半句只能由这一组拼出来,证不出就必须是空的。
        if not expected:
            assert evidence["self_reported_values"] == [], row


def test_projected_value_is_still_a_rejection_when_it_mismatches():
    """分出 ``projected`` 不是给它开后门:对不上照样不合格。"""
    selected, contract = _qualify(
        [(906, {"language": "ja", "language_source": "platform_content_metadata"})],
        languages=["en"],
    )
    assert selected == []
    assert contract["funnel"]["language_pass"] == 0


def test_projected_value_still_passes_the_filter_when_it_matches():
    """反过来也一样:证不出是自报,不等于把这个人删掉。"""
    selected, _ = _qualify(
        [(907, {"language": "en", "language_source": "platform_content_metadata"})],
        languages=["en"],
    )
    assert [item["kol_pool_id"] for item in selected] == [907]
    evidence = selected[0]["qualification_evidence"]["language"]
    assert evidence["origin"] == ORIGIN_PROJECTED
    assert evidence["self_reported"] is False
    assert evidence["inferred"] is False
    assert evidence["projected_values"] == ["en"]


# ── 置信门槛:同分布重估之后落在 medium ─────────────────────────────────────


def test_confidence_floor_requires_a_corroborated_inference():
    """``low`` 档 = 只有一条文本给出判定,投票机制根本没起作用。

    同分布重估(自报为空 + low 档的 327 人里随机抽 40 人人工核对)得出
    「判成英语」这一票的真实准确率 32/35 = 91.4%,95% CI [77.6%, 97.0%] ——
    旧决策所依赖的 96.7% 站不住,因此门槛落在 ``medium``。依据全文见
    ``profile_recall_language_gate.MIN_INFERRED_CONFIDENCE`` 的常量注释。
    """
    assert MIN_INFERRED_CONFIDENCE == "medium"
    low = _resolve({"language": "", "language_inferred": "en",
                    "language_inferred_confidence": "low"})
    assert low["values"] == []
    # 被门槛挡下的人是「未知」,不是「不合格」——与新鲜闸拆桶同口径。
    assert low["origin"] == ORIGIN_UNKNOWN
    # 而且不许把这一票藏起来:操作员要看得见「我们其实有一个没敢用的判断」。
    assert low["inferred_values"] == ["en"]
    assert low["inference_confidence"] == "low"
    assert low["inference_below_floor"] == "medium"
    for tier in ("medium", "high"):
        assert _resolve({"language": "", "language_inferred": "en",
                         "language_inferred_confidence": tier})["origin"] == ORIGIN_INFERRED


def test_a_person_blocked_by_the_floor_is_recoverable_via_include_unknown():
    """挡下来的人拿得回来 —— 这是这一刀敢下的前提。"""
    blocked, contract = _qualify(
        [(908, {"language": "", "language_inferred": "en",
                "language_inferred_confidence": "low"})],
        languages=["en"],
    )
    assert blocked == []
    assert contract["rejected_by_reason"].get("language_unknown") == 1
    # 没点语言筛选时,「未知」档照旧进得来(缺省 allow_unknown_language=True)。
    admitted, _ = _qualify([(909, {"language": "", "language_inferred": "en",
                                   "language_inferred_confidence": "low"})])
    assert [item["kol_pool_id"] for item in admitted] == [909]
