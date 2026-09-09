"""Offline release admission: only synthetic connections and provider callbacks."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from app.core import release_validation
from app.platform import llm_batch, llm_budget_reservations as reservations
from app.platform import llm_gateway as gateway, llm_gateway_providers as providers
from app.platform import llm_production_anthropic as anthropic_sdk
from app.platform import llm_production_google as google_sdk
from app.platform import llm_production_openai as openai_sdk
from app.platform import llm_release_fence as fence
from tests.test_llm_gateway_invoke_characterization import InvokeHarness
from tests.test_llm_gateway_text_limits import Clock, invoke, set_chain
from tests.test_llm_gateway_json_limits import runtime  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def inactive_marker(monkeypatch):
    monkeypatch.setattr(release_validation, "IS_PRODUCTION", False)
    monkeypatch.delenv("VKPI_RELEASE_VALIDATION_FENCE_PATH", raising=False)


def _status(monkeypatch, value):
    def read():
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(release_validation, "release_validation_status", read)


@pytest.mark.parametrize("value", [
    {"active": True, "valid": True}, {"active": True, "valid": False},
    {"active": False, "valid": False}, {"active": 0, "valid": True},
    {"active": False, "valid": 1}, {}, None,
    PermissionError("secret marker failure"), UnicodeError("secret marker failure"),
])
def test_only_valid_explicitly_inactive_status_permits_dispatch(monkeypatch, value):
    _status(monkeypatch, value)
    with pytest.raises(fence.LlmReleaseFenced) as exc:
        fence.assert_llm_provider_io_allowed()
    assert str(exc.value) == "release_validation_fenced"
    assert exc.value.retryable is False


@pytest.mark.parametrize("shape", ["valid", "invalid", "symlink"])
def test_real_marker_shape_is_fenced(tmp_path, monkeypatch, shape):
    marker = tmp_path / "release.fence"
    marker.write_text(release_validation.FENCE_PAYLOAD if shape == "valid" else "bad")
    marker.chmod(0o444)
    if shape == "symlink":
        link = tmp_path / "link"
        link.symlink_to(marker)
        marker = link
    monkeypatch.setenv("VKPI_RELEASE_VALIDATION_FENCE_PATH", str(marker))
    with pytest.raises(fence.LlmReleaseFenced):
        fence.assert_llm_provider_io_allowed()


@pytest.mark.parametrize("entry", ["reserve", "start", "text", "json", "http"])
def test_active_fence_blocks_before_database_or_transport(monkeypatch, entry):
    _status(monkeypatch, {"active": True, "valid": True})
    def forbidden(*args, **kwargs):
        pytest.fail("fenced entry touched database or transport")
    monkeypatch.setattr(reservations, "get_conn", forbidden)
    monkeypatch.setattr(reservations, "_ensure_schema", forbidden)
    monkeypatch.setattr(providers, "_get_http_client", forbidden)
    monkeypatch.setattr(gateway, "record_call", forbidden)
    calls = {
        "reserve": lambda: reservations.reserve_llm_budget(
            provider="anthropic", model="fixture", purpose="test", prompt="fixture",
            estimated_cost_usd=0.01),
        "start": lambda: reservations.mark_llm_provider_started("fixture-res"),
        "text": lambda: gateway.invoke("fixture"),
        "json": lambda: gateway.invoke_json("fixture"),
        "http": lambda: providers._request_json("https://unused.invalid", {}, {}, 1),
    }
    with pytest.raises(fence.LlmReleaseFenced):
        calls[entry]()


@pytest.mark.parametrize("provider", ["openai", "google", "anthropic"])
def test_direct_http_adapter_preserves_typed_fence(monkeypatch, provider):
    _status(monkeypatch, {"active": True, "valid": True})
    monkeypatch.setattr(providers, "_get_api_key", lambda _provider: "fixture-key")
    monkeypatch.setattr(providers, "_get_http_client", lambda: pytest.fail("no HTTP client"))
    with pytest.raises(fence.LlmReleaseFenced):
        getattr(providers, "_call_" + provider)("fixture", 16)


def test_http_rechecks_after_client_preparation(monkeypatch):
    calls = []
    def prepare():
        _status(monkeypatch, PermissionError("private path"))
        return SimpleNamespace(post=lambda *a, **k: calls.append("post"))
    monkeypatch.setattr(providers, "_get_http_client", prepare)
    with pytest.raises(fence.LlmReleaseFenced):
        providers._request_json("https://unused.invalid", {}, {}, 1)
    assert calls == []


@pytest.mark.parametrize("stage", ["reserved", "started", "transport"])
def test_text_mid_attempt_fence_stops_fallback_chain(monkeypatch, stage):
    harness = InvokeHarness()
    harness.install(monkeypatch)
    set_chain(harness)
    if stage == "transport":
        def caller(_provider):
            def blocked(*args, **kwargs):
                _status(monkeypatch, PermissionError("private path"))
                fence.assert_llm_provider_io_allowed()
            return blocked
        monkeypatch.setattr(harness, "provider_caller", caller)
    else:
        original = harness.mark_llm_provider_started
        def start(key):
            if stage == "started":
                original(key)
            _status(monkeypatch, {"active": True, "valid": False})
            if stage == "reserved":
                fence.assert_llm_provider_io_allowed()
        monkeypatch.setattr(harness, "mark_llm_provider_started", start)
    result = invoke(harness, Clock())
    assert result["reason"] == "release_validation_fenced"
    assert not any(e[0] in {"provider", "unknown"} for e in harness.events)
    assert len([e for e in harness.events if e[0] == "reserve"]) == 1
    expected = ("release", "res-openai") if stage == "reserved" else ("settle", "res-openai", 0.0)
    assert expected in harness.events


@pytest.mark.parametrize("stage", ["reserved", "started", "transport"])
def test_json_mid_attempt_fence_stops_fallback_chain(runtime, monkeypatch, stage):
    store = gateway._llm_budget_reservations()
    if stage == "transport":
        def blocked(*args, **kwargs):
            _status(monkeypatch, PermissionError("private path"))
            fence.assert_llm_provider_io_allowed()
        monkeypatch.setitem(gateway._PROVIDER_CALLERS, "openai", blocked)
    else:
        def start(key):
            if stage == "started":
                runtime.events.append(("started", key))
            _status(monkeypatch, {"active": True, "valid": False})
            if stage == "reserved":
                fence.assert_llm_provider_io_allowed()
        monkeypatch.setattr(store, "mark_llm_provider_started", start)
        monkeypatch.setattr(gateway, "_llm_budget_reservations", lambda: store)
    result = runtime.invoke()
    assert result["reason"] == "release_validation_fenced"
    assert runtime.calls == []
    assert not any(e[0] == "unknown" for e in runtime.events)
    assert len([e for e in runtime.events if e[0] == "reserve"]) == 1
    expected = ("release", "res-openai") if stage == "reserved" else ("settle", "res-openai", 0.0)
    assert expected in runtime.events


@pytest.fixture
def independent_store(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE vkpi_llm_budget_reservations (
            reservation_key TEXT PRIMARY KEY, state TEXT, actual_cost_usd REAL,
            estimated_cost_usd REAL, cumulative_scopes_json TEXT DEFAULT '[]',
            provider_started_at TEXT, settled_at TEXT, updated_at TEXT);
        INSERT INTO vkpi_llm_budget_reservations
            (reservation_key,state,estimated_cost_usd,provider_started_at)
            VALUES ('historical','unknown',0.25,'2026-09-01');
    """)
    conn.commit()
    monkeypatch.setattr(reservations, "get_conn", lambda: conn)
    monkeypatch.setattr(reservations, "_ensure_schema", lambda: None)
    monkeypatch.setattr(reservations, "is_postgres_runtime", lambda: False)
    yield conn
    conn.close()


