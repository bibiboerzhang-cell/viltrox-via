from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app.core import stateless_alert


ROOT = Path(__file__).resolve().parents[1]
FAKE_URL = "https://example.invalid/hooks/private-token-123"
FAKE_SECRET = "feishu-signing-secret-456"


@pytest.fixture(autouse=True)
def _clean_alert_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        stateless_alert.ENV_WEBHOOK_URL,
        stateless_alert.ENV_WEBHOOK_KIND,
        stateless_alert.ENV_WEBHOOK_SECRET,
        stateless_alert.ENV_WEBHOOK_TIMEOUT_S,
        stateless_alert.ENV_SILENCE_KEYS,
    ):
        monkeypatch.delenv(key, raising=False)


def test_watchdog_cold_import_survives_broken_database_and_uses_fake_transport() -> None:
    code = f"""
import importlib.abc
import json
import os
import sys

sys.path.insert(0, {str(ROOT)!r})
sys.path.insert(0, {str(ROOT / 'backend')!r})

blocked = (
    "app.core.config",
    "app.db.connection",
    "app.db.migrate",
    "app.domains.ops",
)

class BrokenApplicationImport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise RuntimeError("blocked cold import: " + fullname)
        return None

sys.meta_path.insert(0, BrokenApplicationImport())
from app.core import stateless_alert
from scripts.ops import vkpi_sync_watchdog

assert vkpi_sync_watchdog.stateless_alert is stateless_alert
assert all(name not in sys.modules for name in blocked)
os.environ[stateless_alert.ENV_WEBHOOK_URL] = {FAKE_URL!r}
captured = []

def fake_transport(payload, timeout_s):
    captured.append({{"payload": payload, "timeout_s": timeout_s}})
    return 204, "ok"

core_notify = stateless_alert.notify_stateless
def notify_through_fake_transport(**kwargs):
    return core_notify(transport=fake_transport, **kwargs)

vkpi_sync_watchdog.stateless_alert.notify_stateless = notify_through_fake_transport
result = vkpi_sync_watchdog._notify(
    key="cold-import",
    title="database import is broken",
    body="failure path remains observable",
)
assert result == {{
    "configured": True,
    "kind": "generic",
    "key": "cold-import",
    "sent": True,
    "reason": "sent",
    "status": 204,
}}
assert len(captured) == 1
try:
    import app.db.connection
except RuntimeError as exc:
    assert str(exc) == "blocked cold import: app.db.connection"
else:
    raise AssertionError("database import blocker was not active")
print(json.dumps({{"result": result, "forbidden_loaded": False}}, sort_keys=True))
"""
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code],
        check=False,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["result"]["sent"] is True
    assert payload["forbidden_loaded"] is False
    assert FAKE_URL not in completed.stdout + completed.stderr


def test_stateless_alert_is_fail_closed_and_never_leaks_channel_secrets(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    capture: list[dict[str, Any]] = []

    def fake(payload: dict[str, Any], timeout_s: float) -> tuple[int, str]:
        capture.append(payload)
        return 200, "ok"

    missing = stateless_alert.notify_stateless(key="deadman", title="failed", transport=fake)
    assert missing["sent"] is False and missing["reason"] == "not_configured"
    assert capture == []

    monkeypatch.setenv(stateless_alert.ENV_WEBHOOK_URL, FAKE_URL)
    monkeypatch.setenv(stateless_alert.ENV_WEBHOOK_SECRET, FAKE_SECRET)
    monkeypatch.setenv(stateless_alert.ENV_SILENCE_KEYS, "deadman")
    silenced = stateless_alert.notify_stateless(key="deadman", title="failed", transport=fake)
    assert silenced["sent"] is False and silenced["reason"] == "silenced"
    assert capture == []

    monkeypatch.delenv(stateless_alert.ENV_SILENCE_KEYS)

    def non_2xx(payload: dict[str, Any], timeout_s: float) -> tuple[int, str]:
        return 503, f"upstream={FAKE_URL} secret={FAKE_SECRET}"

    with caplog.at_level(logging.DEBUG, logger="viltrox.stateless_alert"):
        rejected = stateless_alert.notify_stateless(key="deadman", title="failed", transport=non_2xx)
    assert rejected == {
        "configured": True,
        "kind": "generic",
        "key": "deadman",
        "sent": False,
        "reason": "http_error",
        "status": 503,
    }
    safe_blob = repr(rejected) + repr(stateless_alert.outbound_status()) + caplog.text
    assert FAKE_URL not in safe_blob
    assert FAKE_SECRET not in safe_blob
    assert "private-token-123" not in safe_blob


def test_stateless_alert_rejects_insecure_or_credentialed_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for invalid_url in (
        "http://example.invalid/hook",
        "https://user:password@example.invalid/hook",
        "not-a-url",
    ):
        monkeypatch.setenv(stateless_alert.ENV_WEBHOOK_URL, invalid_url)
        result = stateless_alert.notify_stateless(key="deadman", title="failed")
        assert result["configured"] is False
        assert result["reason"] == "not_configured"


def test_stateless_alert_transport_rejects_every_redirect() -> None:
    handler = stateless_alert._NoRedirectHandler()

    for status in (301, 302, 303, 307, 308):
        assert handler.redirect_request(
            None,
            None,
            status,
            "redirect",
            {},
            "http://attacker.example/collect",
        ) is None


def test_stateless_alert_transport_rejects_changed_or_downgraded_final_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(stateless_alert.ENV_WEBHOOK_URL, FAKE_URL)

    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self) -> str:
            return "http://attacker.example/collect"

    class Opener:
        def open(self, _request, *, timeout):
            assert timeout >= 1
            return Response()

    monkeypatch.setattr(stateless_alert.urllib_request, "build_opener", lambda *_handlers: Opener())

    result = stateless_alert.notify_stateless(key="deadman", title="failed")

    assert result["sent"] is False
    assert result["reason"] == "delivery_error"
    assert result["error"] == "RuntimeError"
