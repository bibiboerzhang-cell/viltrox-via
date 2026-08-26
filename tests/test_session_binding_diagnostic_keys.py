"""会话绑定指纹必须只封印操作员请求本身,不封印服务端自己写的诊断键(2026-08-25)。

## 这条测试在防什么

`record_advance_request_snapshot` 把 filter 快照追加进 `input_payload_json`,而
`provider_job_access._session_binding` 正是拿这个字段算 `input_payload_fingerprint`
来封印会话。两者相撞的后果是**「签发凭证」这个动作本身改变了凭证绑定的内容**:

    会话 1147(2026-08-25 23:12 / 23:45)两次 smart_search_profile_advance
    → 全部 search_session_query_drifted、retry_allowed=False
    → 一次 provider 都没调用,线上搜索整体瘫痪
    → 同类任务 8/13–8/24 共 27 次全部 done,今天部署后 2 次全部 blocked

修法是把诊断键排除出指纹(与 `_MUTABLE_RUNTIME_KEYS` 同理)。但**排除必须是精确的**:
只放行服务端自己写的键,操作员请求里的任何一个字段变了都必须照旧拦下。下面两组用例
一正一反,缺一不可 —— 只测「不再误拦」而不测「仍然拦得住」,等于把围栏拆了还自称修好。
"""
from __future__ import annotations

import pytest

from app.domains.kol.provider_job_access import _session_binding
from app.domains.kol.search_session_diagnostics import (
    ADVANCE_REQUEST_SNAPSHOTS_KEY,
    SERVER_WRITTEN_INPUT_KEYS,
    sealed_session_input,
)

_OPERATOR_REQUEST = {
    "input": "135用户",
    "limit": 30,
    "mode": "text_recall",
    "platforms": ["youtube", "instagram", "tiktok"],
    "candidate_limit": 500,
    "exclude_chinese": True,
}
_SNAPSHOT = {
    "stage": "text_recall",
    "schema": "advance_request_filter_snapshot_v1",
    "source": "kol_smart_search",
    "recorded_at": "2026-08-25T23:12:27+00:00",
}


def _session(input_payload: dict) -> dict:
    return {
        "id": 1147,
        "created_by": 1,
        "query_text": "135用户",
        "query_type": "text_recall",
        "input_payload": input_payload,
    }


def _fingerprint(input_payload: dict) -> str:
    binding = _session_binding(_session(input_payload), fallback_owner_user_id=1, bind_query=True)
    return str(binding["input_payload_fingerprint"])


# ── 一、诊断键不得影响指纹(这是本次线上故障的直接修复)──────────────────


def test_advance_snapshots_do_not_change_the_session_fingerprint():
    """入队后追加快照,指纹必须纹丝不动 —— 否则任务一签发就自我作废。"""
    before = _fingerprint(dict(_OPERATOR_REQUEST))
    after = _fingerprint({**_OPERATOR_REQUEST, ADVANCE_REQUEST_SNAPSHOTS_KEY: [_SNAPSHOT]})
    assert before == after


def test_repeated_advance_attempts_keep_the_same_fingerprint():
    """重试会再追加一条快照(线上 1147 就是 2 条),指纹仍不得变。"""
    one = _fingerprint({**_OPERATOR_REQUEST, ADVANCE_REQUEST_SNAPSHOTS_KEY: [_SNAPSHOT]})
    two = _fingerprint({**_OPERATOR_REQUEST, ADVANCE_REQUEST_SNAPSHOTS_KEY: [_SNAPSHOT, _SNAPSHOT]})
    assert one == two


def test_sealed_input_strips_only_registered_server_keys():
    payload = {**_OPERATOR_REQUEST, ADVANCE_REQUEST_SNAPSHOTS_KEY: [_SNAPSHOT]}
    assert sealed_session_input(payload) == _OPERATOR_REQUEST


@pytest.mark.parametrize("junk", [None, "", 0, [], "not-a-dict"])
def test_sealed_input_tolerates_non_dict(junk):
    """NULL / 脏数据退回空 dict,与既有 `_session_binding` 口径一致,不改变既有行为。"""
    assert sealed_session_input(junk) == {}


# ── 二、围栏仍然拦得住(证明修复没有把闸拆掉)────────────────────────────


