from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import Request

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import fetch_runtime_health as probe


class _Response:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int):
        return self.payload


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/health",
        "http://127.0.0.1/private",
        "http://user:pass@127.0.0.1/health",
        "file:///health",
    ],
)
def test_fetch_rejects_any_non_loopback_health_target(tmp_path: Path, url: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPS_HEALTH_TOKEN=top-secret\n", encoding="utf-8")
    env_file.chmod(0o600)

    with pytest.raises(ValueError, match="loopback /health"):
        probe.fetch_runtime_health(url=url, env_file=env_file)


def test_fetch_reads_remote_secret_and_sends_header_without_returning_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPS_HEALTH_TOKEN=top-secret\n", encoding="utf-8")
    env_file.chmod(0o600)
    monkeypatch.delenv("OPS_HEALTH_TOKEN", raising=False)
    observed: dict[str, object] = {}

    def fake_urlopen(request: Request, *, timeout: float):
        observed["url"] = request.full_url
        observed["token"] = request.get_header("X-ops-token")
        observed["timeout"] = timeout
        return _Response({"status": "ok", "build": {"git_sha": "a" * 40}, "trust": {}})

    monkeypatch.setattr(probe, "urlopen", fake_urlopen)

    result = probe.fetch_runtime_health(
        url="http://127.0.0.1:8001/health",
        env_file=env_file,
        timeout_seconds=2,
    )

    assert observed == {
        "url": "http://127.0.0.1:8001/health",
        "token": "top-secret",
        "timeout": 2.0,
    }
    assert result["status"] == "ok"
    assert "top-secret" not in json.dumps(result)


def test_fetch_rejects_missing_token_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ENVIRONMENT=production\n", encoding="utf-8")
    env_file.chmod(0o600)
    monkeypatch.delenv("OPS_HEALTH_TOKEN", raising=False)
    monkeypatch.setattr(
        probe,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network touched")),
    )

    with pytest.raises(ValueError, match="not configured"):
        probe.fetch_runtime_health(url="http://localhost:8001/health", env_file=env_file)


def test_fetch_rejects_symlink_token_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = tmp_path / "real.env"
    real.write_text("OPS_HEALTH_TOKEN=top-secret\n", encoding="utf-8")
    real.chmod(0o600)
    linked = tmp_path / ".env"
    linked.symlink_to(real)
    monkeypatch.delenv("OPS_HEALTH_TOKEN", raising=False)

    with pytest.raises(OSError):
        probe.fetch_runtime_health(url="http://localhost:8001/health", env_file=linked)


@pytest.mark.parametrize("mode", [0o644, 0o640])
def test_fetch_rejects_non_private_token_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPS_HEALTH_TOKEN=top-secret\n", encoding="utf-8")
    env_file.chmod(mode)
    monkeypatch.delenv("OPS_HEALTH_TOKEN", raising=False)

    with pytest.raises(ValueError, match="owner-only"):
        probe.fetch_runtime_health(url="http://localhost:8001/health", env_file=env_file)


def test_fetch_rejects_hardlinked_token_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPS_HEALTH_TOKEN=top-secret\n", encoding="utf-8")
    env_file.chmod(0o600)
    os.link(env_file, tmp_path / "second-link.env")
    monkeypatch.delenv("OPS_HEALTH_TOKEN", raising=False)

    with pytest.raises(ValueError, match="single-link"):
        probe.fetch_runtime_health(url="http://localhost:8001/health", env_file=env_file)


def test_stdin_bootstrap_does_not_require_stdout_utils(tmp_path: Path) -> None:
    """Legacy hosts can execute the probe before rsync installs helpers."""

    source = (ROOT / "scripts" / "ops" / "fetch_runtime_health.py").read_text(
        encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, "-", "--help"],
        input=source,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--env-file" in result.stdout
