from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "mint_browser_gate_token.py"
SPEC = importlib.util.spec_from_file_location("mint_browser_gate_token", SCRIPT)
assert SPEC and SPEC.loader
mint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mint)


SECRET = "test-browser-gate-secret-with-at-least-32-bytes"
ISSUER = "viltrox-vos"
AUDIENCE = "vos-app"


def test_minted_token_is_admin_and_strictly_bounded() -> None:
    issued_at = int(time.time())
    token = mint.mint_admin_token(
        user_id=42,
        secret=SECRET,
        issuer=ISSUER,
        audience=AUDIENCE,
        ttl_seconds=900,
        now=issued_at,
    )
    payload = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        issuer=ISSUER,
        audience=AUDIENCE,
    )
    assert payload["uid"] == 42
    assert payload["role"] == "admin"
    assert payload["exp"] - payload["iat"] == 900
    assert payload["nbf"] == payload["iat"]


@pytest.mark.parametrize("ttl", [0, 59, 901, 3600])
def test_minted_token_rejects_out_of_contract_ttl(ttl: int) -> None:
    with pytest.raises(mint.MintError):
        mint.mint_admin_token(
            user_id=42,
            secret=SECRET,
            issuer=ISSUER,
            audience=AUDIENCE,
            ttl_seconds=ttl,
        )


def test_admin_lookup_is_postgres_readonly_and_requires_reviewed_admin_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class Cursor:
        def __init__(self) -> None:
            self.rows = iter([("on",), (42,)])

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, query: str) -> None:
            calls.append(("query", " ".join(query.split())))

        def fetchone(self):
            return next(self.rows)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def cursor(self) -> Cursor:
            return Cursor()

        def rollback(self) -> None:
            calls.append(("rollback", True))

    def connect(database_url: str, **kwargs: object) -> Connection:
        calls.append(("database_url", database_url))
        calls.append(("options", kwargs.get("options")))
        return Connection()

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    assert mint.select_admin_user_id("postgresql://controlled.invalid/vkpi") == 42
    assert calls[1] == ("options", "-c default_transaction_read_only=on")
    query_text = " ".join(str(value) for key, value in calls if key == "query")
    for required in (
        "SHOW transaction_read_only",
        "u.status",
        "u.email_verified",
        "s.active",
        "s.role",
        "'admin'",
    ):
        assert required in query_text
    assert ("rollback", True) in calls


def test_cli_stdout_is_only_the_token_and_failures_never_echo_secrets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        mint,
        "load_runtime_contract",
        lambda: ("postgresql://controlled.invalid/vkpi", SECRET, ISSUER, AUDIENCE),
    )
    monkeypatch.setattr(mint, "select_admin_user_id", lambda _url: 42)
    assert mint.main(["--ttl-seconds", "60"]) == 0
    success = capsys.readouterr()
    assert success.err == ""
    assert success.out.count(".") == 2
    assert not any(character.isspace() for character in success.out)

    sensitive = "postgresql://user:do-not-print@db.invalid/vkpi"
    monkeypatch.setattr(
        mint,
        "load_runtime_contract",
        lambda: (_ for _ in ()).throw(mint.MintError(sensitive)),
    )
    assert mint.main([]) == 1
    failure = capsys.readouterr()
    assert failure.out == ""
    assert failure.err.strip() == "browser gate token mint failed"
    assert sensitive not in failure.err


def test_minter_source_has_no_persistence_path() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("write_text(", "write_bytes(", "json.dump(", "token_file"):
        assert forbidden not in source
