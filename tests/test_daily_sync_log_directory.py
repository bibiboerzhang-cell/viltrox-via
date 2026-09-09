"""Producer/readers must agree on the new systemd-owned daily log directory."""
from datetime import datetime, timezone
from pathlib import Path

from scripts.ops import audit_vkpi_post_sync_state as audit
from scripts.ops import vkpi_sync_watchdog as watchdog


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = "/var/log/vkpi-sync-daily"


class _FixedDateTime:
    @staticmethod
    def now(zone):
        assert zone is timezone.utc
        return datetime(2026, 9, 9, 0, 1, tzinfo=zone)


def test_watchdog_and_audit_use_producer_directory_and_utc_date(monkeypatch):
    monkeypatch.setattr(watchdog, "datetime", _FixedDateTime)
    monkeypatch.setattr(audit, "datetime", _FixedDateTime)
    expected = f"{DIRECTORY}/sync_daily_20260909.log"
    assert watchdog._today_log() == expected
    assert audit.parse_args([]).sync_log_path == expected


def test_explicit_historical_log_path_remains_supported():
    historical = "/var/log/vkpi/sync_daily_20260901.log"
    assert audit.parse_args(["--sync-log-path", historical]).sync_log_path == historical


def test_shell_status_default_and_fallback_match_producer():
    source = (ROOT / "scripts/ops/check_vkpi_daily_sync_status.sh").read_text()
    assert f"LOG_PATH=\"${{LOG_PATH:-{DIRECTORY}/sync_daily_${{LOG_DATE}}.log}}\"" in source
    assert f'os.environ.get("LOG_PATH") or "{DIRECTORY}/sync_daily.log"' in source
    assert "/var/log/vkpi/" not in source
