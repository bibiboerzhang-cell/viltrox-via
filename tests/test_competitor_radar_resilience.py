"""competitor_radar_resilience 单测(2026-08-24 审计 F4/F5 配套)。

覆盖:
- provider 异常形状(08-19/08-22 代理隧道 RemoteProtocolError)重试后成功落库;
- ungrounded(08-23 模型漏用 google_search → 零引文)重试后接地落库;
- 连续 3 发 ungrounded → 如实失败、绝不落库(fail-closed 不放宽)、退避 5s/15s;
- budget_blocked 绝不重打;
- 12:00 补漏:当日快照已存在 → 如实跳过零成本;缺失 → 真跑并回写主任务行;
- 注册表:vkpi_competitor_radar_catchup 12:00 中国已注册;
- 主 job 走加固路径且既有 monkeypatch 路径(competitor_radar.generate_competitor_radar)仍受控。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.domains.market import competitor_radar, competitor_radar_resilience
from app.services.scheduler import jobs_tasks_intel


class _Result:
    def __init__(self, row: dict[str, Any] | None = None):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, snapshot_row: dict[str, Any] | None = None):
        self.snapshot_row = snapshot_row
        self.insert_params: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None):
        if "SELECT snapshot_date" in sql:
            return _Result(self.snapshot_row)
        if "INSERT INTO vkpi_competitor_radar" in sql:
            self.insert_params = params
        return _Result()

    def commit(self) -> None:
        return None


_GROUNDED_ITEMS_JSON = json.dumps(
    {
        "items": [
            {
                "signal_type": "competitor",
                "brand": "Sigma",
                "title": "Sigma new lens announcement",
                "summary": "New fast prime",
                "impact": "Opportunity for Viltrox AF line",
                "content_origin": "external",
                "source_platform": "website",
                "source_url": "https://petapixel.com/sigma-grounded",
                "published_at": "2026-08-20T12:00:00Z",
            }
        ]
    }
)
_GROUNDED_SOURCES = [
    {"title": "Sigma grounded report", "url": "https://petapixel.com/sigma-grounded"}
]


def _provider_exception_generation(_prompt: str):
    """08-19/08-22 形状:代理隧道掉线,_generate 吞异常 → 空 raw + 异常型 provenance。"""
    return (
        "",
        "gemini:gemini-2.5-pro+google_search",
        [],
        {
            "provider": "google",
            "model": "gemini-2.5-pro",
            "status": "RemoteProtocolError",
            "grounding_tool": "google_search",
            "source_urls": [],
            "fallback_used": False,
            "attempts": [{"attempt": 1, "status": "provider_exception"}],
        },
    )


def _ungrounded_generation(_prompt: str):
    """08-23 形状:模型漏用 google_search → 合同 OK 但零引文。"""
    return (_GROUNDED_ITEMS_JSON, "gemini:gemini-2.5-pro+google_search", [])


def _grounded_generation(_prompt: str):
    return (_GROUNDED_ITEMS_JSON, "gemini:gemini-2.5-pro+google_search", list(_GROUNDED_SOURCES))


def _patch_radar_env(monkeypatch, conn: _Conn) -> None:
    monkeypatch.setattr(competitor_radar, "get_conn", lambda: conn)
    monkeypatch.setattr(competitor_radar.budget_guard, "check_budget", lambda *_args: True)
    monkeypatch.setattr(competitor_radar.budget_guard, "record_cost", lambda **_kwargs: None)


def _sequenced_generate(monkeypatch, sequence: list[Any]) -> list[int]:
    calls: list[int] = []

    def fake_generate(prompt: str):
        calls.append(1)
        step = sequence[min(len(calls) - 1, len(sequence) - 1)]
        return step(prompt)

    monkeypatch.setattr(competitor_radar, "_generate", fake_generate)
    return calls


def test_provider_exception_then_success_persists(monkeypatch) -> None:
    conn = _Conn()
    _patch_radar_env(monkeypatch, conn)
    calls = _sequenced_generate(
        monkeypatch, [_provider_exception_generation, _grounded_generation]
    )
    sleeps: list[float] = []

    result = competitor_radar_resilience.generate_competitor_radar_with_retries(
        sleeper=sleeps.append
    )

    assert len(calls) == 2
    assert sleeps == [5.0]
    assert result["status"] == "ok"
    assert conn.insert_params is not None
    payload = json.loads(str(conn.insert_params[0]))
    assert payload["items"][0]["brand"] == "Sigma"


def test_ungrounded_then_grounded_persists(monkeypatch) -> None:
    conn = _Conn()
    _patch_radar_env(monkeypatch, conn)
    calls = _sequenced_generate(monkeypatch, [_ungrounded_generation, _grounded_generation])
    sleeps: list[float] = []

    result = competitor_radar_resilience.generate_competitor_radar_with_retries(
        sleeper=sleeps.append
    )

    assert len(calls) == 2
    assert sleeps == [5.0]
    assert result["status"] == "ok"
    assert conn.insert_params is not None


def test_three_ungrounded_attempts_fail_closed_nothing_persisted(monkeypatch) -> None:
    conn = _Conn()
    _patch_radar_env(monkeypatch, conn)
    calls = _sequenced_generate(monkeypatch, [_ungrounded_generation])
    sleeps: list[float] = []

    result = competitor_radar_resilience.generate_competitor_radar_with_retries(
        sleeper=sleeps.append
    )

    assert len(calls) == 3  # 1 正常发 + 2 重试,封顶
    assert sleeps == [5.0, 15.0]
    assert result["status"] == "ungrounded"  # 如实失败,不粉饰
    assert conn.insert_params is None  # fail-closed:未接地绝不落库


def test_budget_blocked_never_retried(monkeypatch) -> None:
    calls: list[int] = []

    def fake_once() -> dict[str, Any]:
        calls.append(1)
        return {"status": "budget_blocked", "reason": "budget_blocked"}

    monkeypatch.setattr(competitor_radar, "generate_competitor_radar", fake_once)
    sleeps: list[float] = []

    result = competitor_radar_resilience.generate_competitor_radar_with_retries(
        sleeper=sleeps.append
    )

    assert len(calls) == 1 and sleeps == []
    assert result["status"] == "budget_blocked"


def test_wrapper_resolves_generate_at_call_time_for_monkeypatch(monkeypatch) -> None:
    # 既有测试约定:对 competitor_radar.generate_competitor_radar 打补丁必须仍然受控。
    monkeypatch.setattr(
        competitor_radar, "generate_competitor_radar", lambda: {"status": "ok"}
    )

    result = competitor_radar_resilience.generate_competitor_radar_with_retries()

    assert result == {"status": "ok"}


# ── today_snapshot_exists(写端同一 CURRENT_DATE 口径)───────────────────────


def test_today_snapshot_exists_true_when_row_present(monkeypatch) -> None:
    conn = _Conn(snapshot_row={"d": "2026-08-24"})
    monkeypatch.setattr("app.db.connection.get_conn", lambda: conn)
    monkeypatch.setattr(competitor_radar, "_ensure_schema", lambda: None)

    assert competitor_radar_resilience.today_snapshot_exists() is True


def test_today_snapshot_exists_false_when_missing(monkeypatch) -> None:
    conn = _Conn(snapshot_row=None)
    monkeypatch.setattr("app.db.connection.get_conn", lambda: conn)
    monkeypatch.setattr(competitor_radar, "_ensure_schema", lambda: None)

    assert competitor_radar_resilience.today_snapshot_exists() is False


def test_today_snapshot_probe_failure_treated_as_missing(monkeypatch) -> None:
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.connection.get_conn", boom)
    monkeypatch.setattr(competitor_radar, "_ensure_schema", lambda: None)

    # 探针故障 → 按缺失处理(宁可补打一发幂等 upsert,也不静默丢当天补漏)。
    assert competitor_radar_resilience.today_snapshot_exists() is False


# ── 12:00 补漏 job ──────────────────────────────────────────────────────────


def test_catchup_job_noops_when_snapshot_exists(monkeypatch) -> None:
    recorded: list[tuple[str, bool, str]] = []
    generation_calls: list[int] = []
    monkeypatch.setattr(jobs_tasks_intel, "_scheduler_task_enabled", lambda _key: True)
    monkeypatch.setattr(
        jobs_tasks_intel,
        "_record_scheduler_run",
        lambda key, *, ok, error="": recorded.append((key, ok, error)),
    )
    monkeypatch.setattr(competitor_radar_resilience, "today_snapshot_exists", lambda: True)
    monkeypatch.setattr(
        competitor_radar_resilience,
        "generate_competitor_radar_with_retries",
        lambda **_kwargs: generation_calls.append(1),
    )

    asyncio.run(jobs_tasks_intel.job_vkpi_competitor_radar_catchup())

    assert generation_calls == []  # 已有快照 → 零成本跳过
    assert recorded == []  # 没真跑,不伪造运行记录


def test_catchup_job_runs_and_records_when_snapshot_missing(monkeypatch) -> None:
    recorded: list[tuple[str, bool, str]] = []
    monkeypatch.setattr(jobs_tasks_intel, "_scheduler_task_enabled", lambda _key: True)
    monkeypatch.setattr(
        jobs_tasks_intel,
        "_record_scheduler_run",
        lambda key, *, ok, error="": recorded.append((key, ok, error)),
    )
    monkeypatch.setattr(competitor_radar_resilience, "today_snapshot_exists", lambda: False)
    monkeypatch.setattr(
        competitor_radar_resilience,
        "generate_competitor_radar_with_retries",
        lambda **_kwargs: {"status": "ok"},
    )

    asyncio.run(jobs_tasks_intel.job_vkpi_competitor_radar_catchup())

    # 真跑成功 → 回写主任务行 vkpi_competitor_radar(catchup 自身无注册表行)。
    assert recorded == [("vkpi_competitor_radar", True, "")]


def test_catchup_job_respects_config_gate(monkeypatch) -> None:
    probes: list[int] = []
    monkeypatch.setattr(jobs_tasks_intel, "_scheduler_task_enabled", lambda _key: False)
    monkeypatch.setattr(
        competitor_radar_resilience,
        "today_snapshot_exists",
        lambda: probes.append(1) or False,
    )

    asyncio.run(jobs_tasks_intel.job_vkpi_competitor_radar_catchup())

    assert probes == []  # gate 关 → 连快照探针都不打


def test_main_radar_job_routes_through_resilience_wrapper(monkeypatch) -> None:
    recorded: list[tuple[str, bool, str]] = []
    monkeypatch.setattr(jobs_tasks_intel, "_scheduler_task_enabled", lambda _key: True)
    monkeypatch.setattr(
        jobs_tasks_intel,
        "_record_scheduler_run",
        lambda key, *, ok, error="": recorded.append((key, ok, error)),
    )
    wrapper_calls: list[int] = []

    def fake_wrapper(**_kwargs: Any) -> dict[str, Any]:
        wrapper_calls.append(1)
        return {"status": "ok"}

    monkeypatch.setattr(
        competitor_radar_resilience, "generate_competitor_radar_with_retries", fake_wrapper
    )

    asyncio.run(jobs_tasks_intel.job_vkpi_competitor_radar())

    assert wrapper_calls == [1]
    assert recorded == [("vkpi_competitor_radar", True, "")]


# ── 注册表:12:00 中国补漏班车已挂 ───────────────────────────────────────────


def test_catchup_trigger_registered_at_noon_china() -> None:
    from app.services.scheduler import jobs_registry
    from app.services.scheduler.jobs import CHINA_TZ

    class _FakeScheduler:
        def __init__(self) -> None:
            self.jobs: dict[str, tuple[Any, dict[str, Any]]] = {}

        def add_job(self, func: Any, trigger: Any = None, **kwargs: Any) -> Any:
            self.jobs[str(kwargs.get("id"))] = (trigger, kwargs)
            return None

    fake = _FakeScheduler()
    jobs_registry._register_intel_content_jobs(fake)

    assert "vkpi_competitor_radar_catchup" in fake.jobs
    trigger, kwargs = fake.jobs["vkpi_competitor_radar_catchup"]
    assert "hour='12'" in str(trigger) and "minute='0'" in str(trigger)
    assert getattr(trigger, "timezone", None) == CHINA_TZ
    assert kwargs.get("max_instances") == 1 and kwargs.get("coalesce") is True
    # 早班雷达注册不受影响。
    assert "vkpi_competitor_radar" in fake.jobs
