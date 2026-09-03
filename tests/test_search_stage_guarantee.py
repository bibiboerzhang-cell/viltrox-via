"""四段抓取保证:每一段要么出数据,要么给「为什么 + 下一步」。

背景(2026-09-03 取证):本地全量里最大的几个失败桶(档案深抓 `url_unknown_unsupported` 202、
视频 `non_video_post` 14、内容匹配 `llm_json_malformed` 12 / `budget_blocked` 7、评论
`comments_collect_failed`)的写点都绕开了 ``apify_jobs_worker._block_job``,``last_error_category``
恒 NULL —— 分类只能靠文本标记。补标记之前它们全部落 ``unknown``「分析未完成:原因待排查」,
在原因轴上挤成一坨「未分类」,用户看到的只有「未请求 / 可用数据 0」。

本文件钉三件事:
1. 这些桶在类别缺失时也必须classify出人话 + 封闭动作码,一条都不许回落 unknown;
2. 进度契约的每个 stage 在「没数据」时必须带 reason(失败原因或未请求原因),有数据时**不长这个键**
   (冻结金串 ``test_kol_search_progress_contract_characterization`` 逐字不变);
3. 所有用户可见文案零内部术语,且封闭词表跨前后端同步。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.domains.kol import search_progress_contract as progress_contract
from app.domains.kol import search_progress_projection as projection
from app.domains.kol import video_analysis_progress_reasons as reasons

ROOT = Path(__file__).resolve().parents[1]
FAILURE_REASON_TS = ROOT / "frontend/src/services/vkpi/failureReason.ts"
OBSERVED_AT = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)

# 门面禁内部术语(逐条对照红线);大小写不敏感,整块文案扫描。
JARGON = re.compile(
    r"llm|lexicon|rule_v0|词表|embedding|qdrant|apify|payload|final_v1|yt-?dlp|"
    r"media_resolve|provider|\bjob\b|作业队列",
    re.IGNORECASE,
)

# (last_error 原文, 期望类别, 期望动作码, 是否「本就不适用」);全部模拟 last_error_category 缺失。
UNCATEGORISED_BUCKETS: tuple[tuple[str, str, str, bool], ...] = (
    ("url_unknown_unsupported", "download", "switch_source", True),       # 档案深抓 202 条最大桶
    ("cn_platform_video_only", "download", "switch_source", True),
    ("unsupported_platform", "download", "switch_source", True),
    ("non_video_post_no_video_signal", "download", "none", True),         # 视频 14 条
    ("media_resolve_failed:instagram:scraped_no_downloadable_url", "download", "none", True),
    ("comments_collect_failed", "download", "retry", False),              # 评论段:类别恒 NULL
    ("no_posts", "download", "retry", False),
    ("no_commenters", "download", "none", True),
    ("comments_job_not_ready", "download", "wait_auto_retry", False),
    ("llm_json_malformed", "model", "retry", False),                      # 内容匹配 12 条
    ("budget_blocked", "budget", "check_budget", False),                  # 内容匹配 7 条
    ("deep_crawl_not_executed", "download", "retry", False),
    ("insufficient_evidence", "download", "retry", False),
)


def _worker() -> dict[str, Any]:
    return {"observed": False, "online": None, "reason": "fixture"}


def _project(session: dict[str, Any], items: list[Any]) -> dict[str, Any]:
    return progress_contract.project_search_progress(
        session, items, worker_health=_worker(), observed_at=OBSERVED_AT
    )


def _failing_item(item_id: int = 1) -> dict[str, Any]:
    """四段各带一条真实原因串的会话项(全部是 last_error_category 缺失的形态)。"""
    return {
        "id": item_id,
        "status": "failed",
        "stage": "profile",
        "payload": {
            "profile_execute": {"status": "failed", "last_error": "url_unknown_unsupported"},
            "downstream_jobs": {
                "video": {"state": "failed", "job_ids": [201], "last_error": '{"reason": "non_video_post"}'},
                "comments": {"state": "failed", "job_ids": [202], "last_error": "comments_collect_failed"},
                "audience": {"state": "failed", "job_ids": [203], "last_error": "unsupported_platform"},
            },
        },
    }


# --------------------------------------------------------------------------- 1


@pytest.mark.parametrize(("text", "category", "next_step", "not_applicable"), UNCATEGORISED_BUCKETS)
def test_uncategorised_buckets_never_fall_back_to_unknown(
    text: str, category: str, next_step: str, not_applicable: bool
) -> None:
    """类别列恒 NULL 的最大失败桶:必须靠文本标记分对类、给人话、给动作码。"""
    fields = reasons.failure_guidance_fields(status="blocked", last_error_category=None, last_error=text)

    assert fields["failure_category"] == category, text
    assert fields["failure_category"] != "unknown", text
    assert fields["failure_reason_human"] != reasons.UNKNOWN_REASON_HUMAN, text
    assert fields["failure_next_step"] == next_step, text
    assert fields["failure_not_applicable"] is not_applicable, text
    assert fields["failure_next_step"] in reasons.NEXT_STEPS


def test_profile_url_reason_names_the_supported_platforms() -> None:
    fields = reasons.failure_guidance_fields(
        status="blocked", last_error_category="blocked", last_error='{"reason": "url_unknown_unsupported"}'
    )
    human = fields["failure_reason_human"]
    for platform in ("YouTube", "Instagram", "TikTok"):
        assert platform in human
    assert fields["failure_code"] == "url_unknown_unsupported"
    assert fields["failure_next_step"] == "switch_source"


def test_instagram_image_post_no_longer_reported_as_a_download_failure() -> None:
    """回归:图文帖此前错报成「视频下载失败:平台限制或代理不稳」,把「换链接」变成「等重试」。"""
    fields = reasons.failure_guidance_fields(
        status="blocked",
        last_error_category="media_resolve",
        last_error="media_resolve_failed:instagram:scraped_no_downloadable_url",
    )
    assert "下载失败" not in fields["failure_reason_human"]
    assert "没有视频" in fields["failure_reason_human"]
    assert (fields["failure_next_step"], fields["failure_not_applicable"]) == ("none", True)


def test_failure_fields_stays_a_three_key_frozen_contract() -> None:
    """加法兼容:六类元组与既有三键一字不动,新语义只走 failure_guidance_fields。"""
    assert reasons.FAILURE_CATEGORIES == ("download", "authorization", "budget", "model", "provider", "unknown")
    assert set(reasons.failure_fields(status="blocked", last_error_category=None, last_error="x")) == {
        "failure_category", "failure_reason_human", "failure_code",
    }
    assert reasons.failure_fields(status="running", last_error_category=None, last_error=None) == {
        "failure_category": None, "failure_reason_human": None, "failure_code": None,
    }
    assert reasons.failure_guidance_fields(status="running", last_error_category=None, last_error=None) == {
        "failure_category": None, "failure_reason_human": None, "failure_code": None,
        "failure_next_step": None, "failure_not_applicable": None,
    }


# --------------------------------------------------------------------------- 2


def test_every_failing_stage_carries_a_reason_with_a_next_step() -> None:
    result = _project({"status": "partial", "result_summary": {"progress": {"total": 1}}}, [_failing_item()])

    for role in ("profile", "video", "comments", "audience"):
        reason = result["stages"][role].get("reason")
        assert reason is not None, role
        assert reason["kind"] == "failure", role
        assert reason["failure_reason_human"].strip(), role
        assert reason["failure_reason_human"] != reasons.UNKNOWN_REASON_HUMAN, role
        assert reason["next_step"] in reasons.NEXT_STEPS, role
        assert reason["affected"] >= 1, role
    codes = {role: result["stages"][role]["reason"]["failure_code"] for role in ("profile", "video", "comments")}
    assert codes == {
        "profile": "url_unknown_unsupported",
        "video": "non_video_post",
        "comments": "comments_collect_failed",
    }


def test_empty_session_explains_why_every_stage_is_not_requested() -> None:
    """prod 那一幕:0/0 已返回 + 四段「未请求」,现在每段都说得出为什么。"""
    result = _project({"status": "ready", "result_summary": {"phase": "", "progress": None}}, [])

    for role in ("search", "profile", "video", "comments", "audience"):
        reason = result["stages"][role]["reason"]
        assert reason["kind"] == "not_requested", role
        assert reason["not_requested_reason"] == "no_candidates", role
        assert reason["not_requested_reason"] in projection.STAGE_NOT_REQUESTED_REASONS
        assert reason["human"].strip(), role
        assert reason["next_step"] in reasons.NEXT_STEPS, role
    # 诚实空态不许被伪装成完成
    assert result["stages"]["comments"]["data_ready"] is None
    assert result["completion_kind"] == "empty_result"


def test_downstream_stages_blame_the_upstream_when_profile_never_finished() -> None:
    item = {
        "id": 1,
        "status": "partial",
        "stage": "profile",
        "payload": {"profile_execute": {"status": "queued"}, "downstream_jobs": {}},
    }
    stages = _project({"status": "running", "result_summary": {"progress": {"total": 1}}}, [item])["stages"]

    for role in ("video", "comments", "audience"):
        assert stages[role]["reason"]["not_requested_reason"] == "upstream_incomplete", role
    assert stages["profile"].get("reason") is None  # 档案段在跑,没有可说的原因 → 不编


def test_stage_without_evidence_grows_no_reason_key_at_all() -> None:
    """诚实空态 + 保住冻结金串:失败但没有任何原因证据时,一个字都不渲(键都不长)。"""
    item = {
        "id": 1,
        "status": "failed",
        "stage": "profile",
        "payload": {
            "profile_execute": {"status": "failed"},
            "downstream_jobs": {"video": {"state": "failed", "job_ids": [1]}},
        },
    }
    stages = _project({"status": "partial", "result_summary": {"progress": {"total": 1}}}, [item])["stages"]

    assert "reason" not in stages["profile"]
    assert "reason" not in stages["video"]


def test_comments_stage_keeps_its_completion_semantics_and_flags_unobservable() -> None:
    """裁决(a):不改完成度口径 —— data_ready 仍是 None,「本段不可观测」只写进 reason。"""
    stages = _project({"status": "partial", "result_summary": {"progress": {"total": 1}}}, [_failing_item()])["stages"]

    assert stages["comments"]["data_ready"] is None
    assert stages["comments"]["data_ready_basis"] == "not_observable_from_session"
    assert stages["comments"]["reason"]["data_observable"] is False
    for role in ("profile", "video", "audience"):
        assert stages[role]["reason"]["data_observable"] is True


@pytest.mark.parametrize(
    ("state", "code", "next_step", "not_applicable"),
    [
        ("no_comments", "no_comments", "none", True),
        ("no_posts", "no_posts", "retry", False),
        ("no_data", "no_posts", "retry", False),
    ],
)
def test_partial_state_reasons_come_from_the_downstream_state_itself(
    state: str, code: str, next_step: str, not_applicable: bool
) -> None:
    """no_posts / no_comments 这类「状态本身就是原因」的段,没有 last_error 也要说得出话。"""
    item = {
        "id": 1,
        "status": "partial",
        "stage": "summary",
        "kol_pool_id": 9,
        "payload": {
            "profile_execute": {"status": "ready", "kol_pool_id": 9},
            "downstream_jobs": {"comments": {"state": state, "job_ids": [7]}},
        },
    }
    reason = _project({"status": "partial", "result_summary": {"progress": {"total": 1}}}, [item])["stages"]["comments"]["reason"]

    assert reason["failure_code"] == code
    assert reason["failure_not_applicable"] is not_applicable
    assert reason["next_step"] == next_step
    assert projection._STATE_REASON_CODES[state] == code


# --------------------------------------------------------------------------- 3


def _all_user_facing_copy() -> list[str]:
    copy = [human for _category, _markers, human in reasons._HUMAN_RULES]
    copy.extend(human for human, _step in projection._NOT_REQUESTED_COPY.values())
    copy.append(reasons.UNKNOWN_REASON_HUMAN)
    return copy


@pytest.mark.parametrize("text", _all_user_facing_copy())
def test_no_internal_jargon_reaches_the_user(text: str) -> None:
    match = JARGON.search(text)
    assert match is None, f"内部术语 {match.group(0)!r} 出现在门面文案:{text}"


def test_every_stage_reason_string_is_jargon_free_end_to_end() -> None:
    stages = _project({"status": "partial", "result_summary": {"progress": {"total": 1}}}, [_failing_item()])["stages"]
    for role, stage in stages.items():
        reason = stage.get("reason")
        if reason is None:
            continue
        match = JARGON.search(reason["human"])
        assert match is None, f"{role}: {match.group(0) if match else ''} in {reason['human']}"


def test_next_step_vocabulary_is_closed_and_mirrored_in_the_frontend() -> None:
    """封闭词表跨语言同步:动作码/未请求原因码在两侧一致,不然前端静默落回 unknown。"""
    source = FAILURE_REASON_TS.read_text(encoding="utf-8")
    for step in reasons.NEXT_STEPS:
        assert f'"{step}"' in source, step
    for code in projection.STAGE_NOT_REQUESTED_REASONS:
        assert f'"{code}"' in source, code
    # 每个动作码在前端都要有对应的提示条目,否则按钮/提示会静默消失
    for step in reasons.NEXT_STEPS:
        assert re.search(rf"\b{re.escape(step)}: \{{ action:", source), step


def test_every_category_and_marker_rule_yields_a_known_next_step() -> None:
    for category in reasons.FAILURE_CATEGORIES:
        assert reasons._NEXT_STEP_BY_CATEGORY[category] in reasons.NEXT_STEPS
    for _markers, step, not_applicable in reasons._NEXT_STEP_RULES:
        assert step in reasons.NEXT_STEPS
        assert isinstance(not_applicable, bool)
