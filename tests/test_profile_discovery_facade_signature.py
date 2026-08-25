"""门面壳与真实现签名同集:_PIPELINE_COMPAT 会把门面函数塞回流水线模块,
任何只在真实现上新增的关键字参数都会在 prod 变成 TypeError(2026-08-22 严格 30+30 事故)。

外加「装完要还原」的守卫:门面把自己的值塞进真实现模块只能持续到调用结束,
否则 monkeypatch 门面的用例会把桩永久留在真实现模块里污染整个进程。
"""
from __future__ import annotations

import asyncio
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


# ───────────────────── 门面注入不许留脚印(测试隔离) ─────────────────────

_ABSENT = object()


def _snapshot(module, *name_groups) -> dict:
    return {
        name: getattr(module, name, _ABSENT)
        for group in name_groups
        for name in group
    }


def test_provider_module_is_restored_after_a_facade_call(monkeypatch):
    """monkeypatch 门面 → 调用别的门面壳 → 真实现模块必须一字不差地还原。

    2026-08-25 之前这里是无条件 setattr 写死:tests/test_kol_search_quality_guardrails.py
    打的 `_auto_enroll_discoveries` 桩会漏进 profile_discovery_provider 并留到进程结束。
    """
    names = (profile_discovery._PROVIDER_SHARED_NAMES, profile_discovery._PROVIDER_IMPLEMENTATIONS)
    before = _snapshot(profile_discovery_provider, *names)
    seen: dict = {}

    def stub(new_creators):
        return 0

    def recorder(**kwargs):
        seen["auto_enroll"] = profile_discovery_provider._auto_enroll_discoveries
        seen["logger"] = profile_discovery_provider.logger
        return {"ok": True}

    monkeypatch.setattr(profile_discovery, "_auto_enroll_discoveries", stub)
    monkeypatch.setitem(profile_discovery._PROVIDER_IMPLEMENTATIONS, "discovery_plan", recorder)

    assert profile_discovery.discovery_plan(query_text="camera") == {"ok": True}
    # 契约一:调用期间真实现看见的仍是门面上的当前值(路由/worker/测试的 patch 点照旧生效)
    assert seen["auto_enroll"] is stub
    assert seen["logger"] is profile_discovery.logger
    # 契约二:调用结束后真实现模块一个脚印都不留
    assert _snapshot(profile_discovery_provider, *names) == before


def test_session_module_is_restored_after_a_facade_call(monkeypatch):
    names = (profile_discovery._SESSION_SHARED_NAMES, profile_discovery._SESSION_IMPLEMENTATIONS)
    before = _snapshot(profile_discovery_session, *names)
    seen: dict = {}

    def stub(item):
        return ""

    def recorder(kol_pool_id):
        seen["from_item"] = profile_discovery_session._profile_url_from_item
        return "https://example.test/x"

    monkeypatch.setattr(profile_discovery, "_profile_url_from_item", stub)
    monkeypatch.setitem(
        profile_discovery._SESSION_IMPLEMENTATIONS, "_profile_url_from_kol_pool_id", recorder
    )

    assert profile_discovery._profile_url_from_kol_pool_id(1) == "https://example.test/x"
    assert seen["from_item"] is stub
    assert _snapshot(profile_discovery_session, *names) == before


def test_pipeline_module_is_restored_after_a_facade_call(monkeypatch):
    names = (profile_discovery._PIPELINE_SHARED_NAMES,)
    before = _snapshot(profile_discovery_pipeline, *names)
    seen: dict = {}

    async def recorder(*, session_id, payload, provider_actor=None):
        seen["discover"] = profile_discovery_pipeline.discover_new_creators
        return {"ok": True}

    monkeypatch.setattr(profile_discovery, "_PIPELINE_IMPLEMENTATION", recorder)
    out = asyncio.run(
        profile_discovery.execute_smart_search_profile_advance_pipeline(session_id=1, payload={})
    )

    assert out == {"ok": True}
    # 流水线在调用期间拿到的是门面壳(严格 30+30 那条契约),但出栈后必须还回真实现
    assert seen["discover"] is profile_discovery.discover_new_creators
    assert _snapshot(profile_discovery_pipeline, *names) == before


def test_nested_facade_calls_only_restore_once_everything_unwinds(monkeypatch):
    """嵌套调用(流水线 → 门面壳 → 真实现)期间不许中途还原,否则外层看见的是真实现。"""
    names = (profile_discovery._PROVIDER_SHARED_NAMES, profile_discovery._PROVIDER_IMPLEMENTATIONS)
    before = _snapshot(profile_discovery_provider, *names)
    seen: dict = {}

    def stub(new_creators):
        return 0

    def inner(item):
        return 0

    def outer(**kwargs):
        # 内层再走一次门面壳,回到外层时覆盖必须还在
        profile_discovery._existing_match_pool_id({})
        seen["after_inner"] = profile_discovery_provider._auto_enroll_discoveries
        return {"ok": True}

    monkeypatch.setattr(profile_discovery, "_auto_enroll_discoveries", stub)
    monkeypatch.setitem(profile_discovery._PROVIDER_IMPLEMENTATIONS, "discovery_plan", outer)
    monkeypatch.setitem(profile_discovery._PROVIDER_IMPLEMENTATIONS, "_existing_match_pool_id", inner)

    assert profile_discovery.discovery_plan(query_text="camera") == {"ok": True}
    assert seen["after_inner"] is stub
    assert _snapshot(profile_discovery_provider, *names) == before