def _wire_sdk(monkeypatch, conn, provider, stage):
    events = []
    original_start = reservations.mark_llm_provider_started
    binding = provider + "/fixture-model"
    monkeypatch.setattr(gateway, "budget_preflight", lambda *a, **k: {
        "providers": [{"binding": binding, "provider_calls_allowed": True}]})
    monkeypatch.setattr(gateway, "_resolve_gateway_binding", lambda *a: object())
    monkeypatch.setattr(gateway, "_estimate_cost_micro_usd", lambda *a, **k: 1000)
    monkeypatch.setattr(gateway, "_cost_scope_for_purpose", lambda *a: "fixture")
    monkeypatch.setattr(gateway, "_acquire_strict_fleet_breaker", lambda **k: "permit")
    monkeypatch.setattr(gateway, "_abandon_strict_fleet_breaker", lambda p: events.append("abandon"))
    monkeypatch.setattr(gateway, "record_call", lambda **k: pytest.fail("no provider ledger on fence"))
    for module in (anthropic_sdk, openai_sdk):
        monkeypatch.setattr(module, "_assert_chain_bound_binding", lambda *a, **k: None)
    monkeypatch.setattr(google_sdk, "_validate_google_task_binding", lambda *a, **k: (binding, False))
    monkeypatch.setattr(google_sdk, "_google_budget_gate", lambda **k: None)
    def reserve(**kwargs):
        events.append("reserve")
        conn.execute("INSERT INTO vkpi_llm_budget_reservations (reservation_key,state,estimated_cost_usd) VALUES ('current','reserved',0.01)")
        conn.commit()
        if stage == "reserved":
            _status(monkeypatch, {"active": True, "valid": True})
        return SimpleNamespace(reservation_key="current")
    def start(key):
        original_start(key)
        events.append("started")
        if stage in {"started", "lookup_error"}:
            _status(monkeypatch, PermissionError("private error") if stage == "lookup_error" else {"active": True, "valid": False})
    store = SimpleNamespace(
        reserve_llm_budget=reserve, mark_llm_provider_started=start,
        release_llm_reservation=reservations.release_llm_reservation,
        settle_llm_reservation=reservations.settle_llm_reservation,
        mark_llm_provider_unknown=reservations.mark_llm_provider_unknown,
    )
    monkeypatch.setattr(gateway, "_llm_budget_reservations", lambda: store)
    def forbidden(**kwargs):
        events.append("provider")
        pytest.fail("no provider call")
    client = SimpleNamespace(messages=SimpleNamespace(create=forbidden),
                             responses=SimpleNamespace(create=forbidden),
                             models=SimpleNamespace(generate_content=forbidden))
    common = dict(client=client, model="fixture-model", purpose="fixture", max_output_tokens=32)
    callers = {
        "anthropic": lambda: anthropic_sdk.generate_anthropic_messages(**common, messages=[{"role": "user", "content": "fixture"}]),
        "openai": lambda: openai_sdk.generate_openai_responses(**common, input_items=[{"role": "user", "content": [{"type": "input_text", "text": "fixture"}]}]),
        "google": lambda: google_sdk.generate_google_content(**common, contents=["fixture"], config=None, estimated_input_tokens=5),
    }
    return callers[provider], events


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
@pytest.mark.parametrize("stage", ["before", "reserved", "started", "lookup_error"])
def test_sdk_independent_connection_preserves_unknown_and_cleans_only_current(
    independent_store, monkeypatch, provider, stage,
):
    conn = independent_store
    before = tuple(conn.execute("SELECT * FROM vkpi_llm_budget_reservations WHERE reservation_key='historical'").fetchone())
    call, events = _wire_sdk(monkeypatch, conn, provider, stage)
    if stage == "before":
        _status(monkeypatch, {"active": True, "valid": True})
    with pytest.raises(fence.LlmReleaseFenced):
        call()
    assert "provider" not in events
    assert tuple(conn.execute("SELECT * FROM vkpi_llm_budget_reservations WHERE reservation_key='historical'").fetchone()) == before
    current = conn.execute("SELECT state,actual_cost_usd FROM vkpi_llm_budget_reservations WHERE reservation_key='current'").fetchone()
    if stage == "before":
        assert current is None and events == []
    elif stage == "reserved":
        assert tuple(current) == ("released", None)
    else:
        assert tuple(current) == ("settled", 0.0)