@pytest.mark.parametrize(
    "field, tampered",
    [
        ("input", "换一个完全不同的搜索词"),
        ("limit", 500),
        ("mode", "account_deep"),
        ("platforms", ["youtube"]),
        ("candidate_limit", 5000),
        ("exclude_chinese", False),
    ],
)
def test_operator_request_fields_are_still_sealed(field, tampered):
    """操作员请求里任何一个字段被改,指纹都必须变 —— 这是围栏的本职。"""
    baseline = _fingerprint(dict(_OPERATOR_REQUEST))
    assert _fingerprint({**_OPERATOR_REQUEST, field: tampered}) != baseline


def test_adding_an_unregistered_key_still_changes_the_fingerprint():
    """未登记的新键一律照旧封印;想放行必须显式登记,不能靠命名巧合溜过去。"""
    baseline = _fingerprint(dict(_OPERATOR_REQUEST))
    assert _fingerprint({**_OPERATOR_REQUEST, "some_future_key": [1, 2, 3]}) != baseline


def test_exclusion_set_stays_minimal():
    """排除集必须逐个审过。要加新键,先改这条断言,逼人正面回答「它凭什么不算请求」。"""
    assert SERVER_WRITTEN_INPUT_KEYS == frozenset({ADVANCE_REQUEST_SNAPSHOTS_KEY})


# ── 三、写入之后必须重读(线上瘫痪的直接成因)────────────────────────────


def _patch_session_flow(monkeypatch, *, write_status: str):
    """把 ensure_session_for_result 的两个外部依赖换成可观测的桩。

    `reads` 记录 get_session 被调了几次;第 1 次返回写入前的旧状态,
    第 2 次返回写入后的新状态 —— 与线上真实时序一致。
    """
    from app.domains.kol import search_sessions

    stale = {"id": 1147, "input_payload": {"input": "135用户"}}
    fresh = {"id": 1147, "input_payload": {"input": "135用户", ADVANCE_REQUEST_SNAPSHOTS_KEY: [_SNAPSHOT]}}
    reads: list[int] = []

    def fake_get_session(session_id, **_kwargs):
        reads.append(int(session_id))
        return fresh if len(reads) > 1 else stale

    monkeypatch.setattr(search_sessions, "get_session", fake_get_session)
    monkeypatch.setattr(
        search_sessions.search_session_diagnostics,
        "record_advance_request_snapshot",
        lambda *_a, **_k: {"status": write_status, "count": 1},
    )
    return search_sessions, reads, fresh, stale


def test_recorded_snapshot_forces_a_fresh_session_read(monkeypatch):
    """真写了快照就必须重读 —— 否则调用方拿旧状态签发凭证,worker 校验必然判漂移。"""
    search_sessions, reads, fresh, _stale = _patch_session_flow(monkeypatch, write_status="recorded")
    got = search_sessions.ensure_session_for_result(
        session_id=1147, create=False, query_text="135用户", query_type="text_recall",
        source="kol_smart_search", input_payload={"input": "135用户"}, staff={"staff_id": 1, "id": 1},
    )
    assert len(reads) == 2, "写入之后没有重读,返回的是过期状态"
    assert got == fresh


def test_skipped_write_does_not_pay_for_a_second_read(monkeypatch):
    """重放/轮询会 skipped(一个字节没写),此时不该多付一次查询。"""
    search_sessions, reads, _fresh, stale = _patch_session_flow(monkeypatch, write_status="skipped")
    got = search_sessions.ensure_session_for_result(
        session_id=1147, create=False, query_text="135用户", query_type="text_recall",
        source="kol_smart_search", input_payload={"input": "135用户"}, staff={"staff_id": 1, "id": 1},
    )
    assert len(reads) == 1
    assert got == stale


def test_failed_write_does_not_add_a_read(monkeypatch):
    """写入报 failed 说明 UPDATE/commit 抛了、没落库,不该多读。

    这不只是省一次查询:`test_ensure_existing_session_always_fails_closed_without_an_explicit_owner`
    正是按 get_session 的**调用序列**断言会话归属的 fail-closed 行为,凭空多一次读会把
    那条安全断言打破。所以重读条件只认 recorded,不认 skipped / failed。
    """
    search_sessions, reads, _fresh, stale = _patch_session_flow(monkeypatch, write_status="failed")
    got = search_sessions.ensure_session_for_result(
        session_id=1147, create=False, query_text="135用户", query_type="text_recall",
        source="kol_smart_search", input_payload={"input": "135用户"}, staff={"staff_id": 1, "id": 1},
    )
    assert len(reads) == 1
    assert got == stale
