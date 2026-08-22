"""门面壳与真实现签名同集:_sync_pipeline_compat 会把门面函数塞回流水线模块,
任何只在真实现上新增的关键字参数都会在 prod 变成 TypeError(2026-08-22 严格 30+30 事故)。"""
from __future__ import annotations

import inspect

from app.domains.kol import (
    profile_discovery,
    profile_discovery_pipeline,
    profile_discovery_provider,
    profile_discovery_session,
)


def _kwonly(fn) -> set[str]:
    return {
        name
        for name, p in inspect.signature(fn).parameters.items()
        if p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD) and name != "self"
    }


def test_facade_advance_accepts_every_kwarg_of_the_real_implementation():
    real = _kwonly(profile_discovery_session.advance_search_session_items)
    facade = _kwonly(profile_discovery.advance_search_session_items)
    assert real <= facade, f"门面壳缺参数: {sorted(real - facade)}"


def test_every_pipeline_shared_name_keeps_real_signature():
    for name in profile_discovery._PIPELINE_SHARED_NAMES:
        facade_obj = getattr(profile_discovery, name)
        real_obj = (
            getattr(profile_discovery_session, name, None)
            or getattr(profile_discovery_provider, name, None)
            or getattr(profile_discovery_pipeline, name, None)
        )
        if not callable(facade_obj) or not callable(real_obj) or facade_obj is real_obj:
            continue
        missing = _kwonly(real_obj) - _kwonly(facade_obj)
        assert not missing, f"{name} 门面壳缺参数: {sorted(missing)}"


def test_facade_forwards_smart_local_contract(monkeypatch):
    seen: dict = {}

    def fake(*, session_id, body=None, smart_local_contract=False):
        seen.update(session_id=session_id, body=body, smart_local_contract=smart_local_contract)
        return {"ok": True}

    monkeypatch.setitem(profile_discovery._SESSION_IMPLEMENTATIONS, "advance_search_session_items", fake)
    out = profile_discovery.advance_search_session_items(session_id=7, body={"execute": True}, smart_local_contract=True)
    assert out == {"ok": True}
    assert seen == {"session_id": 7, "body": {"execute": True}, "smart_local_contract": True}


def test_facade_discover_new_creators_matches_provider_signature():
    real = _kwonly(profile_discovery_provider.discover_new_creators)
    facade = _kwonly(profile_discovery.discover_new_creators)
    assert real <= facade, f"门面壳缺参数: {sorted(real - facade)}"