def test_sdk_cleanup_cannot_settle_historical_unknown(independent_store, monkeypatch):
    _status(monkeypatch, {"active": True, "valid": True})
    mock_gateway = SimpleNamespace(_llm_budget_reservations=lambda: reservations,
                                   _abandon_strict_fleet_breaker=lambda b: None)
    before = tuple(independent_store.execute("SELECT * FROM vkpi_llm_budget_reservations").fetchone())
    with pytest.raises(fence.LlmReleaseFenced):
        fence.finish_fenced_sdk_attempt(mock_gateway, "historical", "permit", fence.LlmReleaseFenced())
    assert tuple(independent_store.execute("SELECT * FROM vkpi_llm_budget_reservations").fetchone()) == before


def test_anthropic_batch_stays_disabled_without_provider_or_database(monkeypatch):
    _status(monkeypatch, PermissionError("private path"))
    monkeypatch.setattr(reservations, "get_conn", lambda: pytest.fail("no database"))
    assert llm_batch.anthropic_batch_transport_enabled() is False
    assert llm_batch.submit_anthropic_batch([{"fixture": True}], consumer="fixture") is None
    assert llm_batch.poll_pending_batches()["status"] == "disabled"


@pytest.mark.parametrize("failure", [True, PermissionError("secret path")])
def test_gemini_retry_checks_fence_before_second_attempt(monkeypatch, failure):
    from app.services.ai.clients.gemini_client import _RetryingModels, last_generate_retry_info
    calls = []
    def request(**kwargs):
        calls.append("request")
        raise ConnectionError("synthetic interrupted response")
    def freeze(_seconds):
        _status(monkeypatch, failure if isinstance(failure, Exception) else {"active": True, "valid": True})
    models = _RetryingModels(SimpleNamespace(generate_content=request), delays=(0,), sleep=freeze)
    with pytest.raises(fence.LlmReleaseFenced) as exc:
        models.generate_content(model="fixture")
    assert exc.value.provider_attempted is True
    assert calls == ["request"]
    assert last_generate_retry_info()["attempts"] == 1


