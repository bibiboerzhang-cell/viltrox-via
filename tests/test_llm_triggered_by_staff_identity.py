"""身份类型化(波 C·C2):四处 LLM 调用的 ``triggered_by`` 传 staff dict,绝不把 user_id 当 staff 传。

背景:``(staff or {}).get("user_id")`` 进 ``triggered_by`` 后,台账(budget_guard.record_cost 的
``_resolve_staff``)与预留元数据(llm_budget_reservations._progress_metadata)会把这个 user id
当 staff 外键/staff_id 落库——owner 从 UI 点深析 100% 台账写失败的同源病。台账层已在
``llm_gateway_ledger._actor_staff_id`` 做了安全化兜底,这里是正本清源:调用方直接传 staff dict。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import outreach_pack
from app.domains.projects import contract_assist, outreach, retrospective_aggregate

STAFF = {"id": 11, "staff_id": 11, "user_id": 101, "role": "member"}


def _capture(monkeypatch: pytest.MonkeyPatch, module: Any, response: dict[str, Any]) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_generate_json(_prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return response

    monkeypatch.setattr(module.llm_production, "generate_json", fake_generate_json)
    return captured


def _assert_staff_identity(captured: dict[str, Any], *, label: str, staff: dict[str, Any] | None) -> None:
    assert "triggered_by" in captured, "generate_json must be reached"
    if staff:
        assert captured["triggered_by"] is staff          # dict → 台账按 staff_id 解析
        assert captured["staff"] == staff
    else:
        assert captured["triggered_by"] == label           # 无人触发 → 稳定来源标签
    # 任何情况下 user_id 都不能裸传成 triggered_by(那会被当 staff 外键)
    assert captured["triggered_by"] != STAFF["user_id"]
    assert captured["triggered_by"] != str(STAFF["user_id"])


@pytest.mark.parametrize("staff", [STAFF, None])
def test_project_outreach_passes_staff_dict_not_user_id(monkeypatch: pytest.MonkeyPatch, staff) -> None:
    monkeypatch.setattr(
        outreach,
        "_load_creators",
        lambda _ids: ([{"id": 1, "display_name": "Creator", "handle": "creator", "platform": "youtube"}], []),
    )
    monkeypatch.setattr(outreach, "_outreach_binding", lambda _preferred: ("openai", "gpt-exact"))
    captured = _capture(monkeypatch, outreach, {"status": "unavailable", "failure": {"code": "budget_guard_blocked"}})

    outreach.generate_outreach([1], brief={"query_text": "camera reviewer"}, staff=staff)

    _assert_staff_identity(captured, label="projects.outreach", staff=staff)


@pytest.mark.parametrize("staff", [STAFF, None])
def test_outreach_pack_email_draft_passes_staff_dict_not_user_id(monkeypatch: pytest.MonkeyPatch, staff) -> None:
    monkeypatch.setattr(outreach_pack, "_model_binding", lambda: ("anthropic", "claude-sonnet-5"))
    captured = _capture(monkeypatch, outreach_pack, {"status": "unavailable", "provider": "rule_v0", "model": ""})

    outreach_pack._generate_email_draft(
        {"id": 3, "display_name": "Creator", "handle": "creator", "platform": "youtube"},
        {"why_fit": ["camera reviews"]},
        staff=staff,
    )

    _assert_staff_identity(captured, label=outreach_pack.MODEL_TASK, staff=staff)


@pytest.mark.parametrize("staff", [STAFF, None])
def test_contract_polish_passes_staff_dict_not_user_id(monkeypatch: pytest.MonkeyPatch, staff) -> None:
    captured = _capture(monkeypatch, contract_assist, {"status": "unavailable", "failure": {"code": "model_readiness_blocked"}})
    monkeypatch.setattr(contract_assist, "get_conn", lambda: (_ for _ in ()).throw(AssertionError("must not write")))

    contract_assist.run_contract_polish_for_job(
        {"polish_key": "p1", "project_id": 7, "fields": {"deliverables": "One video"}},
        staff=staff,
    )

    _assert_staff_identity(captured, label="projects.contract_polish", staff=staff)


@pytest.mark.parametrize("staff", [STAFF, None])
def test_project_retrospective_passes_staff_dict_not_user_id(monkeypatch: pytest.MonkeyPatch, staff) -> None:
    class Conn:
        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("must not write")

    monkeypatch.setattr(retrospective_aggregate, "get_conn", lambda: Conn())
    monkeypatch.setattr(
        retrospective_aggregate.cache_repo,
        "list_project_video_analysis_cache",
        lambda *_args, **_kwargs: {
            "items": [{"state": "ready", "entry": {"result": {}}, "evidence_id": 1, "view_count": 10}]
        },
    )
    from app.domains.projects import observation_windows

    monkeypatch.setattr(observation_windows, "matched_content_posts_for_retrospective", lambda *_args, **_kwargs: [])
    captured = _capture(
        monkeypatch, retrospective_aggregate, {"status": "unavailable", "failure": {"code": "budget_guard_blocked"}}
    )

    retrospective_aggregate.run_project_retrospective(7, staff=staff)

    _assert_staff_identity(captured, label="projects.retrospective", staff=staff)
    # user_id 仍只进 cache 行的 triggered_by_user_id 列(类型化列),不混进 triggered_by
    assert retrospective_aggregate._triggered_by_user_id(STAFF) == 101


def test_no_user_id_sneaks_into_triggered_by_in_source() -> None:
    """源码级守门:四个模块里 ``triggered_by=`` 右侧不得再出现 user_id 取值。"""
    import inspect
    import re

    pattern = re.compile(r"triggered_by=[^#\n]*user_id")  # 注释不算
    for module in (outreach, outreach_pack, retrospective_aggregate, contract_assist):
        source = inspect.getsource(module)
        assert not pattern.search(source), module.__name__
