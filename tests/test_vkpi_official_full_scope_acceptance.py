from __future__ import annotations

from scripts import vkpi_official_full_scope_acceptance


def _fake_baseline_plan() -> dict:
    return {
        "account_count": 2,
        "platforms": ["instagram", "youtube"],
        "totals": {
            "baseline_target_items": 1500,
            "current_safe_first_batch_items": 150,
            "daily_recent_items": 60,
            "accounts_needing_full_unlock": 2,
        },
        "accounts": [
            {
                "channel_id": 1,
                "platform": "youtube",
                "handle": "viltroxofficial",
                "known_posts": 811,
                "daily_recent_limit": 30,
                "current_safe_limit": 50,
                "baseline_target": 811,
                "first_batch_action": "baseline_partial",
            },
            {
                "channel_id": 2,
                "platform": "instagram",
                "handle": "viltrox.official",
                "known_posts": 3686,
                "daily_recent_limit": 30,
                "current_safe_limit": 100,
                "baseline_target": 500,
                "first_batch_action": "baseline_partial",
            },
        ],
    }


def _fake_delta_report() -> dict:
    return {
        "provider_calls": False,
        "totals": {
            "accounts": 2,
            "channels_with_post_metrics": 2,
            "accounts_missing_post_metrics": 0,
            "baseline_protected_accounts": 2,
        },
    }


def test_full_scope_acceptance_passes_when_timer_and_manual_gate_are_safe(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_official_full_scope_acceptance.vkpi_official_baseline_plan, "build_plan", _fake_baseline_plan)
    monkeypatch.setattr(vkpi_official_full_scope_acceptance.vkpi_channel_delta_dry_run, "build_report", _fake_delta_report)

    report = vkpi_official_full_scope_acceptance.build_report(
        timer_command=".venv/bin/python scripts/cron_daily_sync.py --official-max-posts 50 --skip-kol"
    )

    assert report["provider_calls"] is False
    assert report["passed"] is True
    assert report["checks"]["manual_job_confirm_text"] is True
    assert report["checks"]["run_prod_wrapper_uses_manual_gate"] is True
    assert report["checks"]["current_timer_official_only"] is True
    assert report["checks"]["daily_recent_within_policy_caps"] is True
    assert report["baseline_plan"]["accounts_needing_full_unlock"] == 2


def test_full_scope_acceptance_flags_unsafe_timer(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_official_full_scope_acceptance.vkpi_official_baseline_plan, "build_plan", _fake_baseline_plan)
    monkeypatch.setattr(vkpi_official_full_scope_acceptance.vkpi_channel_delta_dry_run, "build_report", _fake_delta_report)

    report = vkpi_official_full_scope_acceptance.build_report(
        timer_command=".venv/bin/python scripts/cron_daily_sync.py --official-max-posts 50 --include-legacy-kol"
    )

    assert report["passed"] is False
    assert report["checks"]["current_timer_official_only"] is False


def test_full_scope_acceptance_markdown_lists_guard_checks(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_official_full_scope_acceptance.vkpi_official_baseline_plan, "build_plan", _fake_baseline_plan)
    monkeypatch.setattr(vkpi_official_full_scope_acceptance.vkpi_channel_delta_dry_run, "build_report", _fake_delta_report)

    report = vkpi_official_full_scope_acceptance.build_report(
        timer_command=".venv/bin/python scripts/cron_daily_sync.py --official-max-posts 50 --skip-kol"
    )
    markdown = vkpi_official_full_scope_acceptance.render_markdown(report)

    assert "Official Full-Scope Refresh Acceptance" in markdown
    assert "`run_prod_wrapper_uses_manual_gate`: `True`" in markdown
    assert "baseline_partial" in markdown
