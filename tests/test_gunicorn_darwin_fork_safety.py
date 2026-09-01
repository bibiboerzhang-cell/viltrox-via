from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from gunicorn import util

from deploy import gunicorn_config


def test_darwin_startup_disables_optional_post_fork_process_title(
    monkeypatch,
) -> None:
    attempted_titles: list[str] = []
    monkeypatch.setattr(gunicorn_config.sys, "platform", "darwin")
    monkeypatch.setattr(util, "_setproctitle", attempted_titles.append)
    server = SimpleNamespace(log=Mock())

    gunicorn_config.on_starting(server)
    util._setproctitle("worker [app.main:app]")

    assert attempted_titles == []
    assert util._setproctitle is gunicorn_config._noop_process_title
    server.log.info.assert_any_call(
        "Darwin fork safety: optional Gunicorn process-title updates disabled"
    )


def test_non_darwin_startup_preserves_gunicorn_process_titles(monkeypatch) -> None:
    attempted_titles: list[str] = []
    monkeypatch.setattr(gunicorn_config.sys, "platform", "linux")
    monkeypatch.setattr(util, "_setproctitle", attempted_titles.append)
    server = SimpleNamespace(log=Mock())

    gunicorn_config.on_starting(server)
    util._setproctitle("worker [app.main:app]")

    assert attempted_titles == ["worker [app.main:app]"]
