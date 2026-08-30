from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.services.scheduler import jobs


ROOT = Path(__file__).resolve().parents[1]
SCHEDULER_DIR = ROOT / "backend" / "app" / "services" / "scheduler"

# These are the formerly implicit schedules plus the two-region official-report
# pair.  Each value names the deliberate business timezone expression used by
# the registration code; the ZoneInfo bindings themselves are checked below.
EXPECTED_JOB_TIMEZONES = {
    "bh_daily_snapshot": "UTC_TZ",
    "via_daily_learning": "US_EASTERN_TZ",
    "vkpi_recommendation_outcomes": "CHINA_TZ",
    "vkpi_fulfillment_sweep": "US_EASTERN_TZ",
    "vkpi_kpi_rollup": "US_EASTERN_TZ",
    "logistics_track_sync": "US_EASTERN_TZ",
    "token_broker_reset_daily": "US_EASTERN_TZ",
    "vkpi_weekly_report": "US_EASTERN_TZ",
    "vkpi_comment_sentiment_refresh": "US_EASTERN_TZ",
    "vkpi_sentiment_annotate": "US_EASTERN_TZ",
    "vkpi_market_mention_sentiment": "US_EASTERN_TZ",
    "vkpi_content_fit_batch_refresh": "US_EASTERN_TZ",
    "vkpi_fit_snapshot": "US_EASTERN_TZ",
    "vkpi_brief_agent": "US_EASTERN_TZ",
    "market_voice_alerts": "US_EASTERN_TZ",
    "fulfillment_content_scan": "US_EASTERN_TZ",
    "fulfillment_window_backfill": "US_EASTERN_TZ",
    "fulfillment_retrospective_enqueue": "US_EASTERN_TZ",
    "fulfillment_due_scan": "US_EASTERN_TZ",
    "vkpi_official_daily_report_asia": "CHINA_TZ",
    "vkpi_official_daily_report_americas": "US_PACIFIC_TZ",
}


def _is_cron_trigger(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Name) and call.func.id == "CronTrigger"


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((item.value for item in call.keywords if item.arg == name), None)


def _registered_job_timezones() -> dict[str, str]:
    registrations: dict[str, str] = {}
    for path in sorted(SCHEDULER_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            trigger = _keyword(call, "trigger")
            job_id = _keyword(call, "id")
            if not isinstance(trigger, ast.Call) or not _is_cron_trigger(trigger):
                continue
            if not isinstance(job_id, ast.Constant) or not isinstance(job_id.value, str):
                continue
            timezone = _keyword(trigger, "timezone")
            registrations[job_id.value] = ast.unparse(timezone) if timezone is not None else ""
    return registrations


def test_every_scheduler_cron_trigger_declares_timezone() -> None:
    missing: list[str] = []
    for path in sorted(SCHEDULER_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not _is_cron_trigger(call):
                continue
            if _keyword(call, "timezone") is None:
                missing.append(f"{path.relative_to(ROOT)}:{call.lineno}")

    assert not missing, "CronTrigger without explicit timezone: " + ", ".join(missing)


@pytest.mark.parametrize(
    ("job_id", "expected_timezone"),
    sorted(EXPECTED_JOB_TIMEZONES.items()),
)
def test_scheduler_job_uses_expected_business_timezone(
    job_id: str,
    expected_timezone: str,
) -> None:
    registrations = _registered_job_timezones()
    assert registrations.get(job_id) == expected_timezone


@pytest.mark.parametrize(
    ("timezone", "expected_key"),
    (
        (jobs.CHINA_TZ, "Asia/Shanghai"),
        (jobs.US_PACIFIC_TZ, "America/Los_Angeles"),
        (jobs.UTC_TZ, "UTC"),
        (jobs.US_EASTERN_TZ, "America/New_York"),
    ),
)
def test_scheduler_timezone_bindings_are_iana_zones(timezone: object, expected_key: str) -> None:
    assert getattr(timezone, "key", None) == expected_key
