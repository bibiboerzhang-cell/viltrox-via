"""High-branch-value characterization tables for the first coverage pilot."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool_url_crawl_orchestration as url_orchestration
from app.api.routers import vkpi_projects_lifecycle_routes as lifecycle_routes
from app.domains.access import scope
from app.domains.kol import product_fit_evidence
from app.domains.projects import workflow_assignment_feedback
from app.services.projects.creator_lifecycle_adapters import DEFAULT_CLAIM_LIFECYCLE_PORT


def test_catalog_fit_evidence_decision_table() -> None:
    family = {"id": 7, "entity_uid": "family-7"}
    no_match_products = [{"sku": "catalog-default"}]
    evidence_pro: list[dict[str, Any]] = []
    product, products, matched = product_fit_evidence.append_catalog_fit_evidence(
        family,
        {"dimensions11_match": None},
        evidence_pro,
        catalog_products_for_match=lambda match, received_family: (
            no_match_products
            if match is None and received_family is family
            else []
        ),
        catalog_product_for_sku=lambda _sku: None,
    )
    assert (product, products, matched, evidence_pro) == (
        no_match_products[0],
        no_match_products,
        False,
        [],
    )

    dimensions_match = {
        "sku": "AF 16mm F1.8 FE",
        "score": 91,
        "confidence": 0.93,
        "profile_deep_id": 44,
    }
    fallback_product = {"sku": "AF 16mm F1.8 FE", "source": "sku-fallback"}
    evidence_pro = []
    product, products, matched = product_fit_evidence.append_catalog_fit_evidence(
        family,
        {"dimensions11_match": dimensions_match},
        evidence_pro,
        catalog_products_for_match=lambda match, received_family: (
            [] if match is dimensions_match and received_family is family else ["unexpected"]
        ),
        catalog_product_for_sku=lambda sku: fallback_product if sku == dimensions_match["sku"] else None,
    )
    assert (product, products, matched) == (fallback_product, [], True)
    assert evidence_pro == [
        {
            "type": "dimensions11_product_fit",
            "polarity": "pro",
            "severity": "info",
            "detail": "11D product fit matched AF 16mm F1.8 FE score=91/100 confidence=0.93",
            "score_component": "dimensions11_product_fit",
            "source_table": "vkpi_kol_profile_deep",
            "source_id": "44",
            "source_ref": "vkpi_kol_profile_deep:44",
            "source_sheet": "dimensions_11_json.block4_specialty.product_fit",
            "source_row": "",
            "confidence_score": 0.93,
        }
    ]


def test_activity_and_readiness_evidence_decision_tables() -> None:
    evidence_pro: list[dict[str, Any]] = []
    product_fit_evidence.append_activity_fit_evidence(
        7,
        {
            "market_evidence": [
                {
                    "id": 1,
                    "fact_type": "launch_plan",
                    "fact_json": '{"signal_type":"launch_plan","product_name":"A"}',
                },
                {
                    "id": 2,
                    "fact_type": "official_content",
                    "fact_json": '{"signal_type":"official_content","product_name":"B"}',
                },
                {
                    "id": 3,
                    "fact_type": "ignored_third_signal",
                    "fact_json": "{}",
                },
            ]
        },
        {
            "links": [{"id": 9, "source_json": '{"source_ref":"link:9"}'}],
            "cooperation_count": 2,
            "official_links": {},
        },
        evidence_pro,
    )
    assert [item["type"] for item in evidence_pro] == [
        "cooperation_depth",
        "launch_plan",
        "official_content",
    ]

    official_only: list[dict[str, Any]] = []
    product_fit_evidence.append_activity_fit_evidence(
        7,
        {"market_evidence": []},
        {
            "links": [],
            "cooperation_count": 0,
            "official_links": {
                7: [{"id": 10, "source_json": '{"source_ref":"official:10"}'}]
            },
        },
        official_only,
    )
    assert [item["type"] for item in official_only] == ["official_account_activity"]

    no_activity: list[dict[str, Any]] = []
    product_fit_evidence.append_activity_fit_evidence(
        7,
        {"market_evidence": []},
        {"links": [], "cooperation_count": 0, "official_links": {}},
        no_activity,
    )
    assert no_activity == []

    readiness_pro: list[dict[str, Any]] = []
    readiness_con: list[dict[str, Any]] = []
    product_fit_evidence.append_readiness_fit_evidence(
        {"region_reason": "target_market"},
        {
            "contact_fact": {"id": 11, "fact_json": '{"channel":"email"}'},
            "contact_score": 7,
            "contact_label": "email_available_restricted",
            "country_fact": {"id": 12, "fact_json": '{"country":"US"}'},
            "country": "United States",
            "evidence_fact": {"id": 13, "fact_json": '{"count":5}'},
            "evidence_count": 5,
        },
        readiness_pro,
        readiness_con,
    )
    assert [item["type"] for item in readiness_pro] == [
        "contact_available",
        "region_relevance",
        "data_quality",
    ]
    assert readiness_con == []

    readiness_pro = []
    readiness_con = []
    product_fit_evidence.append_readiness_fit_evidence(
        {"region_reason": "unknown"},
        {
            "contact_fact": {},
            "contact_score": 0,
            "contact_label": "missing",
            "country_fact": {},
            "country": "",
            "evidence_fact": {},
            "evidence_count": 0,
        },
        readiness_pro,
        readiness_con,
    )
    assert readiness_pro == []
    assert [item["type"] for item in readiness_con] == [
        "contact_missing",
        "missing_country",
        "missing_evidence_count",
    ]


def test_assignment_feedback_missing_success_and_failure_ports_are_non_blocking() -> None:
    class RecordingLogger:
        def __init__(self) -> None:
            self.warnings: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def warning(self, *args: Any, **kwargs: Any) -> None:
            self.warnings.append((args, kwargs))

    class FeedbackSink:
        def __init__(self, *, fail: bool = False) -> None:
            self.fail = fail
            self.calls: list[tuple[Any, ...]] = []

        def record_pool_action(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            self.calls.append((args, kwargs))
            if self.fail:
                raise RuntimeError("feedback unavailable")
            return {"recorded": True}

    logger = RecordingLogger()
    updated = {"id": 19, "kol_pool_id": 88}
    staff = {"id": 3}
    workflow_assignment_feedback.record_contact_feedback(
        None,
        updated,
        project_id=5,
        staff=staff,
        logger=logger,
    )
    assert logger.warnings[0][0][0].startswith("project.feedback_sink_missing")

    successful = FeedbackSink()
    workflow_assignment_feedback.record_contact_feedback(
        successful,
        updated,
        project_id=5,
        staff=staff,
        logger=logger,
    )
    assert successful.calls == [
        (
            (88, "contact"),
            {
                "staff": staff,
                "payload": {"stage": "contacted", "project_id": 5, "assignment_id": 19},
                "source": "assignment_stage",
            },
        )
    ]

    failing = FeedbackSink(fail=True)
    workflow_assignment_feedback.record_contact_feedback(
        failing,
        updated,
        project_id=5,
        staff=staff,
        logger=logger,
    )
    assert logger.warnings[-1][0][0].startswith("project.feedback_sink_failed")
    assert logger.warnings[-1][1] == {"exc_info": True}


class _UrlQueueDouble:
    SUPPORTED_PLATFORMS = {"youtube"}
    CN_VIDEO_ANALYSIS_PLATFORMS = {"bilibili"}

    def __init__(self, *, fresh: bool = False) -> None:
        self.fresh = fresh
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def profile_deep_crawl_is_fresh(self, kol_pool_id: Any) -> bool:
        self.calls.append(("fresh", (kol_pool_id,), {}))
        return self.fresh

    def enqueue_stored_video_analysis_job(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("stored", (), kwargs))
        return {"status": "queued", "job_id": 1, "write_db": True}

    def enqueue_video_url_resolve_job(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("resolver", args, kwargs))
        return {"status": "queued", "job_id": 2, "write_db": True}

    def enqueue_profile_deep_crawl_job(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("profile", args, kwargs))
        return {"status": "queued", "job_id": 3}


def _int_or_none(value: Any) -> int | None:
    return int(value) if value not in (None, "") else None


def test_url_enqueue_four_way_decision_table() -> None:
    cases = [
        {
            "name": "stored_video",
            "classified": SimpleNamespace(url_type="video", platform="youtube"),
            "result": {"matched_kol_pool_id": "8", "video_flow": {"evidence_id": "9"}},
            "fresh": False,
            "expected_call": "stored",
            "expected_flags": (False, True),
        },
        {
            "name": "fresh_profile",
            "classified": SimpleNamespace(url_type="profile", platform="youtube"),
            "result": {"matched_kol_pool_id": "8", "profile_flow": {}},
            "fresh": True,
            "expected_call": "fresh",
            "expected_flags": (True, False),
        },
        {
            "name": "video_resolver",
            "classified": SimpleNamespace(url_type="video", platform="youtube"),
            "result": {"matched_kol_pool_id": None, "video_flow": None},
            "fresh": False,
            "expected_call": "resolver",
            "expected_flags": (False, False),
        },
        {
            "name": "profile_queue",
            "classified": SimpleNamespace(url_type="profile", platform="youtube"),
            "result": {"matched_kol_pool_id": None, "profile_flow": {}},
            "fresh": False,
            "expected_call": "profile",
            "expected_flags": (True, False),
        },
    ]
    for case in cases:
        queue = _UrlQueueDouble(fresh=case["fresh"])
        state = url_orchestration._enqueue_deferred_work(
            body={"url": "https://example.test/item", "max_posts": 4},
            result=case["result"],
            session={"id": "44"},
            classified=case["classified"],
            staff={"id": 3},
            default_source="pilot",
            url_deep_crawl=queue,
            int_or_none=_int_or_none,
            reused_video_session_lineage=lambda *_args, **_kwargs: ({"id": "44"}, 55),
            prepare_video_resolver_session_item=lambda *_args: 66,
        )
        assert (state["is_profile"], state["reused_stored_video"]) == case[
            "expected_flags"
        ], case["name"]
        assert queue.calls[-1][0] == case["expected_call"], case["name"]
        if case["name"] == "fresh_profile":
            assert state["queued"] == {"status": "already_fresh", "job_id": None}


def test_url_defer_and_flow_message_decision_tables() -> None:
    queue = _UrlQueueDouble()
    defer_cases = [
        (None, False),
        (SimpleNamespace(url_type="profile", platform="youtube"), True),
        (SimpleNamespace(url_type="video", platform="bilibili"), True),
        (SimpleNamespace(url_type="profile", platform="bilibili"), False),
        (SimpleNamespace(url_type="unknown", platform="other"), False),
    ]
    assert [
        url_orchestration._should_defer_provider(classified, queue)
        for classified, _expected in defer_cases
    ] == [expected for _classified, expected in defer_cases]

    message_cases = [
        (
            dict(
                already_fresh=True,
                reused_stored_video=False,
                queue_active=False,
                video_resolver_queued=False,
                direct_video_status="",
            ),
            "账号资料在 24 小时内已更新，直接复用现有档案。",
        ),
        (
            dict(
                already_fresh=False,
                reused_stored_video=True,
                queue_active=True,
                video_resolver_queued=False,
                direct_video_status="queued",
            ),
            "已复用本地视频证据并排入 final_v1 深析。",
        ),
        (
            dict(
                already_fresh=False,
                reused_stored_video=True,
                queue_active=False,
                video_resolver_queued=False,
                direct_video_status="ai_disabled",
            ),
            "已复用本地视频证据；AI 深析当前未启用，本轮没有创建模型任务。",
        ),
        (
            dict(
                already_fresh=False,
                reused_stored_video=True,
                queue_active=False,
                video_resolver_queued=False,
                direct_video_status="ready",
            ),
            "已复用本地视频证据与现有分析。",
        ),
        (
            dict(
                already_fresh=False,
                reused_stored_video=False,
                queue_active=True,
                video_resolver_queued=True,
                direct_video_status="queued",
            ),
            "已进入视频 URL 专用队列；将按解析视频、识别作者、缓存媒体、AI 分析分阶段回填。",
        ),
        (
            dict(
                already_fresh=False,
                reused_stored_video=False,
                queue_active=True,
                video_resolver_queued=False,
                direct_video_status="",
            ),
            "已进入后台队列；抓取、联系方式、受众和视频分析结果会分阶段回填。",
        ),
    ]
    assert [url_orchestration._flow_message(**values) for values, _ in message_cases] == [
        expected for _, expected in message_cases
    ]


def test_url_http_hidden_inputs_reject_and_non_deferred_path_stays_read_only() -> None:
    class CrawlDouble(_UrlQueueDouble):
        def classify_url(self, _url: str) -> Any:
            raise AssertionError("execute=false must not classify or queue")

        def dry_run_url_deep_crawl(self, body: dict[str, Any]) -> dict[str, Any]:
            assert body["execute"] is False
            return {"url_type": "unknown", "execute": False}

    class SessionDouble:
        def __init__(self) -> None:
            self.ensure: dict[str, Any] | None = None

        def ensure_session_for_result(self, **kwargs: Any) -> None:
            self.ensure = kwargs
            return None

        def attach_url_result(self, *_args: Any) -> Any:
            raise AssertionError("no session means no attachment")

    dependencies = {
        "staff": {"id": 3},
        "default_create_session": False,
        "default_source": "pilot",
        "url_deep_crawl": CrawlDouble(),
        "search_sessions": SessionDouble(),
        "body_bool": lambda body, key, default=False: bool(body.get(key, default)),
        "int_or_none": _int_or_none,
        "reused_video_session_lineage": lambda *_args, **_kwargs: ({"id": 1}, 2),
        "prepare_video_resolver_session_item": lambda *_args: 2,
        "pending_enrichment_state": lambda: {"status": "pending"},
        "url_response_status": lambda _result: "ready",
    }
    with pytest.raises(ValueError, match="local_evaluation_http_forbidden"):
        url_orchestration.run_url_deep_crawl(
            {"local_evaluation": True},
            **dependencies,
        )
    with pytest.raises(ValueError, match="session_id must be an integer"):
        url_orchestration.run_url_deep_crawl(
            {"session_id": ["bad"]},
            **dependencies,
        )

    result = url_orchestration.run_url_deep_crawl(
        {
            "url": "https://example.test/unknown",
            "execute": False,
            "api_token": "must-not-enter-session-evidence",
        },
        **dependencies,
    )
    sessions = dependencies["search_sessions"]
    assert result == {"url_type": "unknown", "execute": False, "status": "ready"}
    assert sessions.ensure is not None
    assert sessions.ensure["query_type"] == "unknown"
    assert "api_token" not in sessions.ensure["input_payload"]


def _raise(error: Exception):
    def inner(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    return inner


def _assert_http_error(call, expected_status: int, expected_detail: str) -> None:
    with pytest.raises(HTTPException) as raised:
        call()
    assert (raised.value.status_code, raised.value.detail) == (
        expected_status,
        expected_detail,
    )


def test_transition_route_maps_all_domain_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    staff = {"id": 3}
    for error, status in (
        (LookupError("missing"), 404),
        (ValueError("invalid"), 400),
        (scope.ScopeDenied("denied"), 403),
    ):
        monkeypatch.setattr(lifecycle_routes, "_transition", _raise(error))
        _assert_http_error(
            lambda: lifecycle_routes.transition_project(5, {"to_stage": "closed"}, staff=staff),
            status,
            str(error),
        )


def test_delete_route_defaults_body_and_maps_domain_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []
    staff = {"id": 3}
    monkeypatch.setattr(
        lifecycle_routes.workflow,
        "delete_project",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"deleted": True},
    )
    assert lifecycle_routes.delete_project(5, None, staff=staff) == {"deleted": True}
    assert calls == [
        (
            (5, {}),
            {"staff": staff, "claim_lifecycle": DEFAULT_CLAIM_LIFECYCLE_PORT},
        )
    ]

    for error, status in (
        (LookupError("missing"), 404),
        (scope.ScopeDenied("denied"), 403),
    ):
        monkeypatch.setattr(lifecycle_routes.workflow, "delete_project", _raise(error))
        _assert_http_error(
            lambda: lifecycle_routes.delete_project(5, {}, staff=staff),
            status,
            str(error),
        )


def test_ship_and_publish_routes_force_forward_state_and_map_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    staff = {"id": 3}
    monkeypatch.setattr(
        lifecycle_routes,
        "_transition",
        lambda project_id, body, **kwargs: calls.append((project_id, body, kwargs)) or body,
    )
    hostile_body = {"to_stage": "draft", "event_type": "reopen", "note": "keep"}
    assert lifecycle_routes.ship_project(5, hostile_body, staff=staff) == {
        "to_stage": "shipped",
        "event_type": "ship",
        "note": "keep",
    }
    assert lifecycle_routes.publish_project(5, hostile_body, staff=staff) == {
        "to_stage": "published",
        "event_type": "publish",
        "note": "keep",
    }
    assert hostile_body == {"to_stage": "draft", "event_type": "reopen", "note": "keep"}
    assert [call[2] for call in calls] == [{"staff": staff}, {"staff": staff}]

    for route in (lifecycle_routes.ship_project, lifecycle_routes.publish_project):
        for error, status in (
            (LookupError("missing"), 404),
            (ValueError("invalid"), 400),
            (scope.ScopeDenied("denied"), 403),
        ):
            monkeypatch.setattr(lifecycle_routes, "_transition", _raise(error))
            _assert_http_error(
                lambda route=route: route(5, {}, staff=staff),
                status,
                str(error),
            )