def test_gemini_retry_initial_fence_has_no_prior_attempt(monkeypatch):
    from app.services.ai.clients.gemini_client import _RetryingModels, last_generate_retry_info
    _status(monkeypatch, {"active": True, "valid": False})
    models = _RetryingModels(SimpleNamespace(generate_content=lambda **k: pytest.fail("no SDK")), delays=(0,))
    with pytest.raises(fence.LlmReleaseFenced) as exc:
        models.generate_content(model="fixture")
    assert exc.value.provider_attempted is False
    assert last_generate_retry_info()["attempts"] == 0


def test_google_outer_preserves_unknown_after_retry_was_fenced(independent_store, monkeypatch):
    from app.services.ai.clients.gemini_client import _RetryingModels
    from app.platform import llm_production_google as module
    call, events = _wire_sdk(monkeypatch, independent_store, "google", "retry")
    original = module._call_google_provider
    def first(**kwargs):
        events.append("provider")
        raise ConnectionError("synthetic interrupted response")
    def freeze(_seconds):
        _status(monkeypatch, {"active": True, "valid": True})
    retrying = _RetryingModels(SimpleNamespace(generate_content=first), delays=(0,), sleep=freeze)
    def use_retrying(**kwargs):
        kwargs["client"] = SimpleNamespace(models=retrying)
        return original(**kwargs)
    monkeypatch.setattr(module, "_call_google_provider", use_retrying)
    with pytest.raises(fence.LlmReleaseFenced) as exc:
        call()
    assert exc.value.provider_attempted is True
    assert events.count("provider") == 1
    rows = independent_store.execute("SELECT reservation_key,state,actual_cost_usd FROM vkpi_llm_budget_reservations ORDER BY reservation_key").fetchall()
    assert [tuple(row) for row in rows] == [("current", "unknown", None), ("historical", "unknown", None)]


@pytest.mark.parametrize("entry", ["public", "direct"])
def test_verification_comment_fence_cannot_fall_back_to_template(monkeypatch, entry):
    from app.services.verification import comment_generator as module
    _status(monkeypatch, {"active": True, "valid": True})
    monkeypatch.setattr(module, "_openai_client", SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: pytest.fail("no provider")))))
    monkeypatch.setattr(module, "_generate_with_template", lambda code: pytest.fail("no fake success"))
    with pytest.raises(fence.LlmReleaseFenced):
        (module.generate_praise_comment if entry == "public" else module._generate_with_gpt)("FIXTURE")


def test_verification_comment_propagates_typed_inner_block(monkeypatch):
    from app.services.verification import comment_generator as module
    monkeypatch.setattr(module, "_openai_client", object())
    def blocked(code):
        raise fence.LlmReleaseFenced()
    monkeypatch.setattr(module, "_generate_with_gpt", blocked)
    monkeypatch.setattr(module, "_generate_with_template", lambda code: pytest.fail("no fake success"))
    with pytest.raises(fence.LlmReleaseFenced):
        module.generate_praise_comment("FIXTURE")


def test_verification_worker_never_writes_done_after_fenced_generation(monkeypatch):
    import asyncio
    from app.services.verification import comment_generator
    from app.workers.tasks import verification as worker
    _status(monkeypatch, {"active": True, "valid": True})
    monkeypatch.setattr(worker, "generate_praise_comment", comment_generator.generate_praise_comment)
    monkeypatch.setattr(worker, "db_write", lambda fn: pytest.fail("no database write"))
    events = []
    async def set_status(task_id, status, **kwargs):
        events.append(status)
    with pytest.raises(fence.LlmReleaseFenced):
        asyncio.run(worker.process_verification_comment_job(
            SimpleNamespace(set_status=set_status),
            {"task_id": "fixture", "payload": {"verification_id": 1, "code": "FIXTURE"}},
        ))
    assert events == ["processing"]


