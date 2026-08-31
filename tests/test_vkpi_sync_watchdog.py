from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.ops import vkpi_sync_watchdog as watchdog


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_silenced_watchdog_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watchdog.stateless_alert, "silenced_keys", lambda: frozenset())


def _result(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def test_unit_failure_missing_webhook_is_explicit_and_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        watchdog.stateless_alert,
        "notify_stateless",
        lambda **kwargs: {
            "configured": False,
            "kind": "generic",
            "key": kwargs["key"],
            "sent": False,
            "reason": "not_configured",
        },
    )

    assert watchdog.run_unit_failure("vkpi-sync-daily.service") == 2
    payload = _result(capsys)
    assert payload["delivery"]["reason"] == "not_configured"
    assert payload["delivery"]["configured"] is False


def test_unit_failure_returns_success_only_after_delivery(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        watchdog.stateless_alert,
        "notify_stateless",
        lambda **kwargs: {
            "configured": True,
            "kind": "feishu",
            "key": kwargs["key"],
            "sent": True,
            "reason": "sent",
            "status": 200,
        },
    )

    assert watchdog.run_unit_failure("vkpi-sync-daily.service") == 0
    assert _result(capsys)["delivery"]["sent"] is True


def test_deadman_healthy_audit_still_fails_closed_without_alert_channel(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(watchdog, "_run_audit", lambda args: ({"acceptance_ready": True}, 0))
    monkeypatch.setattr(
        watchdog.stateless_alert,
        "outbound_status",
        lambda: {"configured": False, "kind": "generic", "signed": False},
    )

    assert watchdog.run_deadman(SimpleNamespace()) == 2
    payload = _result(capsys)
    assert payload["reason"] == "alert_channel_not_configured"
    assert payload["status"] == "failed"


def test_deadman_healthy_audit_fails_closed_when_its_key_is_silenced(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(watchdog, "_run_audit", lambda args: ({"acceptance_ready": True}, 0))
    monkeypatch.setattr(
        watchdog.stateless_alert,
        "outbound_status",
        lambda: {"configured": True, "kind": "generic", "signed": False},
    )
    monkeypatch.setattr(
        watchdog.stateless_alert,
        "silenced_keys",
        lambda: frozenset({"daily-sync-deadman"}),
    )

    assert watchdog.run_deadman(SimpleNamespace()) == 2
    payload = _result(capsys)
    assert payload["reason"] == "alert_channel_silenced"
    assert payload["alert_channel"]["deadman_silenced"] is True


def test_deadman_failed_audit_delivers_bounded_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audit = {
        "acceptance_ready": False,
        "failed_checks": ["official_all_synced", "maintenance_completed"],
        "service_state": "failed",
        "sync_log": {"batch_id": "daily-1"},
    }
    delivered: dict = {}
    monkeypatch.setattr(watchdog, "_run_audit", lambda args: (audit, 1))
    monkeypatch.setattr(
        watchdog.stateless_alert,
        "outbound_status",
        lambda: {"configured": True, "kind": "generic", "signed": False},
    )

    def notify(**kwargs):
        delivered.update(kwargs)
        return {"configured": True, "kind": "generic", "sent": True, "reason": "sent"}

    monkeypatch.setattr(watchdog.stateless_alert, "notify_stateless", notify)

    assert watchdog.run_deadman(SimpleNamespace()) == 1
    payload = _result(capsys)
    assert payload["audit"]["batch_id"] == "daily-1"
    assert payload["audit"]["failed_checks"] == ["official_all_synced", "maintenance_completed"]
    assert delivered["key"] == "daily-sync-deadman"
    assert "official_all_synced,maintenance_completed" in delivered["body"]


def test_deadman_delivery_failure_is_distinct_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        watchdog,
        "_run_audit",
        lambda args: ({"acceptance_ready": False, "failed_checks": ["audit_timeout"]}, 124),
    )
    monkeypatch.setattr(
        watchdog.stateless_alert,
        "outbound_status",
        lambda: {"configured": True, "kind": "generic", "signed": False},
    )
    monkeypatch.setattr(
        watchdog.stateless_alert,
        "notify_stateless",
        lambda **kwargs: {
            "configured": True,
            "kind": "generic",
            "sent": False,
            "reason": "delivery_error",
            "error": "TimeoutError",
        },
    )

    assert watchdog.run_deadman(SimpleNamespace()) == 2
    assert _result(capsys)["delivery"]["reason"] == "delivery_error"


def test_systemd_units_use_env_only_alert_config_and_journal() -> None:
    alert = (ROOT / "scripts/ops/systemd/vkpi-sync-daily-alert@.service").read_text(encoding="utf-8")
    deadman = (ROOT / "scripts/ops/systemd/vkpi-sync-deadman.service").read_text(encoding="utf-8")
    timer = (ROOT / "scripts/ops/systemd/vkpi-sync-deadman.timer").read_text(encoding="utf-8")

    for unit in (alert, deadman):
        assert "User=viltrox" in unit
        assert "Group=viltrox" in unit
        assert "EnvironmentFile=/opt/viltrox-2.0/.env" in unit
        assert "vkpi_sync_watchdog.py" in unit
        assert "StandardOutput=journal" in unit
        assert "NoNewPrivileges=true" in unit
        assert "ProtectSystem=strict" in unit
        assert "http://" not in unit and "https://" not in unit
        assert "VKPI_ALERT_WEBHOOK_URL=" not in unit
    # The deadman sends its own detailed audit alert and exits non-zero, so it
    # stays failed/journal-visible without triggering a duplicate OnFailure send.
    assert "OnFailure=" not in deadman
    assert "OnCalendar=*-*-* 10:15:00 UTC" in timer
    assert "Persistent=true" in timer
    assert "Unit=vkpi-sync-deadman.service" in timer


def test_installer_replaces_local_only_alert_with_deadman_timer() -> None:
    installer = (ROOT / "scripts/ops/install_vkpi_daily_timers.sh").read_text(encoding="utf-8")
    deadman = (ROOT / "scripts/ops/systemd/vkpi-sync-deadman.service").read_text(
        encoding="utf-8"
    )

    assert "sync_failure_alert.log" not in installer
    assert "vkpi_sync_watchdog.py unit-failure" in installer
    assert "vkpi_sync_watchdog.py deadman" in installer
    assert "OnCalendar=*-*-* 10:15:00 UTC" in installer
    assert "systemctl enable vkpi-sync-deadman.timer vkpi-sync-daily.timer" in installer
    assert (
        "systemctl start --no-block vkpi-sync-deadman.timer "
        "vkpi-sync-daily.timer"
    ) in installer
    assert "VKPI_ALERT_WEBHOOK_URL=" not in installer

    deadman_installer_unit = installer.split("<<DEADMANSERVICE\n", 1)[1].split(
        "\nDEADMANSERVICE", 1
    )[0]
    assert "OnFailure=" not in deadman_installer_unit
    assert "OnFailure=" not in deadman
