"""波 C·C1(2026-08-23):final_v1 回退链 gemini-3.6-flash → gemini-3.5-flash-lite 在 worker 路径真正生效。

覆盖四段:
- model_registry:audit_video_analysis 的允许回退 binding(主绑定语义不变,链含主+回退,就绪范围纳入回退);
- llm_production_google:链成员过绑定校验且台账记实际模型;非成员仍 task_binding_model_mismatch;
- video_analysis_enqueue:预检按链主模型预约 + 回退成员就绪;任一 ready 即入队(payload 带 ready 子链),全不 ready 才 ai_disabled;
- apify_jobs_worker_gemini:analyzer payload 传链(payload 只能收窄;本地评测/非 final_v1 钉主力)、
  post-hoc model_binding_mismatch 放宽到链成员、子进程不再强制单模型覆盖。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

PRIMARY = "gemini-3.6-flash"
LITE = "gemini-3.5-flash-lite"
VIDEO_TASK = "audit_video_analysis"


# ---------------------------------------------------------------- registry


def test_registry_allowed_bindings_primary_first_and_current_binding_unchanged(monkeypatch) -> None:
    from app.core import model_registry as reg

    for key in ("APIFY_WORKER_GEMINI_MODEL", "GEMINI_FINAL_V1_QA_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert reg.current_task_model_binding()[VIDEO_TASK] == f"google/{PRIMARY}"
    assert reg.allowed_task_model_bindings(VIDEO_TASK) == (f"google/{PRIMARY}", f"google/{LITE}")
    assert reg.task_model_fallback_bindings(VIDEO_TASK) == (f"google/{LITE}",)
    assert reg.is_allowed_task_model_binding(VIDEO_TASK, f"google/{LITE}")
    assert not reg.is_allowed_task_model_binding(VIDEO_TASK, "google/gemini-2.5-flash")
    # 其余任务没有回退:链 = 单节主绑定;未登记任务为空。
    assert reg.allowed_task_model_bindings("keyframe_qa") == (f"google/{LITE}",)
    assert reg.task_model_fallback_bindings("keyframe_qa") == ()
    assert reg.allowed_task_model_bindings("no_such_task") == ()
    # 就绪范围:lite 既是 keyframe_qa 的主绑定,也是视频任务的回退成员。
    by_binding = reg.tasks_by_allowed_binding()
    assert VIDEO_TASK in by_binding[f"google/{LITE}"]
    assert "keyframe_qa" in by_binding[f"google/{LITE}"]
    assert by_binding[f"google/{PRIMARY}"].count(VIDEO_TASK) == 1


def test_registry_fallback_honours_judge_env_and_ignores_latest(monkeypatch) -> None:
    from app.core import model_registry as reg

    monkeypatch.setenv("GEMINI_FINAL_V1_QA_MODEL", "gemini-3.5-flash")
    assert reg.task_model_fallback_bindings(VIDEO_TASK) == ("google/gemini-3.5-flash",)
    monkeypatch.setenv("GEMINI_FINAL_V1_QA_MODEL", "gemini-flash-latest")
    assert reg.task_model_fallback_bindings(VIDEO_TASK) == (f"google/{LITE}",)
    # 主/回退同名 → 退化成单节链(与 gemini_models 去重保序一致)。
    monkeypatch.setenv("GEMINI_FINAL_V1_QA_MODEL", PRIMARY)
    assert reg.allowed_task_model_bindings(VIDEO_TASK) == (f"google/{PRIMARY}",)


def test_chain_module_matches_leaf_chain_and_narrowing_rules(monkeypatch) -> None:
    from app.core import gemini_models as leaf
    from app.core import video_model_chain as vmc

    for key in ("APIFY_WORKER_GEMINI_MODEL", "GEMINI_FINAL_V1_QA_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert vmc.final_v1_model_chain() == list(leaf.DEFAULT_FINAL_V1_CHAIN) == [PRIMARY, LITE]
    assert vmc.model_fallback_candidates([PRIMARY, LITE]) == [("google", LITE)]
    assert vmc.narrow_model_chain([PRIMARY, LITE], [LITE]) == [LITE]
    assert vmc.narrow_model_chain([PRIMARY, LITE], ["gemini-2.5-flash"]) == [PRIMARY, LITE]
    assert vmc.narrow_model_chain([PRIMARY, LITE], None) == [PRIMARY, LITE]
    # registry 没认可的 leaf 成员绝不进链。
    monkeypatch.setattr(vmc, "registry_allowed_models", lambda: [PRIMARY])
    assert vmc.final_v1_model_chain() == [PRIMARY]


def test_ready_subchain_from_preflight_candidates() -> None:
    from app.core.video_model_chain import ready_model_subchain

    def _preflight(primary_ok: bool, lite_ok: bool) -> dict[str, Any]:
        return {
            "provider_gate_reason": "provider_calls_allowed" if (primary_ok or lite_ok) else "model_binding_blocked",
            "providers": [
                {"provider": "google", "model": PRIMARY, "provider_calls_allowed": primary_ok, "binding_gate_reason": "" if primary_ok else "readiness_not_production_ready"},
                {"provider": "google", "model": LITE, "provider_calls_allowed": lite_ok, "binding_gate_reason": "" if lite_ok else "budget_hard_stop"},
            ],
        }

    assert ready_model_subchain(_preflight(True, True), [PRIMARY, LITE]) == ([PRIMARY, LITE], {})
    assert ready_model_subchain(_preflight(True, False), [PRIMARY, LITE]) == ([PRIMARY], {LITE: "budget_hard_stop"})
    assert ready_model_subchain(_preflight(False, True), [PRIMARY, LITE]) == ([LITE], {PRIMARY: "readiness_not_production_ready"})
    ready, blocked = ready_model_subchain(_preflight(False, False), [PRIMARY, LITE])
    assert ready == [] and set(blocked) == {PRIMARY, LITE}
    # 旧桩:单候选无 model 字段 → 视为主模型槽位。
    assert ready_model_subchain({"providers": [{"provider": "google", "provider_calls_allowed": True}]}, [PRIMARY, LITE]) == ([PRIMARY], {})


# --------------------------------------------------------- llm_production


class _Config:
    def __init__(self, **values: Any) -> None:
        self.values = dict(values)

    def model_copy(self, *, update: dict[str, Any]):
        return _Config(**{**self.values, **update})


class _Reservations:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.counter = 0

    def reserve_llm_budget(self, **kwargs):
        self.counter += 1
        self.events.append(("reserve", kwargs))
        return SimpleNamespace(reservation_key=f"llmres-{self.counter}")

    def mark_llm_provider_started(self, key: str) -> None:
        self.events.append(("started", key))

    def settle_llm_reservation(self, key: str, actual: float):
        self.events.append(("settled", (key, actual)))
        return {"settled": True}

    def mark_llm_provider_unknown(self, key: str) -> bool:
        self.events.append(("unknown", key))
        return True

    def release_llm_reservation(self, key: str) -> bool:
        self.events.append(("released", key))
        return True


class _Client:
    def __init__(self, model_version: str) -> None:
        self.model_version = model_version
        self.calls: list[dict[str, Any]] = []
        self.models = self

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            text="{}",
            model_version=self.model_version,
            usage_metadata=SimpleNamespace(prompt_token_count=1200, candidates_token_count=80),
        )


def _install_boundary(monkeypatch, *, model: str):
    from app.platform import llm_production

    reservations = _Reservations()
    ledgers: list[dict[str, Any]] = []
    monkeypatch.delenv("GEMINI_FINAL_V1_QA_MODEL", raising=False)
    monkeypatch.setattr(llm_production, "current_task_model_binding", lambda: {VIDEO_TASK: f"google/{PRIMARY}"})
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "budget_preflight",
        lambda *_args, **_kwargs: {
            "provider_gate_reason": "provider_calls_allowed",
            "providers": [{"binding": f"google/{model}", "provider_calls_allowed": True, "binding_gate_reason": ""}],
        },
    )
    monkeypatch.setattr(llm_production.llm_gateway, "_llm_budget_reservations", lambda: reservations)
    monkeypatch.setattr(llm_production.llm_gateway, "_acquire_strict_fleet_breaker", lambda **_kwargs: None)
    monkeypatch.setattr(llm_production.llm_gateway, "_complete_strict_fleet_breaker", lambda *_args, **_kwargs: None)

    def record_call(**kwargs):
        ledgers.append(kwargs)
        return {"call": {"call_uid": "unit"}}

    monkeypatch.setattr(llm_production.llm_gateway, "record_call", record_call)
    return llm_production, reservations, ledgers


def _generate(llm_production, *, client: _Client, model: str):
    return llm_production.generate_google_content(
        client=client,
        contents=[SimpleNamespace(uri="https://example.invalid/video"), "prompt"],
        config=_Config(media_resolution="LOW"),
        model=model,
        purpose=VIDEO_TASK,
        max_output_tokens=4096,
        estimated_input_tokens=25_000,
        cost_tag="cron:vkpi_analysis_worker",
        metadata={"task_binding": VIDEO_TASK, "phase": "video_analysis", "attempt_index": 2, "attempt_total": 2},
        attempt_log=[],
    )


def test_google_adapter_accepts_fallback_chain_member_and_ledgers_actual_model(monkeypatch) -> None:
    llm_production, reservations, ledgers = _install_boundary(monkeypatch, model=LITE)
    client = _Client(LITE)
    _generate(llm_production, client=client, model=LITE)
    assert client.calls and client.calls[0]["model"] == LITE
    reserve = next(kwargs for name, kwargs in reservations.events if name == "reserve")
    assert reserve["model"] == LITE
    assert reserve["metadata"]["task_binding_role"] == "fallback"
    assert reserve["metadata"]["task_binding_primary"] == f"google/{PRIMARY}"
    settled = [kwargs for kwargs in ledgers if kwargs["status"] == "success"]
    assert settled and settled[0]["model"] == LITE
    assert settled[0]["metadata"]["task_binding_role"] == "fallback"


def test_google_adapter_primary_is_labelled_primary(monkeypatch) -> None:
    llm_production, reservations, _ledgers = _install_boundary(monkeypatch, model=PRIMARY)
    _generate(llm_production, client=_Client(PRIMARY), model=PRIMARY)
    reserve = next(kwargs for name, kwargs in reservations.events if name == "reserve")
    assert reserve["metadata"]["task_binding_role"] == "primary"


def test_google_adapter_still_rejects_non_chain_member_before_any_reservation(monkeypatch) -> None:
    llm_production, reservations, ledgers = _install_boundary(monkeypatch, model="gemini-2.5-flash")
    client = _Client("gemini-2.5-flash")
    with pytest.raises(llm_production.ProductionLlmUnavailable) as info:
        _generate(llm_production, client=client, model="gemini-2.5-flash")
    assert info.value.code == "task_binding_model_mismatch"
    assert info.value.result["expected_binding"] == f"google/{PRIMARY}"
    assert info.value.result["allowed_bindings"] == [f"google/{PRIMARY}", f"google/{LITE}"]
    assert info.value.result["actual_binding"] == "google/gemini-2.5-flash"
    assert client.calls == [] and reservations.events == [] and ledgers == []


# ---------------------------------------------------------------- enqueue


class _Rows:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def fetchone(self):
        return dict(self.row) if self.row is not None else None


class _EnqueueConn:
    def __init__(self) -> None:
        self.commits = 0

    def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _Rows:
        text = " ".join(str(sql).split())
        if "FROM vkpi_kol_video_evidence" in text:
            return _Rows(
                {
                    "evidence_id": 7,
                    "kol_pool_id": 88,
                    "content_url": "https://www.youtube.com/watch?v=abcdefghijk",
                    "title": "demo",
                    "view_count": 10,
                    "duration_seconds": 60,
                    "evidence_platform": "youtube",
                    "evidence_type": "video",
                    "media_kind": "video",
                    "viltrox_fit_score": 91,
                    "kol_handle": "creator",
                }
            )
        if "FROM vkpi_analysis_cache" in text or "FROM apify_jobs" in text:
            return _Rows(None)
        if "SELECT viltrox_fit_score FROM vkpi_kol_pool" in text:
            return _Rows({"viltrox_fit_score": 91})
        raise AssertionError(text)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        return None


def _chain_preflight(primary_ok: bool, lite_ok: bool):
    def fake(prompt: str, **kwargs: Any) -> dict[str, Any]:
        fake.captured = {"prompt": prompt, **kwargs}  # type: ignore[attr-defined]
        any_ok = primary_ok or lite_ok
        return {
            "provider_calls_allowed": any_ok,
            "provider_gate_reason": "provider_calls_allowed" if any_ok else "model_binding_blocked",
            "model_readiness_status": "production_ready" if any_ok else "not_ready",
            "providers": [
                {
                    "provider": "google",
                    "model": PRIMARY,
                    "binding": f"google/{PRIMARY}",
                    "provider_calls_allowed": primary_ok,
                    "binding_gate_reason": "ready" if primary_ok else "readiness_not_production_ready",
                    "model_readiness_status": "production_ready" if primary_ok else "not_ready",
                    "estimated_cost_usd": 0.05,
                    "checks": [],
                },
                {
                    "provider": "google",
                    "model": LITE,
                    "binding": f"google/{LITE}",
                    "provider_calls_allowed": lite_ok,
                    "binding_gate_reason": "ready" if lite_ok else "readiness_not_production_ready",
                    "model_readiness_status": "production_ready" if lite_ok else "not_ready",
                    "estimated_cost_usd": 0.01,
                    "checks": [],
                },
            ],
        }

    return fake


def _run_enqueue(monkeypatch, preflight) -> tuple[dict[str, Any], dict[str, Any]]:
    from app.domains.kol import video_analysis_enqueue as enqueue

    captured: dict[str, Any] = {}
    monkeypatch.setattr(enqueue.llm_gateway, "budget_preflight", preflight)
    monkeypatch.setattr(
        enqueue,
        "enqueue_active_apify_job",
        lambda _conn, **kwargs: captured.update(kwargs)
        or ({"id": 7001, "job_type": kwargs["job_type"], "status": "queued", "payload": kwargs["payload"]}, True),
    )
    result = enqueue._enqueue_final_v1_video_analysis(_EnqueueConn(), kol_pool_id=88, evidence_id=7)
    return result, captured


@pytest.mark.parametrize(
    ("primary_ok", "lite_ok", "expected_chain"),
    [(True, True, [PRIMARY, LITE]), (True, False, [PRIMARY]), (False, True, [LITE])],
)
def test_enqueue_reserves_primary_and_queues_ready_subchain(monkeypatch, primary_ok, lite_ok, expected_chain) -> None:
    from app.domains.kol import video_analysis_enqueue as enqueue

    assert enqueue.PRODUCTION_VIDEO_CHAIN == [PRIMARY, LITE]
    preflight = _chain_preflight(primary_ok, lite_ok)
    result, captured = _run_enqueue(monkeypatch, preflight)
    assert preflight.captured["model_override"] == PRIMARY
    assert preflight.captured["model_fallbacks"] == [("google", LITE)]
    assert result["status"] == "queued"
    assert result["ai_analysis"]["provider_calls_allowed"] is True
    assert result["budget"]["ready_models"] == expected_chain
    assert result["budget"]["model"] == expected_chain[0]
    assert captured["payload"]["gemini_final_v1_models"] == expected_chain
    assert "preflight" not in result["budget"]


def test_enqueue_is_ai_disabled_only_when_no_chain_member_is_ready(monkeypatch) -> None:
    from app.domains.kol import video_analysis_enqueue as enqueue

    monkeypatch.setattr(
        enqueue,
        "enqueue_active_apify_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("AI job must not be inserted")),
    )
    result, _captured = _run_enqueue(monkeypatch, _chain_preflight(False, False))
    assert result["status"] == "ai_disabled"
    assert result["ai_analysis"]["gate_reason"] == "model_binding_blocked"
    assert result["budget"]["ready_models"] == []
    assert set(result["budget"]["blocked_models"]) == {PRIMARY, LITE}


# ----------------------------------------------------------------- worker


def test_worker_analyzer_payload_sends_chain_and_drops_forced_model_override(monkeypatch) -> None:
    from app.workers import apify_jobs_worker  # noqa: F401 - 先装父模块,再取 gemini 簇(底部互 import)
    from app.workers import apify_jobs_worker_gemini as gemini

    for key in ("APIFY_WORKER_GEMINI_MODEL", "GEMINI_FINAL_V1_QA_MODEL"):
        monkeypatch.delenv(key, raising=False)
    full = gemini._gemini_analyzer_payload({"target_id": "1"}, "video_analysis_final_v1")
    assert full["gemini_model"] == ""
    assert full["gemini_models"] == [PRIMARY, LITE]
    assert full["gemini_final_v1_models"] == [PRIMARY, LITE]
    # payload 只能收窄(入队侧 ready 子链),不能放宽。
    narrowed = gemini._gemini_analyzer_payload({"gemini_final_v1_models": [LITE]}, "video_analysis_final_v1")
    assert narrowed["gemini_final_v1_models"] == [LITE] and narrowed["gemini_models"] == [LITE]
    widened = gemini._gemini_analyzer_payload({"gemini_final_v1_models": [LITE, "gemini-2.5-pro"]}, "video_analysis_final_v1")
    assert widened["gemini_final_v1_models"] == [LITE]
    # 本地评测(allowlist 只认精确单模型)与非 final_v1 derive 钉主力单节。
    local = gemini._gemini_analyzer_payload({"local_evaluation": True}, "video_analysis_final_v1")
    assert local["gemini_final_v1_models"] == [PRIMARY] and local["gemini_models"] == [PRIMARY]
    signed = gemini._gemini_analyzer_payload({"_llm_execution": {"execution_class": "local_evaluation"}}, "video_analysis_final_v1")
    assert signed["gemini_models"] == [PRIMARY]
    legacy = gemini._gemini_analyzer_payload({}, "gemini_video_v2")
    assert legacy["gemini_models"] == [PRIMARY] and "gemini_final_v1_models" not in legacy


def test_child_code_skips_forced_override_when_gemini_model_is_blank() -> None:
    """子进程只在 payload.gemini_model 非空时强制覆盖 generate_content;链模式下置空即不覆盖、不盖写 raw.model。"""
    from app.workers import apify_jobs_worker_media as media

    namespace: dict[str, Any] = {}
    source = media._gemini_analyzer_child_code()
    body = source.split("\n\ntry:\n    main()", 1)[0]
    exec(compile(body, "<child>", "exec"), namespace)  # noqa: S102 - 受控测试,子进程源码本身
    stack, override = namespace["_apply_worker_overrides"]({"gemini_model": "", "gemini_models": [PRIMARY, LITE]})
    with stack:
        pass
    assert override == ""
    raw = {"analyzed": True, "model": LITE, "method": f"gemini_direct_{LITE}"}
    assert namespace["_stamp_model"](dict(raw), override) == raw


def _run_worker_with_reported_model(monkeypatch, reported: str, payload_chain: list[str] | None = None):
    from app.services.ai.analyzers.gemini_video_results import InvalidFinalV1ResultError
    from app.workers import apify_jobs_worker  # noqa: F401 - 先装父模块,再取 gemini 簇(底部互 import)
    from app.workers import apify_jobs_worker_gemini as gemini

    for key in ("APIFY_WORKER_GEMINI_MODEL", "GEMINI_FINAL_V1_QA_MODEL"):
        monkeypatch.delenv(key, raising=False)
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        gemini,
        "_load_video_evidence",
        lambda *_args: {"id": 701, "kol_pool_id": 88, "content_url": "https://www.youtube.com/watch?v=abcdefghijk", "title": "demo"},
    )
    monkeypatch.setattr(gemini, "_final_v1_scope_checkpoint", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        gemini,
        "_run_gemini_analyzer_with_timeout",
        lambda payload, **_kwargs: seen.update({"analyzer_payload": payload})
        or {"analyzed": True, "model": reported, "method": f"gemini_direct_{reported}", "video_analysis_final_v1": {}},
    )

    def _reject(raw: dict[str, Any]) -> None:
        raise InvalidFinalV1ResultError("stop_here_for_test")

    monkeypatch.setattr(gemini, "ensure_final_v1_result_cacheable", _reject)
    monkeypatch.setattr(gemini, "_record_gemini_cost", lambda **kwargs: seen.update({"ledger_raw": kwargs["raw"]}) or {})
    monkeypatch.setattr(gemini, "record_final_v1_outcome_diagnostics", lambda *_args, **kwargs: seen.update({"diag_raw": kwargs["raw"]}))
    payload: dict[str, Any] = {"target_type": "video", "target_id": "701", "derive_method": "video_analysis_final_v1"}
    if payload_chain is not None:
        payload["gemini_final_v1_models"] = payload_chain
    try:
        gemini._process_gemini_video(object(), {"id": 905}, payload, 0.01)  # type: ignore[arg-type]
    except InvalidFinalV1ResultError:
        seen["stopped_at_validation"] = True
    except RuntimeError as exc:
        seen["runtime_error"] = str(exc)
    return seen


def test_worker_post_hoc_gate_accepts_fallback_member_and_records_fallback_used(monkeypatch) -> None:
    seen = _run_worker_with_reported_model(monkeypatch, LITE)
    assert seen.get("stopped_at_validation") is True, seen
    assert seen["analyzer_payload"]["gemini_final_v1_models"] == [PRIMARY, LITE]
    execution = seen["ledger_raw"]["llm_execution"]
    assert execution["binding"] == f"google/{LITE}"
    assert execution["model"] == LITE
    assert execution["reported_model"] == LITE
    assert execution["model_chain"] == [PRIMARY, LITE]
    assert execution["fallback_used"] is True
    assert execution["model_match"] is True
    assert seen["diag_raw"] is seen["ledger_raw"]


def test_worker_post_hoc_gate_primary_is_not_fallback(monkeypatch) -> None:
    seen = _run_worker_with_reported_model(monkeypatch, PRIMARY)
    execution = seen["ledger_raw"]["llm_execution"]
    assert execution["binding"] == f"google/{PRIMARY}"
    assert execution["fallback_used"] is False and execution["model_match"] is True


def test_worker_post_hoc_gate_still_rejects_non_chain_model(monkeypatch) -> None:
    seen = _run_worker_with_reported_model(monkeypatch, "gemini-2.5-flash")
    assert "ledger_raw" not in seen
    assert seen["runtime_error"].startswith("model_binding_mismatch:")
    assert f"expected=google/{PRIMARY}|{LITE}" in seen["runtime_error"]
    assert "reported=gemini-2.5-flash" in seen["runtime_error"]


def test_worker_post_hoc_gate_respects_enqueue_narrowed_chain(monkeypatch) -> None:
    # 入队侧只放行 [主力] 时,worker 报 lite 也算链外(payload 收窄对 post-hoc 闸同样生效)。
    seen = _run_worker_with_reported_model(monkeypatch, LITE, payload_chain=[PRIMARY])
    assert seen["analyzer_payload"]["gemini_final_v1_models"] == [PRIMARY]
    assert seen["runtime_error"].startswith("model_binding_mismatch:")


# ------------------------------------------------------------- readiness


def test_readiness_scope_includes_fallback_task_for_lite() -> None:
    from app.platform.models import readiness

    item, _source = readiness.exact_binding_readiness_from_environment(f"google/{LITE}")
    assert VIDEO_TASK in item["evaluation"]["expected_tasks"]
    assert "keyframe_qa" in item["evaluation"]["expected_tasks"]
    primary_item, _ = readiness.exact_binding_readiness_from_environment(f"google/{PRIMARY}")
    assert VIDEO_TASK in primary_item["evaluation"]["expected_tasks"]
