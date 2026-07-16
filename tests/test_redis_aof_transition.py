from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import redis_aof_transition as transition


class FakeRedis:
    def __init__(self, *, already_enabled: bool = False) -> None:
        self.config = {
            "appendonly": "yes" if already_enabled else "no",
            "appendfsync": "everysec",
            "save": "3600 1 300 100 60 10000",
        }
        self.persistence: dict[str, Any] = {
            "aof_enabled": 1 if already_enabled else 0,
            "aof_last_write_status": "ok",
            "rdb_last_save_time": 1_700_000_000,
            "rdb_bgsave_in_progress": 0,
            "rdb_last_bgsave_status": "ok",
            "aof_rewrite_in_progress": 0,
            "ignored_server_field": "must-not-leak",
        }
        self.size = 17
        self.calls: list[tuple[object, ...]] = []
        self.closed = False
        self.fail_bgsave = False
        self.fail_rewrite = False
        self.extra_config_field = False

    def ping(self) -> bool:
        self.calls.append(("ping",))
        return True

    def dbsize(self) -> int:
        self.calls.append(("dbsize",))
        return self.size

    def config_get(self, pattern: str) -> dict[str, str]:
        self.calls.append(("config_get", pattern))
        result = {pattern: self.config[pattern]}
        if self.extra_config_field:
            result["unexpected"] = "value"
        return result

    def info(self, section: str) -> dict[str, Any]:
        self.calls.append(("info", section))
        assert section == "persistence"
        return dict(self.persistence)

    def bgsave(self) -> bool:
        self.calls.append(("bgsave",))
        if self.fail_bgsave:
            return False
        self.persistence["rdb_last_save_time"] += 1
        return True

    def config_set(self, name: str, value: str) -> bool:
        self.calls.append(("config_set", name, value))
        self.config[name] = value
        if name == "appendonly" and value == "yes":
            self.persistence["aof_enabled"] = 1
            self.persistence["aof_last_write_status"] = "ok"
        return True

    def config_rewrite(self) -> object:
        self.calls.append(("config_rewrite",))
        return "OK" if not self.fail_rewrite else False

    def close(self) -> None:
        self.closed = True
        self.calls.append(("close",))


