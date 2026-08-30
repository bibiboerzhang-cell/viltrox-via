"""Characterization lock for retrospective_aggregate.run_project_retrospective (CC 车道).

Locks the worker orchestration byte-for-byte before the decomposition:
- fence gates (target drift / three revalidations / provider_called flag);
- evidence gathering order + matched-post fallback (suppressed exception);
- diagnostics assembly (source_status / selection_truncated / partial);
- LLM call kwargs (provider/model/validator/metadata) and failure conversion;
- cache write happens ONLY on success, with the exact row params (逐键相等).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.domains.projects import ai_job_access
from app.domains.projects import observation_windows
from app.domains.projects import retrospective_aggregate as ra


FIXED_NOW = "2026-08-30T00:11:22Z"

EXPECTED_CACHE_SQL = (
    "INSERT INTO vkpi_analysis_cache ( target_type, target_id, model, derive_method, "
    "result, cost, status, triggered_by_user_id, created_at, updated_at ) "
    "VALUES ('project', ?, ?, ?, ?::jsonb, ?, 'ready', ?, ?, ?) "
    "ON CONFLICT (target_type, target_id, derive_method) "
    "DO UPDATE SET model=EXCLUDED.model, result=EXCLUDED.result, cost=EXCLUDED.cost, "
    "status='ready', triggered_by_user_id=EXCLUDED.triggered_by_user_id, "
    "updated_at=EXCLUDED.updated_at"
)

VALID_PAYLOAD = {
    "insight_text": "项目整体表现稳定,内容与产品卖点契合。",
    "highlights": ["亮点甲", "亮点乙"],
    "risks": ["风险甲"],
    "next_steps": ["下一步甲", "下一步乙"],
}

ITEM_1 = {"evidence_ids": [11, 3], "post_ids": [], "source_kinds": ["final_v1"], "view_count": 500}
ITEM_2 = {
    "evidence_ids": [7],
    "post_ids": [201, 105],
    "source_kinds": ["matched_content_post", "final_v1"],
    "view_count": None,
}
BASE_DIAGNOSTICS = {
    "dedupe_matches": {"evidence_id": 1},
    "identity_conflicts": {"pairs": 0},
    "partial": False,
}
METRICS = {"view_count": {"total": 1234, "measured": 2}, "engagement": {"total": 56, "measured": 2}}
PROMPT_ITEMS = [{"kol_name": "A", "platform": "youtube"}]


class _RecordingConn:
    def __init__(self) -> None:
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: Any = ()) -> "_RecordingConn":
        self.executes.append((" ".join(sql.split()), tuple(params)))
        return self

    def commit(self) -> None:
        self.commits += 1


class _Rig:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.conn = _RecordingConn()
        self.calls: dict[str, list[Any]] = {
            "get_conn": [],
            "cache_list": [],
            "matched": [],
            "reconcile": [],
            "metrics": [],
            "items_for_llm": [],
            "generate_json": [],
            "revalidate": [],
        }
        self.cache_items: list[dict[str, Any]] = []
        self.matched_posts: list[dict[str, Any]] = []
        self.matched_raises = False
        self.reconciled: dict[str, Any] = {"items": [], "diagnostics": dict(BASE_DIAGNOSTICS)}
        self.llm_resp: Any = None
        self.llm_raises: Exception | None = None
        self.revalidate_raise_on: set[int] = set()

        monkeypatch.setattr(ra, "get_conn", self._get_conn)
        monkeypatch.setattr(ra, "utcnow", lambda: FIXED_NOW)
        monkeypatch.setattr(ra.cache_repo, "list_project_video_analysis_cache", self._cache_list)
        monkeypatch.setattr(
            observation_windows, "matched_content_posts_for_retrospective", self._matched
        )
        monkeypatch.setattr(ra, "reconcile_retrospective_content", self._reconcile)
        monkeypatch.setattr(ra, "summarize_content_metrics", self._metrics)
        monkeypatch.setattr(ra, "project_retrospective_items_for_llm", self._items_for_llm)
        monkeypatch.setattr(ra.llm_production, "generate_json", self._generate_json)
        monkeypatch.setattr(ai_job_access, "revalidate_job_fence", self._revalidate)

    def _get_conn(self) -> _RecordingConn:
        self.calls["get_conn"].append(True)
        return self.conn

    def _cache_list(self, project_id: int, **kwargs: Any) -> dict[str, Any]:
        self.calls["cache_list"].append((project_id, kwargs))
        return {"items": self.cache_items}

    def _matched(self, project_id: int, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls["matched"].append((project_id, kwargs))
        if self.matched_raises:
            raise RuntimeError("matched posts backend down")
        return self.matched_posts

    def _reconcile(self, ready: list[Any], matched: list[Any]) -> dict[str, Any]:
        self.calls["reconcile"].append((ready, matched))
        return self.reconciled

    def _metrics(self, selected: list[Any]) -> dict[str, Any]:
        self.calls["metrics"].append(selected)
        return METRICS

    def _items_for_llm(self, selected: list[Any]) -> tuple[list[Any], int]:
        self.calls["items_for_llm"].append(selected)
        return PROMPT_ITEMS, 1

    def _generate_json(self, prompt: str, **kwargs: Any) -> Any:
        self.calls["generate_json"].append((prompt, kwargs))
        if self.llm_raises is not None:
            raise self.llm_raises
        return self.llm_resp

    def _revalidate(self, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls["revalidate"].append((payload, kwargs))
        if len(self.calls["revalidate"]) in self.revalidate_raise_on:
            raise ai_job_access.ProjectAiAccessError("project_ai_fence_stale", 403)
        return {}


@pytest.fixture()
def rig(monkeypatch: pytest.MonkeyPatch) -> _Rig:
    return _Rig(monkeypatch)


def _ready_cache_items() -> list[dict[str, Any]]:
    return [
        {"state": "ready", "entry": {"eid": 11}, "kol_id": 1},
        {"state": "pending", "entry": {"eid": 12}},
        {"state": "ready", "entry": None},
    ]


def _success_resp(model_suffix: str = "") -> dict[str, Any]:
    return {
        "status": "success",
        "provider": "openai",
        "model": f"{ra.OPENAI_MODEL}{model_suffix}",
        "json": dict(VALID_PAYLOAD),
        "cost_cents": 123,
    }


def _expected_diagnostics(**overrides: Any) -> dict[str, Any]:
    expected = {
        **BASE_DIAGNOSTICS,
        "source_status": {"final_v1": "ready", "matched_content_posts": "ready"},
        "selection_truncated": False,
        "partial": False,
        "selected_content_count": 2,
        "selected_metrics": METRICS,
    }
    expected.update(overrides)
    return expected


def _expected_result(resp: dict[str, Any], diagnostics: dict[str, Any]) -> dict[str, Any]:
    return {
        "insight_text": VALID_PAYLOAD["insight_text"],
        "highlights": ["亮点甲", "亮点乙"],
        "risks": ["风险甲"],
        "next_steps": ["下一步甲", "下一步乙"],
        "provenance": {
            "content_count": 2,
            "video_count": 2,
            "evidence_ids": [3, 7, 11],
            "matched_post_count": 1,
            "matched_post_ids": [105, 201],
            "selection": "dedupe_evidence_id_then_canonical_url;measured_views_desc_then_identity",
            "top_n": ra.TOP_N_VIDEOS,
            "source_derive_method": ra.SOURCE_DERIVE_METHOD,
            "source_includes": ["video_analysis_final_v1", "matched_content_posts"],
            "model": resp.get("model"),
            "provider": resp.get("provider"),
            "generated_at": FIXED_NOW,
            "totals": {"views": 1234, "engagement": 56},
            "redacted_count": 1,
            "diagnostics": diagnostics,
        },
    }


def test_success_writes_cache_locked(rig: _Rig) -> None:
    rig.cache_items = _ready_cache_items()
    rig.matched_posts = [{"post_id": 201}]
    rig.reconciled = {"items": [ITEM_1, ITEM_2], "diagnostics": dict(BASE_DIAGNOSTICS)}
    rig.llm_resp = _success_resp("-2026")
    staff = {"user_id": 9, "id": 4}

    out = ra.run_project_retrospective(3, staff=staff)

    diagnostics = _expected_diagnostics()
    expected_result = _expected_result(rig.llm_resp, diagnostics)
    assert out == {"status": "ready", "project_id": 3, "result": expected_result}

    # 采集编排:cache 读 → matched 读 → reconcile,参数逐一锁定。
    assert rig.calls["cache_list"] == [
        (3, {"derive_method": ra.SOURCE_DERIVE_METHOD, "conn": rig.conn})
    ]
    assert rig.calls["matched"] == [(3, {"conn": rig.conn})]
    ready_passed, matched_passed = rig.calls["reconcile"][0]
    assert ready_passed == [{"state": "ready", "entry": {"eid": 11}, "kol_id": 1}]
    assert matched_passed == [{"post_id": 201}]
    assert rig.calls["metrics"] == [[ITEM_1, ITEM_2]]
    assert rig.calls["items_for_llm"] == [[ITEM_1, ITEM_2]]
    assert rig.calls["revalidate"] == []

    # LLM 调用参数逐键锁定(prompt 用未改动的 _build_prompt 作 oracle)。
    prompt, kwargs = rig.calls["generate_json"][0]
    assert prompt == ra._build_prompt(3, PROMPT_ITEMS, diagnostics)
    assert kwargs["validator"] is ra._valid_retrospective_payload
    assert {k: v for k, v in kwargs.items() if k != "validator"} == {
        "provider": "openai",
        "model": ra.OPENAI_MODEL,
        "purpose": "vkpi_project_retrospective",
        "max_output_tokens": ra.MAX_OUTPUT_TOKENS,
        "cost_tag": ra.BUDGET_SCOPE,
        "triggered_by": staff,
        "staff": staff,
        "required_keys": ("insight_text", "highlights", "risks", "next_steps"),
        "deadline_seconds": 90.0,
        "metadata": {
            "project_id": 3,
            "content_count": 2,
            "video_count": 2,
            "matched_post_count": 1,
            "partial": False,
            "phase": "project_retrospective",
            "subphase": "aggregate_evidence",
            "attempt_index": 1,
            "total": 1,
            "target_label": "project:3",
        },
    }

    # 落库行结构逐键相等 + 单次 commit。
    assert len(rig.conn.executes) == 1
    sql, params = rig.conn.executes[0]
    assert sql == EXPECTED_CACHE_SQL
    assert params[0] == "3"
    assert params[1] == f"{ra.OPENAI_MODEL}-2026"
    assert params[2] == ra.DERIVE_METHOD
    assert json.loads(params[3]) == {"schema_version": ra.DERIVE_METHOD, **expected_result}
    assert params[3] == json.dumps(
        {"schema_version": ra.DERIVE_METHOD, **expected_result}, ensure_ascii=False
    )
    assert params[4] == pytest.approx(1.23)
    assert params[5] == 9
    assert params[6] == FIXED_NOW
    assert params[7] == FIXED_NOW
    assert rig.conn.commits == 1


def test_skip_when_no_content(rig: _Rig) -> None:
    rig.reconciled = {"items": [], "diagnostics": dict(BASE_DIAGNOSTICS)}
    out = ra.run_project_retrospective(5)
    assert out == {
        "status": "skipped",
        "reason": "no_evidence_and_no_matched_content",
        "project_id": 5,
        "diagnostics": {
            **BASE_DIAGNOSTICS,
            "source_status": {"final_v1": "ready", "matched_content_posts": "ready"},
            "selection_truncated": False,
            "partial": False,
        },
    }
    assert rig.calls["generate_json"] == []
    assert rig.conn.executes == [] and rig.conn.commits == 0


def test_matched_posts_failure_suppressed_marks_error_partial(rig: _Rig) -> None:
    rig.cache_items = _ready_cache_items()
    rig.matched_raises = True
    rig.reconciled = {"items": [], "diagnostics": dict(BASE_DIAGNOSTICS)}
    out = ra.run_project_retrospective(6)
    # 降级不炸:matched 侧异常吞掉后以 [] 参与 reconcile,状态 error、partial=True。
    _, matched_passed = rig.calls["reconcile"][0]
    assert matched_passed == []
    assert out["status"] == "skipped"
    assert out["diagnostics"]["source_status"] == {
        "final_v1": "ready",
        "matched_content_posts": "error",
    }
    assert out["diagnostics"]["partial"] is True


def test_llm_exception_returns_failed_and_writes_nothing(rig: _Rig) -> None:
    rig.cache_items = _ready_cache_items()
    rig.reconciled = {"items": [ITEM_1, ITEM_2], "diagnostics": dict(BASE_DIAGNOSTICS)}
    rig.llm_raises = RuntimeError("gateway exploded")
    out = ra.run_project_retrospective(3)
    assert out == {
        "status": "failed",
        "reason": "gateway exploded",
        "project_id": 3,
        "provider": None,
        "diagnostics": _expected_diagnostics(),
    }
    assert rig.conn.executes == [] and rig.conn.commits == 0


def test_llm_exception_empty_message_uses_type_name(rig: _Rig) -> None:
    rig.cache_items = _ready_cache_items()
    rig.reconciled = {"items": [ITEM_1], "diagnostics": dict(BASE_DIAGNOSTICS)}
    rig.llm_raises = ValueError()
    out = ra.run_project_retrospective(3)
    assert out["status"] == "failed"
    assert out["reason"] == "ValueError"


@pytest.mark.parametrize(
    "resp_mutation, expected_reason",
    [
        ({"provider": "gemini"}, "success"),
        ({"status": "failed", "failure": {"code": "budget_hard_stop"}}, "budget_hard_stop"),
        ({"json": {"insight_text": ""}}, "success"),
    ],
)
def test_unacceptable_llm_response_fails_without_write(
    rig: _Rig, resp_mutation: dict[str, Any], expected_reason: str
) -> None:
    rig.cache_items = _ready_cache_items()
    rig.reconciled = {"items": [ITEM_1, ITEM_2], "diagnostics": dict(BASE_DIAGNOSTICS)}
    resp = _success_resp()
    resp.update(resp_mutation)
    rig.llm_resp = resp
    out = ra.run_project_retrospective(3)
    assert out == {
        "status": "failed",
        "reason": expected_reason,
        "project_id": 3,
        "provider": resp.get("provider"),
        "diagnostics": _expected_diagnostics(),
    }
    assert rig.conn.executes == [] and rig.conn.commits == 0


def test_fence_target_drift_blocks_before_any_read(rig: _Rig) -> None:
    out = ra.run_project_retrospective(3, access_payload={"project_id": 99})
    assert out == {
        "status": "blocked",
        "reason": "project_ai_target_drifted",
        "provider_calls_performed": False,
        "retryable": False,
    }
    assert rig.calls["get_conn"] == []
    assert rig.calls["revalidate"] == []
    assert rig.calls["generate_json"] == []


def test_fence_target_id_fallback_key_allows(rig: _Rig) -> None:
    rig.reconciled = {"items": [], "diagnostics": dict(BASE_DIAGNOSTICS)}
    out = ra.run_project_retrospective(3, access_payload={"target_id": "3"})
    assert out["status"] == "skipped"
    assert len(rig.calls["revalidate"]) == 1


def test_fence_first_revalidation_failure_blocks_before_reads(rig: _Rig) -> None:
    rig.revalidate_raise_on = {1}
    out = ra.run_project_retrospective(3, access_payload={"project_id": 3})
    assert out == {
        "status": "blocked",
        "reason": "project_ai_fence_stale",
        "provider_calls_performed": False,
        "retryable": False,
    }
    assert rig.calls["get_conn"] == []
    assert rig.calls["generate_json"] == []


def test_fence_second_revalidation_failure_blocks_before_llm(rig: _Rig) -> None:
    rig.cache_items = _ready_cache_items()
    rig.reconciled = {"items": [ITEM_1], "diagnostics": dict(BASE_DIAGNOSTICS)}
    rig.revalidate_raise_on = {2}
    out = ra.run_project_retrospective(3, access_payload={"project_id": 3})
    assert out == {
        "status": "blocked",
        "reason": "project_ai_fence_stale",
        "provider_calls_performed": False,
        "retryable": False,
    }
    assert rig.calls["generate_json"] == []
    assert rig.conn.executes == []


def test_fence_third_revalidation_failure_marks_provider_called(rig: _Rig) -> None:
    rig.cache_items = _ready_cache_items()
    rig.reconciled = {"items": [ITEM_1, ITEM_2], "diagnostics": dict(BASE_DIAGNOSTICS)}
    rig.llm_resp = _success_resp()
    rig.revalidate_raise_on = {3}
    payload = {"project_id": 3}
    out = ra.run_project_retrospective(3, access_payload=payload)
    assert out == {
        "status": "blocked",
        "reason": "project_ai_fence_stale",
        "provider_calls_performed": True,
        "retryable": False,
    }
    assert len(rig.calls["generate_json"]) == 1
    assert rig.conn.executes == [] and rig.conn.commits == 0
    assert rig.calls["revalidate"] == [
        (payload, {"action": ai_job_access.PROJECT_RETROSPECTIVE}),
        (payload, {"action": ai_job_access.PROJECT_RETROSPECTIVE}),
        (payload, {"action": ai_job_access.PROJECT_RETROSPECTIVE}),
    ]


def test_fence_success_path_revalidates_three_times(rig: _Rig) -> None:
    rig.cache_items = _ready_cache_items()
    rig.reconciled = {"items": [ITEM_1, ITEM_2], "diagnostics": dict(BASE_DIAGNOSTICS)}
    rig.llm_resp = _success_resp()
    out = ra.run_project_retrospective(3, access_payload={"project_id": 3})
    assert out["status"] == "ready"
    assert len(rig.calls["revalidate"]) == 3
    assert rig.conn.commits == 1


def test_top_n_truncation_marks_partial(rig: _Rig) -> None:
    rig.cache_items = _ready_cache_items()
    many = [dict(ITEM_1, evidence_ids=[i]) for i in range(ra.TOP_N_VIDEOS + 1)]
    rig.reconciled = {"items": many, "diagnostics": dict(BASE_DIAGNOSTICS)}
    rig.llm_resp = _success_resp()
    out = ra.run_project_retrospective(3)
    assert out["status"] == "ready"
    diagnostics = out["result"]["provenance"]["diagnostics"]
    assert diagnostics["selection_truncated"] is True
    assert diagnostics["partial"] is True
    assert diagnostics["selected_content_count"] == ra.TOP_N_VIDEOS
    assert rig.calls["items_for_llm"] == [many[: ra.TOP_N_VIDEOS]]
    _, kwargs = rig.calls["generate_json"][0]
    assert kwargs["metadata"]["content_count"] == ra.TOP_N_VIDEOS
    assert kwargs["metadata"]["partial"] is True
    assert kwargs["triggered_by"] == "projects.retrospective"
    assert kwargs["staff"] == {}