@pytest.mark.parametrize("cleanup_stage", ["abandon", "release", "settle"])
def test_cleanup_failure_preserves_typed_stop_without_provider_or_unknown_clear(monkeypatch, cleanup_stage):
    def failure(*args):
        raise RuntimeError("private cleanup detail")
    store = SimpleNamespace(
        release_llm_reservation=failure if cleanup_stage == "release" else lambda key: False,
        settle_llm_reservation=failure if cleanup_stage == "settle" else lambda *a: {"settled": False},
    )
    fake_gateway = SimpleNamespace(
        _llm_budget_reservations=lambda: store,
        _abandon_strict_fleet_breaker=failure if cleanup_stage == "abandon" else lambda p: None,
    )
    with pytest.raises(fence.LlmReleaseFenced) as exc:
        fence.finish_fenced_sdk_attempt(fake_gateway, "current", "permit", fence.LlmReleaseFenced())
    assert str(exc.value) == "release_validation_fenced"


@pytest.mark.parametrize("status", [
    {"active": True, "valid": True}, {"active": True, "valid": False},
    PermissionError("private marker detail"),
])
@pytest.mark.parametrize("entry", ["via", "kol", "kol_budget"])
def test_embedding_fence_blocks_sdk_and_budget(monkeypatch, status, entry):
    from app.domains.kol import profile_recall
    from app.services.via.vector_memory_embeddings import embed_openai_sync
    _status(monkeypatch, status)
    def forbidden(*args, **kwargs):
        pytest.fail("fenced embedding touched SDK or budget")
    client = SimpleNamespace(embeddings=SimpleNamespace(create=forbidden))
    monkeypatch.setattr(profile_recall, "check_budget", forbidden)
    monkeypatch.setattr(profile_recall, "record_cost", forbidden)
    calls = {
        "via": lambda: embed_openai_sync(["fixture"], openai_available=True,
            openai_client=client, model="fixture", max_batch=1),
        "kol": lambda: profile_recall._create_embedding_with_failover("fixture", client_factory=forbidden),
        "kol_budget": lambda: profile_recall._embed_query("fixture"),
    }
    with pytest.raises(fence.LlmReleaseFenced) as exc:
        calls[entry]()
    assert exc.value.provider_attempted is False


@pytest.mark.parametrize("stage", ["client_prepare", "after_first_attempt"])
def test_embedding_rechecks_and_stops_proxy_failover(monkeypatch, stage):
    from app.domains.kol import profile_recall
    events = []
    monkeypatch.setattr(profile_recall, "_embed_transport_plan", lambda: [
        {"transport": "first"}, {"transport": "second"}])
    monkeypatch.setattr(profile_recall, "_should_failover", lambda exc: True)
    def create(**kwargs):
        events.append("provider")
        _status(monkeypatch, {"active": True, "valid": True})
        raise ConnectionError("synthetic response interruption")
    def factory(spec):
        events.append(spec["transport"])
        if stage == "client_prepare":
            _status(monkeypatch, {"active": True, "valid": True})
        return SimpleNamespace(embeddings=SimpleNamespace(create=create))
    with pytest.raises(fence.LlmReleaseFenced) as exc:
        profile_recall._create_embedding_with_failover("fixture", client_factory=factory)
    assert events == (["first"] if stage == "client_prepare" else ["first", "provider"])
    assert exc.value.provider_attempted is (stage == "after_first_attempt")


@pytest.mark.parametrize("backend_kind", ["qdrant", "weaviate"])
def test_fenced_embedding_never_reports_vector_upsert_success(monkeypatch, backend_kind):
    import asyncio
    from app.services.via import vector_memory
    from tests.test_via_vector_memory_http import _bundle, _seed_items
    _status(monkeypatch, {"active": True, "valid": True})
    def forbidden(*args, **kwargs):
        pytest.fail("fenced embedding performed remote I/O")
    monkeypatch.setattr(vector_memory, "VIA_EMBEDDING_BACKEND", "openai")
    monkeypatch.setattr(vector_memory, "OPENAI_AVAILABLE", True)
    monkeypatch.setattr(vector_memory, "openai_client", SimpleNamespace(
        embeddings=SimpleNamespace(create=forbidden)))
    monkeypatch.setattr(vector_memory.httpx, "AsyncClient", forbidden)
    if backend_kind == "qdrant":
        monkeypatch.setattr(vector_memory, "QDRANT_URL", "https://unused.invalid")
        backend = vector_memory._QdrantVectorBackend()
    else:
        monkeypatch.setattr(vector_memory, "WEAVIATE_URL", "https://unused.invalid")
        backend = vector_memory._WeaviateVectorBackend()
    monkeypatch.setattr(vector_memory, "_vector_backend_singleton", backend)
    result = asyncio.run(vector_memory.store_via_seed_documents(_bundle(), _seed_items()))
    assert result["status"] == "partial"
    assert result["upserted"] == 0
    assert result["ready"] is False
    # The existing vector projection intentionally redacts exception detail.
    assert result["error"] == "embedding: unavailable"