def _env(tmp_path: Path, url: str = "redis://:never-print-me@127.0.0.1:6379/0") -> Path:
    path = tmp_path / "production.env"
    path.write_text(
        f"ENVIRONMENT=production\nREDIS_URL={url}\nOTHER_VALUE=preserved\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _factory(client: FakeRedis):
    return lambda _url: client


def _digest_without_self(payload: dict[str, object]) -> str:
    base = dict(payload)
    base.pop("pre_state_sha256")
    encoded = json.dumps(base, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def test_inspect_is_deterministic_secret_free_and_digest_bound(tmp_path: Path) -> None:
    env_file = _env(tmp_path)
    first_client = FakeRedis()
    second_client = FakeRedis()

    first = transition.inspect_redis(
        env_file=env_file,
        client_factory=_factory(first_client),
    )
    second = transition.inspect_redis(
        env_file=env_file,
        client_factory=_factory(second_client),
    )

    assert first == second
    assert first == {
        "schema": transition.SCHEMA,
        "host_kind": "loopback",
        "port": 6379,
        "db": 0,
        "dbsize": 17,
        "appendonly": "no",
        "appendfsync": "everysec",
        "aof_enabled": 0,
        "aof_last_write_status": "ok",
        "rdb_last_save_time": 1_700_000_000,
        "rdb_bgsave_in_progress": 0,
        "rdb_last_bgsave_status": "ok",
        "aof_rewrite_in_progress": 0,
        "persistence_config": {
            "appendonly": "no",
            "appendfsync": "everysec",
            "save": "3600 1 300 100 60 10000",
        },
        "pre_state_sha256": first["pre_state_sha256"],
    }
    assert first["pre_state_sha256"] == _digest_without_self(first)
    serialized = json.dumps(first, sort_keys=True)
    assert "never-print-me" not in serialized
    assert "REDIS_URL" not in serialized
    assert "ignored_server_field" not in serialized
    assert first_client.closed and second_client.closed


@pytest.mark.parametrize(
    "url",
    [
        "redis://example.com:6379/0",
        "redis+unix:///tmp/redis.sock",
        "http://127.0.0.1:6379/0",
        "redis://127.0.0.1:6379/not-a-db",
        "redis://127.0.0.1:6379/0?decode=true",
    ],
)
def test_inspect_refuses_non_loopback_or_unexpected_url_fields(
    tmp_path: Path,
    url: str,
) -> None:
    env_file = _env(tmp_path, url)
    touched = False

    def factory(_url: str) -> FakeRedis:
        nonlocal touched
        touched = True
        return FakeRedis()

    with pytest.raises(transition.TransitionError):
        transition.inspect_redis(env_file=env_file, client_factory=factory)
    assert touched is False


@pytest.mark.parametrize("mode", [0o000, 0o444, 0o644, 0o660, 0o666])
def test_inspect_refuses_unsafe_dotenv_permissions(tmp_path: Path, mode: int) -> None:
    env_file = _env(tmp_path)
    env_file.chmod(mode)

    with pytest.raises(transition.TransitionError, match="mode|opened safely"):
        transition.inspect_redis(env_file=env_file, client_factory=_factory(FakeRedis()))


@pytest.mark.parametrize("mode", [0o400, 0o440, 0o600, 0o640])
def test_inspect_accepts_only_reviewed_private_dotenv_modes(
    tmp_path: Path,
    mode: int,
) -> None:
    env_file = _env(tmp_path)
    env_file.chmod(mode)
    result = transition.inspect_redis(
        env_file=env_file,
        allowed_group_ids={os.getegid()},
        client_factory=_factory(FakeRedis()),
    )
    assert result["host_kind"] == "loopback"


def test_inspect_refuses_symlink_hardlink_duplicate_and_unexpected_config(
    tmp_path: Path,
) -> None:
    env_file = _env(tmp_path)
    symlink = tmp_path / "linked.env"
    symlink.symlink_to(env_file)
    with pytest.raises(transition.TransitionError):
        transition.inspect_redis(env_file=symlink, client_factory=_factory(FakeRedis()))

    hardlink = tmp_path / "hardlinked.env"
    os.link(env_file, hardlink)
    with pytest.raises(transition.TransitionError, match="hard link"):
        transition.inspect_redis(env_file=env_file, client_factory=_factory(FakeRedis()))
    hardlink.unlink()

    env_file.write_text(
        "REDIS_URL=redis://127.0.0.1/0\nREDIS_URL=redis://localhost/0\n",
        encoding="utf-8",
    )
    with pytest.raises(transition.TransitionError, match="exactly one"):
        transition.inspect_redis(env_file=env_file, client_factory=_factory(FakeRedis()))

    env_file.write_text("REDIS_URL=redis://127.0.0.1/0\n", encoding="utf-8")
    client = FakeRedis()
    client.extra_config_field = True
    with pytest.raises(transition.TransitionError, match="unexpected"):
        transition.inspect_redis(env_file=env_file, client_factory=_factory(client))


def test_enable_binds_exact_pre_state_and_writes_durable_secret_free_receipt(
    tmp_path: Path,
) -> None:
    env_file = _env(tmp_path)
    client = FakeRedis()
    before = transition.inspect_redis(env_file=env_file, client_factory=_factory(client))
    receipt_path = tmp_path / "redis-aof-receipt.json"

    receipt = transition.enable_aof(
        env_file=env_file,
        expected_pre_state_sha256=str(before["pre_state_sha256"]),
        confirm=str(before["pre_state_sha256"]),
        receipt_path=receipt_path,
        client_factory=_factory(client),
        sleep=lambda _seconds: None,
    )

    assert receipt_path.is_file() and not receipt_path.is_symlink()
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt
    assert receipt["pre_state_sha256"] == before["pre_state_sha256"]
    assert receipt["post_state_sha256"] != before["pre_state_sha256"]
    assert receipt["verified"] == {
        "appendonly": "yes",
        "appendfsync": "everysec",
        "aof_enabled": 1,
        "aof_last_write_status": "ok",
        "config_rewrite": True,
    }
    assert ("bgsave",) in client.calls
    assert ("config_set", "appendonly", "yes") in client.calls
    assert ("config_set", "appendfsync", "everysec") in client.calls
    assert ("config_rewrite",) in client.calls
    output = receipt_path.read_text(encoding="utf-8")
    assert "never-print-me" not in output
    assert "REDIS_URL" not in output
    assert "127.0.0.1" not in output


def test_enable_refuses_pre_state_drift_before_any_mutation(tmp_path: Path) -> None:
    env_file = _env(tmp_path)
    client = FakeRedis()
    before = transition.inspect_redis(env_file=env_file, client_factory=_factory(client))
    client.size += 1
    client.calls.clear()
    receipt = tmp_path / "must-not-exist.json"

    with pytest.raises(transition.TransitionError, match="drifted"):
        transition.enable_aof(
            env_file=env_file,
            expected_pre_state_sha256=str(before["pre_state_sha256"]),
            confirm=str(before["pre_state_sha256"]),
            receipt_path=receipt,
            client_factory=_factory(client),
        )

    assert ("bgsave",) not in client.calls
    assert not any(call[0] == "config_set" for call in client.calls)
    assert not receipt.exists()


def test_enable_refuses_existing_receipt_before_any_mutation(tmp_path: Path) -> None:
    env_file = _env(tmp_path)
    client = FakeRedis()
    before = transition.inspect_redis(env_file=env_file, client_factory=_factory(client))
    receipt = tmp_path / "existing.json"
    receipt.write_text("do-not-overwrite\n", encoding="utf-8")
    client.calls.clear()

    with pytest.raises(transition.TransitionError, match="already exists"):
        transition.enable_aof(
            env_file=env_file,
            expected_pre_state_sha256=str(before["pre_state_sha256"]),
            confirm=str(before["pre_state_sha256"]),
            receipt_path=receipt,
            client_factory=_factory(client),
        )

    assert receipt.read_text(encoding="utf-8") == "do-not-overwrite\n"
    assert ("bgsave",) not in client.calls
    assert not any(call[0] == "config_set" for call in client.calls)


def test_enable_failure_removes_reserved_receipt_and_never_claims_success(
    tmp_path: Path,
) -> None:
    env_file = _env(tmp_path)
    client = FakeRedis()
    before = transition.inspect_redis(env_file=env_file, client_factory=_factory(client))
    client.fail_bgsave = True
    receipt = tmp_path / "failed.json"

    with pytest.raises(transition.TransitionError, match="BGSAVE"):
        transition.enable_aof(
            env_file=env_file,
            expected_pre_state_sha256=str(before["pre_state_sha256"]),
            confirm=str(before["pre_state_sha256"]),
            receipt_path=receipt,
            client_factory=_factory(client),
        )
    assert not receipt.exists()
    assert not any(call[0] == "config_set" for call in client.calls)


def test_enable_is_idempotent_but_still_snapshots_rewrites_and_receipts(
    tmp_path: Path,
) -> None:
    env_file = _env(tmp_path)
    client = FakeRedis(already_enabled=True)
    before = transition.inspect_redis(env_file=env_file, client_factory=_factory(client))
    client.calls.clear()

    receipt = transition.enable_aof(
        env_file=env_file,
        expected_pre_state_sha256=str(before["pre_state_sha256"]),
        confirm=str(before["pre_state_sha256"]),
        receipt_path=tmp_path / "already-enabled.json",
        client_factory=_factory(client),
        sleep=lambda _seconds: None,
    )

    assert receipt["verified"]["aof_enabled"] == 1
    assert ("bgsave",) in client.calls
    assert ("config_set", "appendonly", "yes") in client.calls
    assert ("config_rewrite",) in client.calls


def test_main_redacts_secret_on_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "unique-password-that-must-never-appear"
    env_file = _env(tmp_path, f"redis://:{secret}@not-loopback.example:6379/0")

    result = transition.main(["inspect", "--env-file", str(env_file)])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert secret not in captured.err
    assert "REDIS_URL" in captured.err
